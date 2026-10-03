import {retryIdentity} from './request-retry.mjs';
/** Direct-worker transport. Hosted execution retains quote/approval in HostedClient. */
export function createWorkerExecution({fetcher= (...args)=>(window.computeFetch||window.workerFetch)(...args), identity=()=>window.workerConnection?.().url||location.origin, storage=sessionStorage}={}) {
 let epoch=0;
 const key=()=> 'fork-unified-job:'+identity();
 async function request(route,body){const generation=epoch;const r=await fetcher('/api/live/'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let value;try{value=await r.json();}catch{throw Error('Connect a compatible Fork Microscope machine in Compute.');}if(generation!==epoch)throw Error('Machine changed. Refresh before continuing.');if(!r.ok){const e=Error(value.error||'Machine request failed.');e.httpStatus=r.status;throw e;}return value;}
 return {
  status:()=>request('status'),
  async start(config){const generation=epoch,k=key(),worker=identity();let pending;try{pending=JSON.parse(storage.getItem(k+':pending')||'null');}catch{}
   const body=pending||{config};const id=await retryIdentity('unified-workflow:'+worker,body);if(generation!==epoch)throw Error('Machine changed.');const payload=pending||{config,request_id:id.id};storage.setItem(k+':pending',JSON.stringify(payload));
   try{const job=await request('workflow-start',payload);storage.setItem(k,job.id);storage.removeItem(k+':pending');id.ack();return job;}catch(e){if(e.httpStatus===400){storage.removeItem(k+':pending');id.ack();}throw e;}
  },
  restoredId:()=>storage.getItem(key()),
  read:id=>request('workflow?id='+encodeURIComponent(id)),
  cancel:id=>request('workflow-cancel',{id}),
  export:id=>request('workflow-export?id='+encodeURIComponent(id)),
  list:()=>request('workflows'),
  inspect:settings=>request('model-preflight',settings),
  load:settings=>request('load',settings),
  hasPending:()=>!!storage.getItem(key()+':pending'),
  reset(){epoch++;},
 };
}
export function computeLabel(session,job){
 if(!session)return 'No managed session';
 if(session.observed_state==='terminated')return 'Compute stopped';
 if(session.desired_state==='terminated')return 'Stopping compute';
 if(session.observed_state==='failed')return 'Session failed';
 if(job?.progress?.phase==='loading')return 'Loading model';
 if(job?.status==='running')return 'Running';
 return session.observed_state==='ready'?'Ready':'Starting / '+session.observed_state;
}
