import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {quoteWithinAllowance} from '../public/fork-microscope/hosted-client.mjs';
import {createSavedImportController} from '../public/fork-microscope/hosted-import-ui.mjs';

const source=await readFile(new URL('../public/fork-microscope/hosted-ui.mjs',import.meta.url),'utf8');
const fragment=await readFile(new URL('../public/fork-microscope/hosted-fragment.html',import.meta.url),'utf8');

// Execute the actual UI's functions and event handlers with DOM, authentication,
// private-evidence cleanup and request seams replaced. No browser, keys or network.
async function fixture({drive={enabled:false,connected:false},draft=null,config=null,submit=null,catalog=null,request=null,offline=null,readArtifact=null,saveBytes=null}={}){
 const nodes=new Map(),calls=[],storage=new Map(),listeners=new Map(),timers=[],configCalls=[];let identity=0;
 class Element{
  constructor(tag='div',id=''){this.tagName=tag;this.id=id;this.children=[];this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.textContent='';this.dataset={};this.handlers={};}
  append(...children){for(const child of children){child.parent=this;this.children.push(child);if(child.id)nodes.set(child.id,child);if(this.tagName==='select'&&!this.value)this.value=child.value;}}
  replaceChildren(...children){this.children=[];this.append(...children);}
  querySelector(selector){
   if(selector.startsWith('#'))return nodes.get(selector.slice(1))||null;
   if(selector==='option[value="drive"]')return driveOption;
   if(selector==='button')return this.submitButton??=new Element('button');
   if(selector==='summary')return this.summary??=new Element('summary');
   return null;
  }
  querySelectorAll(selector){
   if(this.id==='hosted-resource-fields')return ['destination','minutes','usd','device-ack'].map(id=>nodes.get(id));
   if(selector==='input,textarea,select')return [...nodes.values()].filter(node=>['input','textarea','select'].includes(node.tagName));
   return [];
  }
  addEventListener(name,handler){this.handlers[name]=handler;}
  closest(){return this.parent||new Element('label');}
  focus(){} remove(){} setAttribute(){}
 }
 for(const match of fragment.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/g))nodes.set(match[2],new Element(match[1],match[2]));
 const root=nodes.get('hosted-root');root.dataset.view='question';
 const driveOption=new Element('option');driveOption.value='drive';nodes.get('destination').append(driveOption);
 for(const [id,value] of Object.entries({destination:'device','execution-kind':'hosted',minutes:'30',usd:'.5',prompt:'Question',answers:'Yes\nNo','worker-revision':'main'}))nodes.get(id).value=value;
 const client={user:{uid:'alice'},epoch:0,setUser(user){this.user=user;this.epoch++;},
  async request(path){calls.push({type:'request',path});if(request){const value=await request(path);if(value!==undefined)return value;}if(path==='/me')return {runpod_connected:true};if(path==='/models')return {models:[{id:'model',revision:'exact'}]};if(path==='/connections/drive')return drive;if(path==='/sessions')return {sessions:[]};if(path==='/jobs')return {jobs:[]};if(path==='/artifacts')return {artifacts:[]};if(path==='/evidence?limit=10')return catalog||{investigations:[],total_candidates:0};throw Error('Unexpected request');},
  intent(method,path,body){calls.push({type:'intent',method,path,body});return {method,path,body,key:'request-'+(++identity)};},
  async submit(intent){calls.push({type:'submit',...intent});if(submit)return submit(intent);throw Error('Unexpected mutation');},
 };
 const savedDraft=draft&&JSON.stringify(draft);if(savedDraft)storage.set('fork-question-v2:alice',savedDraft);
 const defaults={stride:16,samples:10,cont_max:512,max_tokens:512,temperature:1,top_k:5,threshold:.05,seed:0,max_seconds:100,max_samples:500,max_generated_tokens:200000,max_rounds:0,refine_stride:4,refine_samples:20,min_tvd:.25};
 const context={structuredClone,
  document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)},
  location:{href:'https://app.example/workspace.html',search:'',assign(){throw Error('Unexpected navigation');}},
  history:{replaceState(){}},window:{dispatchEvent(){},addEventListener(name,handler){listeners.set(name,handler);}},CustomEvent:class{},
  localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
  URL,URLSearchParams,Map,Set,JSON,Number,Boolean,String,Object,Date,Error,clearTimeout(){},setTimeout(fn){timers.push(fn);return timers.length;},
  createSavedImportController,quoteWithinAllowance,QUICK_DEFAULTS:defaults,quickConfig(model,prompt,answers,settings){configCalls.push(settings);if(config)return structuredClone(config);throw Error('Config should not be reached before acknowledgment');},quickAllowance(){return {};},
  createWorkerExecution:()=>({status:async()=>null}),computeLabel:()=>'',offlineStub:offline||{clearHostedEvidence:async()=>{}},readHostedArtifact:readArtifact||(()=>{throw Error('Unexpected artifact retrieval');}),fixtureSaveBytes:saveBytes,fixtureClient:client,
 };
 let executable=source.replace(/^import\s+[^\n]+;\s*$/gm,'').replaceAll("import('./offline-evidence.mjs')",'Promise.resolve(offlineStub)');
 // The remote Firebase bootstrap is outside this test. All actual event bindings
 // and UI methods above it remain intact, including drive safety gates.
 executable=executable.slice(0,executable.lastIndexOf('updateConnectionStep();\nawait initialize();'));
 executable+=`\nclient=fixtureClient;userUid='alice';if(fixtureSaveBytes)saveFile=fixtureSaveBytes;return {refreshWorkspace,refreshSavedLibrary,poll,importArtifact,reviewPreparedPlan,renderSession,restoreQuestion,executionChanged,updateConnectionStep,changedUser,setSession:(s,j)=>{session=s;job=j;},getState:()=>({driveEnabled,driveConnected,session,job}),setApproval:()=>{quote={id:'quoted'};preparedConfig={};},setOwner:uid=>{userUid=uid;}};`;
 const api=await vm.runInNewContext(`(async()=>{${executable}\n})()`,context);
 return {api,nodes,calls,storage,client,driveOption,drive,timers,configCalls,element:()=>new Element()};
}

