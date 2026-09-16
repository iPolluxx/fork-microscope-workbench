// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — selection travels across working areas.
import {updateInvestigationContext,contextNotice} from './investigation-shell.mjs';
import {getSelection,setSelection,selectionURL,subscribeSelection,validateSelection,saveDraft,readDraft} from './selection.mjs';
import {saveJSON} from './download.mjs';
import {mountRefinement} from './refinement-panel.mjs';
import {mountPatching} from './patching-panel.mjs';
import {mountInvestigation} from './investigation-panel.mjs';
import {mountLens,completedDraws,outcomePair,pairFocus} from './lens-panel.mjs?v=workbench-20260914';
import './evidence-import.mjs';
import {passEvidence,wilsonInterval,reconstructionPoints,suggestedOutcome} from './graph-evidence.mjs';
import {resultPasses} from './passes.mjs';
const $=id=>document.getElementById(id);
let result=null,passes=[],pass=null,record=null,evidence=null,index=0,drawIndex=0,view='reply',revision=0;
const refinementHost=$('refinement-host');
const refinement=mountRefinement(refinementHost,()=>({result,pass,record,evidence}));
const investigation=mountInvestigation($('investigation-host'),()=>({result,pass,record,position:evidence?.observed[index]?.t,continuation:getSelection().continuation}));
const lens=mountLens($('lens-host'),()=>({result,pass,record,position:evidence?.observed[index]?.t,
  selectCheckpoint(t){const next=evidence?.observed.findIndex(p=>p.t===t)??-1;if(next<0)return false;showCheckpoint(next);return true;}}));
const patching=mountPatching($('patching-host'),()=>({result,pass,record,position:evidence?.observed[index]?.t,selectedPair:pairSelection}));
const draftEdits=new Map();
let runCatalog=[];
function rootOf(r){
  const seen=new Set();
  while(r.lineage?.source_run_id&&!seen.has(r.id)){
    seen.add(r.id);const parent=runCatalog.find(x=>x.id===r.lineage.source_run_id);
    if(!parent)return r.lineage.source_run_id;r=parent;
  }return r.id;
}
function runLabel(r){
  const p=r.passes?.[0];
  return `${r.lineage?.source_run_id?'Refinement':'Initial scan'}${p?` · tokens ${p.start}–${p.end} · ${p.samples} samples/checkpoint`:''} · ${r.id.slice(0,6)}`;
}
function renderRunCatalog(){
  const groups=new Map();
  for(const r of runCatalog){const root=rootOf(r);if(!groups.has(root))groups.set(root,[]);groups.get(root).push(r);}
  $('runs').replaceChildren(...[...groups].map(([root,rows])=>{
    const group=document.createElement('optgroup');const first=runCatalog.find(r=>r.id===root)??rows[0];
    group.label=`${first.model.split('/').at(-1)} · ${(first.prompt??'').slice(0,65)} · ${root.slice(0,6)}`;
    rows.sort((a,b)=>a.created-b.created);group.append(...rows.map(r=>new Option(runLabel(r),r.id)));return group;
  }));
}
function renderRunChain(id){
  const current=runCatalog.find(r=>r.id===id);$('run-chain').replaceChildren();if(!current)return;
  const family=runCatalog.filter(r=>rootOf(r)===rootOf(current)).sort((a,b)=>a.created-b.created);
  if(family.length<2){$('run-chain').hidden=true;return;}$('run-chain').hidden=false;
  $('run-chain').append(node('p',`Investigation · ${family.length} saved runs linked by refinement`));
  for(const r of family){const b=node('button',runLabel(r),'text-button');b.style.display='block';b.style.marginBottom='8px';
    if(r.id===id)b.setAttribute('aria-current','true');
    const parent=r.lineage?.source_run_id;if(parent)b.append(node('small',` · from ${parent.slice(0,6)}`));
    b.onclick=()=>load(r.id).catch(showError);$('run-chain').append(b);
  }
}

