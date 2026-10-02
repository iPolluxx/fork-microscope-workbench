import {HostedClient,FIREBASE_VERSION,quoteWithinAllowance,readHostedArtifact} from './hosted-client.mjs';
import {QUICK_DEFAULTS,quickConfig,quickAllowance} from './quick-investigation-config.mjs';
const $=id=>document.getElementById(id);
const error=e=>{$('error').textContent=e?.message||String(e);};
const notice=text=>{$('notice').textContent=text;};
let client,auth,sdk,userUid=null,accountEpoch=0,models=[],driveConnected=false,quote=null,preparedConfig=null,session=null,job=null,pollTimer=null,approvedIntent=null,jobIntent=null,quoteIntent=null;
const intents=new Map(),downloaded=new Set(),downloading=new Set();
const terminalJobs=new Set(['completed','failed','cancelled','interrupted']);
const terminalSessions=new Set(['terminated','failed']);
const advanced=[['stride','Checkpoint stride',1,128],['samples','Samples per checkpoint',5,512],['cont_max','Continuation token cap',1,4096],['max_tokens','Original response token cap',8,4096],['temperature','Temperature',.05,2,.05],['top_k','Top-k branches',1,50],['threshold','Fork threshold',0,1,.01],['seed','Random seed',0,2147483647],['max_samples','Total sample allowance',1,1e9],['max_generated_tokens','Generated token allowance',1,1e9],['max_rounds','Refinement rounds',0,8],['refine_stride','Refinement stride',1,128],['refine_samples','Refinement samples',5,512],['min_tvd','Refinement TVD threshold',0,1,.01]];
for(const [key,label,min,max,step=1]of advanced){const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement('input');input.type='number';input.id='advanced-'+key;input.min=min;input.max=max;input.step=step;input.value=QUICK_DEFAULTS[key];wrap.append(input);$('advanced').append(wrap);}
function values(){return Object.fromEntries(advanced.map(([key])=>[key,Number($('advanced-'+key).value)]));}
function currentConfig(runtimeSeconds){const model=models.find(m=>m.id===$('model').value);return quickConfig({model_id:model?.id,resolved_revision:model?.revision,device:'cuda',batch_size:1,chat_template:true},$('prompt').value,$('answers').value.split('\n'),{...values(),max_seconds:Math.floor(runtimeSeconds)});}
function invalidateQuote(){if(approvedIntent)return;quote=null;preparedConfig=null;quoteIntent=null;$('quote-panel').hidden=true;$('quote-button').disabled=false;}
$('investigation-form').addEventListener('input',()=>{invalidateQuote();$('device-ack-label').hidden=$('destination').value!=='device';const model=models.find(m=>m.id===$('model').value);$('model-pin').textContent=model?'Revision '+model.revision:'';try{const allowance=quickAllowance(currentConfig(Number($('minutes').value)*60-430));$('sampling-estimate').textContent=`At the original response cap: up to ${allowance.checkpoints} checkpoints, ${allowance.samples} continuation draws and ${allowance.tokens.toLocaleString()} generated tokens.${allowance.exceeds?' Your allowances can stop this scan early.':''}`;}catch{$('sampling-estimate').textContent='Enter a valid prompt and answers to estimate sampling work.';}});
async function mutation(name,method,path,body={}){let intent=intents.get(name);if(!intent){intent=client.intent(method,path,body);intents.set(name,intent);}const result=await client.submit(intent);intents.delete(name);return result;}
function stopPolling(){clearTimeout(pollTimer);pollTimer=null;}
function renderSession(){
 $('session-panel').hidden=!session;if(!session)return;
 $('session-status').textContent=`Session: ${session.observed_state}. ${session.desired_state==='terminated'&&session.observed_state!=='terminated'?'Deletion requested; waiting for provider confirmation.':''}`;
 $('job-status').textContent=job?`Investigation: ${job.status}. ${job.status==='interrupted'?'The worker lost its lease. It will not rerun this job automatically.':''}`:'Waiting to queue the investigation.';
 $('deadline').textContent=`Compute deadline: ${new Date(session.expires_at*1000).toLocaleString()}. ${session.observed_state==='terminated'?'Provider deletion confirmed.':''}`;
 const progress=job?.progress;const phase={loading:'Loading the model',generating:'Generating the original response',scanning:'Sampling continuations',refining:'Refining selected intervals',inspecting:'Inspecting activations',saving:'Saving evidence'};
 $('progress').textContent=terminalJobs.has(job?.status)?'Execution has ended. Open saved evidence below for results or the failure details.':progress?`${phase[progress.phase]||'Working'}${progress.total>0?` · ${progress.completed} of ${progress.total}`:''}.`:terminalJobs.has(job?.status)?'Open the evidence library below to inspect saved results.':'The worker will report progress here once it starts.';
 if(session.manual_attention_required||session.cleanup_status==='credential_blocked')$('progress').textContent='Compute cleanup needs attention. Check the specific session in your RunPod account before starting another.';
 $('cancel').disabled=!job||terminalJobs.has(job.status);$('terminate').disabled=session.desired_state==='terminated'||session.observed_state==='terminated';
 $('quote-button').disabled=!terminalSessions.has(session.observed_state);$('approve').disabled=true;
}
async function refreshWorkspace(){
 const epoch=accountEpoch;const me=await client.request('/me');if(epoch!==accountEpoch)return;
 const [catalog,drive,sessions,jobs]=await Promise.all([client.request('/models'),client.request('/connections/drive'),client.request('/sessions'),client.request('/jobs')]);if(epoch!==accountEpoch)return;
 models=catalog.models||[];$('model').replaceChildren();for(const model of models){const option=document.createElement('option');option.value=model.id;option.textContent=model.id;$('model').append(option);}$('model-pin').textContent=models[0]?'Revision '+models[0].revision:'';
 $('runpod-status').textContent=me.runpod_connected===true?'RunPod connected':me.runpod_connected===false?'RunPod is disconnected':'RunPod connection checked when quoting';driveConnected=Boolean(drive.connected);$('drive-status').textContent=drive.connected?'Google Drive connected':'Google Drive is disconnected';
 $('drive-disconnect').disabled=!drive.connected;$('drive-connect').disabled=Boolean(drive.connected);
 session=(sessions.sessions||[]).find(s=>!terminalSessions.has(s.observed_state))||(sessions.sessions||[]).at(-1)||null;job=session?(jobs.jobs||[]).filter(j=>j.session_id===session.id).at(-1)||null:null;
 renderSession();await refreshLibrary();if(session&&!terminalSessions.has(session.observed_state))schedulePoll();
}
function schedulePoll(){stopPolling();pollTimer=setTimeout(()=>poll().catch(error),3000);}
async function poll(){
 if(!session||!client.user)return;const epoch=accountEpoch,sid=session.id;
 try{const [updated,jobs]=await Promise.all([client.request('/sessions/'+encodeURIComponent(sid)),client.request('/jobs')]);if(epoch!==accountEpoch)return;session=updated;$('error').textContent='';job=(jobs.jobs||[]).filter(j=>j.session_id===sid).at(-1)||null;renderSession();
 if(terminalJobs.has(job?.status)||job?.status==='saving')await refreshLibrary({autoDevice:true});
 if(!terminalSessions.has(session.observed_state))schedulePoll();else {for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;await refreshLibrary();approvedIntent=null;jobIntent=null;quoteIntent=null;$('discard').disabled=false;invalidateQuote();}
 }catch(e){if(epoch!==accountEpoch)return;notice('Progress refresh failed. The independent controller still enforces the session deadline. Use Refresh progress to reconnect.');throw e;}
}
function explorerURL(result,bundle){return '/observatory.html?evidence=local&run='+encodeURIComponent(result.id||'')+(bundle.payload?.investigation?.id?'&investigation='+encodeURIComponent(bundle.payload.investigation.id):'');}
function saveFile(bytes,id){const url=URL.createObjectURL(new Blob([bytes],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='fork-investigation-'+id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function importArtifact(artifact,li,{download=false}={}){
 if(downloading.has(artifact.id))return;downloading.add(artifact.id);const epoch=accountEpoch,owner=userUid;
 try{const offline=await import('./offline-evidence.mjs');
 const cacheKey='fork-hosted-delivery-index:'+owner;let index={};try{index=JSON.parse(localStorage.getItem(cacheKey)||'{}');}catch{}
 const cached=await offline.getHostedEvidence?.(owner,index[artifact.id]);if(epoch!==accountEpoch)return;
 const {bundle,bytes}=cached?{bundle:cached,bytes:new TextEncoder().encode(JSON.stringify(cached))}:await readHostedArtifact(client,artifact);if(epoch!==accountEpoch)return;
 if(typeof offline.installHostedEvidence!=='function')throw Error('This site does not yet support an isolated hosted evidence library. Download a portable copy.');
 const result=await offline.installHostedEvidence(bundle,{ownerUid:owner});if(epoch!==accountEpoch){await offline.clearHostedEvidence?.(owner);return;}
 index[artifact.id]=bundle.sha256;localStorage.setItem(cacheKey,JSON.stringify(index));
 if(download)saveFile(bytes,artifact.id);
 if(artifact.storage_mode==='device')await mutation('received-'+artifact.id,'POST','/artifacts/'+encodeURIComponent(artifact.id)+'/received');
 downloaded.add(artifact.id);
 const link=document.createElement('a');link.href=explorerURL(result,bundle);link.textContent='Explore saved evidence →';li.querySelector('[data-explore]')?.remove();link.dataset.explore='';li.append(link);const investigation=bundle.payload?.investigation;const detail=investigation?.status==='error'&&typeof investigation.message==='string'?` Run stopped: ${investigation.message.slice(0,1000)}`:'';notice('Evidence checksum verified and saved in this account’s browser library. Keep a portable copy.'+detail);
 }finally{downloading.delete(artifact.id);}
}
async function refreshLibrary({autoDevice=false}={}){
 const epoch=accountEpoch;const result=await client.request('/artifacts');if(epoch!==accountEpoch)return;
 $('library').replaceChildren();const artifacts=result.artifacts||[];if(!artifacts.length){const li=document.createElement('li');li.textContent='No completed evidence yet.';$('library').append(li);return;}
 for(const artifact of artifacts){const li=document.createElement('li');const label=document.createElement('span');label.textContent=`Investigation ${String(artifact.job_id||artifact.id).slice(0,10)} · ${artifact.storage_mode==='drive'?'Google Drive':'this device'}`;li.append(label);
 for(const [text,download]of [['Open in Explorer',false],['Download bundle',true]]){const button=document.createElement('button');button.textContent=text;button.onclick=async()=>{button.disabled=true;try{await importArtifact(artifact,li,{download});}catch(e){error(e);}finally{button.disabled=false;}};li.append(button);}$('library').append(li);
 if(autoDevice&&artifact.storage_mode==='device'&&!downloaded.has(artifact.id)&&artifact.session_id===session?.id)try{await importArtifact(artifact,li);}catch(e){error(e);}
 }
}
$('investigation-form').onsubmit=async event=>{event.preventDefault();$('error').textContent='';const epoch=accountEpoch;$('quote-button').disabled=true;for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=true;
 try{if(session&&!terminalSessions.has(session.observed_state))throw Error('Finish or delete your active session before requesting another.');
 if($('destination').value==='device'&&!$('device-ack').checked)throw Error('Acknowledge that this browser must remain open to save device evidence.');
 if($('destination').value==='drive'&&!driveConnected)throw Error('Connect Google Drive before quoting a Drive session, or choose this device.');
 const seconds=Number($('minutes').value)*60;preparedConfig=currentConfig(seconds-430);
 if(!quoteIntent)quoteIntent=client.intent('POST','/quotes',{model_id:$('model').value,max_duration_seconds:seconds,max_usd:Number($('usd').value)});
 quote=await client.submit(quoteIntent);quoteIntent=null;if(epoch!==accountEpoch)return;quoteWithinAllowance(quote,Number($('usd').value));
 preparedConfig=currentConfig(quote.max_duration_seconds-quote.startup_allowance_seconds-quote.export_reserve_seconds-10);
 $('quote-details').textContent=`${quote.model_id}\nRevision ${quote.revision||quote.model_revision}\n${quote.gpu_id} · ${quote.gpu_count||1} GPU · ${quote.cloud}\nGPU: $${Number(quote.gpu_usd_per_hour).toFixed(4)}/hour\nDisk: ${quote.disk_gb} GB × $${Number(quote.disk_usd_per_gb_hour).toFixed(6)}/GB/hour\nSession: ${quote.max_duration_seconds/60} minutes, including ${quote.startup_allowance_seconds/60} minutes startup and ${quote.export_reserve_seconds/60} minutes export reserve\nEstimated total: $${Number(quote.estimated_usd).toFixed(3)} · allowance $${Number($('usd').value).toFixed(2)}\nSave to: ${$('destination').value==='drive'?'Google Drive':'this device'}\nAvailability: ${quote.availability} (not reserved)`;
 $('quote-remaining').textContent='Quote expires '+new Date(quote.expires_at*1000).toLocaleTimeString()+'. '+quote.cost_notice;$('quote-panel').hidden=false;$('approve').disabled=false;notice('Review the provider estimate and approve only if you want to start paid compute.');
 }catch(e){error(e);}finally{if(epoch===accountEpoch){$('quote-button').disabled=false;for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;}}
};
$('discard').onclick=()=>{approvedIntent=null;jobIntent=null;invalidateQuote();};
$('approve').onclick=async()=>{const epoch=accountEpoch;$('approve').disabled=true;$('discard').disabled=true;$('error').textContent='';for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=true;
 try{if(!quote||!preparedConfig)throw Error('Request and review a quote first.');if(!approvedIntent)quoteWithinAllowance(quote,Number($('usd').value));
 if(!approvedIntent)approvedIntent=client.intent('POST','/sessions',{quote_id:quote.id,storage_mode:$('destination').value,acknowledge_device_loss:$('destination').value==='device'&&$('device-ack').checked});
 session=await client.submit(approvedIntent);if(epoch!==accountEpoch)return;renderSession();notice('GPU session requested. Queuing the investigation while the worker starts.');
 if(!jobIntent)jobIntent=client.intent('POST','/jobs',{session_id:session.id,command:{action:'investigate',model_id:quote.model_id,config:preparedConfig}});
 job=await client.submit(jobIntent);if(epoch!==accountEpoch)return;renderSession();schedulePoll();$('quote-panel').hidden=true;notice('Investigation queued. Keep this page open when saving to this device.');
 }catch(e){if(epoch!==accountEpoch)return;error(e);$('approve').textContent=approvedIntent?'Retry the same approved request':'Approve this GPU session';$('approve').disabled=false;if(session){renderSession();schedulePoll();}$('approve').disabled=false; }
 finally{if(epoch===accountEpoch)$('discard').disabled=Boolean(approvedIntent);}
};
$('runpod-form').onsubmit=async event=>{event.preventDefault();const key=$('runpod-key').value;$('runpod-key').value='';try{const result=await mutation('runpod-connect','PUT','/connections/runpod',{api_key:key});$('runpod-status').textContent=result.connected?'RunPod connected':'RunPod disconnected';notice('RunPod credential stored in the server vault.');}catch(e){error(e);}};
$('runpod-disconnect').onclick=async()=>{try{await mutation('runpod-disconnect','DELETE','/connections/runpod');$('runpod-status').textContent='RunPod disconnected.';}catch(e){error(e);}};
$('drive-connect').onclick=async()=>{try{const result=await mutation('drive-authorize','POST','/connections/drive/begin');const url=new URL(result.authorization_url||result.url);if(url.protocol!=='https:'||url.hostname!=='accounts.google.com'||url.username||url.password)throw Error('Unsafe Google authorization address.');location.assign(url.href);}catch(e){error(e);}};
$('drive-disconnect').onclick=async()=>{try{await mutation('drive-disconnect','POST','/connections/drive/disconnect');driveConnected=false;$('drive-status').textContent='Google Drive disconnected';$('drive-connect').disabled=false;$('drive-disconnect').disabled=true;}catch(e){error(e);}};
$('refresh').onclick=()=>poll().catch(error);$('library-refresh').onclick=()=>refreshLibrary().catch(error);
$('cancel').onclick=async()=>{try{if(job)job=await mutation('cancel-'+job.id,'POST','/jobs/'+encodeURIComponent(job.id)+'/cancel');renderSession();schedulePoll();}catch(e){error(e);}};
$('terminate').onclick=async()=>{try{if(session)session=await mutation('terminate-'+session.id,'POST','/sessions/'+encodeURIComponent(session.id)+'/terminate');renderSession();schedulePoll();}catch(e){error(e);}};
async function changedUser(user){
 const previous=userUid;accountEpoch++;const epoch=accountEpoch;stopPolling();client.setUser(user);userUid=user?.uid||null;intents.clear();downloaded.clear();downloading.clear();quote=preparedConfig=session=job=approvedIntent=jobIntent=quoteIntent=null;
 $('library').replaceChildren();$('workspace').hidden=true;$('quote-panel').hidden=true;$('session-panel').hidden=true;$('runpod-key').value='';for(const input of $('investigation-form').querySelectorAll('input,textarea,select'))input.disabled=false;$('error').textContent='';$('approve').textContent='Approve this GPU session';$('discard').disabled=false;
 if(previous&&previous!==userUid){$('prompt').value='';$('answers').value='';const offline=await import('./offline-evidence.mjs');await offline.clearHostedEvidence?.(previous);}
 if(epoch!==accountEpoch)return;$('sign-in').hidden=Boolean(user);$('sign-out').hidden=!user;$('account-status').textContent=user?`Signed in as ${user.email||'your Google account'}. Checking workspace access…`:'Hosted access is invite only.';
 if(!user){notice('Sign in to your invited workspace.');return;}
 try{await refreshWorkspace();if(epoch!==accountEpoch)return;$('workspace').hidden=false;$('account-status').textContent=`Signed in as ${user.email||'your Google account'}.`;notice('Your personal hosted workspace is ready.');}catch(e){if(epoch===accountEpoch){error(e);notice('Hosted access could not be verified. Sign out to use another invited account.');}}
}
$('sign-in').onclick=async()=>{try{const provider=new sdk.GoogleAuthProvider();if(matchMedia('(max-width: 600px)').matches)await sdk.signInWithRedirect(auth,provider);else await sdk.signInWithPopup(auth,provider);}catch(e){error(e);}};
$('sign-out').onclick=async()=>{try{await sdk.signOut(auth);}catch(e){error(e);}};
async function initialize(){try{const response=await fetch('/hosted-config.json',{cache:'no-store',credentials:'omit'});if(!response.ok)throw Error('Hosted configuration is unavailable.');client=new HostedClient(await response.json());
 if(!client.config.enabled){notice('Hosted compute is not enabled on this site. You can explore saved evidence or use local compute.');return;}
 const [appSdk,authSdk]=await Promise.all([import(`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-app.js`),import(`https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-auth.js`)]);sdk=authSdk;auth=sdk.getAuth(appSdk.initializeApp(client.config.firebase));
 await sdk.setPersistence(auth,sdk.browserLocalPersistence);$('sign-in').disabled=false;sdk.onAuthStateChanged(auth,user=>changedUser(user).catch(error));await sdk.getRedirectResult(auth);
 }catch(e){error(e);notice('Hosted compute could not be initialized. Local compute and saved evidence remain available.');}}
await initialize();
