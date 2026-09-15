import {plan,costs,gpuEstimate} from './math.mjs';

const $=id=>document.getElementById(id);
const fields=['row','samples','stride','shift','start','end','draw_start'];
const nf=new Intl.NumberFormat('en-US',{maximumFractionDigits:0});
let metadata=null,lastResult=null,currentCost=null,revision=0,timer,controller,questionRevision=0;
let activeTask=Promise.resolve();
const cache=new Map();
const config=()=>Object.fromEntries(fields.map(k=>[k,$(k).value.trim()===''?NaN:Number($(k).value)]));
const compact=n=>n>=1e6?`${(n/1e6).toFixed(2)}M`:n>=1000?`${(n/1000).toFixed(1)}k`:nf.format(n);

async function json(url,options){const r=await window.workerFetch(url,options);const value=await r.json();if(!r.ok)throw new Error(value.error||'Could not load the recorded data.');return value;}
async function getMetadata(row){if(!cache.has(row))cache.set(row,await json(`/api/question?row=${row}`));return cache.get(row);}
function error(message){$('error').textContent=message;$('error').hidden=!message;document.body.classList.toggle('invalid',Boolean(message));}
function patterns(p){$('first-pattern').textContent=p.first.slice(0,4).join(', ')+(p.first.length>4?'…':'');$('second-pattern').textContent=p.second.slice(0,4).join(', ')+(p.second.length>4?'…':'');}
function numberAssumption(id){return $(id).value.trim()===''?null:Number($(id).value);}
function gpu(){
  const box=$('gpu-result');box.replaceChildren();
  if(!currentCost){box.textContent='Choose valid sampling settings first.';return;}
  const throughput=numberAssumption('throughput'), rate=numberAssumption('hourly-rate');
  if((throughput!==null && (!Number.isFinite(throughput)||throughput<=0))||(rate!==null && (!Number.isFinite(rate)||rate<0))){box.textContent='Use positive throughput and a non-negative hourly rate.';return;}
  const first=gpuEstimate(currentCost.combined,throughput,rate),dense=gpuEstimate(currentCost.dense,throughput,rate);
  if(first.hours===null){box.textContent='Enter throughput to estimate time.';return;}
  for(const [label,value] of [['Both passes',first],['Every position',dense]]){const p=document.createElement('p');p.textContent=`${label}: ${value.hours.toFixed(2)} hours${value.dollars!==null?' · '+value.dollars.toLocaleString('en-US',{style:'currency',currency:'USD'}):''}`;box.append(p);}
  const foot=document.createElement('p');foot.className='help';foot.textContent='Projection from your inputs; excludes fixed overhead and confirmation runs.';box.append(foot);
}
function updateCost(c){
  const p=plan(c,metadata.last_token);currentCost=costs(c,metadata.expected_lengths,p);patterns(p);
  $('combined-tokens').textContent=compact(currentCost.combined);$('dense-tokens').textContent=compact(currentCost.dense);
  $('combined-tokens').title=nf.format(currentCost.combined)+' expected continuation tokens';$('dense-tokens').title=nf.format(currentCost.dense)+' expected continuation tokens';
  $('saving').textContent=currentCost.saving===null?'—':`${(currentCost.saving*100).toFixed(1)}%`;
  $('checkpoints').textContent=`${nf.format(currentCost.combinedCheckpoints)} / ${nf.format(currentCost.denseCheckpoints)}`;
  $('continuations').textContent=`${nf.format(currentCost.combinedContinuations)} vs ${nf.format(currentCost.denseContinuations)} continuations`;
  $('first-cost').textContent=compact(currentCost.first)+' tokens';$('second-cost').textContent=compact(currentCost.second)+' tokens';
  $('checkpoint-count').textContent=`${p.first.length} checkpoints per pass`;
  gpu();return p;
}
function setMetadata(m){
  metadata=m;$('model-label').textContent=`Llama-3-8B-Instruct · Row ${String(m.row).padStart(3,'0')} · ${m.last_token+1} tokens`;
  $('question-text').textContent=m.question;$('answer-key').textContent=`Correct: ${m.answer}`;
  $('choices').replaceChildren(...m.choices.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  $('start').max=m.last_token-1;$('end').max=m.last_token;
  $('region-help').textContent=`Available positions: 0–${m.last_token}. The same region is used for the dense baseline.`;
  $('category').replaceChildren(...m.categories.map(k=>{const o=document.createElement('option');o.value=k;o.textContent=k+(k===m.answer?' · correct':'');return o;}));
  $('category').value=m.answer;
}
function drawTrace(result){
  const a=new Set(result.first.positions),b=new Set(result.second.positions);
  const fragment=document.createDocumentFragment();metadata.tokens.forEach((text,t)=>{const span=document.createElement('span');span.className='token'+(a.has(t)?' first':b.has(t)?' second':'');span.dataset.token=t;span.title=`Token ${t}${a.has(t)?' · Pass 1':b.has(t)?' · Pass 2':''}`;span.textContent=text;fragment.append(span);});$('trace').replaceChildren(fragment);
}
async function draw(result){
  const k=metadata.categories.indexOf($('category').value),traces=[];
  const text=positions=>positions.map(t=>`Token ${t}: ${JSON.stringify(metadata.tokens[t]??'')}`);
  if($('show-reference').checked)traces.push({x:result.reference.positions,y:result.reference.values.map(v=>v[k]),name:'Recorded reference',type:'scatter',mode:'lines',line:{color:'#9aa5b5',width:1.5},customdata:text(result.reference.positions),hovertemplate:'%{customdata}<br>P = %{y:.3f}<extra>Reference</extra>'});
  for(const [key,label,color,dash] of [['first','Pass 1','#2563eb','solid'],['second','Pass 2','#c87816','dash']]){
    const p=result[key];
    if($('show-smooth').checked)traces.push({x:p.support,y:p.smoothed.map(v=>v[k]),name:label+' · fitted',legendgroup:key,type:'scatter',mode:'lines',line:{color,width:2,dash},customdata:text(p.support),hovertemplate:'%{customdata}<br>P = %{y:.3f}<extra>'+label+' fit</extra>'});
    traces.push({x:p.positions,y:p.raw.map(v=>v[k]),name:label+' · observed',legendgroup:key,type:'scatter',mode:'markers',marker:{color,size:6,symbol:key==='first'?'circle':'diamond'},customdata:text(p.positions),hovertemplate:'%{customdata}<br>P = %{y:.3f}<extra>'+label+'</extra>'});
  }
  await Plotly.react('plot',traces,{margin:{l:65,r:24,t:12,b:88},paper_bgcolor:'#fff',plot_bgcolor:'#fff',font:{family:'Inter, system-ui, sans-serif',size:12,color:'#536276'},xaxis:{title:{text:'Response-token position'},range:[result.config.start,result.config.end],gridcolor:'#edf0f5',zeroline:false},yaxis:{title:{text:`P(outcome ${$('category').value})`},range:[-.025,1.025],gridcolor:'#edf0f5',zeroline:false},legend:{orientation:'h',y:-.2,x:0,font:{size:12},groupclick:'toggleitem'},hovermode:'closest',uirevision:`${result.config.row}:${result.config.start}:${result.config.end}`},{responsive:true,displaylogo:false,modeBarButtonsToRemove:['select2d','lasso2d'],toImageButtonOptions:{filename:'fork-microscope'}});
  const plot=$('plot');plot.removeAllListeners('plotly_hover');plot.on('plotly_hover',event=>{const t=event.points?.[0]?.x;if(!Number.isInteger(t))return;$('token-inspector').textContent=`Token ${t} · ${JSON.stringify(metadata.tokens[t]??'')}`;document.querySelector('.token.hovered')?.classList.remove('hovered');document.querySelector(`[data-token="${t}"]`)?.classList.add('hovered');});
}
async function run(c,version){
  controller=new AbortController();
  try{
    const result=await json('/api/analyze?'+new URLSearchParams(c),{signal:controller.signal});
    if(version!==revision)return;
    await draw(result);if(version!==revision)return;
    lastResult=result;drawTrace(result);$('plot').classList.remove('pending');
    $('plot-status').textContent=`${c.samples} samples · spacing ${c.stride} · offset +${c.shift} · tokens ${c.start}–${c.end}`;
    return {config:result.config,cost:result.cost};
  }catch(e){if(e.name==='AbortError'||version!==revision)return;error(e.message);$('plot-status').textContent='Could not update the readouts. Previous plot is dimmed.';throw e;}
}
function update(immediate=false){
  clearTimeout(timer);controller?.abort();const version=++revision;
  if(!metadata)return Promise.resolve();
  try{const c=config();updateCost(c);error('');$('plot').classList.add('pending');$('plot-status').textContent='Updating both readouts…';
    if(immediate){activeTask=run(c,version);return activeTask;}
    timer=setTimeout(()=>{activeTask=run(c,version);activeTask.catch(()=>{});},350);
  }catch(e){currentCost=null;gpu();error(e.message);$('plot').classList.add('pending');$('plot-status').textContent='Correct the settings to update. Previous results are dimmed.';if(immediate)return Promise.reject(e);}
  return Promise.resolve();
}
async function loadQuestion(row,reset=false){
  const version=++questionRevision;clearTimeout(timer);controller?.abort();revision++;
  $('plot').classList.add('pending');$('plot-status').textContent='Loading question…';
  try{const m=await getMetadata(row);if(version!==questionRevision)return;setMetadata(m);$('row').value=row;$('start').value=0;$('end').value=m.last_token;
    if(reset){$('samples').value=30;$('stride').value=4;$('shift').value=1;$('draw_start').value=0;$('throughput').value='';$('hourly-rate').value='';$('show-smooth').checked=true;$('show-reference').checked=true;}
    $('shift').max=Number($('stride').value)-1;await update(true);
  }catch(e){error(e.message);$('plot-status').textContent='Unable to load this question.';}
}
$('controls').addEventListener('submit',e=>e.preventDefault());
$('controls').addEventListener('input',e=>{if(e.target.id==='row')return;if(e.target.id==='stride')$('shift').max=Number($('stride').value)-1;update();});
$('row').addEventListener('change',()=>loadQuestion(Number($('row').value)));
for(const id of ['category','show-smooth','show-reference'])$(id).addEventListener('change',()=>{if(lastResult&&metadata.row===lastResult.config.row)draw(lastResult).catch(e=>error(e.message));});
for(const id of ['throughput','hourly-rate'])$(id).addEventListener('input',gpu);
$('reset').addEventListener('click',()=>loadQuestion(39,true));

async function configure(input){
  if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(k=>!fields.includes(k)||!Number.isInteger(input[k])))throw new Error('Use the listed whole-number sampling settings.');
  const next={...config(),...input};const m=await getMetadata(next.row);
  if(next.row!==metadata?.row){if(input.start===undefined)next.start=0;if(input.end===undefined)next.end=m.last_token;}
  plan(next,m.last_token); // Validate before changing the visible settings.
  questionRevision++;setMetadata(m);fields.forEach(k=>{$(k).value=next[k];});$('shift').max=next.stride-1;
  return await update(true);
}
function registerTools(){
  const context=document.modelContext;if(!context?.registerTool)return;
  const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
  const props=Object.fromEntries(fields.map(k=>[k,{type:'integer'}]));
  for(const tool of [
    {name:'configure_fork_sampling',description:'Set the local recorded-data sampling controls and wait for both readouts and the cost estimate to update. Does not generate model tokens.',inputSchema:{type:'object',properties:props,additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:configure},
    {name:'read_fork_sampling',description:'Read the current local sampling settings, last completed readout settings, estimated cost, and any validation error.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>({settings:config(),completedSettings:lastResult?.config??null,cost:currentCost,error:$('error').hidden?null:$('error').textContent,pending:$('plot').classList.contains('pending')})}
  ]){try{Promise.resolve(context.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}}
}

async function init(){
  try{const questions=await json('/api/questions');$('row').replaceChildren(...questions.map(q=>{const o=document.createElement('option');o.value=q.row;o.textContent=`${String(q.row).padStart(3,'0')} · ${q.question.slice(0,90)}${q.question.length>90?'…':''}`;return o;}));$('row').value=39;await loadQuestion(39);registerTools();}
  catch(e){error(e.message);$('plot-status').textContent='Start the local microscope service to load the data.';}
}
init();