let stage='scan',pairSelection=[];
const stageCopy={
  scan:['2','Find a place to look closer.','Scan the outcome map, then choose a checkpoint to compare its saved continuations.'],
  compare:['3','Same starting point. Different outcomes.','Read what changed between completed paths before inspecting their layer readouts.'],
  inspect:['4','Look inside the paths.','Use a matching Jacobian lens to inspect vocabulary readouts from selected model layers.'],
};
function showStage(next,focus=true){
  stage=stageCopy[next]?next:'scan';
  for(const panel of document.querySelectorAll('[data-stage]'))panel.hidden=panel.dataset.stage!==stage;
  for(const button of document.querySelectorAll('.journey [data-stage-target]')){
    if(button.dataset.stageTarget===stage)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');
  }
  const [step,title,description]=stageCopy[stage];$('stage-number').textContent='EXPLORE SAVED EVIDENCE';$('stage-title').textContent=title;$('stage-description').textContent=description;
  const responses=document.querySelector('[data-response-disclosure]');if(responses&&stage==='inspect')responses.open=false;const url=new URL(location.href);url.hash=stage;history.replaceState(null,'',url);setSelection({area:stage==='inspect'?'inspect':'explore'});
  if(focus)$('stage-title').focus();
}
function inspectRegion(left,right){
  const inRegion=evidence.observed.map((p,i)=>({p,i})).filter(({p})=>p.t>=left&&p.t<=right);
  const chosen=inRegion.find(({p})=>outcomePair(completedDraws(record,p.t)).length)||inRegion[0];
  if(chosen)showCheckpoint(chosen.i);
  showStage('compare');
}
function renderPair(reset=false){
  const checkpoint=evidence.observed[index].t,rows=completedDraws(record,checkpoint);
  const pair=outcomePair(rows,...(reset?[]:pairSelection));pairSelection=pair.map(o=>o.draw_index);setSelection({pair:pairSelection.length===2?{draw_indices:pairSelection}:null});
  const option=o=>new Option(`Continuation ${o.draw_index+1} · ${o.label}`,String(o.draw_index));
  $('path-a').replaceChildren(...rows.map(option));$('path-b').replaceChildren(...rows.filter(o=>o.label!==pair[0]?.label).map(option));
  $('path-comparison').hidden=!pair.length;$('pair-view').disabled=!pair.length;$('inspect-pair').disabled=!pair.length;
  const pairs=contrastingPairs(rows),pairIndex=pairs.findIndex(p=>p.includes(pairSelection[0])&&p.includes(pairSelection[1]));
  $('pair-number').textContent=pairs.length?`${pairIndex+1} / ${pairs.length} contrasting pairs`:'No contrasting pairs';
  $('pair-previous').disabled=$('pair-next').disabled=pairs.length<2;
  $('pair-divergence').textContent='';
  $('compare-checkpoint').textContent=`Compare paths at token ${checkpoint} →`;
  if(!pair.length){
    const all=draws(),labels=new Set(rows.map(o=>o.label));
    $('pair-status').textContent=labels.size===1?`At token ${checkpoint}, every eligible completed continuation has the same recorded outcome: ${rows[0].label}. Choose another checkpoint to compare outcomes, or inspect the original response. All ${all.length} saved continuations remain available below.`:`No completed, classified pair with exact saved token IDs is available at token ${checkpoint}. Choose another checkpoint, inspect the original response, or open the saved continuations below. Token-capped and Other records are not treated as different decisions.`;
    return;
  }
  const focus=pairFocus(pair,checkpoint);
  $('pair-divergence').textContent=focus?.first!==null?`First unequal saved token: response position ${focus.first}. This is where these token sequences differ, not proof of a causal decision point.`:focus?.prefixOnly?'One saved token sequence ends before the other; no unequal token exists in their shared length.':'These saved token sequences contain no unequal token.';
  const field=$('pair-view').value;
  $('pair-status').textContent=`Both paths keep the first ${checkpoint} response tokens and the same prompt. ${field==='continuation_text'?'This view starts after the forced branch token. ':field==='full_response_text'?'This view includes their shared prefix. ':''}Outcome labels come from the saved matching rule; read the text to check what each label means.`;
  for(const [i,side] of ['a','b'].entries()){
    const draw=pair[i];$('path-'+side).value=String(draw.draw_index);
    $('path-'+side+'-note').textContent=`${draw.label} · finished · ${draw.length-checkpoint} tokens from this checkpoint`;
    $('path-'+side+'-text').textContent=draw[field]??'This text view was not stored. Try the full response view.';
    $('path-'+side+'-text').scrollTop=0;
  }
}

