/** Simulated transport tests; no provider or model is called. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkerExecution,computeLabel} from '../public/fork-microscope/execution-adapter.mjs';
import {webcrypto} from 'node:crypto';
globalThis.crypto??=webcrypto;
function fixture(){const data=new Map(),requests=[];let lose=true;const storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};const adapter=createWorkerExecution({storage,identity:()=> 'fixture-machine',fetcher:async(url,init={})=>{requests.push({url,body:init.body&&JSON.parse(init.body)});if(url.endsWith('workflow-start')&&lose){lose=false;throw Error('Lost reply');}return new Response(JSON.stringify({id:'fixture-job',status:'running'}));}});return {adapter,requests,storage};}
test('uncertain submission reuses exact payload even if draft changes',async()=>{const {adapter,requests}=fixture();await assert.rejects(adapter.start({prompt:'original'}),/Lost reply/);assert.equal(adapter.hasPending(),true);await adapter.start({prompt:'changed'});assert.deepEqual(requests[0].body,requests[1].body);assert.equal(adapter.restoredId(),'fixture-job');assert.equal(adapter.hasPending(),false);});
test('worker library uses supported endpoint and cancellation is scoped to selected job',async()=>{const {adapter,requests}=fixture();await adapter.list();await adapter.cancel('chosen');assert.equal(requests[0].url,'/api/live/workflows');assert.deepEqual(requests[1].body,{id:'chosen'});});
test('switching machine rejects stale in-flight result',async()=>{let resolve;const adapter=createWorkerExecution({identity:()=> 'fixture',storage:{},fetcher:()=>new Promise(r=>resolve=r)});const pending=adapter.status();adapter.reset();resolve(new Response('{}'));await assert.rejects(pending,/Machine changed/);});
test('compute shutdown differs from job completion',()=>{assert.equal(computeLabel({observed_state:'ready'},{status:'completed'}),'Ready');assert.equal(computeLabel({observed_state:'ready',desired_state:'terminated'},{}),'Stopping compute');assert.equal(computeLabel({observed_state:'terminated'},{}),'Compute stopped');});
