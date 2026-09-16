// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — manual evidence actions use the saved investigation ledger.
import {getSelection} from './selection.mjs';
import {retryableRequest} from './request-retry.mjs';
async function api(route,body){const r=await window.workerFetch('/api/live/'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.error||'Operation failed.');return d;}
export async function startEvidenceOperation(action,payload){
 const id=getSelection().investigation_id;
 if(!id)return api(action,payload); // Historic runs remain usable; UI labels these unscoped below.
 let record=await retryableRequest(api,'workflow-operation',{id,action,payload});
 for(let i=0;i<100;i++){
  if(record.pending?.job_id){return {job_id:record.pending.job_id,investigation_id:id};}
  if(record.status!=='running'){throw new Error(record.message||'Operation ended before a model job started. Refresh saved artifacts.');}
  await new Promise(resolve=>setTimeout(resolve,100));record=await api('workflow?id='+encodeURIComponent(id));
 }
 throw new Error('The operation is reserved and still starting. Refresh the investigation before retrying.');
}
export function budgetScopeNote(){return getSelection().investigation_id?'This action uses the selected investigation’s remaining allowance.':'Historical scan: this action is outside a saved investigation budget. Its own preview limits still apply.';}
