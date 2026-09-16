// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — approved UI glossary.
// Saved prompt sets live on the selected worker. Browser edits are unsaved until POST succeeds.
export const DEFAULT_SCAN = Object.freeze({stride:32,samples:20,cont_max:768,temperature:1,top_k:10,threshold:.05,seed:0,tuning:'cv'});
const isInt=(value,min,max)=>Number.isInteger(value)&&value>=min&&value<=max;
const isReal=(value,min,max)=>Number.isFinite(value)&&value>=min&&value<=max;
export function validatePromptSet(set){
  if(typeof set?.name!=='string'||!set.name.trim()||set.name.length>120)return 'Name this prompt set using 1–120 characters.';
  if(!Array.isArray(set.prompts)||set.prompts.length<1||set.prompts.length>50)return 'A set needs 1–50 prompts.';
  if(new Set(set.prompts.map(p=>p.id)).size!==set.prompts.length)return 'Prompt IDs must be unique within this set.';
  for(let i=0;i<set.prompts.length;i++){
    const p=set.prompts[i],label=`Prompt ${i+1}`;
    if(typeof p.title!=='string'||!p.title.trim()||p.title.length>120)return `${label}: enter a title with 1–120 characters.`;
    if(typeof p.prompt!=='string'||!p.prompt.trim()||p.prompt.length>16000)return `${label}: enter prompt text up to 16,000 characters.`;
    if(!Array.isArray(p.answers)||p.answers.length<1||p.answers.length>32)return `${label}: enter 1–32 answer labels, one per line.`;
    if(p.answers.some(a=>typeof a!=='string'||!a.trim()||a.length>200||/[\r\n]/.test(a)))return `${label}: each answer label needs 1–200 characters on one line.`;
    const labels=p.answers.map(a=>a.normalize('NFKC').toLowerCase().trim().replace(/\s+/g,' '));
    if(labels.includes('other')||new Set(labels).size!==labels.length)return `${label}: answer labels must be unique; Other is reserved.`;
    if(!['chat','base'].includes(p.mode))return `${label}: choose chat or base format.`;
    if(!isInt(p.max_tokens,8,4096))return `${label}: original-response cap must be a whole number from 8 to 4,096.`;
    if(!isInt(p.seed,0,2147483647))return `${label}: seed must be a whole number from 0 to 2,147,483,647.`;
  }
  return '';
}
export function validateScan(scan){
  for(const [key,label,min,max] of [['stride','Checkpoint spacing',1,128],['samples','Samples per checkpoint',5,512],['cont_max','Continuation cap',1,4096],['top_k','Top-k candidates',1,50],['seed','Scan seed',0,2147483647]])if(!isInt(scan?.[key],min,max))return `${label} must be a whole number from ${min.toLocaleString()} to ${max.toLocaleString()}.`;
  if(!isReal(scan.temperature,.05,2))return 'Continuation temperature must be between 0.05 and 2.';
  if(!isReal(scan.threshold,0,1))return 'Minimum branch probability must be between 0 and 1.';
  if(!['cv','fixed'].includes(scan.tuning))return 'Choose cross-validation or fixed reconstruction tuning.';
  return '';
}
export function upperBound(prompts,scan){
  if(validateScan(scan)||prompts.some(p=>!isInt(p.max_tokens,8,4096)))return null;
  const checkpoints=prompts.reduce((n,p)=>{const last=p.max_tokens-1;return n+Math.floor(last/scan.stride)+1+(last%scan.stride!==0?1:0);},0);
  return {checkpoints,draws:checkpoints*scan.samples,new_tokens:checkpoints*scan.samples*scan.cont_max};
}
export function batchReadiness({connected,model,job,saved,dirty,selectedCount,pending,scanIssue}){
  if(pending)return 'Finishing the current workspace request…';
  if(!connected)return 'Connect a worker before running. You can still prepare prompt text here.';
  if(!model)return 'No model is attached. Open Configure and load a compatible model on this worker.';
  if(['running','queued','starting','stopping','cancelling'].includes(job?.status))return 'This worker is busy. Wait for its current job to finish or stop that job first.';
  if(!saved||dirty)return 'Save this prompt set before running so the batch has a fixed snapshot.';
  if(!selectedCount)return 'Select at least one prompt using its Run checkbox.';
  if(scanIssue)return scanIssue;
  return '';
}
export function parsePromptSetImport(value,newId=()=>crypto.randomUUID()){
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Choose a JSON prompt-set object.');
  if(value.schema&&value.schema!=='fork-microscope-prompt-set-v1')throw new Error('This is not a supported prompt-set export.');
  const source=value.set??value;
  if(!Array.isArray(source.prompts))throw new Error('The JSON file has no prompt list.');
  const result={name:source.name,prompts:source.prompts.map(p=>({id:newId(),title:p.title,prompt:p.prompt,answers:p.answers,mode:p.mode,max_tokens:p.max_tokens,seed:p.seed}))};
  const issue=validatePromptSet(result);if(issue)throw new Error(issue);return result;
}
export function isSaveShortcut(event){return Boolean((event.ctrlKey||event.metaKey)&&!event.altKey&&!event.shiftKey&&event.key?.toLowerCase()==='s');}
if(typeof document!=='undefined')bootstrap();