const driveDraft={prompt:'Saved question',answers:'Yes\nNo',kind:'hosted',resources:{destination:'drive',minutes:30,usd:.5}};

test('disabled Drive hides actions and destination and coerces a restored draft without carrying acknowledgment',async()=>{
 const f=await fixture({draft:driveDraft});f.nodes.get('device-ack').checked=true;
 await f.api.refreshWorkspace();
 assert.equal(f.nodes.get('destination').value,'device');assert.equal(f.nodes.get('device-ack').checked,false);assert.equal(f.nodes.get('device-ack-label').hidden,false);
 for(const id of ['drive-connect','drive-disconnect']){assert.equal(f.nodes.get(id).hidden,true);assert.equal(f.nodes.get(id).disabled,true);}
 assert.equal(f.driveOption.hidden,true);assert.equal(f.driveOption.disabled,true);
 assert.match(f.nodes.get('drive-status').textContent,/unavailable on this deployment/);
 assert.match(f.nodes.get('drive-note').textContent,/save device evidence/);
 assert.equal(JSON.parse(f.storage.get('fork-question-v2:alice')).resources.destination,'device');
 assert.equal(f.nodes.get('prompt').value,'Saved question');
});

test('execution and connection updates cannot reenable forbidden Drive actions',async()=>{
 const f=await fixture();await f.api.refreshWorkspace();
 for(const kind of ['worker','hosted']){f.nodes.get('execution-kind').value=kind;f.api.executionChanged();f.api.updateConnectionStep();
  assert.equal(f.nodes.get('drive-connect').disabled,true);assert.equal(f.nodes.get('drive-disconnect').disabled,true);assert.equal(f.driveOption.disabled,true);
 }
 const before=f.calls.length;await f.nodes.get('drive-connect').onclick();await f.nodes.get('drive-disconnect').onclick();
 assert.equal(f.calls.length,before);
});

test('coerced device delivery requires acknowledgment before any quote request',async()=>{
 const f=await fixture({draft:driveDraft});await f.api.refreshWorkspace();
 const before=f.calls.length;await f.nodes.get('investigation-form').onsubmit({preventDefault(){}});
 assert.equal(f.calls.length,before);
 assert.match(f.nodes.get('error').textContent,/Acknowledge.*save device evidence/);
 assert.equal(f.nodes.get('destination').value,'device');
});

