// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — preserve shared selection during setup.
import './investigation-shell.mjs';
import {getSelection,setSelection,selectionURL} from './selection.mjs';
import {computeReadiness} from './compute-readiness.mjs';
import {gpuEstimate} from './math.mjs';
import {newPass,resultPasses} from './passes.mjs';
import {passEvidence,wilsonInterval,reconstructionPoints,suggestedOutcome} from './graph-evidence.mjs';
import {jobProgress} from './job-progress.mjs';
import {reviewState,fullTracePass} from './response-review.mjs';
import {saveJSON} from './download.mjs';
let graphIntervals=[],graphRange=null;
const $=id=>document.getElementById(id),num=id=>$(id).value.trim()===''?NaN:Number($(id).value);
let resultRevision=0,baseFormDirty=false,baseReviewed=false;
const responseReadiness=()=>reviewState(state?.base,{dirty:baseFormDirty,reviewed:baseReviewed,acceptOther:$('accept-other').checked});
let state=null,result=null,budget=null,estimateTimer,estimateRevision=0,lastBaseSignature='',lastResultId='';
let passes=[newPass()],activePass=passes[0].id;
let runtimeConnected=false,setupStep='model',setupInitialized=false,budgetIssue='';
let modelInspection=null,inspectionSignature='',inspectionBusy=false,inspectionEpoch=0;
const modelPayload=()=>({model_id:$('model-id').value.trim(),revision:$('revision').value.trim(),device:$('device').value,batch_size:num('batch')});
const modelSignature=()=>JSON.stringify(modelPayload());
const fmt=n=>new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(n);
const fields=['cont-cap','temperature','top-k','threshold','dense','reference-samples','tuning'];
function config(){return {passes:passes.map(p=>({...p})),cont_max:num('cont-cap'),temperature:num('temperature'),top_k:num('top-k'),threshold:num('threshold'),dense:$('dense').checked,reference_samples:num('reference-samples'),tuning:$('tuning').value};}
function error(text=''){$('error').hidden=!text;$('error').textContent=text;if(text&&state?.job.status==='error'){$('connection-fix').hidden=false;$('connection-advice').textContent=connectionAdvice(text);}}
function connectionAdvice(message=''){
  if(!runtimeConnected)return 'Start the Fork Microscope app on your machine or GPU VM, then reopen its app URL or restore your SSH port forwarding. Saved runs need their host to be reachable. Retry when the runtime is available.';
  if(/out of memory|cuda.*memory/i.test(message))return 'The model or continuation batch does not fit this runtime. Try one concurrent continuation, a shorter context, or a compatible model that fits the available GPU memory.';
  if(/401|403|gated|unauthorized|token|access denied/i.test(message))return 'The runtime could not access the model. Check model permissions and Hugging Face authentication on the machine running the app. Do not put credentials in the model ID field.';
  if(/cuda|gpu/i.test(message))return 'Check that the app is running on your intended GPU machine and that CUDA is available there. A browser on your laptop does not move the model to a remote GPU.';
  if(/revision|not found|model type|architecture|unrecognized|safetensor/i.test(message))return 'Check the model ID, revision and compatibility with this runtime. Model revision and hardware settings are available under Model.';
  return 'Review the runtime message above. Check the model settings or restart the app on its host, then retry. Your saved run files are not changed by reconnecting.';
}
function showSetupStep(step,{focus=false}={}){
  if(step==='scan'&&!responseReadiness().canScan)step='prompt';
  setupStep=step;setupInitialized=true; document.querySelector('.live-workspace').dataset.setupStage=step;
  for(const section of document.querySelectorAll('[data-setup-step]'))section.hidden=section.dataset.setupStep!==step;
  for(const button of document.querySelectorAll('[data-step]')){if(button.dataset.step===step)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');}
  if(focus)document.querySelector(`[data-setup-step="${step}"]`).scrollIntoView({block:'start',behavior:'auto'});
}
function observatoryRoute(id){const link=document.getElementById('observatory-link');if(link)link.href=selectionURL({...getSelection(),...(id?{run_id:id}:{})},'explore',location.href);}
$('runtime-address').textContent=location.host;
for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>showSetupStep(button.dataset.step);
$('to-prompt').onclick=()=>showSetupStep('prompt',{focus:true});$('to-scan').onclick=()=>{if(!responseReadiness().canReview)return;baseReviewed=true;actions();showSetupStep('scan',{focus:true});};
$('accept-other').onchange=()=>{baseReviewed=false;actions();};
$('retry-runtime').onclick=()=>refresh().then(()=>error()).catch(e=>error(e.message));
async function api(path,payload){let response;try{response=await (window.workerFetch||window.fetch.bind(window))('/api/live/'+path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}catch{throw new Error('Cannot reach the app runtime. Check its connection and retry.');}let value;try{value=await response.json();}catch{throw new Error('This address did not return the app runtime. Check the forwarded app port and retry.');}if(!response.ok)throw new Error(value.error||'The runtime request failed.');return value;}
function queueEstimate(){budget=null;budgetIssue='';money();actions();clearTimeout(estimateTimer);estimateRevision++;estimateTimer=setTimeout(estimate,250);}
function renderPasses(){
  $('pass-tabs').replaceChildren();
  for(const p of passes){const b=document.createElement('button');b.type='button';b.role='tab';b.id='tab-'+p.id;b.textContent=p.label;b.setAttribute('aria-selected',String(p.id===activePass));b.setAttribute('aria-controls','pass-details');b.tabIndex=p.id===activePass?0:-1;
    b.onclick=()=>{activePass=p.id;renderPasses();$('tab-'+p.id).focus();};
    b.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();let i=passes.findIndex(x=>x.id===activePass);i=e.key==='Home'?0:e.key==='End'?passes.length-1:(i+(e.key==='ArrowRight'?1:-1)+passes.length)%passes.length;activePass=passes[i].id;renderPasses();$('tab-'+activePass).focus();}};
    $('pass-tabs').append(b);}
  const p=passes.find(x=>x.id===activePass),details=$('pass-details');details.replaceChildren();details.setAttribute('aria-labelledby','tab-'+p.id);
  const advanced=document.createElement('details'),summary=document.createElement('summary'),advancedFields=document.createElement('div');summary.textContent='Pass name, offset & random seed';advancedFields.className='pass-advanced-fields';advanced.append(summary,advancedFields);
  for(const [key,label,min,max]of [['label','Pass name'],['samples','Samples per checkpoint',5,512],['start','Region first token',0,4095],['end','Region last token',0,4095],['stride','Checkpoint spacing',1,128],['offset','Offset from region start',0,127],['seed','Pass random seed',0,2147483647]]){
    const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement('input');input.id='pass-'+key;input.type=key==='label'?'text':'number';if(min!==undefined){input.min=min;input.max=max;}input.value=p[key];if(key==='label')input.maxLength=80;
    input.oninput=()=>{p[key]=key==='label'?input.value:(input.value.trim()===''?NaN:Number(input.value));if(key==='label')$('tab-'+p.id).textContent=input.value||'Unnamed pass';queueEstimate();};wrap.append(input);(['label','offset','seed'].includes(key)?advancedFields:details).append(wrap);}
  details.append(advanced);
  const remove=document.createElement('button');remove.textContent='Remove pass';remove.id='remove-pass';remove.className='secondary';remove.disabled=passes.length===1;remove.onclick=()=>{passes=passes.filter(x=>x.id!==p.id);activePass=passes[0].id;renderPasses();queueEstimate();};details.append(remove);actions();
}
$('add-pass').onclick=()=>{const p=newPass(passes);passes.push(p);activePass=p.id;renderPasses();queueEstimate();};
function money(){
  if(!budget){$('money').textContent='';return;}
  const throughput=$('throughput').value.trim()?num('throughput'):null,rate=$('rate').value.trim()?num('rate'):null;
  if((throughput!==null&&(!Number.isFinite(throughput)||throughput<=0))||(rate!==null&&(!Number.isFinite(rate)||rate<0))){$('money').textContent='Use positive throughput and a non-negative hourly rate.';return;}
  const v=gpuEstimate(budget.max_continuation_tokens,throughput,rate);
  $('money').textContent=v.hours===null?'Enter measured throughput to project generation time.':`At the full token allowance: ${v.hours.toFixed(2)} hours${v.dollars===null?'':` · $${v.dollars.toFixed(2)}`} for the selected passes and reference. Projection only.`;
}
async function estimate(){
  const version=++estimateRevision;$('temperature-notice').hidden=num('temperature')===1;
  if(!runtimeConnected||!state?.base||state.job.status==='running')return;
  try{const v=await api('estimate',config());if(version!==estimateRevision)return;budget=v;budgetIssue='';$('budget').replaceChildren();
    const lines=v.passes.map(p=>`${p.label}: ${p.positions.length} checkpoints × ${p.samples} samples = ${fmt(p.positions.length*p.samples)} continuations.`);
    lines.push(`${v.unique_checkpoints} distinct checkpoints; ${v.sampled_checkpoint_visits} visits including overlaps.`,`Independent reference: ${fmt(v.reference_rollouts)} additional continuations.`,`Total: ${fmt(v.total_rollouts)} samples, at most ${fmt(v.max_continuation_tokens)} generated continuation tokens.`,`Fewer samples is not evidence of savings at comparable accuracy.`);
    for(const text of lines){const p=document.createElement('div');p.textContent=text;$('budget').append(p);}money();actions();
  }catch(e){if(version!==estimateRevision)return;budget=null;budgetIssue=e.message;$('budget').textContent=e.message;money();actions();}
}
function hardwareReadiness(){return computeReadiness(runtimeConnected,state?.runtime,$('device').value,state?.model);}
function actions(){const busy=state?.job.status==='running',modelReady=runtimeConnected&&!!state?.model,baseReady=modelReady&&!!state?.base,hardware=hardwareReadiness(),review=responseReadiness();
  $('compute-environment').dataset.state=hardware.kind;$('compute-heading').textContent=hardware.title;$('runtime-hardware').textContent=hardware.summary;$('hardware-warning').textContent=hardware.message;$('hardware-warning').hidden=!hardware.message;$('hardware-warning').classList.toggle('blocked',hardware.blocked);
  $('load').disabled=busy||inspectionBusy||!runtimeConnected||hardware.blocked||!modelInspection?.can_load||inspectionSignature!==modelSignature();$('inspect-model').disabled=busy||inspectionBusy||!runtimeConnected||!$('model-id').value.trim()||!$('revision').value.trim();$('model-source').disabled=busy||inspectionBusy;$('unload').disabled=busy||!modelReady;$('base').disabled=busy||!modelReady;$('run').disabled=busy||!baseReady||!review.canScan||!budget;$('base-stale').hidden=!baseFormDirty||!state?.base;$('stop').disabled=!busy||!runtimeConnected;
  for(const id of [...fields,'seed','model-id','revision','device','batch','question','answers','mode','base-cap'])$(id).disabled=busy||(inspectionBusy&&['model-id','revision','device','batch'].includes(id));
  $('add-pass').disabled=busy||passes.length>=8;for(const el of $('pass-details').querySelectorAll('input,button'))el.disabled=busy||(el.id==='remove-pass'&&passes.length===1);
  $('to-prompt').disabled=busy||!modelReady;$('to-scan').disabled=busy||!baseReady||!review.canReview;
  document.querySelector('[data-step="scan"]').disabled=busy||!review.canScan;
  $('accept-other').disabled=busy;$('base-review').hidden=!state?.base;
  $('review-summary').textContent=state?.base?`${state.base.length} output tokens · ${state.base.finish_reason==='stop'?'Response finished':'Token limit reached — scanning blocked'}`:'';
  $('review-text').textContent=state?.base?.text??'';
  $('review-match').textContent=review.message;$('base-review').dataset.state=review.other||!review.canReview?'attention':'ready';
  $('accept-other-wrap').hidden=!review.other;
  $('scan-coverage').textContent=state?.base?`Original response: tokens 0–${state.base.length-1}. New responses start with a full-response scan region; adjust it below to focus on a smaller span.`:'';
  const wait=busy?'A job is running. Wait for it to finish or stop it above.':null;
  $('load-help').textContent=inspectionBusy?'Inspecting configuration and tokenizer; model weights are not being loaded.':!runtimeConnected?'The runtime must be reachable before a model can load. Use Retry connection above.':wait||(hardware.blocked?hardware.message:modelInspection?.can_load&&inspectionSignature===modelSignature()?'Metadata checks passed. Load downloads missing weights and uses your worker’s memory.':modelReady?'A model is attached. Continue to your question, or inspect new settings to change models.':'Enter a model and inspect its requirements to enable loading.');
  $('base-help').textContent=!modelReady?'Load a model under Model to generate a response.':wait||(baseReady&&!baseFormDirty?'Review the response and matching result below.':'This creates the original response that your checkpoint scan will explore.');
  $('run-help').textContent=!baseReady?'Generate an original response under Question before starting a scan.':wait||(!review.canScan?`Review the response under Question. ${review.message}`:budgetIssue||(!budget?'Checking your scan settings and token allowance…':'Ready. Review the checkpoint count and maximum token allowance, then start the scan.'));
  for(const [id,help]of [['load','load-help'],['base','base-help'],['run','run-help']])$(id).title=$(id).disabled?$(help).textContent:'';
  $('step-model-state').textContent=modelReady?'Model ready':runtimeConnected?(hardware.blocked?'GPU worker needed':'Choose & load'):'Connect runtime';
  $('step-prompt-state').textContent=baseReady&&!baseFormDirty?(review.canScan?'Response reviewed':'Review response'):baseFormDirty&&baseReady?'Response needs update':'Prompt & answers';
  $('step-scan-state').textContent=budget?`${fmt(budget.total_rollouts)} planned samples`:'Checkpoints & budget';
}
async function refresh(){
  const before=state,wasConnected=runtimeConnected;try{state=await api('status');runtimeConnected=true;}catch(e){runtimeConnected=false;$('connection-badge').textContent='Runtime unavailable';$('connection-badge').dataset.state='offline';$('phase').textContent='Connection lost — your runtime is not reachable.';$('connection-fix').hidden=false;$('connection-advice').textContent=connectionAdvice();actions();throw e;}
  $('connection-badge').textContent=state.job.status==='running'?'Runtime working':'Runtime connected';$('connection-badge').dataset.state='ready';$('connection-fix').hidden=state.job.status!=='error';
  if(!wasConnected&&state.job.status!=='error')error();if(!setupInitialized)showSetupStep(state.base?'scan':state.model?'prompt':'model');
  actions();$('phase').textContent=state.job.phase;$('progress-detail').textContent=jobProgress(state.job);$('progress').max=state.job.total||1;$('progress').value=state.job.completed||0;if(state.job.status==='running'&&!state.job.total)$('progress').removeAttribute('value');
  $('progress').hidden=state.job.status!=='running';
  if(state.job.activity?.kind==='download'&&state.job.activity.total>0){$('progress').max=state.job.activity.total;$('progress').value=state.job.activity.completed;}
  if(state.model&&state.model.resolved_revision!==before?.model?.resolved_revision&&!state.model.chat_template)$('mode').value='base';
  $('model-status').textContent=state.model?`${state.model.model_id} · ${state.model.device.toUpperCase()} · ${state.model.dtype} · ${fmt(state.model.parameters)} parameters`:'No model attached.';
  if(state.job.status==='error')error(state.job.phase);
  const signature=state.base?JSON.stringify([state.model?.resolved_revision,state.base.config,state.base.text]):'';
  const changed=signature!==lastBaseSignature;
  if(changed){lastBaseSignature=signature;baseFormDirty=false;baseReviewed=false;$('accept-other').checked=false;if(state.base){const cfg=state.base.config;if(cfg?.prompt!==undefined){$('question').value=cfg.prompt;$('answers').value=cfg.answers.join('\n');$('mode').value=cfg.mode;$('base-cap').value=cfg.max_tokens;$('seed').value=cfg.seed;}$('base-text').textContent=state.base.text;$('base-description').textContent=`${state.base.length} tokens · finished by ${state.base.finish_reason}${state.base.finish_reason!=='stop'?' — INCOMPLETE BASE':''} · ${state.base.question.question}`;
    $('base-tokens').textContent=state.base.tokens.map((token,i)=>`${i}\t${JSON.stringify(token)}\tP(top)=${state.base.top_token_probabilities?.[i]?.toFixed(6)??'not recorded'}`).join('\n');
    passes=passes.map(p=>fullTracePass(p,state.base.length));renderPasses();
  }else{$('base-text').textContent='No generated response yet.';$('base-tokens').textContent='No response yet.';budget=null;$('budget').textContent='Generate a base response first.';money();}}
  if(state.job.status==='complete'&&state.job.action==='base'&&before?.job.status==='running'){baseFormDirty=false;actions();showSetupStep('prompt');$('base-review').scrollIntoView({block:'start',behavior:'auto'});$('review-text').focus({preventScroll:true});}
  if(state.job.status!=='running'&&(before?.job.status==='running'||changed))await estimate();
  if(state.job.result_id&&state.job.result_id!==lastResultId){lastResultId=state.job.result_id;const route=new URL(location.href);if(before||(!['run','source','set'].some(key=>route.searchParams.has(key))&&route.searchParams.get('view')!=='setup')){await listRuns();$('runs').value=lastResultId;await loadResult(lastResultId);}}return state;
}
async function start(action,payload){error();const response=await api(action,payload);
  if(action==='load')for(const [key,id]of [['model_id','model-id'],['revision','revision'],['device','device'],['batch_size','batch']])$(id).value=payload[key];
  if(action==='base'){$('question').value=payload.prompt??payload.question;$('answers').value=(payload.answers??['A','B','C','D']).join('\n');$('mode').value=payload.mode;$('base-cap').value=payload.max_tokens;$('seed').value=payload.seed;}
  if(action==='run'){for(const [key,id]of [['cont_max','cont-cap'],['temperature','temperature'],['top_k','top-k'],['threshold','threshold'],['reference_samples','reference-samples'],['tuning','tuning']])$(id).value=payload[key];$('dense').checked=payload.dense;if(payload.passes){passes=payload.passes.map(p=>({...p}));activePass=passes[0].id;renderPasses();}}
  await refresh();if(state?.job.status==='running')document.querySelector('.status-panel').scrollIntoView({block:'start',behavior:'auto'});return response;}
