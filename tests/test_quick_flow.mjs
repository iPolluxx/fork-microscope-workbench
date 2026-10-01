/** Headless DOM doubles + simulated worker. These are not browser or model runs. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {QUICK_DEFAULTS,quickConfig,quickAllowance,quickPhase} from '../public/fork-microscope/quick-investigation-config.mjs';
import {retryIdentity} from '../public/fork-microscope/request-retry.mjs';
const source=fs.readFileSync(new URL('../public/fork-microscope/quick-investigation.mjs',import.meta.url),'utf8').replace(/^import .*;\n/gm,'');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({loseReply=false,onStatus=null}={}){
 const nodes=new Map(),events=new Map(),requests=[],jobs=new Map(),storage=new Map(),timers=new Map();let sequence=0,exported=null,current=null;
 const model={model_id:'fixture/model',resolved_revision:'fixed-revision',device:'cpu',batch_size:1,chat_template:true};
 class Element{constructor(){this.dataset={};this.value='';this.hidden=false;this.disabled=false;}setAttribute(k,v){this[k]=v;}removeAttribute(k){delete this[k];}addEventListener(k,fn){this['on'+k]=fn;}}
 const node=q=>{if(!nodes.has(q))nodes.set(q,new Element());return nodes.get(q);};
 const settings=Object.entries(QUICK_DEFAULTS).map(([key,value])=>{const n=new Element();n.dataset.setting=key;n.value=value;return n;});
 const stages=Array.from({length:4},()=>new Element());
 const host={querySelector:node,querySelectorAll:q=>q==='[data-setting]'?settings:q==='.quick-steps li'?stages:q.includes('textarea')?[node('#quick-prompt'),node('#quick-answers'),...settings]:[]};
 const store={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 const reply=x=>({ok:true,status:200,json:async()=>structuredClone(x)});
 const api=async(url,options={})=>{const route=url.split('/api/live/')[1],body=options.body?JSON.parse(options.body):null;requests.push({route,body});
  if(route==='status'){await onStatus?.();return reply({model,runtime:{cuda_available:false},job:{status:current?.status==='running'?'running':'idle'}});}
  if(route==='workflow-start'){
   if(!jobs.has(body.request_id)){current={id:'a'.repeat(32),config:body.config,status:'running',runs:[],responses:[],steps:[{action:'base'}],reservations:{samples:0,generated_tokens:512}};jobs.set(body.request_id,current);}
   if(loseReply){loseReply=false;throw Error('Simulated lost reply');}return reply(current);
  }
  if(route.startsWith('workflow?id='))return reply(current);
  if(route==='workflow-cancel'){current.status='cancelled';return reply(current);}
  if(route.startsWith('workflow-export?id='))return reply({schema:'simulated-bundle',id:current.id});
  throw Error('Unexpected route '+route);
 };
 const window={computeFetch:api,workerConnection:()=>({url:'https://fixture-worker.example'}),addEventListener:(name,fn)=>events.set(name,fn)};
 vm.runInNewContext(source,{document:{getElementById:()=>host},window,location:{origin:'https://fixture-dashboard.example'},localStorage:store,sessionStorage:store,QUICK_DEFAULTS,quickConfig,quickAllowance,quickPhase,retryIdentity:(r,p)=>retryIdentity(r,p,store),saveJSON:async(_button,_name,fn)=>{exported=await fn();},clearOfflineEvidence(){},setTimeout:fn=>{timers.set(++sequence,fn);return sequence;},clearTimeout:id=>timers.delete(id),Event});
 return {events,nodes,node,requests,jobs,storage,timers,stages,get job(){return current;},get exported(){return exported;},submit:()=>node('[data-quick-form]').onsubmit({preventDefault(){}})};
}

test('front door runs once then opens and exports the worker result',async()=>{
 const f=fixture();await turn();f.node('#quick-prompt').value='Choose A or B.';f.node('#quick-answers').value='CHOICE=A\nCHOICE=B';await f.submit();await turn();
 assert.equal(f.jobs.size,1);assert.equal(f.node('[data-run]').disabled,true);assert.equal(f.job.config.refinement.max_rounds,0);
 f.job.status='complete';f.job.runs=['b'.repeat(32)];await f.node('[data-retry]').onclick();await turn();
 assert.equal(f.node('[data-explore]').hidden,false);assert.match(f.node('[data-explore]').href,/evidence=worker/);
 await f.node('[data-export]').onclick();assert.equal(f.exported.id,f.job.id);
 f.node('[data-new]').onclick();assert.equal(f.node('#quick-prompt').disabled,false);
});
test('a lost start reply is recovered while worker is busy without launching twice',async()=>{
 const f=fixture({loseReply:true});await turn();f.node('#quick-prompt').value='Choose A or B.';f.node('#quick-answers').value='A\nB';await f.submit();await turn();
 assert.equal(f.jobs.size,1);assert.equal(f.node('[data-run]').textContent,'Recover submitted investigation');assert.equal(f.node('#quick-prompt').disabled,true);
 await f.submit();await turn();const starts=f.requests.filter(r=>r.route==='workflow-start');assert.equal(starts.length,2);assert.equal(starts[0].body.request_id,starts[1].body.request_id);assert.equal(f.jobs.size,1);
});
test('stop preserves the investigation and permits a new explicit run',async()=>{
 const f=fixture();await turn();f.node('#quick-prompt').value='Choose A or B.';f.node('#quick-answers').value='A\nB';await f.submit();await turn();await f.node('[data-stop]').onclick();await turn();
 assert.equal(f.job.status,'cancelled');assert.equal(f.node('[data-new]').hidden,false);assert.equal(f.jobs.size,1);
});

test('switching compute during pre-submit status never starts a job',async()=>{
 let block=false,release;
 const f=fixture({onStatus:()=>block?new Promise(resolve=>{release=resolve;}):undefined});await turn();
 f.node('#quick-prompt').value='Choose A or B.';f.node('#quick-answers').value='A\nB';block=true;
 const submitting=f.submit();await turn();f.events.get('worker-connection-change')();release();await submitting;
 assert.equal(f.requests.filter(r=>r.route==='workflow-start').length,0);
});
test('failed response preview keeps polling and permits response-only export',async()=>{
 const f=fixture();await turn();f.node('#quick-prompt').value='Choose A or B.';f.node('#quick-answers').value='A\nB';await f.submit();await turn();
 f.job.responses=['saved-response'];f.node('[data-retry]').onclick();await turn();
 assert.ok(f.timers.size>0);assert.equal(f.node('[data-export]').hidden,false);
 assert.equal(f.node('[data-explore]').hidden,true);await f.node('[data-export]').onclick();assert.equal(f.exported.id,f.job.id);
 f.job.status='cancelled';f.node('[data-retry]').onclick();await turn();
 f.node('[data-response]').textContent='Old preview';f.node('[data-response]').dataset.id='old';
 f.node('[data-new]').onclick();assert.equal(f.node('[data-response]').textContent,'Waiting for the response.');assert.equal(f.node('[data-response]').dataset.id,undefined);
});