function contrastingPairs(rows){return rows.flatMap(a=>rows.filter(b=>b.label!==a.label&&b.draw_index>a.draw_index).map(b=>[a.draw_index,b.draw_index]));}
function cyclePair(direction){const pairs=contrastingPairs(completedDraws(record,evidence.observed[index].t));if(!pairs.length)return;const current=pairs.findIndex(p=>p.includes(pairSelection[0])&&p.includes(pairSelection[1]));pairSelection=pairs[(current+direction+pairs.length)%pairs.length];renderPair();}
function pointDescription(p){const k=result.categories.indexOf($('outcome').value),band=p.counts?wilsonInterval(p.counts[k],p.samples):null;return `Token ${p.t} · ${result.categories[k]} · observed ${(p.values[k]*100).toFixed(1)}%${p.counts?` (${p.counts[k]} / ${p.samples} samples)`:' · raw counts unavailable'}${band?` · 95% Wilson interval ${(band[0]*100).toFixed(1)}–${(band[1]*100).toFixed(1)}%`:''}`;}
const media=matchMedia('(prefers-reduced-motion: reduce)');let staticMotion=true;
function node(tag,value,className){const e=document.createElement(tag);e.textContent=value;if(className)e.className=className;return e;}
function svg(tag,attrs={},value){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(value!==undefined)e.textContent=value;return e;}
async function api(path){const response=await window.workerFetch('/api/live/'+path);const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not open saved evidence.');return data;}
function showError(e){$('loading').hidden=true;$('evidence').hidden=true;$('error').hidden=false;$('error-message').textContent=e.message||String(e);}
function key(){return `${result.id}/${pass.id}/${evidence.observed[index].t}`;}
function tokens(){return result.base?.tokens??record.base?.token_texts??[];}
function draws(){const t=evidence.observed[index].t;return (record.branches??[]).filter(b=>b.t===t).flatMap(b=>(b.observations??[]).map((o,i)=>({...o,branch_token:b.tok_id,draw_index:b.draw_indices?.[i]??i}))).sort((a,b)=>a.draw_index-b.draw_index);}
const isFinished=o=>Boolean(o.stop_reason)&&o.stop_reason!=='length';
function filteredDraws(){const outcome=$('filter-outcome').value,completion=$('filter-completion').value;return draws().filter(o=>(!outcome||o.label===outcome)&&(!completion||(completion==='finished'?isFinished(o):completion==='length'?o.stop_reason==='length':!o.stop_reason)));}
function refreshDrawList(){const list=filteredDraws();$('draw').replaceChildren(...list.map((o,i)=>new Option(`Continuation ${o.draw_index+1} · ${o.label}`,String(i))));if(!list.length)$('draw').add(new Option('No matching continuations',''));$('draw').disabled=!list.length;renderDraw();}
function openRefinement(left,right){if(left===undefined&&getSelection().token_region){left=getSelection().token_region.start;right=getSelection().token_region.end_exclusive-1;}showStage('scan',false);$('refinement-disclosure').open=true;if(left!==undefined){refinement.select(left,right);setSelection({token_region:{space:'response',start:left,end_exclusive:right+1}});}$('refinement-disclosure').scrollIntoView({block:'nearest'});}
function summarizeRun(){
  const all=(record.branches??[]).flatMap(b=>b.observations??[]),caps=all.filter(o=>o.stop_reason==='length').length,finished=all.filter(isFinished).length;
  const data=[['Checkpoints',evidence.observed.length,'Original token positions'],['Recorded continuations',all.length,'Fresh continuations'],['Finished',finished,`${all.length?Math.round(100*finished/all.length):0}% of recorded continuations`],['Reached token cap',caps,'Inspect separately from decisions']];
  $('run-facts').replaceChildren(...data.map(([label,value,note])=>{const el=node('div','','summary-card');el.append(node('span',label),node('strong',String(value)),node('small',note));return el;}));
  $('outcome-totals').replaceChildren(...result.categories.map((label,k)=>{const n=all.filter(o=>o.label===label).length,el=node('button','','outcome-chip');el.style.setProperty('--category-color',['#a5d7ef','#c6b7f0','#e1bd85','#9edac1','#ecabbc'][k%5]);el.append(node('span',label),node('strong',String(n)));el.title='Show '+label+' on the outcome map';el.setAttribute('aria-pressed',String($('outcome').value===label));el.onclick=()=>{$('outcome').value=label;drawChart();updateFacts();updateOutcomeSelection();};return el;}));
}
function updateOutcomeSelection(){[...$('outcome-totals').children].forEach((b,i)=>b.setAttribute('aria-pressed',String(result.categories[i]===$('outcome').value)));}
async function load(id){
  const openingSelection=structuredClone(getSelection()),openingUrl=new URL(location.href),restore=openingUrl.searchParams.get('run')===id;
  const current=++revision;$('evidence').hidden=true;$('error').hidden=true;$('loading').hidden=false;$('download').hidden=true;
  const loaded=await api('export?id='+encodeURIComponent(id));if(current!==revision)return;
  if(!loaded.base||!Array.isArray(loaded.categories)||!loaded.records)throw new Error('This file does not contain the saved response and observation records.');
  result=loaded;const responses=document.querySelector('[data-response-disclosure]');if(responses)responses.open=false;setSelection({run_id:id});updateInvestigationContext({model:result.model?.model_id});window.setForkMethodCredit?.(result);passes=resultPasses(result);if(!passes.length)throw new Error('No completed checkpoint passes in this run.');if(restore){const checked=validateSelection(openingSelection,result,passes);if(checked.reason)contextNotice(checked.reason);}
  $('import-provenance').hidden=!result.records['import-info'];$('import-provenance').textContent=result.records['import-info']?'Imported evidence · saved fit not recomputed locally.':'';
  $('runs').value=id;renderRunChain(id);$('model-name').textContent=result.model.model_id;
  const prompt=result.base.question?.question??result.base_config?.prompt??'Prompt not recorded';$('prompt-text').textContent=prompt;$('prompt-preview').textContent=prompt;
  $('technical').href=selectionURL({...getSelection(),run_id:id,return_area:stage==='inspect'?'inspect':'explore'},'setup',location.href);$('download').href='#';$('download').onclick=async event=>{event.preventDefault();try{await saveJSON($('download'),'fork-run-'+id+'.json',()=>api('export?id='+encodeURIComponent(id)));}catch(e){showError(e);}};$('download').download='fork-run-'+id+'.json';$('download').hidden=false;
  $('new-run').href=selectionURL(getSelection(),'setup',location.href);$('new-run').textContent='Investigation setup ↗';
  $('outcome').replaceChildren(...result.categories.map(c=>new Option(c,c)));$('outcome').selectedIndex=suggestedOutcome(passes,result.categories.length);
  $('filter-outcome').replaceChildren(new Option('All outcomes',''),...result.categories.map(c=>new Option(c,c)));$('filter-completion').value='';
  $('pass').replaceChildren(...passes.map(p=>new Option(p.label??p.id,p.id)));
  $('provenance').textContent=`Saved run ${result.id} · revision ${(result.model.resolved_revision??result.model.requested_revision??'unrecorded').slice(0,12)}`;
  const parentId=result.lineage?.source_run_id??result.records['replay-verification']?.source_run_id;
  if(parentId){const a=node('a','View parent run →');a.href='/observatory.html?run='+encodeURIComponent(parentId);$('provenance').prepend(a,node('span',' · '));}
  const initialStage=restore?(openingUrl.searchParams.get('area')==='inspect'?'inspect':openingUrl.hash.slice(1)):'scan';const requestedPass=restore?openingUrl.searchParams.get('pass'):null;if(requestedPass&&!passes.some(p=>p.id===requestedPass))contextNotice('The requested pass is unavailable. Showing this scan’s first available pass.');choosePass(passes.find(p=>p.id===requestedPass)?.id??passes[0].id);if(restore&&openingUrl.searchParams.has('checkpoint')){const savedIndex=evidence.observed.findIndex(p=>p.t===Number(openingUrl.searchParams.get('checkpoint')));if(savedIndex>=0)showCheckpoint(savedIndex);else contextNotice('The requested checkpoint is unavailable. Showing the first measured checkpoint in this pass.');}$('loading').hidden=true;$('evidence').hidden=false;if(restore&&openingUrl.searchParams.has('pair')){pairSelection=openingUrl.searchParams.get('pair').split(',').map(Number);renderPair();if(pairSelection.length===2)lens.usePair(pairSelection);}const savedDraw=openingUrl.searchParams.get('draw');if(restore&&savedDraw!==null){const d=draws().find(d=>d.draw_index===Number(savedDraw));if(d){setSelection({continuation:{run_id:id,pass_id:pass.id,checkpoint:evidence.observed[index].t,draw_index:d.draw_index}});lens.useDraw(d.draw_index);}}if(restore&&openingSelection.run_id===id){setSelection({token_region:openingSelection.token_region,layers:openingSelection.layers});lens.restoreSettings(openingSelection);}showStage(initialStage==='lens-host'?'inspect':initialStage,false);
}
function choosePass(id){
  pass=passes.find(p=>p.id===id);$('pass').value=id;record=result.records[pass.id];if(!record)throw new Error('This pass has no saved observation record.');
  evidence=passEvidence(pass,record,result.categories.length);if(!evidence.observed.length)throw new Error('No valid measured checkpoints available.');
  index=0;drawIndex=0;
  const m=result.measured?.[pass.id];$('run-summary').textContent=`${evidence.observed.length} measured checkpoints · ${m?.continuations??'Unknown number of'} continuations${m?.wall_seconds!==undefined?' · '+(m.wall_seconds/60).toFixed(1)+' minutes of collection':''}`;
  $('fit-status').textContent=pass.curve?.fit_status==='withheld'?'Fit withheld':pass.curve?.tuning==='cv'?'Cross-validated fit':pass.curve?.parameters?'Fixed-parameter fit':'No saved fit';
  $('warnings').textContent=(pass.curve?.warnings??[]).join(' ');
  const masses=(record.positions??[]).map(p=>p.retained_mass).filter(Number.isFinite);
  $('distribution-scope').textContent=masses.length?`Sampling covered ${(Math.min(...masses)*100).toFixed(1)}–${(Math.max(...masses)*100).toFixed(1)}% of next-token probability across these checkpoints. The plotted distribution is conditional on those retained branches; omitted branches were not sampled.`:'Retained branch coverage was not recorded for this pass. The full next-token distribution has not been verified.';
  $('checkpoint-pages').replaceChildren(...evidence.observed.map((p,i)=>{const b=node('button',String(p.t));b.dataset.index=i;b.setAttribute('aria-label',`Checkpoint at token ${p.t}`);b.onclick=()=>showCheckpoint(i);return b;}));
  const bounds=evidence.segmentationEnabled?(pass.curve?.boundaries??[]):[];$('candidate-links').replaceChildren(...bounds.map(b=>{const el=node('button',`${b.left}–${b.right}`,'candidate-chip');el.title='Compare recorded paths in this interval';el.onclick=()=>inspectRegion(b.left,b.right);return el;}));if(!bounds.length)$('candidate-links').append(node('span','No fitted boundaries in this pass.','micro'));
  summarizeRun();drawChart();showCheckpoint(0);refinement.refresh();
}
function drawChart(){
  const points=evidence.observed,k=result.categories.indexOf($('outcome').value),start=points[0].t,end=points.at(-1).t;
  const x=t=>60+(t-start)/Math.max(1,end-start)*950,y=v=>174-v*150;
  for(const id of ['chart-grid','change-regions','error-bars','observations','axes'])$(id).replaceChildren();
  for(const v of [0,.5,1]){$('chart-grid').append(svg('path',{d:`M60 ${y(v)} H1010`,class:'gridline'}));$('axes').append(svg('text',{x:5,y:y(v)+4,class:'axis-text'},`${v*100}%`));}
  for(const t of [start,end])$('axes').append(svg('text',{x:x(t),y:204,class:'axis-text','text-anchor':t===start?'start':'end'},String(t)));
  $('axes').append(svg('text',{x:535,y:204,class:'axis-text','text-anchor':'middle'},'Original response token position'));
  for(const b of pass.curve?.boundaries??[])if(evidence.segmentationEnabled&&b.left>=start&&b.right<=end&&b.left<b.right){const region=svg('rect',{x:x(b.left),y:20,width:x(b.right)-x(b.left),height:160,class:'candidate'});region.style.cursor='pointer';region.onclick=()=>inspectRegion(b.left,b.right);region.append(svg('title',{},`Compare paths at ${b.left}–${b.right}`));$('change-regions').append(region);}
  const fitted=reconstructionPoints(pass.curve,k);let d='',penDown=false;
  for(const p of fitted){if(p.value===null){penDown=false;continue;}d+=`${penDown?'L':'M'}${x(p.t)} ${y(p.value)} `;penDown=true;}
  $('fitted').setAttribute('d',d);$('fitted').style.display=$('smooth').checked?'':'none';
  points.forEach((p,i)=>{
    if($('intervals').checked&&p.counts){const band=wilsonInterval(p.counts[k],p.samples);if(band)$('error-bars').append(svg('path',{d:`M${x(p.t)} ${y(band[0])}V${y(band[1])} M${x(p.t)-3} ${y(band[0])}h6 M${x(p.t)-3} ${y(band[1])}h6`,class:'point-bar'}));}
    const label=pointDescription(p);
    const circle=svg('circle',{cx:x(p.t),cy:y(p.values[k]),r:5,class:'graph-dot'+(i===index?' active':''),'data-index':i,role:'button',tabindex:0,'aria-label':label});circle.append(svg('title',{},label));circle.onmouseenter=circle.onfocus=()=>{$('point-readout').textContent=label;};circle.onmouseleave=circle.onblur=()=>{$('point-readout').textContent=pointDescription(points[index]);};circle.onclick=()=>showCheckpoint(i);circle.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();showCheckpoint(i);}};$('observations').append(circle);
  });
  const same=evidence.constant?'No sampled outcome differences.':`${evidence.intervals.length} adjacent intervals with observed differences or fitted boundaries.`;
  const changes=(pass.curve?.boundaries??[]).length;
  $('graph-summary').textContent=evidence.constant?'All sampled checkpoints have the same outcome proportions.':`${evidence.segmentationEnabled?`${changes} candidate change ${changes===1?'interval':'intervals'} in the saved fit.`:'No fitted change intervals available.'} A highlighted interval suggests where to inspect; it does not establish a statistically significant or causal fork.`;
  $('chart-title').textContent=`${$('outcome').value} outcome proportions at ${points.length} measured checkpoints. ${same}`;
}
function showCheckpoint(next){
  index=Math.max(0,Math.min(evidence.observed.length-1,next));drawIndex=0;
  setSelection({run_id:result.id,pass_id:pass.id,checkpoint:evidence.observed[index].t});
  const p=evidence.observed[index],source=tokens(),end=Math.min(p.t+32,source.length);
  const promptButton=node('button','Original prompt','text-button');promptButton.onclick=()=>{$('saved-prompt').open=true;$('saved-prompt').scrollIntoView({block:'nearest'});};const library=node('a',`Run ${result.id.slice(0,8)}`);library.href='#library';$('selection-context').replaceChildren(promptButton,node('span','›'),library,node('span',`› ${pass.label??pass.id} › Token ${p.t}`));$('map-selection').textContent=`${pass.label??pass.id} · Token ${p.t}`;
  $('position').textContent=p.t;$('page-number').textContent=`${String(index+1).padStart(2,'0')} / ${String(evidence.observed.length).padStart(2,'0')}`;
  $('previous').disabled=index===0;$('next').disabled=index===evidence.observed.length-1;
  $('context-line').textContent=p.t?source.slice(Math.max(0,p.t-32),p.t).join(''):'Beginning of the generated response. The prompt is the only earlier context.';
  $('anchor-text').textContent=source.slice(p.t,end).join('');
  $('original-suffix').textContent=source.slice(p.t).join('');
  $('prefix-history').textContent=source.slice(0,p.t).join('')||'No original response tokens precede this checkpoint.';
  $('span-label').textContent=`Original tokens ${p.t}–${end-1} · ${source.length} tokens in the full response`;
  for(const b of $('checkpoint-pages').children)b.setAttribute('aria-pressed',String(Number(b.dataset.index)===index));
  for(const c of $('observations').children)c.classList.toggle('active',Number(c.dataset.index)===index);
  $('replacement').value=readDraft('replacement')??draftEdits.get(key())??source.slice(p.t,end).join('');
  $('draft-boundary').textContent=`Source token span [${p.t}, ${end}). Preserve ${p.t} response tokens before it. The replacement has not been tokenized.`;
  $('draft-status').textContent='Draft only. Tokenization, a connected worker, and fresh control/edit continuations are required before execution.';
  updateFacts();refreshDrawList();renderPair(true);investigation.refresh();lens.refresh();patching.refresh();
}
function updateFacts(){
  $('point-readout').textContent=pointDescription(evidence.observed[index]);
  const p=evidence.observed[index],k=result.categories.indexOf($('outcome').value),meta=record.positions?.find(r=>r.t===p.t),list=draws();
  $('match-stat').textContent=p.counts?`${p.counts[k]} / ${p.samples} ${$('outcome').value}`:`${(p.values[k]*100).toFixed(1)}% ${$('outcome').value}`;
  $('mass-stat').textContent=Number.isFinite(meta?.retained_mass)?`${(meta.retained_mass*100).toFixed(2)}%`:'Not recorded';
  $('completion-stat').textContent=list.length?`${list.filter(isFinished).length} / ${list.length}`:'Not recorded';
}
function renderDraw(){
  const all=draws(),list=filteredDraws();drawIndex=Math.max(0,Math.min(Math.max(0,list.length-1),drawIndex));const o=list[drawIndex];
  $('draw').value=String(drawIndex);$('draw-prev').disabled=!list.length||drawIndex===0;$('draw-next').disabled=!list.length||drawIndex>=list.length-1;
  $('recorded-view').hidden=view==='edit';$('draft-view').hidden=view!=='edit';
  for(const b of document.querySelectorAll('[data-view]'))b.setAttribute('aria-pressed',String(b.dataset.view===view));
  $('draw-meta').textContent=o?`${o.label} · ${o.generated_tokens} new tokens · ${o.stop_reason==='length'?'Token cap reached':isFinished(o)?'Finished':'Completion not recorded'} · branch ${o.branch_token}`:all.length?'No continuations match these filters at this checkpoint.':'This historical record contains no saved continuation text.';
  $('draw-meta').classList.toggle('capped',o?.stop_reason==='length');
  $('view-note').textContent=view==='reply'?'Reply text extracted by the saved readout rule.':view==='continuation'?'Newly generated text after the forced branch token.':'Full saved response: earlier source prefix, forced branch token, and generated continuation.';
  const text=o?.[view==='reply'?'reply_text':view==='continuation'?'continuation_text':'full_response_text'];
  $('reply').textContent=text??(list.length?'No text recorded for this view.':all.length?'No continuations match these filters. Clear the filters to see recorded text.':'No continuation text was saved at this checkpoint. Token IDs and outcome counts may still be available in the exported evidence.');$('reply').scrollTop=0;
  $('filter-summary').textContent=`${list.length} of ${all.length} continuations at token ${evidence.observed[index].t}`;$('reset-filters').hidden=!$('filter-outcome').value&&!$('filter-completion').value;$('copy-reply').disabled=view==='edit'||!text;$('reader-status').textContent='';
}
function exportDraft(){
  const p=evidence.observed[index],end=Math.min(p.t+32,tokens().length),replacement=$('replacement').value;
  if(!replacement.trim()){$('draft-status').textContent='Enter replacement text. Deletion needs a separate explicit action.';return;}
  const base=record.base,draft={schema:'fork-observatory-edit-draft-v1',status:'pending-unexecuted',synthetic:false,source:{run_id:result.id,pass_id:pass.id,checkpoint:p.t,model:result.model,span_start:p.t,span_end:end},prompt_ids:base.prompt_ids,preserved_response_ids:base.gen_ids.slice(0,p.t),original_span_ids:base.gen_ids.slice(p.t,end),replacement_text:replacement,replacement_ids:null,discard_original_suffix:true,token_boundary_validation:'source span uses saved IDs; replacement requires tokenizer preview',sampler_contract:null,results:null};
  saveJSON($('save-draft'),`edit-${result.id}-token-${p.t}.json`,()=>draft);
}
function motion(){const off=staticMotion||media.matches;document.body.classList.toggle('motion-static',off);$('motion').textContent=off?'Atmosphere off':'Atmosphere on';$('motion').setAttribute('aria-pressed',String(!off));$('motion').disabled=media.matches;}
$('motion').onclick=()=>{staticMotion=!staticMotion;motion();};media.addEventListener('change',motion);
for(const e of ['focusin','focusout','selectionchange','visibilitychange','pointerover','pointerout'])document.addEventListener(e,()=>{document.body.classList.toggle('motion-paused',Boolean(document.hidden||document.activeElement?.closest('.editor,.reasoning-column')||document.querySelector('.editor:hover,.reasoning-column:hover')||getSelection()?.toString()));});
for(const button of document.querySelectorAll('[data-stage-target]'))button.onclick=()=>showStage(button.dataset.stageTarget);
$('export-investigation').onclick=async()=>{try{await saveJSON($('export-investigation'),'fork-investigation-'+result.id+'.json',()=>api('bundle-export?id='+encodeURIComponent(result.id)));}catch(e){showError(e);}};
$('show-saved-prompt').onclick=()=>{ $('saved-prompt').open=true; $('saved-prompt-heading').focus(); $('saved-prompt').scrollIntoView({block:'nearest'}); };
$('compare-checkpoint').onclick=()=>showStage('compare');
$('pair-previous').onclick=()=>cyclePair(-1);$('pair-next').onclick=()=>cyclePair(1);
document.addEventListener('keydown',event=>{if(stage!=='compare'||event.ctrlKey||event.metaKey||event.altKey||event.target.closest('input,textarea,select,[contenteditable="true"]'))return;if(event.key==='['||event.key===']'){event.preventDefault();cyclePair(event.key==='['?-1:1);}});
$('path-a').onchange=()=>{pairSelection[0]=Number($('path-a').value);renderPair();};
$('path-b').onchange=()=>{pairSelection[1]=Number($('path-b').value);renderPair();};
$('pair-view').onchange=()=>renderPair();
$('inspect-pair').onclick=()=>{if(pairSelection.length!==2)return;lens.usePair(pairSelection);patching.refresh();showStage('inspect');};
$('inspect-original').onclick=()=>{lens.useOriginal();showStage('inspect');};
$('runs').onchange=()=>load($('runs').value).catch(showError);$('pass').onchange=()=>{try{choosePass($('pass').value);}catch(e){showError(e);}};
$('outcome').onchange=()=>{drawChart();updateFacts();updateOutcomeSelection();};$('smooth').onchange=drawChart;$('intervals').onchange=drawChart;
$('previous').onclick=()=>showCheckpoint(index-1);$('next').onclick=()=>showCheckpoint(index+1);
$('checkpoint-pages').onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();showCheckpoint(e.key==='Home'?0:e.key==='End'?evidence.observed.length-1:index+(e.key==='ArrowRight'?1:-1));$('checkpoint-pages').children[index].focus();}};
$('history-toggle').onclick=()=>{const open=$('prefix-history').hidden;$('prefix-history').hidden=!open;$('history-toggle').setAttribute('aria-expanded',String(open));$('history-toggle').textContent=open?'Hide earlier text ↑':'Read earlier text ↑';};
$('draw').onchange=()=>{drawIndex=Number($('draw').value);renderDraw();};$('draw-prev').onclick=()=>{drawIndex--;renderDraw();};$('draw-next').onclick=()=>{drawIndex++;renderDraw();};
for(const id of ['filter-outcome','filter-completion'])$(id).onchange=()=>{drawIndex=0;refreshDrawList();};
$('reset-filters').onclick=()=>{$('filter-outcome').value='';$('filter-completion').value='';drawIndex=0;refreshDrawList();};
$('open-refinement').onclick=()=>openRefinement();
$('copy-reply').onclick=async()=>{try{await navigator.clipboard.writeText($('reply').textContent);$('reader-status').textContent='Displayed text copied.';}catch{$('reader-status').textContent='Copy is unavailable in this browser. Select the text to copy it.';}};
$('back-to-records').onclick=()=>{view='reply';renderDraw();};
$('retry').onclick=()=>location.reload();
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>{view=b.dataset.view;renderDraw();};
$('edit-here').onclick=()=>{view='edit';renderDraw();$('replacement').focus();};$('replacement').oninput=()=>{draftEdits.set(key(),$('replacement').value);saveDraft('replacement',$('replacement').value);$('draft-status').textContent='Local draft changed. Download it to preserve this version; no inference is running.';};$('save-draft').onclick=exportDraft;
motion();
try{const runs=await api('runs');runCatalog=runs;renderRunCatalog();if(!runs.length&&getSelection().investigation_id){$('loading').hidden=true;$('model-name').textContent='Choose or generate a response above.';}else if(!runs.length)throw new Error('No completed runs were found on the selected worker. Check the evidence source in the top bar, connect the worker holding your runs, or import an evidence file.');const requested=new URLSearchParams(location.search).get('run');if(runs.length&&(requested||!getSelection().investigation_id))await load(requested||runs[0].id);else if(!requested){$('loading').hidden=true;$('evidence').hidden=true;$('model-name').textContent='Choose or generate a response above.';};}catch(e){showError(e);$('model-name').textContent='Saved evidence unavailable';}

