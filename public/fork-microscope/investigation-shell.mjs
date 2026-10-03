// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — persistent investigation context.
import {getSelection,setSelection,selectionURL,subscribeSelection} from './selection.mjs';
import {initOfflineEvidence,getOfflineInvestigation,isOfflineEvidence,clearOfflineEvidence} from './offline-evidence.mjs';
await initOfflineEvidence();
let context={},runtime=null;
const el=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;};
const shell=document.createElement('section');shell.className='investigation-context';shell.setAttribute('aria-label','Current investigation');
const title=el('strong','One investigation, from question to evidence'),facts=el('p','Saved evidence is available without compute.','context-facts'),trail=el('nav');trail.setAttribute('aria-label','Evidence breadcrumb');
const notice=el('p','','context-notice');notice.setAttribute('role','status');shell.append(title,facts,trail,notice);
const header=document.querySelector('.app-shell');if(header && ['observatory.html','compare.html','advanced.html'].some(p=>location.pathname.endsWith(p)))header.append(shell);
function render(){
 const s=getSelection(),offline=getOfflineInvestigation();
 title.textContent=context.name||offline?.context?.name||'One investigation, from question to evidence';
 const model=context.model||offline?.context?.model?.model_id||runtime?.model?.model_id||'Model not selected';
 const limits=context.limits||offline?.limits||offline?.config?.limits;
 facts.textContent=`${model} · ${isOfflineEvidence()?'Saved evidence · no GPU needed':runtime?.model?'Compute connected':'Compute not connected'} · ${limits?`Budget: ${limits.max_generated_tokens??'unknown'} generated tokens; ${limits.max_samples??'unknown'} samples`:'Budget not recorded'}`;
 trail.replaceChildren();
 const add=(label,area,patch={})=>{if(trail.children.length)trail.append(el('span',' › '));const a=el('a',label);a.href=selectionURL({...s,...patch},area,location.href);trail.append(a);};
 add('Investigation','setup');if(s.response_id||s.run_id)add('Response','explore');if(s.run_id)add(`Scan ${s.run_id.slice(0,8)}`,'explore');if(s.pass_id)add(`Pass: ${s.pass_id}`,'explore');if(s.checkpoint!==null)add(`Checkpoint ${s.checkpoint}`,'explore');if(s.continuation)add(`Continuation ${s.continuation.draw_index+1}`,'inspect');else if(s.pair)add(`Continuations ${s.pair.draw_indices.map(x=>x+1).join(' & ')}`,'inspect');if(s.inspection_id)add('Saved inspection','inspect');
 // Global selection is determined by the route, never the last investigation stage.
 const nav=document.querySelector('.app-nav');if(nav){const route=location.pathname;const active=route.endsWith('guide.html')?'Guide':route.endsWith('hosted.html')?'Compute':'Investigations';for(const a of nav.querySelectorAll('a')){if(a.getAttribute('href')===({'Guide':'/guide.html','Compute':'/hosted.html','Investigations':'/workspace.html'}[active]))a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}}

 if(isOfflineEvidence()) {notice.replaceChildren(el('span','Browsing saved evidence. New sampling and inspections require compatible compute. '));const a=el('a','Manage compute');a.href='/hosted.html';notice.append(a);const details=el('details'),summary=el('summary','Continue this investigation on my machine');details.append(summary,el('p','Copies this saved investigation to your connected worker. Loading its model and starting new work remain separate actions. Managed hosted sessions do not expose every interactive inspection operation.'));const transfer=el('button','Copy evidence to connected machine');transfer.onclick=async()=>{transfer.disabled=true;try{const provider=await import('./offline-evidence.mjs');const result=await provider.transferOfflineEvidenceToWorker();const url=new URL(selectionURL({...s,investigation_id:result.investigation_id||s.investigation_id,run_id:result.run_id||s.run_id},s.area,location.href),location.href);url.searchParams.set('evidence','worker');location.href=url;}catch(e){details.append(el('p',e.message));transfer.disabled=false;}};details.append(transfer);notice.append(details);}

}
export function updateInvestigationContext(value){context={...context,...value};render();}
export function contextNotice(message){notice.textContent=message;}
subscribeSelection(render);render();
try{if(!isOfflineEvidence()){const r=await(window.workerFetch||fetch)('/api/live/status');if(r.ok)runtime=await r.json();render();}}catch{}
window.addEventListener('fork-investigation-record',e=>{const r=e.detail;updateInvestigationContext({name:r.context?.name,limits:r.limits||r.config?.limits,model:r.context?.model?.model_id});});
