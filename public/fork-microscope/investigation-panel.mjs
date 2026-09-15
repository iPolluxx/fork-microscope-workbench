// generated: Codex — explicit preview, execution and saved investigation inspection.
export function mountInvestigation(host, context) {
  host.innerHTML = `<h2>Investigate this region</h2>
    <p>Locate a candidate on the outcome map, inspect its text, then test a specific edit or collect internal vectors. These are separate experiments from the original scan.</p>
    <div class="investigation-controls">
      <label>Instrument<select data-field="kind"><option value="edit">Edit versus fresh control</option><option value="activation">Read-only activation capture · experimental</option></select></label>
      <div data-edit class="investigation-controls">
        <label>Start token<input data-field="start" type="number" min="0"></label>
        <label>End token (excluded)<input data-field="end" type="number" min="1"></label>
        <label>Draws per arm<input data-field="samples" type="number" min="2" max="128" value="10"></label>
        <label>New tokens per draw, maximum<input data-field="cont_max" type="number" min="1" max="4096" value="512"></label>
        <label>Temperature<input data-field="temperature" type="number" min="0.05" max="2" step="0.05" value="1"></label>
        <label>Seed<input data-field="seed" type="number" min="0" value="17"></label>
      </div>
      <div data-edit><label>Replacement text (empty deletes the span)<textarea data-field="replacement" rows="4"></textarea></label><button data-copy class="secondary">Use the draft above</button><p class="micro">Both arms discard the original suffix after the selected span. The control preserves the original span; the edit substitutes your text. New tokens are sampled from the full vocabulary. Changing text can also change token count and positions.</p></div>
      <div data-capture hidden class="investigation-controls"><label>Checkpoint positions (comma separated)<input data-field="positions" value="0"></label><label>Zero-based decoder layers (up to four)<input data-field="layers" value="0"></label><p class="micro">Captures the last prefix token at each selected decoder block, before the final normalization. Explicit mappings: Llama, Mistral, Qwen2, Gemma2 and Muse-Glimmer. Your model must pass the worker’s architecture checks. This is not a trained probe or a causal intervention.</p></div>
    </div>
    <div class="investigation-actions"><button data-preview class="secondary">Preview investigation</button><button data-run class="primary" disabled>Run on connected compute</button><button data-stop class="secondary" hidden>Stop this investigation</button></div>
    <p data-status role="status"></p><pre data-preview-text class="recorded-text" hidden></pre>
    <h3>Saved investigations</h3><div class="investigation-actions"><select data-saved aria-label="Saved investigation"></select><button data-refresh class="secondary">Refresh saved</button><button data-download class="secondary" disabled>Download artifact</button></div>
    <div data-output></div>`;
  const $ = s => host.querySelector(s), field = name => $(`[data-field="${name}"]`);
  let preview = null, activeJob = null, artifact = null, version = 0;
  const status = text => $('[data-status]').textContent = text;
  const put = (tag, text, parent) => {const e = document.createElement(tag); e.textContent = text; parent.append(e); return e;};
  async function api(route, body) {
    const r = await window.workerFetch('/api/live/' + route, {...(body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}), signal:AbortSignal.timeout(15000)});
    const data = await r.json(); if(!r.ok) throw new Error(data.error || 'Investigation request failed.'); return data;
  }
  function request() {
    const c = context(); if(!c.result || !c.pass) throw new Error('Open a saved run first.');
    const q = {source_run_id:c.result.id, source_pass_id:c.pass.id, kind:field('kind').value};
    if(q.kind === 'edit') {
      for(const name of ['start','end','samples','cont_max','temperature','seed']) q[name] = Number(field(name).value);
      q.replacement = field('replacement').value;
    } else for(const name of ['positions','layers']) q[name] = field(name).value.split(',').map(x => x.trim() === '' ? null : Number(x.trim()));
    return q;
  }
  function invalidate() {version++; preview=null; $('[data-run]').disabled=true; $('[data-preview-text]').hidden=true;}
  host.addEventListener('input', invalidate);
  field('kind').onchange = () => {invalidate(); host.querySelectorAll('[data-edit]').forEach(e => e.hidden = field('kind').value !== 'edit'); $('[data-capture]').hidden=field('kind').value!=='activation';};
  $('[data-copy]').onclick = () => {field('replacement').value=document.getElementById('replacement').value; invalidate();};
  $('[data-preview]').onclick = async () => {
    const v = version; try {
      const q=request(), p=await api('investigation-plan',q); if(v!==version) return;
      preview=JSON.stringify(q); $('[data-preview-text]').hidden=false;
      $('[data-preview-text]').textContent=q.kind==='edit'
        ? `${p.continuations} total continuations · at most ${p.max_new_tokens.toLocaleString()} new tokens\n\nORIGINAL SPAN\n${p.original_span}\n\nCONTROL PREFIX\n${p.prefix_previews.control}\n\nEDITED PREFIX\n${p.prefix_previews.edit}\n\n${p.interpretation}`
        : `${p.vectors} vectors from ${q.positions.length} forward passes\n${p.module_path}\n${p.position_semantics}\n\n${p.interpretation}`;
      $('[data-run]').disabled=!!activeJob; status('Preview ready. Running uses your attached compute.');
    } catch(e) {status(e.message);}
  };
  function render(data) {
    artifact=data; $('[data-download]').disabled=false;
    const out=$('[data-output]'); out.replaceChildren();
    put('p', `${data.request.kind} · ${data.status} · ${data.id}`,out);
    put('p',data.interpretation,out);
    if(data.error) put('p',data.error,out);
    if(data.observations) {
      const labels=[...new Set(data.observations.map(o=>o.label))];
      const table=put('table','',out), head=put('tr','',put('thead','',table));
      for(const label of ['Outcome','Control','Edited','Difference']) put('th',label,head);
      const body=put('tbody','',table);
      for(const label of labels) {
        const row=put('tr','',body); put('td',label,row); const rates=[];
        for(const arm of ['control','edit']) {const obs=data.observations.filter(o=>o.arm===arm), n=obs.filter(o=>o.label===label).length; rates.push(obs.length?n/obs.length:null); put('td',`${n}/${obs.length}${obs.length?' ('+(100*n/obs.length).toFixed(1)+'%)':''}`,row);}
        put('td',rates.every(x=>x!==null)?`${((rates[1]-rates[0])*100).toFixed(1)} percentage points`:'—',row);
      }
      const capped=data.observations.filter(o=>o.stop_reason==='length').length;
      put('p',`${capped} capped continuations. Counts include Other; a percentage difference is not a significance test. Small samples, text matching and selecting this region after viewing results limit the conclusion.`,out);
      const select=put('select','',out);select.setAttribute('aria-label','Investigation continuation');
      data.observations.forEach((o,i)=>select.add(new Option(`${o.arm} · draw ${o.draw+1} · ${o.label} · ${o.stop_reason}`,i)));
      const text=put('pre','',out);text.className='recorded-text';
      const show=()=>{const o=data.observations[Number(select.value)];text.textContent=o?`PRESERVED PREFIX\n${data.prefix_previews[o.arm]}\n\nNEW CONTINUATION\n${o.continuation_text}`:'No completed draws yet.';};select.onchange=show;show();
    }
    if(data.captures) {
      put('p','Vectors are exported in the artifact. Norms summarize magnitude; they do not label decisions or explain their causes.',out);
      const table=put('table','',out), head=put('tr','',put('thead','',table));
      for(const text of ['Checkpoint','Layer','Absolute position','Dimensions','Norm']) put('th',text,head);
      const body=put('tbody','',table);
      for(const c of data.captures) {const row=put('tr','',body);for(const x of [c.checkpoint,c.layer,c.absolute_position,c.vector.length,c.norm.toFixed(4)])put('td',String(x),row);}
    }
  }
  async function saved() {
    const c=context(), list=await api('investigations');
    const matching=list.filter(x=>x.request?.source_run_id===c.result?.id && ['edit','activation'].includes(x.request?.kind));
    $('[data-saved]').replaceChildren(new Option('Choose an investigation',''),...matching.map(x=>new Option(`${x.request.kind} · ${x.status} · ${x.id.slice(0,8)}`,x.id)));
  }
  async function poll() {
    try {
      const {job}=await api('status');
      if(job.id!==activeJob) {status('The worker is now on another job. Your saved artifact remains in the list.'); activeJob=null; await saved(); return;}
      status(`${job.phase} · ${job.status}`);
      if(job.status==='running') {setTimeout(poll,2000);return;}
      if(job.investigation_id) render(await api('investigation?id='+encodeURIComponent(job.investigation_id)));
      activeJob=null;await saved();
    } catch(e) {status(`${e.message} The worker may still be running. Reconnect and refresh saved investigations.`);activeJob=null;}
    finally {if(!activeJob) {$('[data-stop]').hidden=true;$('[data-run]').disabled=true;}}
  }
  $('[data-run]').onclick=async()=>{
    try {const q=request();if(JSON.stringify(q)!==preview)throw new Error('Preview the current settings first.');$('[data-run]').disabled=true;const job=await api('investigate',q); activeJob=job.job_id; $('[data-stop]').hidden=false; status('Investigation started.');poll();}
    catch(e) {status(e.message);}
  };
  $('[data-stop]').onclick=async()=>{try {await api('stop',{job_id:activeJob});status('Stop requested. Completed draws or vectors are saved.');}catch(e){status(e.message);}};
  $('[data-refresh]').onclick=()=>saved().catch(e=>status(e.message));
  $('[data-saved]').onchange=async()=>{if(!$('[data-saved]').value)return;try{render(await api('investigation?id='+encodeURIComponent($('[data-saved]').value)));}catch(e){status(e.message);}};
  $('[data-download]').onclick=()=>{if(!artifact)return;const url=URL.createObjectURL(new Blob([JSON.stringify(artifact,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`investigation-${artifact.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return {refresh() {
    const c=context();if(!c.record)return;invalidate();const t=c.position??0,end=Math.min(t+32,c.record.base.gen_ids.length);
    field('start').value=t;field('end').value=end;field('positions').value=t;
    field('replacement').value=(c.record.base.token_texts??[]).slice(t,end).join('');
    saved().catch(e=>status(e.message));
  }};
}