function bootstrap(){
  const $=id=>document.getElementById(id);
  const clone=value=>JSON.parse(JSON.stringify(value));
  const fmt=value=>new Intl.NumberFormat(undefined,{maximumFractionDigits:0}).format(value);
  let sets=[],batches=[],current,baseline='',selected=new Set(),status=null,connected=false,writePending=false,initialLoading=true;
  let expandedPrompt=null;
  let epoch=0,polling=false,setsLoading=false,batchesLoading=false,discardAction=null,lastBatchesSignature='',pollTimer=null;
  const scanFields={stride:'stride',samples:'samples',cont_max:'cont-max',temperature:'temperature',top_k:'top-k',threshold:'threshold',seed:'scan-seed',tuning:'tuning'};
  const makePrompt=number=>({id:crypto.randomUUID(),title:`Prompt ${number}`,prompt:'',answers:[],mode:'chat',max_tokens:512,seed:0});
  const content=()=>({name:current.name,prompts:current.prompts});
  const dirty=()=>JSON.stringify(content())!==baseline;
  const signal=(id,message='')=>{$(id).textContent=message;$(id).hidden=!message;};
  const text=(tag,value,className)=>{const element=document.createElement(tag);element.textContent=value;if(className)element.className=className;return element;};
  const modelName=model=>typeof model==='string'?model:(model?.model_id||model?.name||'Attached model');
  const scan=()=>Object.fromEntries(Object.entries(scanFields).map(([key,id])=>[key,key==='tuning'?$(id).value:$(id).value.trim()===''?NaN:Number($(id).value)]));
  const selectedPrompts=()=>current.prompts.filter(p=>selected.has(p.id));
  const batchState=batch=>batch.state||'unknown';
  const stamp=value=>{if(value===undefined||value===null)return '';const parsed=new Date(typeof value==='number'&&value<1e12?value*1000:value);return Number.isNaN(parsed.getTime())?'':parsed.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});};
  async function api(path,payload){
    let response;
    try{response=await (window.workerFetch||window.fetch.bind(window))('/api/live/'+path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}catch(error){throw new Error(error?.message||'Cannot reach the worker. Check its connection and retry.');}
    let value;try{value=await response.json();}catch{throw new Error('The worker did not return an API response. Check its address and forwarded app port.');}
    if(!response.ok)throw new Error(value.error||'The worker rejected this request.');
    return value;
  }
  function newSet(){current={name:'',prompts:[makePrompt(1)]};baseline=JSON.stringify(content());selected=new Set(current.prompts.map(p=>p.id));signal('set-error');signal('workspace-notice');renderEditor();renderLibrary();updateURL();$('set-name').focus();}
  function openSet(set){current=clone(set);baseline=JSON.stringify(content());selected=new Set(current.prompts.map(p=>p.id));signal('set-error');signal('workspace-notice');renderEditor();renderLibrary();updateURL();$('set-name').focus();}
  function updateURL(){const url=new URL(location.href);if(current.id)url.searchParams.set('set',current.id);else url.searchParams.delete('set');history.replaceState(null,'',url);}
  function guardUnsaved(action){if(!dirty()){action();return;}discardAction=action;$('discard-dialog').showModal();}
  function renderLibrary(){
    const focus=document.activeElement?.dataset.setId;
    const query=$('set-search').value.toLocaleLowerCase();
    const visible=sets.filter(s=>s.name.toLocaleLowerCase().includes(query));
    $('set-list').replaceChildren(...visible.map(set=>{const button=text('button','','set-choice');button.type='button';button.dataset.setId=set.id;button.setAttribute('aria-pressed',String(set.id===current.id));button.append(text('strong',set.name),text('small',`${set.prompts.length} ${set.prompts.length===1?'prompt':'prompts'} · revision ${set.revision??'—'}`));button.disabled=writePending;button.onclick=()=>guardUnsaved(()=>openSet(set));return button;}));
    if(!visible.length)$('set-list').append(text('p',needsConnection()?'Connect a worker to open its saved prompt sets.':sets.length?'No sets match this name.':'No saved sets yet. Create your first set in the editor.','empty'));
    if(focus)$('set-list').querySelector(`[data-set-id="${CSS.escape(focus)}"]`)?.focus({preventScroll:true});
  }
  function labeled(labelText,element){const label=text('label',labelText);label.htmlFor=element.id;label.append(element);return label;}
  function makeInput(id,type,value,attributes,oninput){const input=document.createElement('input');input.id=id;input.type=type;input.value=value;Object.assign(input,attributes);input.oninput=()=>oninput(type==='number'?(input.value.trim()===''?NaN:Number(input.value)):input.value);return input;}
  function updateModel(p,key,value){p[key]=value;updateActions();}
  function renderEditor(){
    if(!current.prompts.some(p=>p.id===expandedPrompt))expandedPrompt=current.prompts[0]?.id;
    $('set-name').value=current.name;$('editor-heading').textContent=current.id?current.name:'New set';
    $('prompt-list').replaceChildren(...current.prompts.map((p,index)=>{
      const card=document.createElement('section');card.className='prompt-card panel';card.dataset.promptId=p.id;
      const head=document.createElement('div');head.className='prompt-card-heading';
      const check=document.createElement('input');check.type='checkbox';check.id=`select-${p.id}`;check.checked=selected.has(p.id);check.onchange=()=>{check.checked?selected.add(p.id):selected.delete(p.id);updateActions();};
      const choice=labeled('Run',check);choice.className='prompt-selection';choice.prepend(check);check.setAttribute('aria-label',`Select ${p.title||`prompt ${index+1}`} for batch`);
      const summary=document.createElement('div');summary.className='prompt-summary';const heading=text('h3',p.title||`Prompt ${index+1}`);heading.id=`heading-${p.id}`;card.setAttribute('aria-labelledby',heading.id);summary.append(heading,text('small',`Independent original response · prompt ${index+1}`));head.append(choice,summary);card.append(head);
      const toggle=text('button','','prompt-toggle');toggle.type='button';toggle.setAttribute('aria-controls',`body-${p.id}`);toggle.setAttribute('aria-expanded',String(expandedPrompt===p.id));toggle.append(...summary.childNodes);summary.append(toggle);
      const body=document.createElement('div');body.id=`body-${p.id}`;body.hidden=expandedPrompt!==p.id;body.className='prompt-body';card.append(body);
      toggle.onclick=()=>{expandedPrompt=body.hidden?p.id:null;for(const item of $('prompt-list').querySelectorAll('.prompt-card')){const active=item.dataset.promptId===expandedPrompt;item.querySelector('.prompt-toggle').setAttribute('aria-expanded',String(active));item.querySelector('.prompt-body').hidden=!active;}};
      const title=makeInput(`title-${p.id}`,'text',p.title,{maxLength:120},value=>{updateModel(p,'title',value);heading.textContent=value||`Prompt ${index+1}`;check.setAttribute('aria-label',`Select ${value||`prompt ${index+1}`} for batch`);});
      const prompt=document.createElement('textarea');prompt.id=`prompt-${p.id}`;prompt.className='prompt-text';prompt.rows=5;prompt.maxLength=16000;prompt.value=p.prompt;prompt.placeholder='Write the question or task for the model…';prompt.oninput=()=>updateModel(p,'prompt',prompt.value);
      const answers=document.createElement('textarea');answers.id=`answers-${p.id}`;answers.className='answer-text';answers.rows=3;answers.value=p.answers.join('\n');answers.placeholder='One answer label per line';answers.oninput=()=>updateModel(p,'answers',answers.value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean));
      body.append(labeled('Prompt title',title),labeled('Prompt text',prompt),labeled('Answer labels · one per line',answers));
      const advanced=document.createElement('details');advanced.className='prompt-advanced';advanced.append(text('summary','Prompt format, response cap & seed'));const advancedBody=document.createElement('div');
      const mode=document.createElement('select');mode.id=`mode-${p.id}`;mode.append(new Option("Model's chat template",'chat'),new Option('Base / completion','base'));mode.value=p.mode;mode.onchange=()=>updateModel(p,'mode',mode.value);advancedBody.append(labeled('Prompt format',mode));
      const pair=document.createElement('div');pair.className='field-pair';pair.append(labeled('Original-response token cap',makeInput(`cap-${p.id}`,'number',p.max_tokens,{min:8,max:4096,step:1},value=>updateModel(p,'max_tokens',value))),labeled('Original-response seed',makeInput(`seed-${p.id}`,'number',p.seed,{min:0,max:2147483647,step:1},value=>updateModel(p,'seed',value))));advancedBody.append(pair);advanced.append(advancedBody);body.append(advanced);
      const footer=document.createElement('div');footer.className='prompt-card-footer';const link=text('a','Open in Configure ↗','configure-prompt');link.dataset.promptId=p.id;const remove=text('button','Remove prompt','text-button danger');remove.type='button';remove.dataset.removePrompt=p.id;remove.onclick=()=>{current.prompts=current.prompts.filter(item=>item.id!==p.id);selected.delete(p.id);renderEditor();$('add-prompt').focus();};footer.append(link,remove);body.append(footer);return card;
    }));updateActions();
  }
  function updateActions(){
    const changed=dirty(),saved=Boolean(current.id),count=selectedPrompts().length,issue=validateScan(scan());
    $('prompt-count').textContent=`${current.prompts.length} ${current.prompts.length===1?'prompt':'prompts'}`;$('selection-count').textContent=` · ${count} selected`;
    $('save-state').textContent=writePending?'Request in progress…':changed?'Unsaved changes':saved?'Saved on worker':'Not saved';$('save-state').classList.toggle('dirty',changed);
    $('set-revision').textContent=saved?`Revision ${current.revision??'—'}${stamp(current.updated_at)?' · '+stamp(current.updated_at):''}`:'';
    for(const element of $('set-form').querySelectorAll('input,textarea,select,button'))element.disabled=writePending;
    $('save-set').disabled=writePending||(connected&&saved&&!changed);$('save-set').textContent=connected?'Save set':'Export set to file';$('save-set').title=connected?'Save on the connected machine (Ctrl+S or ⌘S)':'Download a portable copy without compute (Ctrl+S or ⌘S)';$('delete-set').disabled=writePending||!saved;$('add-prompt').disabled=writePending||current.prompts.length>=50;
    for(const element of document.querySelectorAll('[data-remove-prompt]'))element.disabled=writePending||current.prompts.length<=1;
    $('new-set').disabled=writePending||initialLoading;$('reload-sets').disabled=writePending||setsLoading;
    $('import-set').disabled=writePending||initialLoading;$('export-set').disabled=writePending;
    for(const button of $('set-list').querySelectorAll('button'))button.disabled=writePending;
    for(const link of document.querySelectorAll('.configure-prompt')){if(saved&&!changed&&!writePending){link.href='/live.html?set='+encodeURIComponent(current.id)+'&prompt='+encodeURIComponent(link.dataset.promptId);link.removeAttribute('aria-disabled');link.textContent='Open in Configure ↗';}else{link.removeAttribute('href');link.setAttribute('aria-disabled','true');link.textContent='Save set to open in Configure';}}
    const reason=batchReadiness({connected,model:status?.model,job:status?.job,saved,dirty:changed,selectedCount:count,pending:writePending,scanIssue:issue});
    $('start-batch').disabled=Boolean(reason);$('start-batch').title=reason||'Generate and scan the selected prompts';$('batch-readiness').textContent=reason||`${count} ${count===1?'prompt':'prompts'} ready. The worker will generate a separate response for each.`;$('scan-fields').disabled=writePending;
    const budget=upperBound(selectedPrompts(),scan());
    $('batch-budget').textContent=!count?'Select prompts to estimate the maximum continuation allowance.':!budget?issue||'Enter valid original-response caps to estimate the allowance.':`At the selected original-response caps: at most ${fmt(budget.checkpoints)} checkpoint visits × ${fmt(scan().samples)} samples = ${fmt(budget.draws)} continuations, up to ${fmt(budget.new_tokens)} new continuation tokens. Actual responses may be shorter. This excludes original-response generation and prefix processing; it is not a time or cost estimate.`;
  }
  const needsConnection=()=>window.workerConnection?.().url===null;
  function renderWorker(){
    $('connection-badge').textContent=connected?'Worker connected':needsConnection()?'Connect your worker':'Worker unreachable';$('connection-badge').className='connection-badge '+(connected?'connected':'disconnected');
    const job=status?.job,working=connected&&['running','queued','starting','stopping','cancelling'].includes(job?.status);
    const workerLabel=needsConnection()?'Not connected':!connected?'Offline':working?'Busy':status?.model?'Ready':'No model';$('worker-state').textContent=workerLabel;$('worker-state').className='status-pill '+(working?'running':status?.model?'ready':'');
    $('worker-model').textContent=!connected?'Worker is not connected.':status?.model?modelName(status.model):'No model attached.';
    $('worker-phase').textContent=needsConnection()?'Use Connect a machine above to attach your local computer or GPU VM. You can prepare and export prompt text before connecting.':!connected?'Check the worker connection or its forwarded app port. Saved sets and batches need that worker to be reachable.':job?.phase||'The worker is idle.';
    $('worker-progress').hidden=!working;if(working){const total=Number(job.total),completed=Number(job.completed);if(total>0){$('worker-progress').max=total;$('worker-progress').value=Number.isFinite(completed)?Math.max(0,Math.min(total,completed)):0;}else $('worker-progress').removeAttribute('value');}
    $('retry-worker').hidden=connected||needsConnection();updateActions();
  }
  async function refreshSets({initial=false}={}){
    if(needsConnection()){initialLoading=false;signal('workspace-error');renderLibrary();updateActions();return;}
    if(setsLoading)return;const requestEpoch=epoch;setsLoading=true;updateActions();
    try{const value=await api('prompt-sets');if(requestEpoch!==epoch)return;if(!Array.isArray(value.sets))throw new Error('The worker returned an invalid prompt-set list.');sets=value.sets;signal('workspace-error');renderLibrary();
      if(initial){const wanted=new URL(location.href).searchParams.get('set');const found=sets.find(s=>s.id===wanted);if(found)guardUnsaved(()=>openSet(found));else if(wanted)signal('workspace-error','That prompt set is not available on this worker. Choose a saved set or create a new one.');}
    }catch(error){if(requestEpoch===epoch)signal('workspace-error',error.message);}finally{if(requestEpoch===epoch){setsLoading=false;initialLoading=false;updateActions();}}
  }
  async function saveSet(){
    if(writePending)return;if(!connected){exportSet();return;}const issue=validatePromptSet(current);if(issue){signal('set-error',issue);return;}
    const payload={...(current.id?{id:current.id}:{}),name:current.name.trim(),prompts:clone(current.prompts)},requestEpoch=epoch;writePending=true;signal('set-error');updateActions();
    try{const value=await api('prompt-set',payload);if(requestEpoch!==epoch)return;if(!value.set?.id)throw new Error('The worker did not confirm the saved set. Refresh saved sets before retrying.');const saved=value.set;sets=[saved,...sets.filter(s=>s.id!==saved.id)];current=clone(saved);baseline=JSON.stringify(content());signal('workspace-notice');renderEditor();renderLibrary();updateURL();$('editor-heading').textContent=current.name;
    }catch(error){if(requestEpoch===epoch)signal('set-error',error.message);}finally{if(requestEpoch===epoch){writePending=false;updateActions();}}
  }
  function exportSet(){
    const issue=validatePromptSet(current);if(issue){signal('set-error',issue);return;}
    const exported={schema:'fork-microscope-prompt-set-v1',name:current.name,prompts:clone(current.prompts)};
    const url=URL.createObjectURL(new Blob([JSON.stringify(exported,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=(current.name.replace(/[^a-z0-9_-]+/gi,'-').replace(/^-|-$/g,'').slice(0,80)||'prompt-set')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);signal('workspace-notice',dirty()?'Exported the current edits. They are still unsaved on the worker.':'Exported a portable prompt-set copy.');
  }
  async function importSet(file){
    if(!file||writePending)return;const requestEpoch=epoch;writePending=true;signal('workspace-error');updateActions();
    try{if(file.size>2*1024*1024)throw new Error('Prompt-set imports must be no larger than 2 MB.');let data;try{data=JSON.parse(await file.text());}catch{throw new Error('That file is not valid JSON.');}const payload=parsePromptSetImport(data);if(requestEpoch!==epoch)return;if(!connected){current=clone(payload);baseline='';selected=new Set(current.prompts.map(p=>p.id));renderEditor();renderLibrary();updateURL();signal('workspace-notice','Imported for editing in this browser. Export a file to keep your edits, or connect a machine to save a set there.');return;}const value=await api('prompt-set',payload);if(requestEpoch!==epoch)return;if(!value.set?.id)throw new Error('The worker did not confirm the imported set. Refresh saved sets before retrying.');sets=[value.set,...sets.filter(s=>s.id!==value.set.id)];openSet(value.set);signal('workspace-notice','Imported as a new saved prompt set. No model execution was started.');
    }catch(error){if(requestEpoch===epoch)signal('workspace-error',error.message);}finally{if(requestEpoch===epoch){writePending=false;$('import-file').value='';updateActions();}}
  }
  async function deleteSet(){
    if(writePending||!current.id)return;const id=current.id,requestEpoch=epoch;writePending=true;$('delete-dialog').close();signal('set-error');updateActions();
    try{const value=await api('prompt-set-delete',{id});if(requestEpoch!==epoch)return;if(value.deleted!==id)throw new Error('The worker did not confirm deletion. Refresh saved sets to check.');sets=sets.filter(s=>s.id!==id);newSet();}catch(error){if(requestEpoch===epoch)signal('set-error',error.message);}finally{if(requestEpoch===epoch){writePending=false;updateActions();}}
  }
  function renderBatches(){
    const signature=JSON.stringify([batches,status?.job?.id,status?.job?.status]);if(signature===lastBatchesSignature)return;lastBatchesSignature=signature;
    const focused=document.activeElement?.dataset.stopBatch;
    $('batch-list').replaceChildren(...batches.map(batch=>{
      const card=document.createElement('article');card.className='batch-record';card.dataset.batchId=batch.id;
      const head=document.createElement('div');head.className='batch-record-head';const label=document.createElement('div');label.append(text('h3',batch.set_name||'Prompt batch'),text('p',`${stamp(batch.created_at)||'Saved batch'} · ${modelName(batch.model)}`));const actions=document.createElement('div');actions.className='batch-record-actions';actions.append(text('span',batchState(batch),'status-pill '+batchState(batch)));
      const active=['pending','queued','running','stopping','cancelling'].includes(batch.state);
      if(active){const stop=text('button','Stop batch','text-button danger');stop.dataset.stopBatch=batch.id;const matching=connected&&status?.job?.id===batch.job_id&&status?.job?.status==='running';stop.disabled=!matching;stop.title=matching?'Stop this batch; completed runs are preserved.':'Waiting for a matching active worker job.';stop.onclick=()=>stopBatch(batch);actions.append(stop);}
      head.append(label,actions);card.append(head);
      const items=Array.isArray(batch.items)?batch.items:[],complete=items.filter(item=>['complete','completed'].includes(item.state)).length,terminal=items.filter(item=>['complete','completed','error','failed','cancelled','interrupted'].includes(item.state)).length;
      card.append(text('p',`${complete}/${items.length} prompts completed · ${terminal}/${items.length} finished or stopped`,'help'));
      const progress=document.createElement('progress');progress.max=Math.max(1,items.length);progress.value=terminal;progress.setAttribute('aria-label',`${terminal} of ${items.length} batch items finished or stopped`);card.append(progress);
      const policy=batch.scan||{};card.append(text('p',`Saved scan snapshot · spacing ${policy.stride??'—'} · ${policy.samples??'—'} samples/checkpoint · continuation cap ${policy.cont_max??'—'} · temperature ${policy.temperature??'—'} · seed ${policy.seed??'—'}`,'batch-scan'));
      if(batch.error)card.append(text('p',String(batch.error),'notice error'));
      const list=document.createElement('ul');list.className='batch-items';for(const item of items){const li=document.createElement('li');const title=text('div',item.title||'Untitled prompt','batch-item-title');if(item.error)title.append(text('small',String(item.error)));if(item.run_id&&item.state!=='complete')title.append(text('small','Run assigned; a completed result is not available. Any partial records remain on the worker.'));li.append(title,text('span',item.state||'pending','status-pill '+(item.state||'pending')));if(item.run_id&&item.state==='complete'){const links=document.createElement('div');links.className='batch-item-links';const explore=text('a','Explore run ↗');explore.href='/observatory.html?run='+encodeURIComponent(item.run_id);const compare=text('a','Compare ↗');compare.href='/compare.html?left='+encodeURIComponent(item.run_id);links.append(explore,compare);li.append(links);}list.append(li);}card.append(list);return card;
    }));
    if(!batches.length)$('batch-list').append(text('p',needsConnection()?'Connect a worker to view its batch history.':'No batches have run on this worker yet. Save a prompt set, attach a model, and select the prompts to run.','empty'));
    if(focused)$('batch-list').querySelector(`[data-stop-batch="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
  }
  async function refreshBatches(){
    if(needsConnection()){signal('batches-error');renderBatches();return;}
    if(batchesLoading)return;const requestEpoch=epoch;batchesLoading=true;$('reload-batches').disabled=true;
    try{const value=await api('batches');if(requestEpoch!==epoch)return;if(!Array.isArray(value.batches))throw new Error('The worker returned an invalid batch list.');batches=value.batches;signal('batches-error');renderBatches();}catch(error){if(requestEpoch===epoch)signal('batches-error',error.message);}finally{if(requestEpoch===epoch){batchesLoading=false;$('reload-batches').disabled=false;}}
  }
  async function refreshStatus(){if(needsConnection()){status=null;connected=false;renderWorker();renderBatches();return;}const requestEpoch=epoch;try{const value=await api('status');if(requestEpoch!==epoch)return;status=value;connected=true;}catch(error){if(requestEpoch!==epoch)return;connected=false;status=null;}if(requestEpoch===epoch){renderWorker();renderBatches();}}
  async function startBatch(){
    updateActions();if($('start-batch').disabled)return;
    const payload={set_id:current.id,prompt_ids:selectedPrompts().map(p=>p.id),scan:scan()},requestEpoch=epoch;writePending=true;signal('batch-error');updateActions();
    try{const value=await api('batch',payload);if(requestEpoch!==epoch)return;if(!value.job_id||!value.batch_id)throw new Error('The worker did not confirm the batch identity. Refresh batch history before retrying.');status={...status,job:{id:value.job_id,status:'running',action:'batch',phase:'Batch accepted. Waiting for worker progress.',completed:0,total:0}};renderWorker();await refreshBatches();$('history-heading').scrollIntoView({block:'start',behavior:'auto'});
    }catch(error){if(requestEpoch===epoch)signal('batch-error',error.message);}finally{if(requestEpoch===epoch){writePending=false;updateActions();}}
  }
  async function stopBatch(batch){
    if(!connected||status?.job?.id!==batch.job_id||status?.job?.status!=='running')return;const requestEpoch=epoch;
    const button=$('batch-list').querySelector(`[data-stop-batch="${CSS.escape(batch.id)}"]`);if(button){button.disabled=true;button.textContent='Stopping…';}
    try{await api('stop',{job_id:batch.job_id});if(requestEpoch!==epoch)return;await Promise.allSettled([refreshStatus(),refreshBatches()]);}catch(error){if(requestEpoch===epoch){signal('batches-error',error.message);lastBatchesSignature='';renderBatches();}}
  }
  async function poll(){if(!polling){polling=true;await Promise.allSettled([refreshStatus(),refreshBatches()]);polling=false;}pollTimer=setTimeout(poll,3000);}
  function workerChanged(){
    epoch++;sets=[];batches=[];status=null;connected=false;setsLoading=false;batchesLoading=false;writePending=false;initialLoading=true;lastBatchesSignature='';
    const hadText=dirty()||Boolean(current.id);if(hadText){delete current.id;delete current.revision;delete current.updated_at;baseline='';}else{current={name:'',prompts:[makePrompt(1)]};baseline=JSON.stringify(content());selected=new Set(current.prompts.map(p=>p.id));}
    discardAction=null;for(const id of ['discard-dialog','delete-dialog'])if($(id).open)$(id).close();signal('workspace-error');signal('workspace-notice',hadText?'Worker changed. Your prompt text is retained as an unsaved new set. Save it explicitly to copy it to this worker.':'');signal('set-error');signal('batch-error');signal('batches-error');renderEditor();renderLibrary();renderBatches();renderWorker();updateURL();refreshSets();refreshStatus();refreshBatches();
  }
  function showIntro(show){$('workspace-intro').hidden=!show;$('show-workspace-intro').hidden=show;if(show)$('workspace-intro').open=true;try{localStorage.setItem('hide_workspace_intro',show?'0':'1');}catch{}}
  try{showIntro(localStorage.getItem('hide_workspace_intro')!=='1');}catch{showIntro(true);}
  $('hide-workspace-intro').onclick=()=>{showIntro(false);$('show-workspace-intro').focus();};$('show-workspace-intro').onclick=()=>{showIntro(true);$('workspace-intro').querySelector('summary').focus();};
  document.addEventListener('keydown',event=>{if(!isSaveShortcut(event)||event.defaultPrevented||document.querySelector('dialog[open]'))return;event.preventDefault();saveSet();});
  $('set-form').onsubmit=event=>{event.preventDefault();saveSet();};
  $('set-name').oninput=()=>{current.name=$('set-name').value;updateActions();};
  $('new-set').onclick=()=>guardUnsaved(newSet);$('reload-sets').onclick=()=>refreshSets();$('set-search').oninput=renderLibrary;
  $('export-set').onclick=exportSet;$('import-set').onclick=()=>guardUnsaved(()=>$('import-file').click());$('import-file').onchange=()=>importSet($('import-file').files[0]);
  $('add-prompt').onclick=()=>{if(current.prompts.length>=50)return;const p=makePrompt(current.prompts.length+1);current.prompts.push(p);selected.add(p.id);expandedPrompt=p.id;renderEditor();$(`title-${p.id}`).focus();};
  $('select-all').onclick=()=>{selected=new Set(current.prompts.map(p=>p.id));for(const input of document.querySelectorAll('.prompt-selection input'))input.checked=true;updateActions();};
  $('select-none').onclick=()=>{selected.clear();for(const input of document.querySelectorAll('.prompt-selection input'))input.checked=false;updateActions();};
  $('delete-set').onclick=()=>{$('delete-message').textContent=`“${current.name}” will be removed from the connected worker.`;$('delete-dialog').showModal();};$('cancel-delete').onclick=()=>$('delete-dialog').close();$('confirm-delete').onclick=deleteSet;
  $('keep-editing').onclick=()=>{discardAction=null;$('discard-dialog').close();};$('discard-edits').onclick=()=>{const action=discardAction;discardAction=null;$('discard-dialog').close();action?.();};$('discard-dialog').addEventListener('cancel',()=>{discardAction=null;});
  for(const id of Object.values(scanFields))$(id).addEventListener('input',updateActions);
  $('start-batch').onclick=startBatch;$('reload-batches').onclick=refreshBatches;$('retry-worker').onclick=refreshStatus;
  document.addEventListener('click',event=>{const link=event.target.closest?.('a[href]');if(!link||event.defaultPrevented||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button!==0||link.target==='_blank'||link.hasAttribute('download')||link.getAttribute('href').startsWith('#'))return;if(dirty()){event.preventDefault();guardUnsaved(()=>{baseline=JSON.stringify(content());location.href=link.href;});}});
  window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});window.addEventListener('worker-connection-change',workerChanged);window.addEventListener('pagehide',()=>clearTimeout(pollTimer));
  current={name:'',prompts:[makePrompt(1)]};baseline=JSON.stringify(content());selected=new Set(current.prompts.map(p=>p.id));renderEditor();
  refreshSets({initial:true});poll();
}
