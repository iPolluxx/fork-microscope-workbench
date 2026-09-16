// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — persistent investigation context.
import {getSelection,setSelection,selectionURL,subscribeSelection} from './selection.mjs';
import {initOfflineEvidence,getOfflineInvestigation,isOfflineEvidence,clearOfflineEvidence} from './offline-evidence.mjs';
await initOfflineEvidence();
let context={},runtime=null;
const el=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;};
const shell=document.createElement('section');shell.className='investigation-context';shell.setAttribute('aria-label','Current investigation');
const title=el('strong','One investigation, from question to evidence'),facts=el('p','Saved evidence is available without compute.','context-facts'),trail=el('nav');trail.setAttribute('aria-label','Evidence breadcrumb');
const notice=el('p','','context-notice');notice.setAttribute('role','status');shell.append(title,facts,trail,notice);
const header=document.querySelector('.app-shell');if(header)header.append(shell);
function render(){
 const s=getSelection(),offline=getOfflineInvestigation();
 title.textContent=context.name||offline?.context?.name||'One investigation, from question to evidence';
 const model=context.model||offline?.context?.model?.model_id||runtime?.model?.model_id||'Model not selected';
 const limits=context.limits||offline?.limits||offline?.config?.limits;
 facts.textContent=`${model} · ${isOfflineEvidence()?'Saved browser evidence · compute actions unavailable':runtime?.model?'Compute connected':'Compute not connected'} · ${limits?`Budget: ${limits.max_generated_tokens??'unknown'} generated tokens; ${limits.max_samples??'unknown'} samples`:'Budget not recorded'}`;
 trail.replaceChildren();
 const add=(label,area,patch={})=>{if(trail.children.length)trail.append(el('span',' › '));const a=el('a',label);a.href=selectionURL({...s,...patch},area,location.href);trail.append(a);};
 add('Investigation','setup');if(s.response_id||s.run_id)add('Response','explore');if(s.run_id)add(`Scan ${s.run_id.slice(0,8)}`,'explore');if(s.pass_id)add(`Pass: ${s.pass_id}`,'explore');if(s.checkpoint!==null)add(`Checkpoint ${s.checkpoint}`,'explore');if(s.continuation)add(`Continuation ${s.continuation.draw_index+1}`,'inspect');else if(s.pair)add(`Continuations ${s.pair.draw_indices.map(x=>x+1).join(' & ')}`,'inspect');if(s.inspection_id)add('Saved inspection','inspect');
 const nav=document.querySelector('.app-nav');if(nav){nav.replaceChildren();for(const [area,label]of [['setup','Setup'],['explore','Explore'],['inspect','Inspect & Test']]){const a=el('a',label);a.href=selectionURL(s,area,location.href);if(area===s.area)a.setAttribute('aria-current','page');nav.append(a);}const help=el('a','Guide');help.href='/guide.html';nav.append(help);}
 if(isOfflineEvidence()) {notice.replaceChildren(el('span','Browsing saved evidence. To run new work, reconnect and transfer this investigation to compatible compute. '));const b=el('button','Use connected compute','text-button');b.onclick=()=>{clearOfflineEvidence();const url=new URL(selectionURL({...s,return_area:s.area},'setup',location.href),location.href);url.searchParams.delete('evidence');url.searchParams.delete('demo');location.href=url;};notice.append(b);const transfer=el('button','Transfer to connected compute','text-button');transfer.onclick=async()=>{transfer.disabled=true;transfer.textContent='Transferring saved investigation…';try{const provider=await import('./offline-evidence.mjs');const imported=await provider.transferOfflineEvidenceToWorker();const selection={...getSelection(),investigation_id:imported.investigation_id??null,run_id:imported.run_id||getSelection().run_id};const url=new URL(selectionURL(selection,'setup',location.href),location.href);url.searchParams.delete('evidence');url.searchParams.delete('demo');location.href=url;}catch(e){transfer.disabled=false;transfer.textContent='Transfer to connected compute';notice.append(el('span',' '+e.message));}};notice.append(transfer);}
}
export function updateInvestigationContext(value){context={...context,...value};render();}
export function contextNotice(message){notice.textContent=message;}
subscribeSelection(render);render();
try{if(!isOfflineEvidence()){const r=await(window.workerFetch||fetch)('/api/live/status');if(r.ok)runtime=await r.json();render();}}catch{}
window.addEventListener('fork-investigation-record',e=>{const r=e.detail;updateInvestigationContext({name:r.context?.name,limits:r.limits||r.config?.limits,model:r.context?.model?.model_id});});
