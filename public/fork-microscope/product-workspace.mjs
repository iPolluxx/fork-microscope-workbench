import {initOfflineEvidence,listBrowserInvestigations} from './offline-evidence.mjs';
import {createWorkerExecution} from './execution-adapter.mjs';
const view=document.body.dataset.productView;
const params=new URL(location.href).searchParams;
if(view==='question'&&!params.has('new')&&!params.has('job')&&['investigation','run','set','prompt'].some(k=>params.has(k))){location.replace('/advanced.html'+location.search+location.hash);}

const mount=document.getElementById('hosted-mount');
try {
 const response=await fetch('/hosted-fragment.html');if(!response.ok)throw Error('Workspace controls could not load. Refresh to retry.');
 // Trusted same-origin application template, never imported evidence HTML.
 mount.innerHTML=await response.text();
 const root=mount.querySelector('#hosted-root');root.dataset.view=view;
 for(const section of root.querySelectorAll('[data-view]'))section.hidden=section.dataset.view!==view && !(section.dataset.view==='account'&&view!=='question');
 if(view==='question')root.querySelector('#workspace').hidden=false;
 if(view!=='question')root.querySelector('#setup-heading').closest('section').hidden=true;
 if(view==='library')root.querySelector('#session-panel').dataset.library=true;
 await import('./hosted-ui.mjs');
} catch(e){mount.textContent=e.message;}
document.getElementById('pair-worker')?.addEventListener('click',()=>window.openWorkerPairing?.());
if(view==='library'){
 await import('./evidence-import.mjs');await initOfflineEvidence();
 const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
 async function browserLibrary(){const list=document.getElementById('browser-investigations');list.replaceChildren();try{const items=await listBrowserInvestigations();for(const item of items){const li=node('li',''),a=node('a',(item.name&&!/^Investigation [a-f0-9]+$/.test(item.name)?item.name:item.question?.slice(0,100))||'Saved investigation');a.href='/observatory.html?evidence=local&investigation='+encodeURIComponent(item.id)+'&run='+encodeURIComponent(item.run_id||'');li.append(a,node('p',`${item.status} · ${item.runs} related scans · Available here`));list.append(li);}if(!items.length)list.append(node('li','No saved bundles in this browser. Import a file or try the demo.'));}catch(e){list.append(node('li',e.message));}}
 const worker=createWorkerExecution();let requestVersion=0;
 async function workerLibrary(){const version=++requestVersion,list=document.getElementById('machine-investigations'),status=document.getElementById('machine-library-status');list.replaceChildren();try{const records=await worker.list();if(version!==requestVersion)return;status.textContent='Available while this machine is connected. Export to keep a portable copy.';for(const item of Array.isArray(records)?records:records.investigations||[]){const li=node('li',''),a=node('a',item.context?.name||item.config?.base?.prompt?.slice(0,100)||item.id);a.href=item.runs?.length?'/observatory.html?evidence=worker&investigation='+encodeURIComponent(item.id)+'&run='+encodeURIComponent(item.runs.at(-1)):'/live.html?job='+encodeURIComponent(item.id);li.append(a,node('p',`${item.status} · ${item.runs?.length||0} scans · Connected machine`));list.append(li);}if(!list.children.length)list.append(node('li','No investigations on this machine.'));}catch{if(version===requestVersion)status.textContent='Machine disconnected. Your browser evidence above remains available.';}}
 window.addEventListener('worker-connection-change',()=>{worker.reset();workerLibrary();});window.addEventListener('offline-evidence-change',browserLibrary);window.addEventListener('storage',browserLibrary);await browserLibrary();await workerLibrary();
}

if(view==='compute'){try{const back=new URL(sessionStorage.getItem('fork-compute-return')||'/live.html',location.origin);if(back.origin===location.origin&&['/live.html','/observatory.html','/advanced.html','/workspace.html','/compare.html','/prompt-sets.html'].includes(back.pathname))document.getElementById('return-investigation').href=back.pathname+back.search+back.hash;}catch{}}