test('approval rejects an unavailable Drive destination and an unchecked device acknowledgment',async()=>{
 const f=await fixture();await f.api.refreshWorkspace();f.api.setApproval();
 f.nodes.get('destination').value='drive';await f.nodes.get('approve').onclick();
 assert.match(f.nodes.get('error').textContent,/Google Drive is unavailable/);
 f.nodes.get('destination').value='device';f.nodes.get('device-ack').checked=false;await f.nodes.get('approve').onclick();
 assert.match(f.nodes.get('error').textContent,/Acknowledge device evidence delivery/);
 assert.equal(f.calls.filter(call=>call.type==='submit'||call.type==='intent').length,0);
});

test('legacy response without enabled keeps Drive available and connection buttons accurate',async()=>{
 const f=await fixture({drive:{connected:true},draft:driveDraft});await f.api.refreshWorkspace();
 assert.equal(f.nodes.get('destination').value,'drive');assert.equal(f.driveOption.hidden,false);assert.equal(f.driveOption.disabled,false);
 assert.equal(f.nodes.get('drive-connect').hidden,false);assert.equal(f.nodes.get('drive-connect').disabled,true);assert.equal(f.nodes.get('drive-disconnect').disabled,false);
 f.api.executionChanged();f.api.updateConnectionStep();assert.equal(f.nodes.get('drive-connect').disabled,true);
 f.drive.connected=false;await f.api.refreshWorkspace();assert.equal(f.nodes.get('drive-connect').disabled,false);assert.equal(f.nodes.get('drive-disconnect').disabled,true);
});

test('signout resets per-account Drive state and acknowledgment without forgetting deployment prohibition',async()=>{
 const f=await fixture();await f.api.refreshWorkspace();f.nodes.get('device-ack').checked=true;
 await f.api.changedUser(null);f.api.executionChanged();f.api.updateConnectionStep();
 assert.equal(f.api.getState().driveEnabled,false);assert.equal(f.api.getState().driveConnected,false);
 assert.equal(f.nodes.get('device-ack').checked,false);assert.equal(f.nodes.get('drive-connect').hidden,true);assert.equal(f.driveOption.disabled,true);
});

test('a fresh capability response can enable Drive again without reusing another account connection',async()=>{
 const f=await fixture();await f.api.refreshWorkspace();
 f.drive.enabled=true;f.drive.connected=false;await f.api.refreshWorkspace();
 assert.equal(f.api.getState().driveEnabled,true);assert.equal(f.api.getState().driveConnected,false);
 assert.equal(f.driveOption.hidden,false);assert.equal(f.nodes.get('drive-connect').disabled,false);assert.equal(f.nodes.get('drive-disconnect').disabled,true);
});

const exactConfig={model:{model_id:'model',revision:'exact'},base:{prompt:'<b>Question</b>',answers:['Yes','No']},scan:{samples:5},refinement:{max_rounds:0},limits:{max_seconds:100}};
function exactPlan(){const expires=Date.now()/1000+600;return {id:'plan-1',status:'prepared',config:structuredClone(exactConfig),approval_digest:'a'.repeat(64),config_hash:'b'.repeat(64),expires_at:expires,storage_mode:'device',acknowledge_device_loss:true,usd_allowance:.5,max_duration_seconds:1800,quote:{id:'quote-1',expires_at:expires,model_id:'model',revision:'exact',worker_image:'example/worker@sha256:'+'d'.repeat(64),gpu_id:'gpu',estimated_usd:.25,gpu_usd_per_hour:1,disk_gb:30,disk_usd_per_gb_hour:.001,max_duration_seconds:1800,startup_allowance_seconds:300,export_reserve_seconds:120,availability:'available'}};}