addEventListener('worker-connection-change', () => location.reload());

$('inspect-continuation').onclick=()=>{const draw=filteredDraws()[drawIndex];if(!draw)return;setSelection({continuation:{run_id:result.id,pass_id:pass.id,checkpoint:evidence.observed[index].t,draw_index:draw.draw_index},pair:null});lens.useDraw(draw.draw_index);showStage('inspect');};
$('edit-continuation').onclick=()=>{const draw=filteredDraws()[drawIndex];if(!draw)return;setSelection({continuation:{run_id:result.id,pass_id:pass.id,checkpoint:evidence.observed[index].t,draw_index:draw.draw_index},pair:null});investigation.refresh();showStage('inspect');$('investigation-host').closest('details').open=true;};
let restoring=false;
subscribeSelection(async(s,options)=>{if(!options.restored||restoring)return;restoring=true;try{if(s.run_id&&s.run_id!==result?.id)await load(s.run_id);else if(result){if(s.pass_id&&s.pass_id!==pass?.id)choosePass(s.pass_id);if(s.checkpoint!==null){const i=evidence.observed.findIndex(p=>p.t===s.checkpoint);if(i>=0)showCheckpoint(i);}if(s.pair){pairSelection=s.pair.draw_indices;renderPair();lens.usePair(pairSelection);}if(s.continuation)lens.useDraw(s.continuation.draw_index);showStage(s.area==='inspect'?'inspect':location.hash==='#compare'?'compare':'scan',false);}}catch(e){contextNotice(e.message);}finally{restoring=false;}});
let dragStart=null;
function graphToken(event){const rect=$('chart').getBoundingClientRect(),x=(event.clientX-rect.left)/rect.width*1040;const a=evidence.observed[0].t,b=evidence.observed.at(-1).t;return Math.max(a,Math.min(b,Math.round(a+(x-60)/950*(b-a))));}
$('chart').addEventListener('pointerdown',event=>{if(!evidence||event.target.closest('[role="button"]'))return;dragStart=graphToken(event);$('chart').setPointerCapture(event.pointerId);});
$('chart').addEventListener('pointerup',event=>{if(dragStart===null)return;const end=graphToken(event),start=Math.min(dragStart,end),last=Math.max(dragStart,end);dragStart=null;if(start===last)return;setSelection({token_region:{space:'response',start,end_exclusive:last+1}});$('point-readout').textContent=`Selected response tokens ${start}–${last}. Refine this region to add measurements.`;refinement.select(start,last);$('open-refinement').textContent=`Refine tokens ${start}–${last} ↓`;});
if(new URLSearchParams(location.search).has('demo')){const card=node('section','','investigation-card');card.append(node('h2','A recorded attendance decision'),node('p','This example compares Plan A and Plan B using saved evidence. No model is connected and opening it spends no compute.'),node('p','1. Click a checkpoint on the outcome map. 2. Compare its continuations. 3. Open Inspect & Test to browse saved J-lens readouts. Proportions describe the recorded samples, not proof of a causal decision point.'));$('workspace').prepend(card);}

$('save-comparison').onclick=async()=>{if(pairSelection.length!==2)return;const comparison={id:crypto.randomUUID().replaceAll('-',''),run_id:result.id,pass_id:pass.id,checkpoint:evidence.observed[index].t,draw_indices:[...pairSelection],rationale:$('comparison-rationale').value};try{await new Promise((resolve,reject)=>window.dispatchEvent(new CustomEvent('fork-save-comparison',{detail:{comparison,resolve,reject}})));$('comparison-save-status').textContent='Comparison saved with its exact continuation references.';}catch(e){$('comparison-save-status').textContent=e.message;}};

window.addEventListener('fork-open-scan',async event=>{if(!event.detail.id){$('evidence').hidden=true;$('loading').hidden=true;$('error').hidden=true;return;}try{runCatalog=await api('runs');renderRunCatalog();await load(event.detail.id);}catch(e){showError(e);}});