const bind=(id,fn)=>$(id).addEventListener('click',()=>Promise.resolve().then(fn).catch(e=>error(e.message)));
function invalidateInspection(){inspectionEpoch++;modelInspection=null;inspectionSignature='';$('model-inspection').hidden=true;actions();}
function sourceHelp(){const local=$('model-source').value==='local';$('model-id-label').textContent=local?'Model directory on the worker':'Hugging Face model ID';$('model-id').placeholder=local?'/path/on/worker/model':'organization/model-name';$('revision-label').textContent=local?'Local identity (optional saved fingerprint)':'Revision or branch';$('model-source-help').textContent=local?'Use an existing directory with config, tokenizer and safetensors weights on the connected worker. File contents are fingerprinted at load.':'Use native Transformers safetensors weights. Gated or private repositories use Hugging Face authentication configured on your worker.';}
$('model-source').addEventListener('change',()=>{$('model-id').value='';$('revision').value=$('model-source').value==='local'?'local':'main';sourceHelp();invalidateInspection();});
for(const id of ['model-id','revision','device','batch'])$(id).addEventListener('input',()=>{if(id==='model-id'&&/^(?:[a-f0-9]{40}$|local-sha256:)/.test($('revision').value))$('revision').value=$('model-source').value==='local'?'local':'main';invalidateInspection();});
bind('inspect-model',async()=>{
  const epoch=++inspectionEpoch;inspectionBusy=true;modelInspection=null;inspectionSignature='';$('model-inspection').hidden=false;$('inspection-status').textContent='Reading configuration and tokenizer metadata…';$('inspection-facts').replaceChildren();$('inspection-blockers').replaceChildren();$('inspection-warnings').replaceChildren();actions();
  try{const inspected=await api('model-preflight',modelPayload());if(epoch!==inspectionEpoch)return;
    if(inspected.source_type!==$('model-source').value){inspected.can_load=false;inspected.blockers.push('The selected source does not match this model. Choose the matching source type and inspect again.');}
    modelInspection=inspected;$('model-id').value=inspected.model_id;if(inspected.source_type==='hub'&&inspected.resolved_revision)$('revision').value=inspected.resolved_revision;inspectionSignature=modelSignature();
    $('inspection-status').textContent=inspected.can_load?'Eligible native model · load to validate generation':'Attachment needs attention';$('model-inspection').dataset.state=inspected.can_load?'eligible':'blocked';
    const memory=inspected.memory||{},facts=[['Architecture',inspected.architecture||'Unavailable'],['Prompt format',inspected.chat_template?'Chat or completion':'Completion; no chat template'],['Context limit',inspected.context_limit?fmt(inspected.context_limit)+' tokens':'Unavailable'],['Weight memory',Number.isFinite(memory.weights_gb)?memory.weights_gb.toFixed(1)+' GiB, before cache and overhead':'Not estimated'],['Identity',inspected.source_type==='local'?'Content fingerprint at load':(inspected.resolved_revision||'Unresolved').slice(0,12)]];
    for(const [label,value] of facts){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('inspection-facts').append(dt,dd);}
    for(const [id,items]of [['inspection-blockers',inspected.blockers||[]],['inspection-warnings',[...(inspected.warnings||[]),...(memory.assumptions?[memory.assumptions]:[])]]])for(const text of items){const li=document.createElement('li');li.textContent=text;$(id).append(li);}
    $('inspection-notes').open=!inspected.can_load;
  }catch(e){if(epoch===inspectionEpoch){$('inspection-status').textContent='Inspection failed. Check the model and worker connection.';error(e.message);}}
  finally{if(epoch===inspectionEpoch)inspectionBusy=false;actions();}
});
bind('load',()=>{if(!modelInspection?.can_load||inspectionSignature!==modelSignature())throw new Error('Inspect these model settings before loading.');return start('load',modelPayload());});
bind('unload',()=>start('unload',{}));
bind('base',()=>start('base',{prompt:$('question').value,answers:$('answers').value.split('\n').filter(x=>x.trim()),mode:$('mode').value,max_tokens:num('base-cap'),seed:num('seed')}));
bind('run',()=>{if(!responseReadiness().canScan)throw new Error('Review the complete original response under Question before scanning.');return start('run',config());});bind('stop',async()=>{if(!state?.job?.id)throw Error('No active job is selected.');await api('stop',{job_id:state.job.id});$('phase').textContent='Stopping after the current operation…';});
for(const id of ['question','answers','mode','base-cap','seed'])$(id).addEventListener('input',()=>{baseFormDirty=true;baseReviewed=false;$('accept-other').checked=false;actions();});
for(const id of fields)$(id).addEventListener('input',queueEstimate);
for(const id of ['throughput','rate'])$(id).addEventListener('input',money);
for(const id of ['model-id','device'])$(id).addEventListener('input',actions);
async function listRuns(){const list=await api('runs');const previous=$('runs').value;$('runs').replaceChildren(new Option('Select a completed run',''),...list.map(r=>new Option(`${new Date(r.created*1000).toLocaleString()} · ${r.model} · ${(r.prompt??'').slice(0,80)}`,r.id)));$('runs').value=previous;}
async function loadResult(id){if(!id)return;const revision=++resultRevision;const loaded=await api('export?id='+encodeURIComponent(id));if(revision!==resultRevision)return;result=loaded;window.setForkMethodCredit?.(result);observatoryRoute(id);const route=new URL(location.href);route.searchParams.set('run',id);history.replaceState(null,'',route);$('outcome').replaceChildren(...result.categories.map(x=>new Option(x,x)));$('outcome').selectedIndex=suggestedOutcome(resultPasses(result),result.categories.length);graphRange=null;$('show-reconstruction').checked=!resultPasses(result).every(p=>{const e=passEvidence(p,result.records?.[p.id],result.categories.length);return e.sparse&&e.constant;});$('viewer-outcome').replaceChildren(new Option('All outcomes',''),...result.categories.map(x=>new Option(x,x)));$('viewer-search').value='';$('viewer-status').value='';$('export').hidden=false;$('export').href='#';$('export').onclick=async event=>{event.preventDefault();try{await saveJSON($('export'),'fork-run-'+id+'.json',()=>api('export?id='+encodeURIComponent(id)));}catch(e){error(e.message);}};$('export').download='fork-run-'+id+'.json';await draw();
  const ps=resultPasses(result);$('viewer-pass').replaceChildren(...ps.map(p=>new Option(p.label,p.id)),...(result.records?.dense?[new Option('Independent reference','dense')]:[]));viewerPositions();navigate('results');}