test('browser prepares and approves one exact digest-bound plan without separate session or job creation',async()=>{
 const prepared=exactPlan();const f=await fixture({config:exactConfig,submit:async intent=>intent.path==='/plans'?prepared:{plan:{...prepared,status:'approved'},session:{id:'session-1',observed_state:'requested',desired_state:'running',expires_at:Date.now()/1000+1800},job:{id:'job-1',session_id:'session-1',status:'queued'}}});
 await f.api.refreshWorkspace();f.nodes.get('device-ack').checked=true;
 await f.nodes.get('investigation-form').onsubmit({preventDefault(){}});
 const prepare=f.calls.find(call=>call.type==='submit'&&call.path==='/plans');assert.deepEqual(prepare.body.config,exactConfig);assert.equal(prepare.body.acknowledge_device_loss,true);
 assert.match(f.nodes.get('quote-config').textContent,/<b>Question<\/b>/);assert(f.nodes.get('quote-config').textContent.includes('a'.repeat(64)));
 await f.nodes.get('approve').onclick();
 const approval=f.calls.find(call=>call.type==='submit'&&call.path.endsWith('/approve'));assert.equal(approval.path,'/plans/plan-1/approve');assert.equal(approval.body.approval_digest,prepared.approval_digest);assert.equal(approval.body.confirm_execution,true);
 assert.equal(f.calls.filter(call=>call.type==='submit'&&['/sessions','/jobs'].includes(call.path)).length,0);
 assert.match(f.nodes.get('notice').textContent,/Worker enrollment and model execution remain to be observed/);
});

test('dashboard review requires an immutable worker image and rechecks it before approval',async()=>{
 const f=await fixture();
 for(const worker_image of [undefined,'example/worker:latest','example/worker@sha256:'+'D'.repeat(64)]){
  const unbound=exactPlan();unbound.quote.worker_image=worker_image;
  assert.throws(()=>f.api.reviewPreparedPlan(unbound),/immutable worker image/);
 }
 const mismatch=exactPlan();mismatch.quote.revision='other';
 assert.throws(()=>f.api.reviewPreparedPlan(mismatch),/model revision/);
 const prepared=exactPlan();f.api.reviewPreparedPlan(prepared);
 assert(f.nodes.get('quote-details').textContent.includes(prepared.quote.worker_image));
 f.nodes.get('device-ack').checked=true;delete prepared.quote.worker_image;
 await f.nodes.get('approve').onclick();
 assert.equal(f.calls.filter(call=>call.type==='submit'&&call.path.endsWith('/approve')).length,0);
 assert.match(f.nodes.get('error').textContent,/immutable worker image/);
});

test('uncertain approval keeps exact retry identity, blocks duplicate click and prohibits changed-plan submission',async()=>{
 const prepared=exactPlan();let release,count=0;const f=await fixture({config:exactConfig,submit:async intent=>{if(intent.path==='/plans')return prepared;if(++count===1)return new Promise((resolve,reject)=>{release=()=>reject(Error('Transport timeout'));});return {plan:{...prepared,status:'approved'},session:{id:'s',observed_state:'requested'},job:{id:'j',session_id:'s',status:'queued'}};}});
 await f.api.refreshWorkspace();f.nodes.get('device-ack').checked=true;await f.nodes.get('investigation-form').onsubmit({preventDefault(){}});
 const first=f.nodes.get('approve').onclick();await f.nodes.get('approve').onclick();assert.equal(count,1);release();await first;
 const before=f.calls.length;await f.nodes.get('investigation-form').onsubmit({preventDefault(){}});assert.equal(f.calls.length,before);
 await f.nodes.get('approve').onclick();const approvals=f.calls.filter(call=>call.type==='submit'&&call.path.endsWith('/approve'));assert.equal(approvals.length,2);assert.equal(approvals[0].key,approvals[1].key);assert.deepEqual(approvals[0].body,approvals[1].body);
});

test('expired plan is rejected before approval and definitive conflicts release configuration for new review',async()=>{
 const prepared=exactPlan();const f=await fixture({config:exactConfig,submit:async intent=>{if(intent.path==='/plans')return prepared;throw Object.assign(Error('Plan expired'),{status:409});}});
 await f.api.refreshWorkspace();f.nodes.get('device-ack').checked=true;await f.nodes.get('investigation-form').onsubmit({preventDefault(){}});
 prepared.expires_at=Date.now()/1000-1;const before=f.calls.length;await f.nodes.get('approve').onclick();assert.equal(f.calls.length,before);assert.match(f.nodes.get('error').textContent,/expired/);
 prepared.expires_at=prepared.quote.expires_at;await f.nodes.get('approve').onclick();assert.equal(f.nodes.get('quote-panel').hidden,true);assert.equal(f.nodes.get('prompt').disabled,false);assert.equal(f.nodes.get('discard').disabled,false);
});

