// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — shared operation accounting.
import {startEvidenceOperation,budgetScopeNote} from './scoped-operation.mjs';
// Controlled activation interventions; saved evidence remains readable without a GPU.
import {completedDraws, outcomePair, pairFocus} from './lens-panel.mjs';
import {saveJSON} from './download.mjs';

export function patchSources(record, checkpoint) {
  const rows = [{value:'original', name:'Original response', selection:{type:'original'}, length:record?.base?.gen_ids?.length ?? 0}];
  for (const branch of record?.branches ?? []) {
    if (branch.t !== checkpoint) continue;
    (branch.draw_indices ?? []).forEach((draw, i) => {
      if (!Number.isInteger(draw) || !Array.isArray(branch.continuation_ids?.[i])) return;
      const o = branch.observations?.[i];
      rows.push({value:`draw:${draw}`, name:`Continuation ${draw} · ${o?.label ?? 'Other'} · ${o?.stop_reason ?? 'unknown stop'}`,
        selection:{type:'draw', checkpoint, draw_index:draw}, length:branch.t+1+branch.continuation_ids[i].length});
    });
  }
  return rows.filter(row => rows.filter(other=>other.value===row.value).length===1 && row.length>0);
}
export function patchDefaultPair(record, checkpoint, selectedPair) {
  const pair = outcomePair(completedDraws(record, checkpoint), selectedPair?.[0], selectedPair?.[1]);
  const focus = pairFocus(pair, checkpoint);
  if (pair.length!==2 || focus?.first==null) return null;
  return {donor:`draw:${pair[0].draw_index}`, recipient:`draw:${pair[1].draw_index}`, position:focus.first};
}

