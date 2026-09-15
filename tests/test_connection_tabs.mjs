import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../public/fork-microscope/worker-connection.js',import.meta.url),'utf8');
function browser(){
  const peers=[];
  return function tab(origin,saved=null,httpStatus=200){
    const storage=new Map(saved?[['fork-worker-session-v1',JSON.stringify(saved)]]:[]),calls=[];
    class Channel {
      constructor(name){this.name=name;peers.push(this);this.origin=origin;}
      postMessage(data){for(const p of peers)if(p!==this&&p.origin===origin&&p.name===this.name)queueMicrotask(()=>p.onmessage?.({data:structuredClone(data)}));}
    }
    const location=new URL(origin);
    const context={URL,Headers,Event,setTimeout,clearTimeout,BroadcastChannel:Channel,location,
      sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
      document:{readyState:'loading',addEventListener(){}},
      fetch:async(url,options)=>{calls.push({url,options});return {status:httpStatus};},dispatchEvent(){}};
    context.window=context;vm.runInNewContext(source,context);
    return {context,calls,storage};
  };
}
test('new same-origin tab inherits live connection before fetching saved evidence',async()=>{
  const tab=browser(),connection={url:'http://127.0.0.1:8767',token:'test-only-token'};
  tab('https://dashboard.example',connection);
  const fresh=tab('https://dashboard.example');
  await fresh.context.workerFetch('/api/live/runs');
  assert.equal(fresh.calls[0].url,connection.url+'/api/live/runs');
  assert.equal(fresh.calls[0].options.headers.get('Authorization'),'Bearer test-only-token');
  assert.deepEqual(JSON.parse(fresh.storage.get('fork-worker-session-v1')),connection);
});
test('different website does not receive another origins credentials',async()=>{
  const tab=browser();tab('https://dashboard.example',{url:'https://private-worker.example',token:'test-token'});
  const fresh=tab('https://unrelated.example');
  await assert.rejects(fresh.context.workerFetch('/api/live/runs'),/Connect the worker/);
  assert.equal(fresh.calls.length,0);
});
test('normal local viewer needs no saved tab credentials',async()=>{
  const fresh=browser()('http://127.0.0.1:8767');
  await fresh.context.workerFetch('/api/live/runs');
  assert.equal(fresh.calls[0].url,'http://127.0.0.1:8767/api/live/runs');
  assert.equal(fresh.calls[0].options.headers.get('Authorization'),null);
});
test('authentication failure explains reconnection rather than missing evidence',async()=>{
  const fresh=browser()('https://dashboard.example',{url:'https://worker.example',token:'old'},401);
  await assert.rejects(fresh.context.workerFetch('/api/live/runs'),/saved runs have not been deleted/);
});