$('runs').addEventListener('change',()=>loadResult($('runs').value).catch(e=>error(e.message)));
for(const id of ['outcome','bands','observed-bands','show-reconstruction'])$(id).addEventListener('change',()=>draw().catch(e=>error(e.message)));
async function draw(){
  if(!result)return;$('match-rule').textContent=result.base.question.matching==='answer_text_anywhere_v1'?'Readout: one distinct answer-text match anywhere in a completed reply. These curves measure matching text, not semantic correctness. Multiple matches, no match and unfinished replies count as Other.':'Historical A–D readout: labels retain the parser used when this run was collected.';const k=result.categories.indexOf($('outcome').value),traces=[],ps=resultPasses(result);
  if(result.reference?.valid!==false&&result.reference)traces.push({x:result.reference.positions,y:result.reference.values.map(v=>v[k]),name:'Independent dense reference',mode:'lines',line:{color:'#8793a5',width:1.5}});
  const colors=['#2563eb','#c87816','#17815c','#963dcc','#c02e52','#367783','#6d641f','#48516f'];
  const summaries=[],shapes=[];graphIntervals=[];
  for(const [i,p]of ps.entries()){
    const c=p.curve,color=colors[i%colors.length],e=passEvidence(p,result.records?.[p.id],result.categories.length);
    const fitted=reconstructionPoints(c,k);
    if($('show-reconstruction').checked&&fitted.length){
      if($('bands').checked){traces.push({x:fitted.map(v=>v.t),y:fitted.map(v=>v.low),mode:'lines',line:{width:0},showlegend:false,hoverinfo:'skip',connectgaps:false});traces.push({x:fitted.map(v=>v.t),y:fitted.map(v=>v.high),mode:'lines',line:{width:0},fill:'tonexty',fillcolor:color+'18',showlegend:false,hoverinfo:'skip',connectgaps:false});}
      traces.push({x:fitted.map(v=>v.t),y:fitted.map(v=>v.value),name:p.label+' · reconstruction',mode:'lines',line:{color,width:2,dash:'dash'},connectgaps:false,hovertemplate:'Token %{x}<br>Reconstructed proportion: %{y:.3f}<br>Not a sampled observation<extra></extra>'});
    }
    const intervals=e.observed.map(v=>v.counts?wilsonInterval(v.counts[k],v.samples):null);
    traces.push({x:e.observed.map(v=>v.t),y:e.observed.map(v=>v.values[k]),name:p.label+(result.schema_version===2?' · observed':' · legacy weighted'),mode:'markers',marker:{color,size:9,line:{width:1,color:'#fff'}},
      error_y:{type:'data',symmetric:false,visible:$('observed-bands').checked,array:intervals.map((v,j)=>v?v[1]-e.observed[j].values[k]:0),arrayminus:intervals.map((v,j)=>v?e.observed[j].values[k]-v[0]:0),thickness:1,width:4,color},
      customdata:e.observed.map(v=>[p.id,JSON.stringify(result.base.tokens[v.t]),v.counts?`${v.counts[k]} / ${v.samples} samples`:'Legacy or unverified counts']),hovertemplate:'Checkpoint %{x}: %{customdata[1]}<br>Observed proportion: %{y:.3f}<br>%{customdata[2]}<extra></extra>'});
    const status=e.constant?'No sampled outcome differences.':e.intervals.some(v=>v.tv>0)?'Sampled outcome differences present; inspect below.':'No comparable adjacent observations.';
    summaries.push(`${p.label}: ${e.observed.length} checkpoints. ${status} ${e.segmentationEnabled?'Segmentation enabled.':'Segmentation unavailable.'}${e.sparse?' Fewer than four checkpoints; gaps remain unmeasured.':''}${e.invalidPoints?' Invalid saved observations were omitted.':''}`);
    for(const interval of e.intervals){
      graphIntervals.push({...interval,passId:p.id,label:p.label});
      if(interval.segmented)shapes.push({type:'rect',xref:'x',yref:'paper',x0:interval.left,x1:interval.right,y0:0,y1:1,fillcolor:color+'12',line:{width:1,color:color+'66'},layer:'below'});
    }
  }
  $('bands').disabled=!$('show-reconstruction').checked;
  $('graph-summary').replaceChildren(...summaries.map(text=>{const p=document.createElement('p');p.textContent=text;return p;}));
  await Plotly.react('live-plot',traces,{height:480,margin:{l:65,r:20,t:20,b:110},xaxis:{title:{text:'Response-token position'},...(graphRange?{range:graphRange}:{autorange:true})},yaxis:{title:{text:`Proportion: ${$('outcome').value}`},range:[-.04,1.04]},legend:{orientation:'h',y:-.22},shapes,paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'#0b1725',font:{color:'#b9cce0'},hovermode:'closest',dragmode:'zoom',uirevision:result.id},{responsive:true,displaylogo:false,scrollZoom:false});
  const plot=$('live-plot');plot.removeAllListeners?.('plotly_click');plot.on?.('plotly_click',event=>{const point=event.points[0];if(!point.customdata)return;openCheckpoint(point.customdata[0],point.x);});
  renderIntervals();
  $('result-label').textContent=`${result.model.model_id} · ${result.schema_version===2?'all collected continuations enter each fit':'legacy per-branch sampling'} · ${result.base.question.question}`;
  $('result-warnings').replaceChildren();
  const warnings=result.schema_version===2?[]:['Legacy run: fitted lines use a subsample of the collected outcomes; per-continuation completion metadata may be unavailable.'];
  if(result.reference?.warning)warnings.push(result.reference.warning);
  $('statistics').replaceChildren();
  for(const p of ps){const c=p.curve,m=result.measured[p.id],box=document.createElement('div');box.className='stat-card';const title=document.createElement('strong');title.textContent=p.label;box.append(title);
    const lines=[`${fmt(m.continuations)} continuations · ${fmt(m.continuation_tokens)} generated tokens`,`${m.wall_seconds.toFixed(1)} seconds collection`,`${m.at_continuation_cap}/${m.continuations} reached the token cap.`];
    if(c.parameters)lines.push(`Fit: ${c.parameters.variant}, penalty ${c.parameters.pen}, bandwidth ${c.parameters.h}`,!passEvidence(p,result.records?.[p.id],result.categories.length).segmentationEnabled?'Segmentation unavailable for this pass.':`Fitted change intervals: ${passEvidence(p,result.records?.[p.id],result.categories.length).intervals.filter(b=>b.segmented).map(b=>`${b.left}–${b.right}`).join(', ')||'none detected'}`);else lines.push('Reconstruction withheld. Inspect the outcomes below.');
    if(c.comparison)lines.push(`Mean TV to reference: ${c.comparison.mean_tv.toFixed(4)}`,`Held-out log likelihood: ${c.comparison.mean_log_likelihood.toFixed(4)}`,`Band coverage: ${(100*c.comparison.empirical_band_coverage).toFixed(1)}% (model-based bands exclude exact 0/1).`);else lines.push('No valid independent-reference comparison.');
    for(const text of lines){const d=document.createElement('div');d.textContent=text;box.append(d);}$('statistics').append(box);
    warnings.push(...(c.warnings||[]).map(w=>`${p.label}: ${w}`));
    if(result.schema_version!==2&&m.at_continuation_cap/m.continuations>.1)warnings.push(`${p.label}: excessive cap hits in this historical run. Do not interpret its fitted curve as completed-answer behavior.`);
  }
  for(const w of warnings){const el=document.createElement('p');el.className='warning';el.textContent=w;$('result-warnings').append(el);}
}
function openCheckpoint(passId,t){
  $('viewer-pass').value=passId;$('viewer-search').value='';$('viewer-status').value='';$('viewer-outcome').value='';viewerPositions(t);navigate('evidence');$('continuations').scrollIntoView({behavior:'smooth',block:'nearest'});
}
function renderIntervals(){
  const select=$('change-interval'),previous=select.value;
  select.replaceChildren(...graphIntervals.map((v,i)=>new Option(`${v.label} · ${v.left}–${v.right} · ${v.segmented?'fitted interval':'observed difference'} · TV ${v.tv.toFixed(3)}`,`${v.passId}:${v.left}:${v.right}`)));
  if([...select.options].some(o=>o.value===previous))select.value=previous;
  if(!graphIntervals.length)select.append(new Option('No sampled differences or supported fitted intervals',''));
  select.disabled=!graphIntervals.length;inspectInterval();
}
function selectedInterval(){return graphIntervals.find(v=>`${v.passId}:${v.left}:${v.right}`===$('change-interval').value);}
function inspectInterval(){
  const v=selectedInterval();for(const id of ['interval-left','interval-right','interval-zoom'])$(id).disabled=!v;
  $('interval-text').hidden=!v;
  if(!v){$('interval-summary').textContent='No change interval to inspect. You can still click any observed point to read the saved continuations. Flat sampled outcomes do not rule out changes inside unsampled gaps.';return;}
  const k=result.categories.indexOf($('outcome').value);
  $('interval-summary').textContent=`${v.segmented?'The segmentation fit selected this interval.':'Adjacent observed distributions differ here; the fit did not select this interval.'} ${$('outcome').value}: ${(v.leftValues[k]*100).toFixed(1)}% → ${(v.rightValues[k]*100).toFixed(1)}%. Total variation (TV) ${v.tv.toFixed(3)} measures the change across all outcome proportions, from 0 (same) to 1 (disjoint). This is descriptive, not a significance test. The later checkpoint additionally preserves original tokens ${v.left}–${v.right-1}:`;
  $('interval-text').textContent=(result.base.tokens??[]).slice(v.left,v.right).join('');
}
$('change-interval').onchange=inspectInterval;
$('interval-left').onclick=()=>{const v=selectedInterval();if(v)openCheckpoint(v.passId,v.left);};
$('interval-right').onclick=()=>{const v=selectedInterval();if(v)openCheckpoint(v.passId,v.right);};
$('interval-zoom').onclick=()=>{const v=selectedInterval();if(!v)return;const padding=Math.max(2,(v.right-v.left)*.25);graphRange=[Math.max(0,v.left-padding),v.right+padding];Plotly.relayout('live-plot',{'xaxis.range':graphRange,'xaxis.autorange':false});};
$('reset-zoom').onclick=()=>{graphRange=null;Plotly.relayout('live-plot',{'xaxis.autorange':true});};
function navigate(view){
  document.querySelector('.live-workspace').dataset.view=view;$('scan-intro').hidden=view!=='setup';
  for(const el of document.querySelectorAll('.configure-tools button[data-view]')){
    if(el.dataset.view===view)el.setAttribute('aria-current','true');else el.removeAttribute('aria-current');
  }
  if(view==='results'&&result)Plotly.Plots?.resize('live-plot');
}
for(const el of document.querySelectorAll('.configure-tools button[data-view]'))el.onclick=()=>navigate(el.dataset.view);
$('browse-evidence').onclick=()=>navigate('evidence');$('back-results').onclick=()=>navigate('results');
let evidencePage=0,selectedDraw=null;
const pageSize=30;
function viewerPositions(selected){
  const rec=result?.records?.[$('viewer-pass').value];const ts=[...new Set((rec?.branches||[]).map(b=>b.t))].sort((a,b)=>a-b);
  $('viewer-position').replaceChildren(new Option('All checkpoints',''),...ts.map(t=>new Option('Token '+t,t)));if(ts.includes(selected))$('viewer-position').value=selected;viewer();
}
function viewer(reset=true){
  if(reset){evidencePage=0;selectedDraw=null;}
  const rec=result?.records?.[$('viewer-pass').value],position=$('viewer-position').value;
  $('continuations').replaceChildren();
  $('viewer-note').textContent=rec?'Run '+result.id+' · '+result.base.question.question:'Select a saved run. No attached model is needed to browse.';
  const pos=rec?.positions?.find(p=>String(p.t)===position);
  $('checkpoint-info').textContent=pos?`${pos.samples} total samples · ${pos.candidates.length} retained branches · ${(100*pos.retained_mass).toFixed(3)}% next-token probability mass retained.`:'Browse all checkpoints or choose one. Each row is an actual saved continuation; completed does not mean correct.';
  const rows=[];let total=0;
  const query=$('viewer-search').value.toLocaleLowerCase(),outcome=$('viewer-outcome').value,status=$('viewer-status').value;
  for(const b of rec?.branches||[]){
    if(position!==''&&String(b.t)!==position)continue;
    b.answers.forEach((answer,i)=>{total++;const obs=b.observations?.[i];
      if(outcome&&outcome!==answer)return;
      if(status==='ambiguous'?obs?.label_source!=='ambiguous':status&&obs?.stop_reason!==status)return;
      if(query&&!(obs?.full_response_text??'').toLocaleLowerCase().includes(query))return;
      rows.push({b,i,answer,obs,key:`${b.t}:${b.tok_id}:${i}`});
    });
  }
  rows.sort((a,b)=>a.b.t-b.b.t||(a.b.draw_indices?.[a.i]??a.i)-(b.b.draw_indices?.[b.i]??b.i));
  evidencePage=Math.min(evidencePage,Math.max(0,Math.ceil(rows.length/pageSize)-1));
  const page=rows.slice(evidencePage*pageSize,(evidencePage+1)*pageSize);
  $('viewer-count').textContent=`${rows.length} of ${total} continuations match · Page ${evidencePage+1} of ${Math.max(1,Math.ceil(rows.length/pageSize))}`;
  $('draw-prev').disabled=evidencePage===0;$('draw-next').disabled=(evidencePage+1)*pageSize>=rows.length;
  if(!page.some(r=>r.key===selectedDraw))selectedDraw=page[0]?.key??null;
  for(const row of page){const b=document.createElement('button');b.className='draw-row';b.setAttribute('role','radio');b.setAttribute('aria-checked',String(row.key===selectedDraw));b.tabIndex=row.key===selectedDraw?0:-1;
    const title=document.createElement('strong');title.textContent=`Token ${row.b.t} · Continuation ${(row.b.draw_indices?.[row.i]??row.i)+1} · ${row.answer}`;
    const meta=document.createElement('span');meta.textContent=`${row.obs?.stop_reason==='length'?'Reached cap':row.obs?.stop_reason==='eos'?'Completed':'Historical'} · ${row.obs?.generated_tokens??row.b.cont_lens?.[row.i]??'?'} new tokens`;
    b.append(title,meta);b.onclick=()=>{selectedDraw=row.key;viewer(false);$('continuations').querySelector('[aria-checked="true"]')?.focus();};b.onkeydown=e=>{if(['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();let i=page.findIndex(r=>r.key===selectedDraw);i=e.key==='Home'?0:e.key==='End'?page.length-1:(i+(['ArrowDown','ArrowRight'].includes(e.key)?1:-1)+page.length)%page.length;selectedDraw=page[i].key;viewer(false);$('continuations').querySelector('[aria-checked="true"]')?.focus();}};$('continuations').append(b);
  }
  showDraw(page.find(r=>r.key===selectedDraw));
}
function showDraw(row){
  const target=$('draw-detail');target.replaceChildren();
  if(!row){const p=document.createElement('p');p.textContent='No continuations match these filters.';target.append(p);return;}
  const {b,i,answer,obs}=row;
  const title=document.createElement('h3');title.textContent=`Checkpoint ${b.t} / Continuation ${(b.draw_indices?.[i]??i)+1}`;target.append(title);
  const meta=document.createElement('p');meta.className='help';meta.textContent=`Outcome: ${answer} · branch token ID ${b.tok_id} · next-token P=${b.tok_p.toFixed(6)} · ${obs?.label_source??'historical extraction'} · ${obs?.channel_reached??'channel unknown'}`;target.append(meta);
  if(obs?.matched_answers?.length){const p=document.createElement('p');p.textContent='Matched answer texts: '+obs.matched_answers.join(' / ');target.append(p);}
  const mode=document.createElement('select');mode.setAttribute('aria-label','Displayed text');mode.append(new Option('Newly generated continuation','continuation'),new Option('Full response: preserved prefix + branch + continuation','full'),new Option('Reply used for matching','reply'),new Option('Continuation token IDs','ids'));target.append(mode);
  const note=document.createElement('p');note.className='help';target.append(note);const pre=document.createElement('pre');target.append(pre);
  const show=()=>{const value=mode.value;
    note.textContent=value==='continuation'?'Only the new text after the selected branch token. The preserved prefix is excluded.':value==='full'?'The response prefix and branch token are included; the original question is excluded.':value==='reply'?'Completed reply text supplied to the matcher. Untagged reasoning in generic models can be included.':'Raw generated continuation IDs, excluding the forced branch token and stripped terminal EOS.';
    pre.textContent=value==='ids'?JSON.stringify(b.continuation_ids?.[i]??[]):value==='full'?(obs?.full_response_text??'Full text was not saved in this historical run.'):value==='reply'?(obs?.reply_text??'No completed reply was saved for matching.'):(obs?.continuation_text??'Decoded text was not saved in this historical run. Choose token IDs.');
  };mode.onchange=show;show();
}
$('viewer-pass').onchange=()=>viewerPositions();
for(const id of ['viewer-position','viewer-outcome','viewer-status'])$(id).onchange=()=>viewer();
$('viewer-search').oninput=()=>viewer();
$('draw-prev').onclick=()=>{evidencePage--;viewer(false);};$('draw-next').onclick=()=>{evidencePage++;viewer(false);};
function register(){const context=document.modelContext;if(!context?.registerTool)return;const lifecycle=new AbortController();window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
  for(const tool of [{name:'read_live_fork',description:'Read attached model, original response and current job. Does not start computation.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:refresh},
    {name:'start_live_fork_action',description:'Start load, base, run or unload. Loading may download weights. Load settings: model_id, revision, device (auto/cpu/cuda), batch_size. Base settings: prompt (text), answers (1–32 unique strings to match anywhere in completed replies), mode (chat/base), max_tokens, seed. Unload takes {}. Run settings: passes array (id,label,start,end,stride,offset,samples 5-512 samples per checkpoint,seed), cont_max,temperature,top_k,threshold,dense,reference_samples,tuning. Maximum 8 passes. Read status to track completion.',inputSchema:{type:'object',properties:{action:{type:'string',enum:['load','base','run','unload']},settings:{type:'object'}},required:['action','settings'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async input=>{const response=await start(input.action,input.settings);if(input.action==='run'&&input.settings.passes){passes=input.settings.passes.map(p=>({...p}));activePass=passes[0].id;renderPasses();}return response;}}]){try{Promise.resolve(context.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}}
}
async function poll(){try{await refresh();}catch(e){error(e.message);}setTimeout(poll,1500);}
renderPasses();await listRuns().catch(e=>error(e.message));await refresh().catch(e=>error(e.message));
const urlParams=new URLSearchParams(location.search),requestedRun=urlParams.get('run'),sourceRun=urlParams.get('source');
if(sourceRun){try{const source=await api('result?id='+encodeURIComponent(sourceRun));$('model-id').value=source.model.model_id;$('model-source').value=source.model.source_type||(/^(?:\/|\.|~)/.test(source.model.model_id)?'local':'hub');sourceHelp();$('revision').value=source.model.resolved_revision||source.model.requested_revision||'main';$('source-model-note').hidden=false;$('source-model-note').textContent=`Continue from saved run ${sourceRun}. Load the same model and pinned revision shown below before refining its checkpoints. Nothing loads automatically.`;observatoryRoute(sourceRun);$('revision').closest('details').open=true;showSetupStep('model');actions();}catch(e){error('Could not read the source run: '+e.message);}}
else if(requestedRun){$('runs').value=requestedRun;await loadResult(requestedRun).catch(e=>error(e.message));}
if(urlParams.get('set')&&urlParams.get('prompt')){try{const saved=await api('prompt-sets'),set=saved.sets.find(item=>item.id===urlParams.get('set')),prompt=set?.prompts.find(item=>item.id===urlParams.get('prompt'));if(!prompt)throw new Error('This saved prompt is unavailable on the connected worker.');$('question').value=prompt.prompt;$('answers').value=prompt.answers.join('\n');$('mode').value=prompt.mode;$('base-cap').value=prompt.max_tokens;$('seed').value=prompt.seed;baseFormDirty=true;showSetupStep('prompt');navigate('setup');actions();}catch(e){error(e.message);}}
if(urlParams.get('view')==='setup'||sourceRun)navigate('setup');sourceHelp();register();poll();
window.addEventListener('worker-connection-change',async()=>{
  inspectionEpoch++;inspectionBusy=false;modelInspection=null;inspectionSignature='';$('model-inspection').hidden=true;state=null;result=null;runtimeConnected=false;budget=null;budgetIssue='';lastBaseSignature='';lastResultId='';baseFormDirty=false;resultRevision++;estimateRevision++;$('runs').replaceChildren(new Option('Select a completed run',''));$('model-id').value='';$('revision').value='main';$('model-source').value='hub';$('source-model-note').hidden=true;$('budget').textContent='Attach a model and generate a response to configure a scan.';$('money').textContent='';$('base-text').textContent='No generated response yet.';$('base-tokens').textContent='No response yet.';$('export').hidden=true;observatoryRoute(null);sourceHelp();showSetupStep('model');navigate('setup');actions();
  try{await listRuns();await refresh();}catch(e){error(e.message);}
});
