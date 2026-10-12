import {HostedClient,FIREBASE_VERSION,quoteWithinAllowance,readHostedArtifact,submitRunPodCredential} from './hosted-client.mjs';
import {createSavedImportController} from './hosted-import-ui.mjs';
import {QUICK_DEFAULTS,quickConfig,quickAllowance} from './quick-investigation-config.mjs';
const root=document.getElementById('hosted-root');
const $=id=>root.querySelector('#'+id);
const view=root.dataset.view;
let freshQuestion=view==='question'&&new URL(location.href).searchParams.has('new');
if(freshQuestion){const url=new URL(location.href);url.searchParams.delete('new');history.replaceState(null,'',url);}
import {createWorkerExecution,computeLabel} from './execution-adapter.mjs';
const direct=createWorkerExecution();let directRuntime=null,directJob=null,directTimer=null,directEpoch=0;
const directMode=()=>$('execution-kind').value==='worker';
const error=e=>{$('error').textContent=e?.message||String(e);};
const notice=text=>{$('notice').textContent=text;};
let client,auth,sdk,userUid=null,accountEpoch=0,preparedPlan=null,planExpiryTimer=null,approvalPending=false,models=[],runpodConnected=false,driveConnected=false,driveEnabled=true,quote=null,preparedConfig=null,session=null,job=null,pollTimer=null,approvedIntent=null,jobIntent=null,quoteIntent=null;
const intents=new Map(),downloaded=new Set(),downloading=new Set();
const savedImport=createSavedImportController({getClient:()=>client,onChange:state=>{
 for(const id of ['saved-import-file','saved-import-synthetic','saved-import-submit','saved-library-refresh'])$(id).disabled=state.pending||!client?.user;
 $('saved-import-status').textContent=state.pending?'Validating and importing this file…':state.result?(state.result.deduplicated?'This exact file is already saved in your private hosted library.':'Saved in your private hosted library. No model or GPU was started.'):'Choose a saved JSON file. Upload begins only when you click Import.';
 $('saved-import-error').textContent=state.error;$('saved-import-receipt').hidden=!state.result;
 if(!client?.user){$('saved-library-list').replaceChildren();$('saved-library-status').textContent='Sign in to list the private hosted library.';}
 $('saved-import-details').textContent=state.result?JSON.stringify({bundle_id:state.result.bundle_id,file_sha256:state.result.file_sha256,bytes:state.result.bytes,provenance:state.result.provenance,cleanup_applicable:false},null,2):'';
}});
$('saved-import-form').onsubmit=async event=>{event.preventDefault();if(await savedImport.submit($('saved-import-file').files?.[0],$('saved-import-synthetic').checked===true))await refreshSavedLibrary();};

