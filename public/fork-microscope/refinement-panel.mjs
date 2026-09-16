import {getSelection,selectionURL} from './selection.mjs';
// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — shared operation accounting.
import {startEvidenceOperation,budgetScopeNote} from './scoped-operation.mjs';
import {jobProgress} from './job-progress.mjs';
function sameModel(left, right) {
  if(!left || !right)return false;
  const a=left.resolved_revision,b=right.resolved_revision;
  if(typeof a==='string' && /^local-sha256:[a-f0-9]{64}$/.test(a))return a===b && (!left.source_type||left.source_type==='local') && (!right.source_type||right.source_type==='local');
  if(left.source_type==='local'||right.source_type==='local'||left.identity_kind==='local_content_sha256'||right.identity_kind==='local_content_sha256'||String(a).startsWith('local-sha256:')||String(b).startsWith('local-sha256:'))return false;
  return !!a && a===b && left.model_id===right.model_id;
}
const whole = (value, low, high) => Number.isInteger(value) && value >= low && value <= high;
const format = value => new Intl.NumberFormat('en-US', {maximumFractionDigits: 0}).format(value);

export function mountRefinement(host, getContext) {
  host.innerHTML = `<section class="refinement-panel" aria-label="Refinement settings">
    <p class="ref-intro">Keep this exact original response. Collect fresh continuations inside a region, or measure a larger reference for comparison.</p><div class="ref-actions"><button id="ref-detail" class="secondary" type="button">Refine interval</button><button id="ref-reference" class="secondary" type="button">Reference preset · 100 per token</button></div>
    <label class="ref-region-label">Region <select id="ref-region"></select></label>
    <div class="ref-grid">
      <label>From token<input id="ref-start" type="number" min="0" step="1"></label>
      <label>Through token<input id="ref-end" type="number" min="1" step="1"></label>
      <label>Every N tokens<input id="ref-stride" type="number" min="1" max="128" step="1" value="8"><small>Smaller = closer checkpoints</small></label>
      <label>Continuations per point<input id="ref-samples" type="number" min="5" max="512" step="1" value="20"><small>More = less sampling noise</small></label>
    </div>
    <details class="ref-advanced"><summary>Length and reproducibility</summary><div class="ref-grid ref-grid-two">
      <label>New-token limit per continuation<input id="ref-cont_max" type="number" min="1" max="4096" step="1"><small>Keep the original limit for comparable results.</small></label>
      <label>Sampling seed<input id="ref-seed" type="number" min="0" max="2147483647" step="1"><small>Recorded with this new run.</small></label>
    </div></details>
    <div class="ref-budget"><strong id="ref-preview"></strong><p id="ref-runtime"></p><details><summary>Checkpoint positions and token allowance</summary><p id="ref-positions"></p></details></div>
    <p id="ref-quality" class="ref-quality"></p>
    <div class="ref-actions"><button id="ref-run" class="primary" disabled>Run refinement</button><button id="ref-export" class="secondary">Export job for a GPU worker</button><button id="ref-stop" class="secondary" hidden>Stop this refinement</button></div>
    <p id="ref-status" role="status" class="ref-status"></p><p id="ref-notice" role="status" class="ref-status" hidden></p>
    <a id="ref-connect" href="/live.html">Set up the source model →</a>
  </section>`;
  const scope=document.createElement('p');scope.className='micro';scope.textContent=budgetScopeNote();host.append(scope);
  const $ = id => host.querySelector('#ref-' + id);
  const fields = ['start', 'end', 'stride', 'samples', 'cont_max', 'seed'];
  let worker = null, job = null, jobSource = null, pending = false, timer = null, serial = 0;

  function request() {
    const {result, pass} = getContext();
    return {source_run_id: result.id, source_pass_id: pass.id,
      ...Object.fromEntries(fields.map(k => [k, $(k).value === '' ? NaN : Number($(k).value)]))};
  }
  function notice(text = '', link = null) {
    $('notice').hidden = !text;
    $('notice').textContent = text;
    if (link) $('notice').append(' ', link);
  }
  function preview() {
    const p = request(), {record, result, pass} = getContext();
    const valid = whole(p.start, 0, result.base.length - 1) && whole(p.end, p.start + 1, result.base.length - 1)
      && whole(p.stride, 1, 128) && whole(p.samples, 5, 512) && whole(p.cont_max, 1, 4096)
      && whole(p.seed, 0, 2147483647);
    let positions = [];
    if (valid) {
      for (let t = p.start; t <= p.end; t += p.stride) positions.push(t);
      if (positions.at(-1) !== p.end) positions.push(p.end);
    }
    const allowed = valid && positions.length <= 4096;
    $('export').disabled = !allowed || pending;
    const count = positions.length * p.samples;
    $('preview').textContent = allowed
      ? `${format(positions.length)} checkpoints × ${format(p.samples)} continuations = ${format(count)} new continuations`
      : 'Choose a valid interval and sampling settings (up to 4,096 checkpoints).';
    $('positions').textContent = allowed
      ? `${positions.join(', ')}. Up to ${format(count * p.cont_max)} newly generated tokens. Both endpoints are included.` : '';
    const measured = result.measured?.[pass.id];
    $('runtime').textContent = '';
    if (allowed && measured?.continuations > 0 && measured.wall_seconds > 0 && p.cont_max === result.settings.cont_max) {
      const minutes = measured.wall_seconds / measured.continuations * count / 60;
      $('runtime').textContent = `About ${minutes < 60 ? Math.max(1, Math.round(minutes)) + ' minutes' : (minutes / 60).toFixed(1) + ' hours'} at the source run’s average speed. A rough projection for the same hardware; model loading and replay are extra.`;
    } else if (allowed) $('runtime').textContent = 'Runtime depends on the model, hardware and generated lengths. The token allowance is a maximum, not a time estimate.';
    const obs = (record.branches ?? []).filter(b => b.t >= p.start && b.t <= p.end).flatMap(b => b.observations ?? []);
    const caps = obs.filter(o => o.stop_reason === 'length').length;
    $('quality').textContent = `${caps} of ${obs.length} recorded continuations in this source region hit the length limit. ${p.cont_max !== result.settings.cont_max ? 'The changed token limit affects comparability. ' : ''}A fitted change is a place to investigate, not a significance test.`;
    const matches = sameModel(worker?.model,result.model);
    $('run').disabled = !allowed || pending || !matches || worker?.job?.status === 'running';
    $('connect').hidden = !!matches;
    const setup=new URL(selectionURL({...getSelection(),return_area:'explore'},'setup',location.href),location.href);setup.searchParams.set('source',result.id);$('connect').href=setup;
    return allowed;
  }
  async function api(path, payload) {
    let response;
    try {
      response = await window.workerFetch('/api/live/' + path, {...(payload === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      }), signal: AbortSignal.timeout(15000)});
    } catch { throw Error('Cannot reach this worker. Reconnect it and try again. Saved evidence remains on disk.'); }
    let value;
    try { value = await response.json(); } catch { throw Error('The worker returned an unreadable response. Reconnect and try again.'); }
    if (!response.ok) throw Error(value.error || 'The worker could not complete this request.');
    return value;
  }
  async function status() {
    const current = ++serial;
    try {
      const response = await api('status');
      if (current !== serial) return;
      worker = response;
      preview();
      const same = job && worker.job.id === job;
      $('stop').hidden = !(same && worker.job.status === 'running');
      if (same) {
        const otherSource = jobSource && jobSource !== getContext().result.id;
        $('status').textContent = `${otherSource ? 'Refinement of another saved run · ' : ''}${worker.job.phase} · ${jobProgress(worker.job)}`;
        if (otherSource) {
          const source = document.createElement('a');
          source.href=selectionURL({...getSelection(),run_id:jobSource},'explore',location.href);
          source.textContent = 'View its source'; $('status').append(' · ', source);
        }
        if (worker.job.status === 'complete' && worker.job.result_id) {
          const link = document.createElement('a');
          link.href=selectionURL({...getSelection(),run_id:worker.job.result_id,pass_id:null,checkpoint:null,pair:null,continuation:null,inspection_id:null},'explore',location.href);
          link.textContent = 'Open refinement →';
          notice('Run saved with its parent response.', link);
          const compareLink = document.createElement('a'); compareLink.href='/compare.html?left='+encodeURIComponent(jobSource)+'&right='+encodeURIComponent(worker.job.result_id); compareLink.textContent='Compare with source →'; $('notice').append(' · ',compareLink);
          job = null;
        }
        return;
      }
      const {result} = getContext();
      const matches = sameModel(worker.model,result.model);
      $('status').textContent = worker.job.status === 'running' ? `Worker busy: ${worker.job.phase}` : matches
        ? 'Source model is ready. Run refinement restores the saved response and starts generation.'
        : 'No matching source model attached. Set it up here, or export this job for another GPU worker.';
    } catch (error) {
      if (current !== serial) return;
      worker = null;
      preview();
      $('status').textContent = error.message;
      $('stop').hidden = true;
    }
  }
  for (const field of fields) $(field).oninput = () => {notice(); preview();};
  $('detail').onclick = () => {$('stride').value = Math.max(1, Math.min(8, Math.floor((Number($('end').value)-Number($('start').value))/4))); $('samples').value=20; notice();preview();};
  $('reference').onclick = () => {$('stride').value=1; $('samples').value=100; notice('Reference settings filled. Review the region and continuation budget before running. This is a finite reference, not ground truth.');preview();};
  $('region').onchange = () => {
    const [a, b] = $('region').value.split(':').map(Number);
    if (Number.isFinite(a) && Number.isFinite(b)) select(a, b);
  };
  $('export').onclick = async () => {
    if (!preview() || pending) return;
    pending = true; preview();
    try {
      const plan = await api('refinement-plan', request());
      const url = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2)], {type: 'application/json'}));
      const link = document.createElement('a'); link.href = url;
      link.download = `refine-${plan.lineage.source_run_id}-${plan.lineage.interval.join('-')}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice('Job downloaded. On the GPU worker, run: fork-microscope refine <downloaded-file.json>. Exporting starts no compute.');
    } catch (error) { notice(error.message); }
    finally { pending = false; preview(); }
  };
  $('run').onclick = async () => {
    if ($('run').disabled || pending) return;
    const settings = request();
    pending = true; preview(); notice();
    try { const response = await startEvidenceOperation('refine', settings); job = response.job_id; jobSource = settings.source_run_id; }
    catch (error) { notice(error.message); }
    finally { pending = false; await status(); }
  };
  $('stop').onclick = async () => {
    if (pending) return;
    pending = true; $('stop').disabled = true;
    try {
      // Verify ownership immediately before requesting worker cancellation.
      const state = await api('status');
      if (state.job.id !== job || state.job.status !== 'running') throw Error('This refinement is no longer the active job.');
      await api('stop', {job_id: job}); notice('Stopping at the next sampling boundary. Partial records stay on the worker.');
    } catch (error) { notice(error.message); }
    finally { pending = false; $('stop').disabled = false; await status(); }
  };
  function select(a, b) {
    $('start').value = a; $('end').value = b; notice();
    $('stride').value = Math.max(1, Math.min(Number($('stride').value) || 8, Math.floor((b - a) / 4)));
    const option = Array.from($('region').options).find(o => o.value === `${a}:${b}`);
    $('region').value = option?.value ?? '';
    preview();
  }
  function refresh() {
    const {result, evidence, pass} = getContext();
    const boundaries = evidence.segmentationEnabled ? (pass.curve?.boundaries ?? []) : [];
    $('region').replaceChildren(new Option('Custom interval', ''), new Option('Entire original response', `0:${result.base.length - 1}`), ...boundaries.map(b => new Option(`Candidate ${b.left}–${b.right}`, `${b.left}:${b.right}`)));
    $('cont_max').value = result.settings.cont_max;
    $('seed').value = crypto.getRandomValues(new Uint32Array(1))[0] & 2147483647;
    const a = boundaries[0]?.left ?? evidence.observed[0]?.t ?? 0;
    const b = boundaries[0]?.right ?? evidence.observed.at(-1)?.t ?? Math.max(1, result.base.length - 1);
    // Refinement should actually add an interior checkpoint to narrow intervals.
    $('stride').value = Math.max(1, Math.min(8, Math.floor((b - a) / 4)));
    select(a, b);
    clearInterval(timer); timer = setInterval(status, 5000); status();
  }
  addEventListener('pagehide', () => {clearInterval(timer); serial++;});
  return {refresh, select};
}
