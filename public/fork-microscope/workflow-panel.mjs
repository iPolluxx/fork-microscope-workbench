import {saveJSON} from './download.mjs';
const host=document.getElementById('workflow-host');
if(host){
  host.style.gridColumn='1 / -1';
  host.innerHTML=`<details><summary>Automate a complete investigation · advanced</summary>
  <p>On connected compute: generate a response, scan outcomes, refine selected regions, and optionally inspect contrasting paths. This starts real model work. No VM is provisioned.</p>
  <p>Prefer one step at a time? Use Model → Question → Scan below. For automation, follow the <a href="https://github.com/iPolluxx/fork-microscope-workbench/blob/main/docs/INVESTIGATION-WORKFLOW.md">job guide</a> and <a href="https://github.com/iPolluxx/fork-microscope-workbench/blob/main/docs/CONFIGURATION.md">full settings reference</a>.</p>
  <label>Investigation configuration (.json)<input id="workflow-config" type="file" accept=".json,application/json"></label>
  <pre id="workflow-preview" style="white-space:pre-wrap"></pre>
  <label>Request ID · reuse to retry safely<input id="workflow-request" autocomplete="off" placeholder="my-investigation-001"></label>
  <button id="workflow-start" disabled>Start on connected compute</button>
  <p id="workflow-message" role="status"></p>
  <label>Saved investigations<select id="workflow-jobs"><option value="">Select a job</option></select></label>
  <button id="workflow-refresh">Refresh progress</button> <button id="workflow-cancel" disabled>Cancel investigation</button>
  <button id="workflow-resume" disabled>Resume interrupted job</button> <button id="workflow-export" disabled>Export investigation</button>
  <a id="workflow-open" hidden>Explore saved results →</a>
  <p>Time limits stop work at cooperative boundaries; they are not dollar billing caps. Token and sample limits reserve worst-case work before each phase. Adaptive regions are exploratory, not proven decision points.</p>
  </details>`;
  const $=id=>document.getElementById('workflow-'+id);let config=null,busy=false;
  const api=async(path,payload)=>{const r=await window.workerFetch('/api/live/'+path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const v=await r.json();if(!r.ok)throw Error(v.error||'Request failed');return v;};
  const message=e=>{$('message').textContent=e.message||String(e);};
  const selected=()=>$('jobs').value;
  async function refresh(id=selected()){
    const jobs=await api('workflows');$('jobs').replaceChildren(new Option('Select a job',''),...jobs.map(j=>new Option(`${j.status} · ${j.config.base.prompt.slice(0,65)} · ${j.id.slice(0,6)}`,j.id)));
    $('jobs').value=id;const j=jobs.find(j=>j.id===id);
    $('cancel').disabled=!j||j.status!=='running';$('resume').disabled=!j||!['interrupted','error'].includes(j.status);$('export').disabled=!j?.runs?.length;
    $('open').hidden=!j?.runs?.length;if(j?.runs?.length)$('open').href='/observatory.html?run='+encodeURIComponent(j.runs.at(-1));
    if(j){const r=j.reservations;$('message').textContent=`${j.status}: ${j.message}\n${j.runs.length} completed runs · ${j.lenses.length} readouts · reserved ${r.samples} samples / ${r.generated_tokens} generated tokens.\n${j.worker_job?.phase||''}`;}
  }
  $('config').onchange=async()=>{config=null;$('start').disabled=true;try{const f=$('config').files[0];if(!f)return;if(f.size>64000)throw Error('Configuration must be under 64 KB.');const c=JSON.parse(await f.text());if(!c.model||!c.base||!c.limits)throw Error('Choose a complete investigation configuration.');config=c;$('preview').textContent=`Model: ${c.model.model_id} @ ${c.model.revision}\nPrompt: ${c.base.prompt}\nLimits: ${c.limits.max_seconds}s, ${c.limits.max_samples} samples, ${c.limits.max_generated_tokens} generated tokens\nRefinement rounds: ${c.refinement?.max_rounds}\nLens: ${c.lens?.profile||'off'}`;$('start').disabled=false;}catch(e){message(e);}};
  $('start').onclick=async()=>{if(busy)return;busy=true;$('start').disabled=true;try{const id=$('request').value.trim();if(!id)throw Error('Enter a request ID. Keep it unchanged when retrying this request.');const job=await api('workflow-start',{request_id:id,config});await refresh(job.id);}catch(e){message(e);}finally{busy=false;$('start').disabled=!config;}};
  $('refresh').onclick=()=>refresh().catch(message);$('jobs').onchange=()=>refresh().catch(message);
  for(const action of ['cancel','resume'])$(action).onclick=async()=>{try{await api('workflow-'+action,{id:selected()});await refresh();}catch(e){message(e);}};
  $('export').onclick=async()=>{try{await saveJSON($('export'),'fork-investigation-'+selected()+'.json',()=>api('workflow-export?id='+encodeURIComponent(selected())));}catch(e){message(e);}};
  host.querySelector('details').addEventListener('toggle',()=>{if(host.querySelector('details').open)refresh().catch(message);});
  setInterval(()=>{if(!document.hidden&&host.querySelector('details').open&&selected()&&!busy)refresh().catch(message);},5000);
}