test('durable hosted catalog is metadata only and preserves fixture labels without interpreting historical compute',async()=>{
 const f=await fixture({catalog:{total_candidates:1,investigations:[{bundle_id:'<img onerror=bad>.json',file_sha256:'a'.repeat(64),bytes:6059,provenance:{source:'user_file',synthetic:true,execution_verified:false}}]}});
 await f.api.refreshSavedLibrary();assert.match(f.nodes.get('saved-library-status').textContent,/1 saved investigation/);
 const item=f.nodes.get('saved-library-list').children[0];assert.match(item.children[0].textContent,/Synthetic fixture/);assert.match(item.children[0].textContent,/<img onerror=bad>/);assert.equal(f.calls.filter(call=>call.path?.includes('/content')).length,0);
});

test('unverified failed cleanup keeps polling and delete requires explicit possible-loss acknowledgment',async()=>{
 const f=await fixture();await f.api.refreshWorkspace();f.api.setSession({id:'s',observed_state:'failed',desired_state:'terminated',expires_at:1,cleanup_status:'credential_blocked'}, {id:'j',status:'running',cancel_requested:true});f.api.renderSession();
 assert.match(f.nodes.get('progress').textContent,/Charges may continue/);assert.match(f.nodes.get('job-status').textContent,/not yet confirmed it stopped/);assert.equal(f.nodes.get('cancel').disabled,true);
 assert.equal(f.nodes.get('delete-loss-label').hidden,false);assert.equal(f.nodes.get('terminate').disabled,true);const before=f.calls.length;await f.nodes.get('terminate').onclick();assert.equal(f.calls.length,before);assert.match(f.nodes.get('error').textContent,/acknowledge/);
});


test('failed cleanup still polls until provider absence is verified',async()=>{
 let verified=false;const f=await fixture({request:async path=>path==='/sessions/s'?{id:'s',observed_state:verified?'terminated':'failed',desired_state:'terminated',cleanup_verified:verified,expires_at:1}:undefined});
 await f.api.refreshWorkspace();f.api.setSession({id:'s',observed_state:'failed',desired_state:'terminated'},null);const before=f.timers.length;
 await f.api.poll();assert(f.timers.length>before);assert.equal(f.api.getState().session.cleanup_verified,false);
 verified=true;const next=f.timers.length;await f.api.poll();assert.equal(f.timers.length,next);assert.equal(f.api.getState().session.cleanup_verified,true);
});

test('cached browser evidence is explorable but portable download fetches original bytes',async()=>{
 const cached={schema:'native',sha256:'payload-hash',payload:{}},original=new TextEncoder().encode(' {"schema":"native","payload":{}}\n');let reads=0,saved=null;
 const f=await fixture({offline:{getHostedEvidence:async()=>cached,installHostedEvidence:async()=>({id:'r'})},readArtifact:async()=>{reads++;return {bundle:cached,bytes:original};},saveBytes:bytes=>{saved=bytes;},submit:async()=>({received:true})});
 await f.api.importArtifact({id:'artifact',storage_mode:'device'},f.element());assert.equal(reads,0);assert.equal(f.calls.filter(call=>call.type==='submit').length,0);assert.match(f.nodes.get('notice').textContent,/original-file download requires source retrieval/);
 await f.api.importArtifact({id:'artifact',storage_mode:'device'},f.element(),{download:true});assert.equal(reads,1);assert.equal(saved,original);assert.equal(f.calls.filter(call=>call.type==='submit'&&call.path.endsWith('/received')).length,1);
});

test('expired original artifact download points to preserved JSON instead of implying active compute',async()=>{
 const f=await fixture({offline:{getHostedEvidence:async()=>({payload:{}})},readArtifact:async()=>{throw Object.assign(Error('Source expired'),{status:410});}});
 await assert.rejects(f.api.importArtifact({id:'old',storage_mode:'device'},f.element(),{download:true}),/worker artifact expired.*preserved original/);assert.equal(f.calls.filter(call=>call.type==='submit').length,0);
});


test('a user-chosen smaller runtime bound survives plan preparation and stays adjustable',async()=>{
 const prepared=exactPlan();const f=await fixture({config:exactConfig,submit:async()=>prepared});await f.api.refreshWorkspace();f.nodes.get('device-ack').checked=true;f.nodes.get('advanced-max_seconds').value='100';
 await f.nodes.get('investigation-form').onsubmit({preventDefault(){}});assert.equal(f.configCalls.at(-1).max_seconds,100);assert.equal(f.nodes.get('advanced-max_seconds').closest().hidden,false);
});