export function mountPatching(host, context) {
  host.classList.add('patching-panel');
  host.innerHTML = `<p>Test a specific internal state by copying it from one saved trajectory (the donor) into another (the recipient). Three fresh groups show the original recipient, a self-copy control, and the donor patch.</p>
    <p class="micro">This is an optional experiment after reading the responses. A changed outcome measures the effect of this intervention; it does not explain the entire original decision.</p>
    <details data-config><summary>Set up an activation test</summary>
      <p data-capability role="status">Connect compute to check model compatibility. Saved tests are available below.</p>
      <div class="patching-grid">
        <label>1. Donor trajectory<select data-field="donor"></select></label>
        <label>2. Recipient trajectory<select data-field="recipient"></select></label>
        <label>Donor response token<input data-field="donor_position" type="number" min="0" value="0"></label>
        <label>Recipient response token<input data-field="recipient_position" type="number" min="0" value="0"></label>
      </div>
      <p class="micro">The selected token is included in each prefix. Generation resumes immediately after it. Different future answers give identical earlier states until the prefix actually differs. Compare the words in the preview; matching token numbers alone do not establish equivalent meaning.</p>
      <label>Why these positions and layers?<textarea data-field="selection_rationale" rows="2" maxlength="2000" placeholder="State the specific hypothesis this replacement tests."></textarea></label>
      <details><summary>Sampling and layer settings</summary><div class="patching-grid">
        <label>Decoder layers (zero-based, up to four)<input data-field="layers" value="0"></label>
        <label>Fresh continuations per group<input data-field="samples" type="number" min="2" max="32" value="4"></label>
        <label>Maximum new tokens per continuation<input data-field="cont_max" type="number" min="1" max="512" value="128"></label>
        <label>Temperature<input data-field="temperature" type="number" min="0.05" max="2" step="0.05" value="1"></label>
        <label>Seed<input data-field="seed" type="number" min="0" max="2147483647" value="17"></label>
        <label>Time limit (seconds)<input data-field="max_seconds" type="number" min="1" max="1800" value="300"></label>
      </div><p class="micro">Selected layers are patched together, once during the initial prefix computation. This uses your existing GPU model; no additional model is downloaded. The time limit is checked between model forward calls, so a running GPU operation may finish first.</p></details>
      <div class="patching-actions"><button data-preview class="secondary" disabled>Preview exact test</button><button data-run class="primary" disabled>Run three comparison groups</button><button data-stop class="secondary" hidden>Stop this test</button></div>
      <pre data-plan class="recorded-text" hidden></pre>
    </details>
    <p data-status role="status"></p>
    <h3>Saved activation tests</h3><p class="micro">Viewing saved text, counts, and controls needs no GPU. Creating another test needs connected compute with the exact source model.</p>
    <div class="patching-actions"><select data-saved aria-label="Saved activation test"><option value="">Choose a saved test</option></select><button data-refresh class="secondary">Refresh saved</button><button data-download class="secondary" disabled>Download artifact</button></div><div data-output></div>`;
  const $=s=>host.querySelector(s), field=n=>$(`[data-field="${n}"]`);
  let sources=[], preview=null, revision=0, capabilityRevision=0, activeJob=null, artifact=null;
  const status=t=>$('[data-status]').textContent=t;
  const put=(tag,text,parent)=>{const e=document.createElement(tag);e.textContent=text;parent.append(e);return e;};
  async function api(route,body) {
    const response=await window.workerFetch('/api/live/'+route,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(15000)});
    const value=await response.json();if(!response.ok)throw new Error(value.error||'Activation test request failed.');return value;
  }
  function invalidate(){revision++;preview=null;$('[data-run]').disabled=true;$('[data-plan]').hidden=true;}
  host.addEventListener('input',invalidate);
  for(const name of ['donor','recipient'])field(name).addEventListener('change',()=>{invalidate();const selected=sources.find(s=>s.value===field(name).value);if(selected)field(name+'_position').max=selected.length-1;});
  function request(){
    const c=context();if(!c.result||!c.pass)throw new Error('Open a saved scan first.');
    const q={source_run_id:c.result.id,source_pass_id:c.pass.id};
    for(const arm of ['donor','recipient']){
      const source=sources.find(s=>s.value===field(arm).value);if(!source)throw new Error(`Choose a saved ${arm} trajectory.`);
      q[arm]={selection:source.selection,position:Number(field(arm+'_position').value)};
    }
    q.layers=field('layers').value.split(',').map(s=>s.trim()===''?null:Number(s.trim()));
    for(const key of ['samples','cont_max','temperature','seed','max_seconds'])q[key]=Number(field(key).value);
    q.selection_rationale=field('selection_rationale').value;
    return q;
  }
  $('[data-preview]').onclick=async()=>{
    const current=revision;try{
      const q=request(), plan=await api('patch-plan',q);if(current!==revision)return;
      preview=JSON.stringify(q);$('[data-plan]').hidden=false;
      $('[data-plan]').textContent=[`${plan.continuations} fresh continuations · at most ${plan.max_new_tokens.toLocaleString()} generated tokens · ${q.max_seconds}s cooperative limit`,
        ...['donor','recipient'].map(arm=>{const p=plan.prefixes[arm];return `${arm.toUpperCase()} · response token ${p.position} · input position ${p.absolute_position} · ${JSON.stringify(p.token_text)}\n${p.prefix_text}`;}),
        `Layers ${q.layers.join(', ')} patched together at the final prefix token.`,plan.interpretation].join('\n\n');
      $('[data-run]').disabled=!!activeJob;status('Review both prefixes. Running starts a separate controlled experiment on your compute.');
    }catch(e){status(e.message);}
  };
  function render(data){
    artifact=data;$('[data-download]').disabled=false;const out=$('[data-output]');out.replaceChildren();
    put('p',`${data.status} · ${data.id}`,out);put('p',data.request?.selection_rationale??'',out);
    const control=data.summary?.identity_control;
    put('p',control?`Self-copy control: ${control.exact_matches}/${control.draws_compared} continuations match the baseline exactly. ${control.passed?'Control passed for these continuations.':'Do not interpret a patch effect until this control is resolved.'}`:'Controls are not complete yet.',out);
    if(data.error)put('p',data.error,out);if(data.control_warning)put('p',data.control_warning,out);
    const table=put('table','',out);table.className='patching-results';const head=put('tr','',put('thead','',table));
    for(const title of ['Outcome','Baseline','Self-copy','Donor patch'])put('th',title,head);
    const observations=data.observations??[], labels=[...new Set(observations.map(o=>o.label))],tbody=put('tbody','',table);
    for(const label of labels){const row=put('tr','',tbody);put('th',label,row);for(const arm of ['baseline','identity','patched']){const group=observations.filter(o=>o.arm===arm),n=group.filter(o=>o.label===label).length;put('td',`${n}/${group.length}${group.length?' · '+(100*n/group.length).toFixed(0)+'%':''}`,row);}}
    put('p',`${data.summary?.capped??0} continuations reached the length cap. Other includes unresolved answers. These observed differences are exploratory; no significance test is implied.`,out);
    const chooser=put('select','',out);chooser.setAttribute('aria-label','Saved activation test continuation');
    observations.forEach((o,i)=>chooser.add(new Option(`${o.arm} · continuation ${o.draw+1} · ${o.label} · ${o.stop_reason}`,String(i))));
    const text=put('pre','',out);text.className='recorded-text';
    function show(){const o=observations[Number(chooser.value)];text.textContent=o?`RECIPIENT PREFIX\n${data.prefixes.recipient.prefix_text}\n\nFRESH CONTINUATION\n${o.continuation_text}`:'No completed continuations have been saved.';}
    chooser.onchange=show;show();
    const details=put('details','',out);put('summary','Method and exact source positions',details);
    for(const arm of ['donor','recipient']){const p=data.prefixes?.[arm];if(p)put('p',`${arm}: response token ${p.position}, input token ${p.absolute_position}, text ${JSON.stringify(p.token_text)}. Prefix hash ${p.prefix_sha256}`,details);}
    put('p',data.interpretation??'',details);
  }
  async function saved(){
    const current=revision,c=context(),list=await api('investigations');if(current!==revision)return;
    const entries=list.filter(x=>x.schema==='fork-activation-patch-v1'&&x.request?.source_run_id===c.result?.id&&x.request?.source_pass_id===c.pass?.id);
    $('[data-saved]').replaceChildren(new Option(entries.length?'Choose a saved test':'No saved activation tests for this pass',''),...entries.map(x=>new Option(`${x.status} · ${x.id.slice(0,8)}`,x.id)));
  }
  async function poll(){
    try{
      const {job}=await api('status');if(job.id!==activeJob){status('Compute is on another job. Refresh saved tests to recover your artifact.');activeJob=null;return;}
      status(`${job.phase} · ${job.status}`);if(job.status==='running'){setTimeout(poll,2000);return;}
      if(job.investigation_id){const data=await api('investigation?id='+encodeURIComponent(job.investigation_id)),c=context();if(data.request?.source_run_id===c.result?.id&&data.request?.source_pass_id===c.pass?.id)render(data);else status('The activation test finished for another run/pass. Return to its source to view the saved result.');}
      activeJob=null;await saved();
    }catch(e){status(`${e.message} Reconnect and refresh saved tests; the worker may still be running.`);activeJob=null;}
    finally{if(!activeJob){$('[data-stop]').hidden=true;$('[data-run]').disabled=true;}}
  }
  $('[data-run]').onclick=async()=>{try{const q=request();if(JSON.stringify(q)!==preview)throw new Error('Preview the current settings first.');$('[data-run]').disabled=true;const job=await startEvidenceOperation('patch',q);activeJob=job.job_id;$('[data-stop]').hidden=false;status('Activation test started.');poll();}catch(e){status(e.message);}};
  $('[data-stop]').onclick=async()=>{try{await api('stop',{job_id:activeJob});status('Stop requested. Completed continuations remain saved.');}catch(e){status(e.message);}};
  $('[data-refresh]').onclick=()=>saved().catch(e=>status(e.message));
  $('[data-saved]').onchange=async()=>{
    const identifier=$('[data-saved]').value,current=revision;
    artifact=null;$('[data-output]').replaceChildren();$('[data-download]').disabled=true;
    if(!identifier)return;
    try{const data=await api('investigation?id='+encodeURIComponent(identifier));if(current!==revision||$('[data-saved]').value!==identifier)return;render(data);}catch(e){if(current===revision&&$('[data-saved]').value===identifier)status(e.message);}
  };
  $('[data-download]').onclick=()=>{if(artifact)saveJSON($('[data-download]'),`activation-test-${artifact.id}.json`,async()=>artifact);};
  async function checkCapabilities(initial=false){
    const current=revision,call=++capabilityRevision;$('[data-preview]').disabled=true;
    try{const value=await api('patch-options');if(current!==revision||call!==capabilityRevision)return;$('[data-capability]').textContent=value.reason;$('[data-preview]').disabled=!value.available;if(initial&&value.available)field('layers').value=String(Math.max(0,Math.floor(value.layer_count/2)-1));}
    catch(e){if(current===revision&&call===capabilityRevision){$('[data-capability]').textContent=`Compute is unavailable. Saved tests can still be read from your evidence worker. ${e.message}`;$('[data-preview]').disabled=true;}}
  }
  $('[data-config]').addEventListener('toggle',()=>{if($('[data-config]').open)checkCapabilities();});
  return {refresh(){
    const c=context();invalidate();artifact=null;$('[data-output]').replaceChildren();$('[data-download]').disabled=true;
    if(!c.record)return;
    const checkpoint=c.position??0;sources=patchSources(c.record,checkpoint);
    for(const arm of ['donor','recipient'])field(arm).replaceChildren(...sources.map(s=>new Option(s.name,s.value)));
    const pair=patchDefaultPair(c.record,checkpoint,c.selectedPair);
    for(const arm of ['donor','recipient']){field(arm).value=pair?.[arm]??'original';const src=sources.find(s=>s.value===field(arm).value);field(arm+'_position').value=pair?.position??Math.min(checkpoint,Math.max(0,(src?.length??1)-1));field(arm+'_position').max=Math.max(0,(src?.length??1)-1);}
    field('selection_rationale').value='';
    status(pair?'A contrasting pair is selected at its first unequal token. Read both prefixes and describe your hypothesis before running.':'Select two saved trajectories with different prefixes. A test is optional; a run with no contrasting outcomes is valid.');
    const current=revision;
    checkCapabilities(true);
    saved().catch(e=>{if(current===revision)status(e.message);});
  }};
}