let runpodConnectTicket=null,knownArtifacts=null,cancelPending=false,deletePending=false;
const terminalJobs=new Set(['completed','failed','cancelled','interrupted']);
const terminalSessions=new Set(['terminated','failed']);
const cleanupVerified=()=>session?.observed_state==='terminated'&&session.cleanup_verified===true;
const advanced=[['stride','Checkpoint stride',1,128],['samples','Samples per checkpoint',5,512],['cont_max','Continuation token cap',1,4096],['max_tokens','Original response token cap',8,4096],['temperature','Temperature',.05,2,.05],['top_k','Top-k branches',1,50],['threshold','Minimum branch probability',0,1,.01],['seed','Random seed',0,2147483647],['max_seconds','Investigation runtime bound · seconds',1,1e9],['max_samples','Total sample allowance',1,1e9],['max_generated_tokens','Generated token allowance',1,1e9],['max_rounds','Refinement rounds',0,8],['refine_stride','Refinement stride',1,128],['refine_samples','Refinement samples',5,512],['min_tvd','Refinement TVD threshold',0,1,.01]];
for(const [key,label,min,max,step=1]of advanced){const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement('input');input.type='number';input.id='advanced-'+key;input.min=min;input.max=max;input.step=step;input.value=QUICK_DEFAULTS[key];wrap.append(input);$('advanced').append(wrap);}
function values(){return Object.fromEntries(advanced.map(([key])=>[key,Number($('advanced-'+key).value)]));}
function currentConfig(runtimeSeconds){if(directMode())return quickConfig(directRuntime?.model,$('prompt').value,$('answers').value.split('\n'),values());const model=models.find(m=>m.id===$('model').value);return quickConfig({model_id:model?.id,resolved_revision:model?.revision,device:'cuda',batch_size:1,chat_template:true},$('prompt').value,$('answers').value.split('\n'),{...values(),max_seconds:Math.min(Number($('advanced-max_seconds').value),Math.floor(runtimeSeconds))});}
function invalidateQuote(){if(approvedIntent)return;quote=null;preparedConfig=null;preparedPlan=null;quoteIntent=null;clearTimeout(planExpiryTimer);$('quote-panel').hidden=true;$('quote-button').disabled=false;}
$('investigation-form').addEventListener('input',()=>{updateDriveControls();saveQuestion();invalidateQuote();$('device-ack-label').hidden=$('destination').value!=='device';const model=models.find(m=>m.id===$('model').value);$('model-pin').textContent=model?'Revision '+model.revision:'';try{const allowance=quickAllowance(currentConfig(Number($('minutes').value)*60-430));$('sampling-estimate').textContent=`At the original response cap: up to ${allowance.checkpoints} checkpoints, ${allowance.samples} continuation draws and ${allowance.tokens.toLocaleString()} generated tokens.${allowance.exceeds?' Your allowances can stop this scan early.':''}`;}catch{$('sampling-estimate').textContent='Enter a valid prompt and answers to estimate sampling work.';}});
async function mutation(name,method,path,body={}){let intent=intents.get(name);if(!intent){intent=client.intent(method,path,body);intents.set(name,intent);}const result=await client.submit(intent);intents.delete(name);return result;}
function stopPolling(){clearTimeout(pollTimer);pollTimer=null;}
function renderSession(){
 if(view==='library'){root.querySelector('#active-investigation')?.remove();if(session){const card=document.createElement('section');card.id='active-investigation';const title=document.createElement('h2'),state=document.createElement('p'),link=document.createElement('a');title.textContent='Latest managed investigation';state.textContent=(job?'Investigation: '+job.status+'. ':'')+'Compute: '+computeLabel(session,job);link.href='/live.html?session='+encodeURIComponent(session.id);link.textContent='Open progress →';card.append(title,state,link);root.append(card);}}
 window.dispatchEvent(new CustomEvent('fork-managed-status',{detail:{label:computeLabel(session,job)}}));
 $('delivery-status').textContent=$('session-results').querySelector('[data-explore]')?'Evidence verified in this browser. Download a portable copy below.':'Evidence will be verified and linked here when ready. Job completion alone does not confirm a saved copy.';
 $('session-panel').hidden=!session||view==='library'||(view==='question'&&cleanupVerified()&&!approvedIntent&&new URLSearchParams(location.search).get('session')!==session.id);if(!session)return;
 $('session-status').textContent=`Session: ${session.observed_state}. ${session.desired_state==='terminated'&&session.observed_state!=='terminated'?'Deletion requested; waiting for provider confirmation.':''}`;
 $('job-status').textContent=job?`Investigation: ${job.status}. ${job.cancel_requested&&!terminalJobs.has(job.status)?'Cancellation requested; the worker has not yet confirmed it stopped. ':''}${job.status==='interrupted'?'The worker lost its lease. It will not rerun this job automatically.':''}`:'Waiting to queue the investigation.';
 $('deadline').textContent=`Compute deadline: ${new Date(session.expires_at*1000).toLocaleString()}. ${session.observed_state==='terminated'&&session.cleanup_verified===true?'Provider deletion confirmed.':''}`;
 const progress=job?.progress;const phase={loading:'Loading the model',generating:'Generating the original response',scanning:'Sampling continuations',refining:'Refining selected intervals',inspecting:'Inspecting activations',saving:'Saving evidence'};
 $('progress').textContent=terminalJobs.has(job?.status)?'Execution has ended. Open saved evidence below for results or the failure details.':progress?`${phase[progress.phase]||progress.phase||'Phase unavailable'}${Number.isInteger(progress.completed)&&Number.isInteger(progress.total)&&progress.completed>=0&&progress.total>0&&progress.completed<=progress.total?` · ${progress.completed} of ${progress.total}${progress.progress_unit?' '+progress.progress_unit:''}`:' · Progress counts unavailable'}.`:'Waiting for worker enrollment or its first progress report. Queued work has not yet demonstrated model execution.';
 if(session.observed_state==='terminated'&&session.cleanup_verified===true)$('progress').textContent='Historical session started '+(Number.isFinite(session.created_at)?new Date(session.created_at*1000).toLocaleString():'at an unavailable time')+'. Its cleanup was verified. This record does not indicate a new investigation or current model readiness.';
 else if(session.worker_last_heartbeat_at&&Date.now()/1000-session.worker_last_heartbeat_at>90&&!terminalJobs.has(job?.status))$('progress').textContent='Worker status is stale. Last reported progress may have stopped. Refresh until recovery or verified cleanup is reported.';
 if(!cleanupVerified()&&(session.manual_attention_required||session.cleanup_status==='credential_blocked'))$('progress').textContent='Compute cleanup needs attention. Check the specific app-owned session in your RunPod account. Charges may continue until deletion is verified.';
 if(job?.error_code==='insufficient_execution_time')$('job-status').textContent+=' Startup used the reviewed execution allowance. The investigation was cancelled without shortening its configuration. Wait for verified cleanup, then prepare and review a fresh plan.';
 $('cancel').textContent=job?.cancel_requested?'Cancellation requested':'Cancel investigation';$('cancel').disabled=cancelPending||!job||terminalJobs.has(job.status)||job.cancel_requested;const unverified=!(session.observed_state==='terminated'&&session.cleanup_verified===true),needsLoss=unverified&&(job&&!terminalJobs.has(job.status)||knownArtifacts===null||knownArtifacts.some(item=>item.session_id===session.id&&item.storage_mode==='device'&&!item.received_at&&!downloaded.has(item.id)));$('delete-loss-label').hidden=!needsLoss;$('terminate').textContent=session.desired_state==='terminated'?'Retry deletion request':'Delete compute';$('terminate').disabled=deletePending||!unverified||Boolean(needsLoss&&!$('delete-loss-ack').checked);
 $('quote-button').disabled=!(session.observed_state==='terminated'&&session.cleanup_verified===true);$('approve').disabled=true;
}
async function refreshWorkspace(){
 const epoch=accountEpoch;const me=await client.request('/me');if(epoch!==accountEpoch)return;
 const [catalog,drive,sessions,jobs]=await Promise.all([client.request('/models'),client.request('/connections/drive'),client.request('/sessions'),client.request('/jobs')]);if(epoch!==accountEpoch)return;
 runpodConnected=me.runpod_connected===true;updateConnectionStep();
 const selected=$('model').value;models=catalog.models||[];$('model').replaceChildren();for(const model of models){const option=document.createElement('option');option.value=model.id;option.textContent=model.id;$('model').append(option);}if(models.some(m=>m.id===selected))$('model').value=selected;$('model-pin').textContent=models[0]?'Revision '+models[0].revision:'';
 $('runpod-status').textContent=me.runpod_connected===true?'RunPod connected':me.runpod_connected===false?'RunPod is disconnected':'RunPod connection checked when quoting';driveEnabled=drive.enabled!==false;driveConnected=driveEnabled&&drive.connected===true;updateDriveControls();
 const requestedSession=new URLSearchParams(location.search).get('session');
 session=(sessions.sessions||[]).find(s=>s.id===requestedSession)||(sessions.sessions||[]).find(s=>!(s.observed_state==='terminated'&&s.cleanup_verified===true))||(sessions.sessions||[]).at(-1)||null;job=session?(jobs.jobs||[]).filter(j=>j.session_id===session.id).at(-1)||null:null;
 restoreQuestion();renderSession();executionChanged();await refreshLibrary();await refreshSavedLibrary();if(session&&!cleanupVerified())schedulePoll();
}
function schedulePoll(){stopPolling();pollTimer=setTimeout(()=>poll().catch(error),client?.config?.pilot===true?30000:3000);}
async function poll(){
 if(!session||!client.user)return;const epoch=accountEpoch,sid=session.id;
 try{const [updated,jobs]=await Promise.all([client.request('/sessions/'+encodeURIComponent(sid)),client.request('/jobs')]);if(epoch!==accountEpoch)return;session=updated;$('error').textContent='';job=(jobs.jobs||[]).filter(j=>j.session_id===sid).at(-1)||null;renderSession();
 if(terminalJobs.has(job?.status)||job?.status==='saving')await refreshLibrary({autoDevice:true});
 if(!cleanupVerified())schedulePoll();else {for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;await refreshLibrary();approvedIntent=null;jobIntent=null;quoteIntent=null;$('discard').disabled=false;invalidateQuote();}
 }catch(e){if(epoch!==accountEpoch)return;notice('Progress refresh failed. The independent controller still enforces the session deadline. Use Refresh progress to reconnect.');throw e;}
}
function explorerURL(result,bundle){return '/observatory.html?evidence=local&run='+encodeURIComponent(result.id||'')+(bundle.payload?.investigation?.id?'&investigation='+encodeURIComponent(bundle.payload.investigation.id):'');}
function saveFile(bytes,id){const url=URL.createObjectURL(new Blob([bytes],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='fork-investigation-'+id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function importArtifact(artifact,li,{download=false}={}){
 if(downloading.has(artifact.id))return;downloading.add(artifact.id);const epoch=accountEpoch,owner=userUid;
 try{const offline=await import('./offline-evidence.mjs');
 const cacheKey='fork-hosted-delivery-index:'+owner;let index={};try{index=JSON.parse(localStorage.getItem(cacheKey)||'{}');}catch{}
 const cached=await offline.getHostedEvidence?.(owner,index[artifact.id]);if(epoch!==accountEpoch)return;
 const {bundle,bytes}=cached&&!download?{bundle:cached,bytes:null}:await readHostedArtifact(client,artifact);if(epoch!==accountEpoch)return;
 if(typeof offline.installHostedEvidence!=='function')throw Error('This site does not yet support an isolated hosted evidence library. Download a portable copy.');
 const result=await offline.installHostedEvidence(bundle,{ownerUid:owner});if(epoch!==accountEpoch){await offline.clearHostedEvidence?.(owner);return;}
 index[artifact.id]=bundle.sha256;localStorage.setItem(cacheKey,JSON.stringify(index));
 if(download)saveFile(bytes,artifact.id);
 if(artifact.storage_mode==='device'&&(!cached||download))await mutation('received-'+artifact.id,'POST','/artifacts/'+encodeURIComponent(artifact.id)+'/received');
 downloaded.add(artifact.id);$('delivery-status').textContent='Evidence verified and available in this account’s browser library. Download a portable copy before closing this browser.';
 const link=document.createElement('a');link.href=explorerURL(result,bundle);link.textContent='Explore saved evidence →';li.querySelector('[data-explore]')?.remove();link.dataset.explore='';li.append(link);
 if(session&&artifact.session_id===session.id){const results=$('session-results');results.replaceChildren(link.cloneNode(true));const downloadButton=document.createElement('button');downloadButton.textContent='Download portable results';downloadButton.onclick=()=>importArtifact(artifact,li,{download:true}).catch(error);results.append(downloadButton);}
 const investigation=bundle.payload?.investigation;const detail=investigation?.status==='error'&&typeof investigation.message==='string'?` Run stopped: ${investigation.message.slice(0,1000)}`:'';notice((cached&&!download?'Saved browser evidence validated. An original-file download requires source retrieval.':'Original evidence bytes verified and saved in this account’s browser library. Keep a portable copy.')+detail);
 }catch(e){if(e?.status===410)throw Error('This worker artifact expired. A saved browser copy can still be explored. To add it to ChatGPT, import a preserved original investigation JSON file.');throw e;}finally{downloading.delete(artifact.id);}
}
async function refreshLibrary({autoDevice=false}={}){
 const epoch=accountEpoch;const result=await client.request('/artifacts');if(epoch!==accountEpoch)return;
 $('library').replaceChildren();const artifacts=result.artifacts||[];knownArtifacts=artifacts;renderSession();if(!artifacts.length){const li=document.createElement('li');li.textContent='No completed evidence yet.';$('library').append(li);return;}
 for(const artifact of artifacts){const li=document.createElement('li');const label=document.createElement('span');label.textContent=`Investigation ${String(artifact.job_id||artifact.id).slice(0,10)} · ${artifact.storage_mode==='drive'?'Google Drive · download required':'Device delivery · retrieve to verify availability'}`;li.append(label);
 for(const [text,download]of [['Open in Explorer',false],['Download bundle',true]]){const button=document.createElement('button');button.textContent=text;button.onclick=async()=>{button.disabled=true;try{await importArtifact(artifact,li,{download});}catch(e){error(e);}finally{button.disabled=false;}};li.append(button);}$('library').append(li);
 if(artifact.session_id===session?.id&&!$('session-results').children.length){const retrieve=document.createElement('button');retrieve.textContent='Retrieve saved results';retrieve.onclick=()=>importArtifact(artifact,li).catch(error);$('session-results').append(retrieve);}
 if(autoDevice&&artifact.storage_mode==='device'&&!downloaded.has(artifact.id)&&artifact.session_id===session?.id)try{await importArtifact(artifact,li);}catch(e){error(e);}
 }
}
async function refreshSavedLibrary(){
 const epoch=accountEpoch;$('saved-library-refresh').disabled=true;
 try{const data=await client.request('/evidence?limit=10');if(epoch!==accountEpoch)return;
 if(!Array.isArray(data.investigations)||!Number.isSafeInteger(data.total_candidates)||data.total_candidates<0)throw Error('The service returned an unreadable saved-library catalog.');
 $('saved-library-list').replaceChildren();$('saved-library-status').textContent=`${data.total_candidates} saved investigation${data.total_candidates===1?'':'s'} in this private hosted library. Showing up to 10. Ask ChatGPT to list or show one; no compute is needed.`;
 for(const item of data.investigations){const li=document.createElement('li'),label=document.createElement('p'),hash=document.createElement('p');label.textContent=(item.provenance?.synthetic===true?'Synthetic fixture · ':item.provenance?.source==='user_file'?'Imported saved evidence · ':'Saved investigation · ')+String(item.bundle_id||'Unavailable bundle ID');hash.className='code';hash.textContent=`SHA-256: ${item.file_sha256||'Unavailable'} · ${Number.isSafeInteger(item.bytes)?item.bytes.toLocaleString():'Unavailable'} bytes`;li.append(label,hash);$('saved-library-list').append(li);}
 }catch(e){if(epoch===accountEpoch)$('saved-library-status').textContent=(e?.status===404?'This deployment does not yet expose the hosted saved-file catalog.':'Saved-library refresh failed. Retry; this does not mean a previously acknowledged import was lost.');}
 finally{if(epoch===accountEpoch)$('saved-library-refresh').disabled=!client?.user;}
}
$('saved-library-refresh').onclick=()=>refreshSavedLibrary();
function validateWorkerBinding(plan){
 if(!/^[^\s@]+@sha256:[0-9a-f]{64}$/.test(plan?.quote?.worker_image||''))throw Error('This plan does not bind an immutable worker image digest. Request and review a fresh plan.');
 const revision=plan?.config?.model?.revision,quoted=plan?.quote?.model_revision??plan?.quote?.revision;
 if(typeof revision!=='string'||!revision||(quoted!=null&&quoted!==revision))throw Error('The model revision is missing or differs between configuration and quote. Request and review a fresh plan.');
}
function reviewPreparedPlan(plan){
 if(!plan||plan.status!=='prepared'||typeof plan.id!=='string'||!/^[a-f0-9]{64}$/.test(plan.approval_digest||'')||!/^[a-f0-9]{64}$/.test(plan.config_hash||'')||!plan.config||!plan.quote||!Number.isFinite(plan.expires_at)||!Number.isFinite(plan.quote.expires_at))throw Error('The service did not return a complete exact plan. Request a fresh plan.');
 validateWorkerBinding(plan);quoteWithinAllowance({...plan.quote,expires_at:Math.min(plan.expires_at,plan.quote.expires_at)},plan.usd_allowance);
 preparedPlan=plan;preparedConfig=plan.config;quote=plan.quote;
 $('quote-details').textContent=`${quote.model_id}\nRevision ${quote.revision||quote.model_revision}\nWorker image: ${/^[^\s@]+@sha256:[0-9a-f]{64}$/.test(quote.worker_image||'')?quote.worker_image:'not bound to an immutable digest; the controller will not create compute for this plan. Request a fresh plan.'}\n${quote.gpu_id} · ${quote.gpu_count||1} GPU · ${quote.cloud}\nGPU: $${Number(quote.gpu_usd_per_hour).toFixed(4)}/hour\nDisk: ${quote.disk_gb} GB × $${Number(quote.disk_usd_per_gb_hour).toFixed(6)}/GB/hour\nSession: ${quote.max_duration_seconds/60} minutes, including ${quote.startup_allowance_seconds/60} minutes startup and ${quote.export_reserve_seconds/60} minutes export reserve\nEstimated total: $${Number(quote.estimated_usd).toFixed(3)} · estimate allowance $${Number(plan.usd_allowance).toFixed(2)}\nSave to: ${plan.storage_mode==='drive'?'Google Drive':'this device'}\nAvailability: ${quote.availability} (not reserved)`;
 $('quote-config').textContent=JSON.stringify({config:plan.config,quote:plan.quote,storage_mode:plan.storage_mode,acknowledge_device_loss:plan.acknowledge_device_loss,usd_allowance:plan.usd_allowance,max_duration_seconds:plan.max_duration_seconds,approval_digest:plan.approval_digest,config_hash:plan.config_hash},null,2);
 $('quote-remaining').textContent='Plan and quote expire '+new Date(Math.min(plan.expires_at,quote.expires_at)*1000).toLocaleTimeString()+'. '+(quote.cost_notice||'Startup and cleanup delays can exceed this estimate. It is not a hard dollar cap.');
 $('quote-panel').hidden=false;$('approve').textContent='Approve this exact plan and start';$('approve').disabled=false;
 clearTimeout(planExpiryTimer);const epoch=accountEpoch;
 planExpiryTimer=setTimeout(()=>{if(epoch!==accountEpoch||preparedPlan!==plan||approvedIntent)return;$('approve').disabled=true;$('quote-remaining').textContent='This plan expired. Change settings or request and review a fresh plan before approval.';},Math.max(1,Math.min(2147483647,(Math.min(plan.expires_at,quote.expires_at)-Date.now()/1000)*1000+10)));
}
$('investigation-form').onsubmit=async event=>{event.preventDefault();updateDriveControls();if(directMode()){await startDirect();return;}if(!client?.user){error(Error('Continue with Google above, then connect RunPod. Your question is preserved.'));$('sign-in').focus();return;}if(!runpodConnected){error(Error('Connect your RunPod account above before reviewing a run. No VM setup is needed.'));$('connection-details')&&($('connection-details').open=true);$('runpod-key').focus();return;}if(approvalPending||approvedIntent){error(Error('Check progress or retry the same approved plan before changing the request.'));return;}$('error').textContent='';const epoch=accountEpoch;$('quote-button').disabled=true;for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=true;
 try{if(session&&!(session.observed_state==='terminated'&&session.cleanup_verified===true))throw Error('Delete your active session and verify cleanup before requesting another.');
 if($('destination').value==='device'&&!$('device-ack').checked)throw Error('Acknowledge that this browser must remain open to save device evidence.');
 if($('destination').value==='drive'&&(!driveEnabled||!driveConnected))throw Error('Connect Google Drive before preparing a Drive plan, or choose this device.');
 const seconds=Number($('minutes').value)*60;
 preparedConfig=currentConfig(seconds-430);
 if(!quoteIntent)quoteIntent=client.intent('POST','/plans',{config:preparedConfig,usd_allowance:Number($('usd').value),max_duration_seconds:seconds,storage_mode:$('destination').value,acknowledge_device_loss:$('destination').value==='device'&&$('device-ack').checked});
 const plan=await client.submit(quoteIntent);if(epoch!==accountEpoch)return;quoteIntent=null;reviewPreparedPlan(plan);notice('Review the exact prompt, outcomes, sampling limits, destination and provider estimate. No compute has been provisioned by preparing this plan.');
 }catch(e){if(epoch===accountEpoch){error(e?.message?.includes('startup or export reserve')?Error(e.message+' Reduce the investigation runtime bound in Advanced settings, or ask the operator for the configured reserves.'):e);if(Number.isInteger(e?.status)&&e.status>=400&&e.status<500&&![408,425,429].includes(e.status))quoteIntent=null;}}finally{if(epoch===accountEpoch){$('quote-button').disabled=false;for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;executionChanged();}}
};
$('discard').onclick=()=>{if(approvedIntent||approvalPending){error(Error('The approved request may have completed. Refresh progress or retry this exact request first.'));return;}invalidateQuote();};
$('approve').onclick=async()=>{if(approvalPending)return;const epoch=accountEpoch;approvalPending=true;$('approve').disabled=true;$('discard').disabled=true;$('error').textContent='';for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=true;
 try{if(!approvedIntent&&$('destination').value==='drive'&&!driveEnabled)throw Error('Google Drive is unavailable on this deployment. Choose this device and review a new plan.');
 if(!approvedIntent&&$('destination').value==='device'&&!$('device-ack').checked)throw Error('Acknowledge device evidence delivery before approving compute.');
 if(!preparedPlan||!quote||!preparedConfig)throw Error('Request and review an exact investigation plan first.');
 if(!approvedIntent){validateWorkerBinding(preparedPlan);quoteWithinAllowance({...quote,expires_at:Math.min(preparedPlan.expires_at,quote.expires_at)},preparedPlan.usd_allowance);approvedIntent=client.intent('POST','/plans/'+encodeURIComponent(preparedPlan.id)+'/approve',{approval_digest:preparedPlan.approval_digest,confirm_execution:true});}
 const result=await client.submit(approvedIntent);if(epoch!==accountEpoch)return;
 if(!result.session?.id||!result.job?.id||result.job.session_id!==result.session.id)throw Error('Approval outcome is uncertain. Refresh progress or retry the same exact request.');
 session=result.session;job=result.job;preparedPlan=result.plan||preparedPlan;
 const progressURL=new URL(location.href);progressURL.searchParams.set('session',session.id);history.replaceState(null,'',progressURL);$('session-results').replaceChildren();renderSession();schedulePoll();$('quote-panel').hidden=true;notice('The exact plan queued one investigation and one compute session. Worker enrollment and model execution remain to be observed. Save device evidence before the deadline.');
 }catch(e){if(epoch!==accountEpoch)return;error(e);if(Number.isInteger(e?.status)&&e.status>=400&&e.status<500&&![408,425,429].includes(e.status)){approvedIntent=null;invalidateQuote();} $('approve').textContent=approvedIntent?'Retry the same approved plan':'Approve this exact plan and start';$('approve').disabled=false;if(session){renderSession();schedulePoll();}$('approve').disabled=false;
 if(approvedIntent)notice('The approval outcome may be uncertain. Retry this same request or refresh progress; do not submit a different plan until its outcome is known.');
 else for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;
 }finally{if(epoch===accountEpoch){approvalPending=false;$('discard').disabled=Boolean(approvedIntent);}}
};
$('runpod-form').onsubmit=async event=>{
 event.preventDefault();if(runpodConnectTicket)return;
 const key=$('runpod-key').value;$('runpod-key').value='';
 const ticket={epoch:accountEpoch};runpodConnectTicket=ticket;updateConnectionStep();
 try{const result=await submitRunPodCredential(client,intents,key);if(ticket.epoch!==accountEpoch)return;
  $('runpod-status').textContent=result.connected?'RunPod connected':'RunPod disconnected';runpodConnected=Boolean(result.connected);notice('RunPod connected. Choose your model and prompt below, then review the price. No GPU has been rented yet.');
 }catch(e){if(ticket.epoch===accountEpoch)error(e);}
 finally{if(runpodConnectTicket===ticket){runpodConnectTicket=null;updateConnectionStep();}}
};
$('runpod-disconnect').onclick=async()=>{try{await mutation('runpod-disconnect','DELETE','/connections/runpod');$('runpod-status').textContent='RunPod disconnected.';runpodConnected=false;updateConnectionStep();}catch(e){error(e);}};
$('drive-connect').onclick=async()=>{if(!driveEnabled){updateDriveControls();return;}try{const result=await mutation('drive-authorize','POST','/connections/drive/begin');const url=new URL(result.authorization_url||result.url);if(url.protocol!=='https:'||url.hostname!=='accounts.google.com'||url.username||url.password)throw Error('Unsafe Google authorization address.');location.assign(url.href);}catch(e){error(e);}};
$('drive-disconnect').onclick=async()=>{if(!driveEnabled){updateDriveControls();return;}try{await mutation('drive-disconnect','POST','/connections/drive/disconnect');driveConnected=false;updateDriveControls();}catch(e){error(e);}};
$('refresh').onclick=()=>poll().catch(error);$('library-refresh').onclick=()=>refreshLibrary().catch(error);
$('cancel').onclick=async()=>{if(cancelPending||!job||terminalJobs.has(job.status)||job.cancel_requested)return;const epoch=accountEpoch;cancelPending=true;$('cancel').disabled=true;try{const updated=await mutation('cancel-'+job.id,'POST','/jobs/'+encodeURIComponent(job.id)+'/cancel');if(epoch!==accountEpoch)return;job=updated;renderSession();schedulePoll();notice('Cancellation requested. In-flight work may finish; save available evidence and verify compute cleanup separately.');}catch(e){if(epoch===accountEpoch)error(e);}finally{if(epoch===accountEpoch){cancelPending=false;renderSession();}}};
$('delete-loss-ack').addEventListener('change',renderSession);
$('terminate').onclick=async()=>{if(deletePending||!session||cleanupVerified())return;const epoch=accountEpoch;deletePending=true;$('terminate').disabled=true;try{if(!$('delete-loss-label').hidden&&!$('delete-loss-ack').checked)throw Error('Save available evidence first, or acknowledge that deletion can lose unfinished or unsaved work.');const updated=await mutation('terminate-'+session.id,'POST','/sessions/'+encodeURIComponent(session.id)+'/terminate',{confirm_delete:true,acknowledge_unsaved_loss:$('delete-loss-ack').checked===true});if(epoch!==accountEpoch)return;session=updated;renderSession();schedulePoll();notice('Deletion requested. Compute can still incur charges until provider absence is verified.');}catch(e){if(epoch===accountEpoch)error(e);}finally{if(epoch===accountEpoch){deletePending=false;renderSession();}}};
async function changedUser(user){
 runpodConnected=false;driveConnected=false;$('device-ack').checked=false;updateConnectionStep();$('session-results').replaceChildren();knownArtifacts=null;cancelPending=deletePending=false;const previous=userUid;const cachedOwner=localStorage.getItem('fork-hosted-evidence-owner');accountEpoch++;const epoch=accountEpoch;stopPolling();client.setUser(user);userUid=user?.uid||null;intents.clear();runpodConnectTicket=null;savedImport.reset();$('saved-import-file').value='';$('saved-import-synthetic').checked=false;downloaded.clear();downloading.clear();quote=preparedConfig=session=job=approvedIntent=jobIntent=quoteIntent=null;
 $('library').replaceChildren();root.querySelector('#active-investigation')?.remove();$('workspace').hidden=true;$('quote-panel').hidden=true;$('session-panel').hidden=true;$('runpod-key').value='';$('delete-loss-ack').checked=false;clearTimeout(planExpiryTimer);preparedPlan=null;approvalPending=false;for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;$('error').textContent='';$('approve').textContent='Approve this exact plan and start';$('discard').disabled=false;
 if(!previous&&cachedOwner&&cachedOwner!==userUid){const offline=await import('./offline-evidence.mjs');await offline.clearHostedEvidence(cachedOwner);sessionStorage.removeItem('fork-question-v2:'+cachedOwner);$('prompt').value='';$('answers').value='';}
 if(userUid&&!previous&&!cachedOwner&&!sessionStorage.getItem(draftKey())){const pending=sessionStorage.getItem('fork-question-v2:local');if(pending){sessionStorage.setItem(draftKey(),pending);sessionStorage.removeItem('fork-question-v2:local');}}
 if(previous&&previous!==userUid){sessionStorage.removeItem('fork-question-v2:'+previous);$('prompt').value='';$('answers').value='';const offline=await import('./offline-evidence.mjs');await offline.clearHostedEvidence?.(previous);}
 if(!user){const cachedOwner=localStorage.getItem('fork-hosted-evidence-owner');if(cachedOwner){const offline=await import('./offline-evidence.mjs');await offline.clearHostedEvidence(cachedOwner);}}
 if(epoch!==accountEpoch)return;updateConnectionStep();restoreQuestion();$('sign-in').hidden=Boolean(user);$('sign-out').hidden=!user;$('account-status').textContent=user?`Signed in as ${user.email||'your Google account'}. Checking workspace access…`:'Hosted access is invite only.';
 if(!user){$('workspace').hidden=view!=='question';notice(view==='question'?'Sign in above and connect RunPod once. Then choose your model and question here; the app sets up the GPU for you.':'Sign in to see your account connections and artifacts. Browser evidence requires no account.');executionChanged();return;}
 try{await refreshWorkspace();if(epoch!==accountEpoch)return;$('workspace').hidden=false;executionChanged();savedImport.reset();$('account-status').textContent=`Signed in as ${user.email||'your Google account'}.`;notice('Your personal hosted workspace is ready.');if(view==='compute'&&location.hash==='#saved-import')$('saved-import').scrollIntoView?.({block:'start'});}catch(e){if(epoch===accountEpoch){error(e);notice('Hosted access could not be verified. Sign out to use another invited account.');}}
}
$('sign-in').onclick=async()=>{try{const provider=new sdk.GoogleAuthProvider();if(matchMedia('(max-width: 600px)').matches)await sdk.signInWithRedirect(auth,provider);else await sdk.signInWithPopup(auth,provider);}catch(e){error(e);}};
$('sign-out').onclick=async()=>{try{await sdk.signOut(auth);}catch(e){error(e);}};

function draftKey(){return 'fork-question-v2:'+(userUid||'local');}
function saveQuestion(){if(view!=='question')return;freshQuestion=false;try{sessionStorage.setItem(draftKey(),JSON.stringify({prompt:$('prompt').value,answers:$('answers').value,kind:$('execution-kind').value,model:$('model').value,settings:values(),resources:{minutes:$('minutes').value,usd:$('usd').value,destination:$('destination').value},workerModel:$('worker-model-id').value,workerRevision:$('worker-revision').value}));}catch{}}
function restoreQuestion(){if(view!=='question')return;if(freshQuestion){sessionStorage.removeItem(draftKey());updateDriveControls();return;}try{const d=JSON.parse(sessionStorage.getItem(draftKey())||'null');if(!d)return;$('prompt').value=d.prompt||'';$('answers').value=d.answers||'';$('execution-kind').value=d.kind||'hosted';for(const [k,v]of Object.entries(d.settings||{})){const field=$('advanced-'+k);if(field)field.value=v;}if(models.some(m=>m.id===d.model))$('model').value=d.model;for(const [k,v]of Object.entries(d.resources||{}))if(['minutes','usd','destination'].includes(k))$(k).value=v;$('worker-model-id').value=d.workerModel||'';$('worker-revision').value=d.workerRevision||'main';}catch{}finally{if(updateDriveControls())saveQuestion();}}
function executionChanged(){
 $('device-ack-label').hidden=$('destination').value!=='device';
 const worker=directMode();$('hosted-model-fields').hidden=worker;$('worker-model-fields').hidden=!worker;$('hosted-resource-fields').hidden=worker;$('hosted-cost-note').hidden=worker;
 $('advanced-max_seconds').closest('label').hidden=false;
 for(const input of $('hosted-resource-fields').querySelectorAll('input,select'))input.disabled=worker;$('model').disabled=worker;
 $('quote-button').textContent=worker?'Run on my machine':'Review price & run';
 $('execution-help').textContent=worker?'Runs on your selected machine. Runtime limits do not stop VM billing.':'The app rents the GPU, loads this model, runs the scan, and manages shutdown. No terminal required. You approve the price before compute starts.';
 $('worker-model-status').textContent=directRuntime?.model?`Selected model: ${directRuntime.model.model_id} · revision ${directRuntime.model.resolved_revision}`:'No model loaded on your selected machine. Open Compute to pair a machine, then check and load your model below.';
 if(view==='question')$('workspace').hidden=false;
 updateDriveControls();
}
$('execution-kind').addEventListener('change',()=>{invalidateQuote();saveQuestion();executionChanged();refreshDirect();});
async function refreshDirect(){if(view!=='question')return;const epoch=directEpoch;try{directRuntime=await direct.status();if(epoch!==directEpoch)return;executionChanged();if(directRuntime.job?.status==='running'&&!directJob)directTimer=setTimeout(refreshDirect,2500);const id=directJob?.id||new URL(location.href).searchParams.get('job');if(id){directJob=await direct.read(id);if(epoch!==directEpoch)return;renderDirect();if(['running','queued','starting'].includes(directJob.status))directTimer=setTimeout(refreshDirect,2500);}}catch{if(epoch===directEpoch){directRuntime=null;executionChanged();}}}
function renderDirect(){
 let panel=root.querySelector('#direct-progress');if(!panel){panel=document.createElement('section');panel.id='direct-progress';root.append(panel);}panel.replaceChildren();
 const h=document.createElement('h2');h.textContent='Run on your machine';const state=document.createElement('p');state.textContent=directJob.status+': '+(directJob.worker_job?.phase||directJob.message||'')+' · '+(directJob.runs?.length||0)+' saved scans';panel.append(h,state);const submitted=document.createElement('details'),title=document.createElement('summary'),text=document.createElement('pre');title.textContent='Submitted question and model';text.textContent=(directJob.config?.model?.model_id||'')+'\n\n'+(directJob.config?.base?.prompt||'Configuration unavailable');submitted.append(title,text);panel.append(submitted);
 const busy=['running','queued','starting'].includes(directJob.status);$('quote-button').disabled=busy;
 if(busy){const cancel=document.createElement('button');cancel.textContent='Stop investigation';cancel.onclick=async()=>{try{await direct.cancel(directJob.id);await refreshDirect();}catch(e){error(e);}};panel.append(cancel);}
 if(directJob.runs?.length){const a=document.createElement('a');a.textContent='Explore results →';a.href='/observatory.html?evidence=worker&investigation='+encodeURIComponent(directJob.id)+'&run='+encodeURIComponent(directJob.runs.at(-1));panel.append(a);}
 if(directJob.runs?.length||directJob.responses?.length){const b=document.createElement('button');b.textContent='Save portable investigation';b.onclick=async()=>{try{saveFile(new TextEncoder().encode(JSON.stringify(await direct.export(directJob.id))),directJob.id);}catch(e){error(e);}};panel.append(b);}
 const note=document.createElement('p');note.textContent='Evidence remains on this machine until exported. Stopping an investigation does not delete rented compute.';panel.append(note);
}
async function startDirect(){const epoch=directEpoch;$('quote-button').disabled=true;try{directRuntime=await direct.status();if(epoch!==directEpoch)return;if(directRuntime.job?.status==='running'&&!direct.hasPending())throw Error('Your machine is busy. Wait or stop its current operation.');directJob=await direct.start(currentConfig());if(epoch!==directEpoch)return;const u=new URL(location.href);u.searchParams.delete('new');u.searchParams.set('job',directJob.id);history.replaceState(null,'',u);renderDirect();await refreshDirect();}catch(e){error(e);}finally{if(epoch===directEpoch)$('quote-button').disabled=directJob?.status==='running';}}
let inspectedModel=null;
const modelSettings=()=>({model_id:$('worker-model-id').value.trim(),revision:$('worker-revision').value.trim(),device:'auto',batch_size:1});
for(const id of ['worker-model-id','worker-revision'])$(id).addEventListener('input',()=>{inspectedModel=null;$('load-worker-model').disabled=true;});
$('inspect-worker-model').onclick=async()=>{try{inspectedModel=null;$('load-worker-model').disabled=true;const result=await direct.inspect(modelSettings());$('worker-model-check').textContent=[...(result.blockers||[]),...(result.warnings||[]),result.can_load?'Compatible metadata. Loading will download weights if needed.':'Model cannot load with these settings.'].join(' ');if(result.can_load){if(result.resolved_revision)$('worker-revision').value=result.resolved_revision;inspectedModel=modelSettings();$('load-worker-model').disabled=false;}}catch(e){error(e);}};
$('load-worker-model').onclick=async()=>{try{if(!inspectedModel||JSON.stringify(inspectedModel)!==JSON.stringify(modelSettings()))throw Error('Check model compatibility first.');$('load-worker-model').disabled=true;await direct.load(inspectedModel);await refreshDirect();}catch(e){error(e);}};
window.addEventListener('worker-connection-change',()=>{directEpoch++;direct.reset();clearTimeout(directTimer);directRuntime=directJob=null;root.querySelector('#direct-progress')?.remove();executionChanged();refreshDirect();});
window.addEventListener('pagehide',()=>{intents.clear();$('runpod-key').value='';savedImport.reset();$('saved-import-file').value='';stopPolling();clearTimeout(directTimer);});
async function initialize(){try{const response=await fetch('/hosted-config.json',{cache:'no-store',credentials:'omit'});if(!response.ok)throw Error('Hosted configuration is unavailable.');client=new HostedClient(await response.json());
 if(!client.config.enabled){root.querySelector('[data-view=account]').hidden=true;notice('Hosted compute is not enabled on this site. You can explore saved evidence or use local compute.');return;}
 const [appSdk,authSdk]=await Promise.all([import(`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-app.js`),import(`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-auth.js`)]);sdk=authSdk;auth=sdk.getAuth(appSdk.initializeApp(client.config.firebase));
 await sdk.setPersistence(auth,sdk.browserLocalPersistence);$('sign-in').disabled=false;sdk.onAuthStateChanged(auth,user=>changedUser(user).catch(error));await sdk.getRedirectResult(auth);
 }catch(e){error(e);notice('Hosted compute could not be initialized. Local compute and saved evidence remain available.');}}
function updateDriveControls(){
 const unavailable=!driveEnabled,signedIn=Boolean(client?.user);
 $('drive-connect').hidden=unavailable;$('drive-disconnect').hidden=unavailable;
 $('drive-connect').disabled=unavailable||!signedIn||driveConnected;
 $('drive-disconnect').disabled=unavailable||!signedIn||!driveConnected;
 $('drive-status').textContent=unavailable?'Google Drive is unavailable on this deployment. Save evidence to this device.':driveConnected?'Google Drive connected':'Google Drive is disconnected';
 const option=$('destination').querySelector('option[value="drive"]');if(option){option.hidden=unavailable;option.disabled=unavailable;}
 const coerced=unavailable&&$('destination').value==='drive';
 if(coerced){$('destination').value='device';$('device-ack').checked=false;invalidateQuote();}
 $('device-ack-label').hidden=$('destination').value!=='device';
 const note=$('drive-note');if(note)note.textContent=unavailable?'Keep this browser open and save device evidence before the session deadline.':'Drive access covers files created by this app. Choose a destination before approving compute.';
 return coerced;
}
function updateConnectionStep(){
 updateDriveControls();
 for(const id of ['runpod-key','runpod-disconnect'])$(id).disabled=!client?.user||Boolean(runpodConnectTicket);
 $('runpod-form').querySelector('button').disabled=!client?.user||Boolean(runpodConnectTicket);
 const details=$('connection-details');if(!details)return;
 details.open=!runpodConnected;
 details.querySelector('summary').textContent=runpodConnected?'1. RunPod connected · change connection':'1. Connect RunPod once';
}
updateConnectionStep();
await initialize();
if(view==='question'){restoreQuestion();executionChanged();refreshDirect();}
