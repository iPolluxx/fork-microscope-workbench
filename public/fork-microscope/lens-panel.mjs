// generated: Codex — exact-token, read-only Jacobian lens exploration.
import {saveJSON} from './download.mjs';
export function parseLensLayers(value) {
  const parts = String(value).split(',').map(x => x.trim());
  if (!parts.length || parts.length > 8 || parts.some(x => !/^\d+$/.test(x))) throw new Error('Enter one to eight zero-based layer numbers, separated by commas.');
  const layers = parts.map(Number);
  if (layers.some(x => !Number.isSafeInteger(x)) || new Set(layers).size !== layers.length) throw new Error('Layer numbers must be distinct non-negative integers.');
  return layers;
}
export function lensDraws(record, checkpoint) {
  return (record?.branches ?? []).filter(b => b.t === checkpoint).flatMap(b => (b.observations ?? []).map((o, i) => ({
    checkpoint: b.t, draw_index: b.draw_indices?.[i] ?? i, label: o.label ?? 'Other',
    length: b.t + 1 + (b.continuation_ids?.[i]?.length ?? o.continuation_ids?.length ?? 0),
  }))).sort((a, b) => a.draw_index - b.draw_index);
}
// Only pairs with exact saved IDs, known completion and distinct measured labels.
export function completedDraws(record, checkpoint) {
  const rows = (record?.branches ?? []).filter(b => b.t === checkpoint).flatMap(b =>
    (b.observations ?? []).map((o, i) => ({...o, checkpoint, draw_index: b.draw_indices?.[i],
      ids: Array.isArray(b.continuation_ids?.[i]) ? [b.tok_id, ...b.continuation_ids[i]] : null,
      length: b.t + 1 + (b.continuation_ids?.[i]?.length ?? 0)})));
  return rows.filter(o => Number.isInteger(o.draw_index) && o.ids && o.ids.every(Number.isInteger) &&
    rows.filter(x => x.draw_index === o.draw_index).length === 1 && o.stop_reason && o.stop_reason !== 'length' &&
    o.label && o.label !== 'Other').sort((a, b) => a.draw_index - b.draw_index);
}
export function outcomePair(rows, first, second) {
  const a = rows.find(o => o.draw_index === first) ?? rows[0];
  const alternatives = rows.filter(o => o.label !== a?.label);
  const b = alternatives.find(o => o.draw_index === second) ?? alternatives[0];
  return a && b ? [a, b] : [];
}
// The actual first unequal saved token may come after the sampled checkpoint.
export function pairFocus(pair, checkpoint) {
  if (pair.length !== 2) return null;
  const [a,b] = pair.map(row => row.ids), limit = Math.min(a.length,b.length);
  let offset = 0;
  while (offset < limit && a[offset] === b[offset]) offset++;
  if (offset === limit) return {first: null, prefixOnly: a.length !== b.length};
  const first = checkpoint + offset;
  return {first, start: Math.max(0, first-1), end: Math.min(checkpoint+limit-1, first+2),
    baselineSpace: first === 0 ? 'prompt' : 'response', baselineIndex: first-1};
}
export function explorationLayers(count) {
  if (!Number.isInteger(count) || count < 2) return [0];
  return [...new Set([.25,.4,.55,.7,.85].map(f => Math.min(count-2, Math.floor((count-1)*f))))];
}
export function expandLensRange(start,end,size) {
  if (!Number.isInteger(size) || size < 1) throw new Error('Choose a saved trajectory first.');
  if (end-start+1 >= 64) throw new Error('This view already covers the maximum 64 tokens. Move the window to inspect another region.');
  const room=64-(end-start+1), left=start-Math.min(2,start,room);
  const right=end+Math.min(2,size-1-end,room-(start-left));
  return {start:left,end:right};
}
export function denserLensLayers(selected,count) {
  const result = [...selected].sort((a,b)=>a-b);
  if (!Number.isInteger(count) || count < 2) throw new Error('Connect the model to discover its available layers.');
  while(result.length < Math.min(8,count-1)) {
    const gaps = [[-1,result[0]],...result.slice(1).map((b,i)=>[result[i],b]),[result.at(-1),count-1]]
      .filter(([a,b])=>b-a>1).sort((x,y)=>(y[1]-y[0])-(x[1]-x[0]));
    if(!gaps.length) break;
    result.push(Math.floor((gaps[0][0]+gaps[0][1])/2)); result.sort((a,b)=>a-b);
  }
  return result;
}
export function lensMatrix(cells, arm) {
  const selected = cells.filter(c => c.arm === arm);
  return {
    layers: [...new Set(selected.map(c => c.layer))].sort((a, b) => a - b),
    positions: [...new Set(selected.map(c => c.index))].sort((a, b) => a - b),
    byKey: new Map(selected.map(c => [`${c.index}/${c.layer}`, c])),
  };
}
const PROFILES = [
  {id: 'muse_glimmer', label: 'Muse-Glimmer-30B · community n900 lens', model_id: 'meta-models/Muse-Glimmer-30B'},
  {id: 'qwen35_4b', label: 'Qwen3.5-4B · published lens', model_id: 'Qwen/Qwen3.5-4B'},
  {id: 'qwen36_27b', label: 'Qwen3.6-27B · published lens', model_id: 'Qwen/Qwen3.6-27B'},
  {id: 'local', label: 'Local lens checkpoint · worker file'},
];
const armTitle = arm => ({original: 'Original response', draw: 'Sampled continuation', draw_a: 'Continuation A', draw_b: 'Continuation B', control: 'Unedited control', edit: 'Edited prefix'}[arm] ?? arm);
const el = (tag, text, parent, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; if (parent) parent.append(node); return node; };
const number = (value, label, min, max) => { const n = Number(value); if (String(value).trim() === '' || !Number.isInteger(n) || n < min || n > max) throw new Error(`${label} must be a whole number from ${min} to ${max}.`); return n; };

// A missing capabilities field means an older worker: keep its native behavior.
export function inspectionBackends(options) {
  const reported = options?.inspection_backends;
  return ['native', 'nnsight'].map(id => {
    const row = reported?.find(r => r.id === id);
    return {id, label: id === 'native' ? 'Standard capture' : 'NNsight capture (experimental)',
      available: row ? row.available === true : id === 'native',
      reason: row?.reason || (id === 'native' ? 'Uses the attached model and existing capture hooks.' : 'This worker has not reported NNsight support. Connect an updated worker with the optional package.')};
  });
}

export function mountLens(host, getContext) {
  host.classList.add('lens-panel');
  host.innerHTML = `<h2>Jacobian lens</h2>
    <p class="lens-intro">Compare layer readouts along continuations that reached different outcomes. Saved readouts open without a GPU.</p>
    <p class="lens-note">These are readout scores and ranks, not outcome probabilities or a complete hidden chain of thought. A suggestive word is a lead to investigate, not proof of intent or causation.</p>
    <details data-config class="lens-config"><summary>Configure a new readout <span data-selection-summary></span></summary><div class="lens-controls">
      <label class="lens-wide">Lens for your model<select data-lens="profile"></select></label>
      <label data-local hidden>Checkpoint path on the worker<input data-lens="path" placeholder="/workspace/lenses/model.pt" spellcheck="false"></label>
      <p data-local hidden class="lens-note lens-wide">A local lens needs a matching <code>.pt.json</code> sidecar with model ID, resolved revision, checkpoint hash, capture site and final decoder target layer (<code>target_layer</code>). This path is on your connected compute. Preview checks compatibility; this panel does not fit new lenses.</p>
      <label>Start token index<input data-lens="start" type="number" min="0" step="1" value="0"></label>
      <label>End token index (included)<input data-lens="end" type="number" min="0" step="1" value="7"></label>
    </div>
    <details class="lens-wide"><summary>Lens compatibility &amp; download details</summary><p data-options class="lens-note"></p></details>
    <div class="lens-actions"><button data-focus class="secondary" type="button">Focus on first difference</button></div>
    <details><summary>Expand inspection or compare the baseline</summary><div class="lens-actions"><button data-expand class="secondary" type="button">Expand token window</button><button data-depth class="secondary" type="button">Add layer detail</button><button data-anchor class="secondary" type="button">Inspect prompt baseline</button></div><p class="lens-note">The five-layer preset samples broadly across depth; it is not a verified workspace boundary for your model. The actual final-layer prediction is always included as a baseline.</p></details>
    <p data-focus-note class="lens-note" role="status"></p>
    <p data-position-note class="lens-note"></p>
    <details class="lens-advanced"><summary>Paths, layers &amp; readout settings</summary><div class="lens-controls">      <label>Trajectory<select data-lens="source"><option value="draw_pair">Two different outcomes</option><option value="original">Original saved response</option><option value="draw">Saved continuation at this checkpoint</option><option value="edit_pair">Saved edit versus control prefixes</option></select></label>
      <label data-draw hidden>Recorded continuation<select data-lens="draw"></select></label>
      <label data-pair hidden>Continuation A<select data-lens="pair_a"></select></label><label data-pair hidden>Continuation B · different outcome<select data-lens="pair_b"></select></label><p data-pair-note hidden class="lens-note lens-wide"></p><label data-edit hidden>Saved text edit<select data-lens="edit"></select></label>
      <label>Token region<select data-lens="space"><option value="response">Generated response</option><option value="prompt">Input prompt (including chat template)</option></select></label>
      <label>Decoder layers · zero-based, up to eight<input data-lens="layers" value="0" placeholder="0, 8, 16" spellcheck="false"></label>
      <label>Capture method<select data-lens="inspection_backend"><option value="native">Standard capture</option><option value="nnsight" disabled>NNsight capture (experimental)</option></select></label>
      <p data-backend-note class="lens-note lens-wide">Standard capture uses your attached model. NNsight availability is checked on your worker.</p>
      <p class="lens-note lens-wide">Both methods replay the exact saved tokens to read activations. This changes how states are captured, not the lens mathematics. It does not connect an AI agent or a hosted NDIF model.</p>
      <label>Ranked tokens per cell<input data-lens="top_k" type="number" min="2" max="30" value="8" step="1"></label>
</div></details>
    <p data-edit-note hidden class="lens-note">Control and edit are compared at the same numeric token indices. An edit can move token positions, so inspect the actual text before interpreting a difference. This compares saved prefixes, not newly generated continuations.</p>
    <div class="lens-actions"><button data-preview class="secondary" type="button">Preview lens readout</button><button data-run class="primary" type="button" disabled>Run readout on connected compute</button><button data-stop class="secondary" type="button" hidden>Stop readout</button></div>
    <p data-status class="lens-status" role="status" aria-live="polite"></p><a data-model-link class="lens-note" href="/live.html">Load this run’s model →</a>
    <pre data-preview-text hidden tabindex="0" aria-label="Lens request preview"></pre>
    </details><h3>Saved lens readouts</h3>
    <div class="lens-saved-controls"><label>Readout artifact<select data-saved><option value="">Choose a saved readout</option></select></label><button data-refresh class="secondary" type="button">Refresh saved</button><button data-export class="secondary" type="button" disabled>Download JSON</button></div>
    <div data-empty-readouts hidden><p class="lens-note">No lens readouts have been saved for this run. Set up an inspection around your selected checkpoint, then preview the exact positions. Running it requires connected compute with the compatible model and lens; opening these settings does not start a job.</p><button data-configure-empty class="primary" type="button">Configure a readout</button></div><p data-saved-status class="lens-note" role="status"></p><div data-output></div>`;
  const $ = selector => host.querySelector(selector), f = name => $(`[data-lens="${name}"]`);
  let revision = 0, preview = null, activeJob = null, artifact = null, contextKey = '', editArtifact = null, options = null, savedRevision = 0;
  const status = message => { $('[data-status]').textContent = message; };
  f('profile').replaceChildren(...PROFILES.map(p => new Option(p.label, p.id)));
  async function api(route, body) {
    if (typeof window.workerFetch !== 'function') throw new Error('Connect your compute to preview or run a lens readout.');
    const response = await window.workerFetch('/api/live/' + route, {...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}), signal: AbortSignal.timeout(20000)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Lens request failed.'); return data;
  }
  function invalidate() { revision++; preview = null; $('[data-run]').disabled = true; $('[data-preview-text]').hidden = true; }
  function updateVisibility() {
    host.querySelectorAll('[data-local]').forEach(n => n.hidden = f('profile').value !== 'local');
    $('[data-draw]').hidden = f('source').value !== 'draw'; $('[data-edit]').hidden = f('source').value !== 'edit_pair';
    $('[data-edit-note]').hidden = f('source').value !== 'edit_pair';
    host.querySelectorAll('[data-pair]').forEach(n => n.hidden = f('source').value !== 'draw_pair');
    $('[data-pair-note]').hidden = f('source').value !== 'draw_pair';
  }
  function lengths() {
    const c = getContext(), base = c.record?.base;
    if (!base) return null;
    if (f('space').value === 'prompt') return base.prompt_ids?.length ?? 0;
    if (f('source').value === 'original') return base.gen_ids?.length ?? 0;
    if (f('source').value === 'draw_pair') { const pair = chosenPair(); return pair.length ? Math.min(...pair.map(d => d.length)) : 0; }
    if (f('source').value === 'draw') return lensDraws(c.record, c.position ?? 0).find(d => String(d.draw_index) === f('draw').value)?.length ?? 0;
    return editArtifact?.arms ? Math.min(...Object.values(editArtifact.arms).map(ids => ids.length)) : null;
  }
  function updatePositionNote(reset = false) {
    const size = lengths(), prompt = f('space').value === 'prompt';
    $('[data-position-note]').textContent = `Select up to 64 token positions, including both endpoints. ${prompt ? 'Prompt indices include the saved chat-template tokens.' : 'Response index 0 is the first generated token. Each cell reads the state AFTER that token; the final-layer baseline predicts the NEXT token. A graph checkpoint at N preserves N tokens (through N − 1).'}${size !== null ? ` This selection supports indices 0–${Math.max(0, size - 1)}${f('source').value === 'edit_pair' ? ' in both prefixes' : ''}.` : ' Preview verifies the available positions.'}`;
    for (const name of ['start', 'end']) { if (size !== null) f(name).max = String(Math.max(0, size - 1)); else f(name).removeAttribute('max'); }
    if (reset) { const focus = !prompt && f('source').value === 'draw_pair' ? pairFocus(chosenPair(), getContext().position) : null; if (focus?.first !== null && focus?.first !== undefined) { f('start').value=String(focus.start); f('end').value=String(focus.end); return; } const start = prompt ? Math.max(0, (size ?? 1) - 8) : Math.min(Math.max(0, (getContext().position ?? 1) - (f('source').value === 'draw_pair' ? 0 : 1)), Math.max(0, (size ?? 1) - 1)); f('start').value = String(start); f('end').value = String(Math.min(start + 7, Math.max(0, (size ?? start + 8) - 1))); }
  }
  function request() {
    const c = getContext(); if (!c.result || !c.pass || !c.record) throw new Error('Open a saved run with exact token records first.');
    const start = number(f('start').value, 'Start index', 0, 10000000), end = number(f('end').value, 'End index', start, start + 63);
    const q = {source_run_id: c.result.id, source_pass_id: c.pass.id, lens: {profile: f('profile').value}, selection: {type: f('source').value}, space: f('space').value, start, end, layers: parseLensLayers(f('layers').value), top_k: number(f('top_k').value, 'Ranked tokens', 2, 30)};
    const backend = inspectionBackends(options).find(b => b.id === f('inspection_backend').value);
    if (!backend?.available) throw new Error(backend?.reason || 'The selected capture method is unavailable.');
    if (options?.inspection_backends || backend.id !== 'native') q.inspection_backend = backend.id;
    if (q.lens.profile === 'local') { q.lens.path = f('path').value.trim(); if (!q.lens.path) throw new Error('Enter the path to a lens checkpoint on the connected worker.'); }
    if (q.selection.type === 'draw_pair') { const pair = chosenPair(); if (pair.length !== 2) throw new Error('Choose a checkpoint with completed continuations reaching different outcomes, or inspect an individual trajectory.'); q.selection.checkpoint = c.position; q.selection.draw_indices = pair.map(d => d.draw_index); }
    if (q.selection.type === 'draw') { if (!f('draw').value) throw new Error('Choose a recorded continuation.'); q.selection.checkpoint = c.position ?? 0; q.selection.draw_index = Number(f('draw').value); }
    if (q.selection.type === 'edit_pair') { if (!f('edit').value) throw new Error('Choose a saved edit investigation.'); q.selection.investigation_id = f('edit').value; }
    const size = lengths(); if (size !== null && (size === 0 || end >= size)) throw new Error('The selected token range extends beyond the saved trajectory.');
    return q;
  }
  function chosenPair() {
    const rows = completedDraws(getContext().record, getContext().position);
    const a = rows.find(o => String(o.draw_index) === f('pair_a').value), b = rows.find(o => String(o.draw_index) === f('pair_b').value);
    return a && b && a.label !== b.label ? [a, b] : [];
  }
  function fillPair(first = Number(f('pair_a').value), second = Number(f('pair_b').value)) {
    const rows = completedDraws(getContext().record, getContext().position), pair = outcomePair(rows, first, second);
    const option = o => new Option(`Draw ${o.draw_index + 1} · ${o.label}`, String(o.draw_index));
    f('pair_a').replaceChildren(...rows.map(option));
    f('pair_b').replaceChildren(...rows.filter(o => o.label !== pair[0]?.label).map(option));
    if (pair.length) { f('pair_a').value = String(pair[0].draw_index); f('pair_b').value = String(pair[1].draw_index); }
    $('[data-pair-note]').textContent = pair.length ? 'Both paths preserve the same checkpoint prefix. Matching token indices after branching do not guarantee matching meaning. Read the text alongside each readout.' : 'No completed pair with different classified outcomes at this checkpoint. Choose another checkpoint or use an individual trajectory.';
    const focus = pairFocus(pair, getContext().position);
    $('[data-focus-note]').textContent = f('source').value !== 'draw_pair' ? 'Explore a small region, then expand as needed. Layer presets are exploratory, not a validated workspace boundary.' : focus?.first != null ? `First differing saved token: ${focus.first}. ${focus.first === 0 ? 'There is no earlier response token; use the prompt baseline to inspect the shared state.' : `Token ${focus.first-1} is the shared pre-divergence baseline.`} This is a text divergence, not a proven decision boundary. Equal indices after it may represent different meanings.` : 'These paths have no unequal token within their shared length. Inspect individually or choose another pair.';
    $('[data-focus]').disabled = f('source').value !== 'draw_pair' || focus?.first == null;
    $('[data-selection-summary]').textContent = pair.length && f('source').value === 'draw_pair' ? `· checkpoint ${getContext().position} · ${pair.map(o => o.label).join(' / ')}` : '';
  }
  function layerCount() { return ({muse_glimmer:52,qwen35_4b:32,qwen36_27b:64})[f('profile').value] ?? options?.layer_count; }
  function defaultLayers() { f('layers').value = explorationLayers(layerCount()).join(', '); }
  function adjust(action) {
    try { action(); invalidate(); updateVisibility(); fillPair(); updatePositionNote(); $('[data-config]').open=true; status(`Selection: ${f('space').value} tokens ${f('start').value}–${f('end').value} · layers ${f('layers').value}. Preview checks the new work estimate; nothing runs automatically.`); }
    catch(e) { status(e.message); }
  }
  $('[data-focus]').onclick = () => adjust(() => { f('space').value='response'; updatePositionNote(true); });
  $('[data-expand]').onclick = () => adjust(() => { const next=expandLensRange(number(f('start').value,'Start',0,1000000),number(f('end').value,'End',0,1000000),lengths()); f('start').value=String(next.start); f('end').value=String(next.end); });
  $('[data-depth]').onclick = () => adjust(() => { f('layers').value=denserLensLayers(parseLensLayers(f('layers').value),layerCount()).join(', '); });
  $('[data-anchor]').onclick = () => adjust(() => { const size=getContext().record?.base?.prompt_ids?.length; if (!size) throw new Error('Saved prompt token IDs are unavailable.'); f('space').value='prompt'; f('start').value=f('end').value=String(size-1); });
  function showBackend() {
    const selected = f('inspection_backend').value || 'native', rows = inspectionBackends(options);
    f('inspection_backend').replaceChildren(...rows.map(row => { const option = new Option(row.label + (row.available ? '' : ' · unavailable'), row.id); option.disabled = !row.available; return option; }));
    f('inspection_backend').value = selected;
    $('[data-backend-note]').textContent = [rows.find(row => row.id === selected)?.reason || 'Select a supported capture method.', ...rows.filter(row => row.id !== selected && !row.available).map(row => `${row.label}: ${row.reason}`)].join(' ');
  }
  f('inspection_backend').addEventListener('change', () => { invalidate(); showBackend(); });
  $('[data-config]').addEventListener('toggle', () => { if ($('[data-config]').open) loadOptions(); });
  function showOptions() {
    showBackend();
    if (!options) return;
    const profile = options.profiles?.find(p => p.id === f('profile').value), download = profile?.size ? ` Selected lens file: ${(profile.size / 1e9).toFixed(2)} GB, downloaded on Run if not cached.` : '';
    $('[data-options]').textContent = `${options.installed ? 'Jacobian lens package available on worker.' : 'The worker needs the optional Jacobian lens package before running a readout.'} ${options.model ? `Attached: ${options.model.model_id}. ${options.layer_count ? `${options.layer_count} decoder layers (0–${options.layer_count - 1}).` : ''}` : 'Attach the exact model and revision used by this saved run.'} Published lenses require their matching model. The community Muse lens requires the verified pinned revision; its readout quality remains experimental.${download}`;
  }
  async function loadOptions() { try { const key=contextKey; const next=await api('lens-options'); if (key!==contextKey) return; options=next; if (f('profile').value==='local' && f('layers').value==='0') { defaultLayers(); invalidate(); } showOptions(); } catch (e) { $('[data-options]').textContent = e.message; } }
  async function saved() {
    const own = ++savedRevision, c = getContext(), list = await api('investigations'); if (own !== savedRevision) return;
     $('[data-saved-status]').textContent = '';
    const rows = list.filter(x => x.request?.source_run_id === c.result?.id), selected = $('[data-saved]').value, edit = f('edit').value;
    const lenses = rows.filter(x => x.schema === 'fork-lens-v1' || x.request?.lens);
    $('[data-empty-readouts]').hidden = lenses.length !== 0;
    $('[data-saved]').replaceChildren(new Option(lenses.length ? 'Choose a saved readout' : 'No saved lens readouts for this run', ''), ...lenses.map(x => new Option(`${x.request?.selection?.type === 'draw_pair' ? 'Outcome pair' : x.request?.selection?.type === 'original' ? 'Original response' : 'Selected trajectory'} · ${x.request?.start}–${x.request?.end} · ${x.status} · ${x.id.slice(0, 8)}`, x.id)));
    if (lenses.some(x => x.id === selected)) $('[data-saved]').value = selected;
    const edits = rows.filter(x => x.request?.kind === 'edit' && x.request?.source_pass_id === c.pass?.id);
    f('edit').replaceChildren(new Option(edits.length ? 'Choose a saved text edit' : 'Run an edit investigation first', ''), ...edits.map(x => new Option(`${x.status} · span ${x.request.start}–${x.request.end} · ${x.id.slice(0, 8)}`, x.id)));
    if (edits.some(x => x.id === edit)) f('edit').value = edit;
  }
  host.addEventListener('input', event => { if (event.target.matches('[data-lens]')) invalidate(); });
  for (const name of ['profile', 'source', 'space', 'draw']) f(name).addEventListener('change', () => { invalidate(); if (name === 'profile') defaultLayers(); updateVisibility(); fillPair(); showOptions(); updatePositionNote(['source', 'space', 'draw'].includes(name)); });
  for (const name of ['pair_a', 'pair_b']) f(name).onchange = () => { invalidate(); fillPair(); updatePositionNote(true); };
  f('edit').onchange = async () => { invalidate(); editArtifact = null; const id = f('edit').value, v = revision; if (!id) return; try { const data = await api('investigation?id=' + encodeURIComponent(id)); if (v !== revision) return; editArtifact = data; updatePositionNote(true); } catch (e) { status(e.message); } };
  $('[data-preview]').onclick = async () => {
    try {
      invalidate(); const v = revision, q = request(); $('[data-preview]').disabled = true; status('Checking exact tokens, lens compatibility and requested layers…');
      const plan = await api('lens-plan', q); if (v !== revision) return;
      preview = JSON.stringify(q); const text = [`${plan.cells} layer × position readouts · ${(plan.arms ?? []).length} ${plan.arms?.length === 1 ? 'trajectory' : 'trajectories'}`, 'Read-only forward pass and lens projections. No new continuation or activation intervention.', `Capture: ${q.inspection_backend === 'nnsight' ? 'NNsight (experimental)' : 'Standard'}.`, plan.lens?.size ? `Lens file ${(plan.lens.size / 1e9).toFixed(2)} GB. Published files are downloaded on Run if not cached; local files are read on the worker.` : '', plan.work ? `Estimated model forwards: ${plan.work.forward_passes} · prefix tokens replayed: ${plan.work.replay_tokens}. ${plan.work.note}` : '', plan.comparison?.first_different_token != null ? `First differing saved response token: ${plan.comparison.first_different_token}. Shared prefix: ${plan.comparison.shared_response_tokens} response tokens.` : '', plan.interpretation, ...(plan.warnings ?? []).map(w => 'Note: ' + w)];
      for (const arm of plan.arms ?? []) text.push(`\n${armTitle(arm.id).toUpperCase()}${arm.source_draw ? ` · draw ${arm.source_draw.draw_index + 1} · ${arm.source_draw.outcome}` : ""} · exact saved token indices`, ...arm.positions.map(p => `[${p.index}] ${p.text} (token ID ${p.token_id}, absolute position ${p.absolute_position})`));
      $('[data-preview-text]').textContent = text.filter(x => x !== undefined).join('\n'); $('[data-preview-text]').hidden = false; $('[data-run]').disabled = !!activeJob; status('Preview ready. Running uses your connected compute and may download the selected published lens.');
    } catch (e) { status(e.message); } finally { $('[data-preview]').disabled = false; }
  };
  function detail(cell, container, buttons) {
    buttons.forEach(({button, value}) => button.setAttribute('aria-pressed', String(value === cell)));
    container.replaceChildren(); container.hidden = false;
    el('h4', `${armTitle(cell.arm)} · token ${cell.index} · layer ${cell.layer}`, container);
    el('p', `Input token ${JSON.stringify(cell.text)} · ID ${cell.token_id} · absolute position ${cell.absolute_position}`, container, 'lens-note');
    el('p', 'Higher scores rank vocabulary tokens within this readout. They are not probabilities; differences across layers or arms are descriptive.', container, 'lens-note');
    const rankTable = (tokens, label, target = container) => {
      el('h4', label, target); const table = el('table', undefined, target), row = el('tr', undefined, el('thead', undefined, table));
      for (const text of ['Rank', 'Vocabulary token', 'Token ID', 'Score']) el('th', text, row).scope = 'col';
      const body = el('tbody', undefined, table); for (const [i, token] of (tokens ?? []).entries()) { const row = el('tr', undefined, body); el('td', String(token.rank ?? i + 1), row); el('td', JSON.stringify(token.text), row, 'lens-token'); el('td', String(token.token_id), row); el('td', Number.isFinite(token.score) ? token.score.toPrecision(6) : 'Unavailable', row); }
    };
    rankTable(cell.tokens, 'Jacobian lens');
    if (cell.model_tokens?.length) { const more = el('details', undefined, container); el('summary', 'Model final-layer vocabulary readout', more); const holder = el('div', undefined, more); rankTable(cell.model_tokens, 'Final-layer readout', holder); }
    if (cell.logit_lens_tokens?.length) { const more = el('details', undefined, container); el('summary', 'Logit lens baseline', more); const holder = el('div', undefined, more); rankTable(cell.logit_lens_tokens, 'Direct logit lens', holder); }
  }
  function render(data) {
    if (data.schema !== 'fork-lens-v1' && !data.request?.lens) throw new Error('The selected artifact is not a lens readout.');
    artifact = data; $('[data-export]').disabled = false; const out = $('[data-output]'); out.replaceChildren();
    el('h3', 'Layer and token readouts', out); el('p', `${data.status} · ${data.id}`, out, 'lens-note');
    const resume=el('button','Continue from these settings',out,'secondary');resume.type='button';
    resume.onclick=()=>{
      const q=data.request,c=getContext();
      if(q.source_run_id!==c.result?.id || q.source_pass_id!==c.pass?.id){status('Open the source run and pass before continuing this readout.');$('[data-config]').open=true;return;}
      if(q.selection?.type==='edit_pair'){status('Select the saved edit in Paths, layers & readout settings to continue inspecting it.');$('[data-config]').open=true;return;}
      if(q.selection?.checkpoint!==undefined && q.selection.checkpoint!==c.position && !c.selectCheckpoint?.(q.selection.checkpoint)){status('Choose this readout’s checkpoint before continuing.');$('[data-config]').open=true;return;}
      adjust(()=>{
        f('inspection_backend').value=q.inspection_backend??'native';f('profile').value=q.lens.profile;f('path').value=q.lens.path??'';f('source').value=q.selection.type;f('space').value=q.space;
        if(q.selection.type==='draw_pair')fillPair(...q.selection.draw_indices);
        if(q.selection.type==='draw')f('draw').value=String(q.selection.draw_index);
        f('start').value=String(q.start);f('end').value=String(q.end);f('layers').value=q.layers.join(', ');f('top_k').value=String(q.top_k);showOptions();
      });
      $('[data-config]').scrollIntoView({block:'start',behavior:'smooth'});
    };
    el('p', data.interpretation || 'Vocabulary readouts are observational evidence, not hidden sentences or causal explanations.', out);
    if (data.execution) el('p', `Compute used: ${data.execution.forward_passes} model forwards · ${data.execution.replay_tokens} prefix tokens replayed · ${data.execution.reused_activation_rows} activation rows reused. Cached rows stay only in this loaded model session. Lens projections and checkpoint validation still run.`, out, 'lens-note');
    if (data.comparison?.first_different_token != null) el('p', `First differing saved response token: ${data.comparison.first_different_token}. ${data.comparison.interpretation}`, out, 'lens-note');
    if (data.error) el('p', data.error, out);
    if (data.request?.source_run_id !== getContext().result?.id) el('p', 'This artifact belongs to a different saved run than the one currently open.', out, 'lens-note');
    if (data.warnings?.length) { const notes = el('details', undefined, out); el('summary', `Readout notes (${data.warnings.length})`, notes); for (const warning of data.warnings) el('p', warning, notes, 'lens-note'); }
    if (data.request?.selection?.type === 'draw_pair') el('p', 'Each column keeps its own text and token positions. Matching rows after branching are not a claim of semantic alignment.', out, 'lens-note');
    if (data.request?.selection?.type === 'edit_pair') el('p', 'Control and edit use the same numeric positions. Different-length edits may shift corresponding text; these columns are not a semantic alignment.', out, 'lens-note');
    const cells = data.cells ?? []; if (!cells.length) { el('p', 'No completed readout cells are available in this artifact.', out); return; }
    el('p', 'Select a vocabulary token in any cell to inspect the saved top-token rankings and the logit lens baseline. Each row identifies the actual input token.', out, 'lens-note');
    const arms = el('div', undefined, out, 'lens-arms'), detailHost = el('div', undefined, out, 'lens-detail'), buttons = [];
    detailHost.hidden = true; detailHost.setAttribute('role', 'region'); detailHost.setAttribute('aria-label', 'Selected cell token rankings'); detailHost.tabIndex = -1;
    const armIds = [...new Set(cells.map(c => c.arm))].sort((a, b) => ['original', 'draw', 'draw_a', 'draw_b', 'control', 'edit'].indexOf(a) - ['original', 'draw', 'draw_a', 'draw_b', 'control', 'edit'].indexOf(b));
    for (const arm of armIds) {
      const section = el('section', undefined, arms, 'lens-arm'); el('h4', armTitle(arm), section); const source = data.arms?.find(a => a.id === arm)?.source_draw; if (source) el('p', `Draw ${source.draw_index + 1} · ${source.outcome} · checkpoint ${source.checkpoint}`, section, 'lens-note');
      const matrix = lensMatrix(cells, arm), wrap = el('div', undefined, section, 'lens-grid-wrap'); wrap.tabIndex = 0; wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', `${armTitle(arm)} layer and position table`);
      const table = el('table', undefined, wrap), header = el('tr', undefined, el('thead', undefined, table)); el('th', 'Input token', header).scope = 'col';
      for (const layer of matrix.layers) el('th', 'Layer ' + layer, header).scope = 'col';
      el('th', 'Final layer · next token', header).scope = 'col';
      const body = el('tbody', undefined, table);
      for (const index of matrix.positions) { const row = el('tr', undefined, body); if (data.request?.space === 'response' && index === data.comparison?.first_different_token) row.classList.add('lens-divergence'); const sample = cells.find(c => c.arm === arm && c.index === index), heading = el('th', `${index} · ${JSON.stringify(sample.text)}`, row); heading.scope = 'row';
        for (const layer of matrix.layers) { const td = el('td', undefined, row), cell = matrix.byKey.get(`${index}/${layer}`); if (!cell?.tokens?.length) { td.textContent = 'Not captured'; continue; }
          const button = el('button', JSON.stringify(cell.tokens[0].text), td, 'lens-cell'); button.type = 'button'; button.setAttribute('aria-pressed', 'false'); button.setAttribute('aria-label', `${armTitle(arm)}, position ${index}, layer ${layer}, top readout ${JSON.stringify(cell.tokens[0].text)}. Show rankings.`); buttons.push({button, value: cell}); button.onclick = () => { detail(cell, detailHost, buttons); detailHost.focus({preventScroll: true}); };
        }
        el('td', JSON.stringify(sample.model_tokens?.[0]?.text ?? 'Unavailable'), row, 'lens-token');
      }
    }
    if (buttons.length) detail(buttons[0].value, detailHost, buttons);
    const provenance = el('details', undefined, out); el('summary', 'Readout provenance and reproducibility', provenance); el('pre', JSON.stringify({model: data.model, lens: data.lens, request: data.request, inspection: data.inspection, created: data.created, finished: data.finished}, null, 2), provenance);
  }
  async function poll(jobId) {
    try {
      const {job} = await api('status'); if (activeJob !== jobId) return;
      if (job.id !== jobId) { activeJob = null; status('The worker is on another job. Refresh saved readouts to recover this artifact.'); await saved(); return; }
      status(`${job.phase ?? 'Lens readout'} · ${job.status}`);
      if (job.status === 'running') { setTimeout(() => poll(jobId), 2000); return; }
      if (job.investigation_id) render(await api('investigation?id=' + encodeURIComponent(job.investigation_id)));
      activeJob = null; await saved();
    } catch (e) { activeJob = null; status(`${e.message} The worker may still be running. Reconnect and refresh saved readouts.`); }
    finally { if (!activeJob) { $('[data-stop]').hidden = true; $('[data-run]').disabled = true; } }
  }
  $('[data-run]').onclick = async () => { try { const q = request(); if (JSON.stringify(q) !== preview) throw new Error('Preview the current settings first.'); if (activeJob) throw new Error('A lens readout is already being monitored.'); $('[data-run]').disabled = true; const job = await api('lens', q); activeJob = job.job_id; $('[data-stop]').hidden = false; status('Lens readout started.'); poll(activeJob); } catch (e) { status(e.message); } };
  $('[data-stop]').onclick = async () => { try { if (!activeJob) return; await api('stop', {job_id: activeJob}); status('Stop requested. Completed readout cells remain saved.'); } catch (e) { status(e.message); } };
  $('[data-configure-empty]').onclick = () => {
    const panel = $('[data-config]'); panel.open = true; panel.scrollIntoView({block:'start', behavior:'auto'}); panel.querySelector('summary').focus();
    status('Configure the selected checkpoint below. Preview checks exact positions and compatibility; no job has been started.');
  };
  $('[data-refresh]').onclick = () => { loadOptions(); saved().catch(e => { $('[data-saved-status]').textContent = e.message; }); };
  $('[data-saved]').onchange = async () => {
    const id = $('[data-saved]').value, sourceKey = contextKey;
    artifact = null; $('[data-export]').disabled = true; $('[data-output]').replaceChildren();
    $('[data-saved-status]').textContent = id ? 'Opening saved readout…' : '';
    if (!id) return;
    try { const data = await api('investigation?id=' + encodeURIComponent(id));
      if ($('[data-saved]').value === id && contextKey === sourceKey) { render(data); $('[data-saved-status]').textContent = ''; }
    } catch (e) { if ($('[data-saved]').value === id && contextKey === sourceKey) $('[data-saved-status]').textContent = e.message; }
  };
  $('[data-export]').onclick = () => { if (artifact) { const data=artifact; saveJSON($('[data-export]'),`lens-${data.id}.json`,()=>data); } };
  function clearSelectedReadout() { artifact = null; $('[data-output]').replaceChildren(); $('[data-saved]').value = ''; $('[data-export]').disabled = true; $('[data-saved-status]').textContent = ''; }
  return {usePair(indices) {
    clearSelectedReadout();
    f('source').value = 'draw_pair'; f('space').value = 'response';
    fillPair(indices[0], indices[1]); updateVisibility(); updatePositionNote(true); invalidate();
    $('[data-config]').open = true;
    status('Your selected paths are ready. Preview verifies exact tokens and model compatibility before any compute runs.');
  }, useOriginal() {
    clearSelectedReadout();
    f('source').value = 'original'; f('space').value = 'response';
    updateVisibility(); fillPair(); updatePositionNote(true); invalidate(); $('[data-config]').open = true;
    status('Original response selected. Preview checks exact tokens before any compute runs.');
  }, refresh() {
    const c = getContext(); invalidate(); $('[data-configure-empty]').textContent = Number.isInteger(c.position) ? `Configure a readout at token ${c.position}` : 'Configure a readout'; if (!c.record) { status('Open a saved run to inspect its internal states.'); return; }
     $('[data-model-link]').href = '/live.html?source=' + encodeURIComponent(c.result.id);
    const key = `${c.result?.id}/${c.pass?.id}`, changed = key !== contextKey; contextKey = key;
    if (changed) { $('[data-empty-readouts]').hidden = true; options=null; f('inspection_backend').value = 'native'; showBackend(); $('[data-saved]').value = ''; $('[data-config]').open = false; $('[data-saved-status]').textContent = ''; editArtifact = null; artifact = null; $('[data-output]').replaceChildren(); $('[data-export]').disabled = true; f('source').value = 'draw_pair'; const modelId = c.result?.model?.model_id ?? ''; f('profile').value = PROFILES.find(p => p.model_id?.toLowerCase() === modelId.toLowerCase())?.id ?? 'local'; defaultLayers(); loadOptions(); saved().catch(e => { $('[data-saved-status]').textContent = e.message; }); }
    const draws = lensDraws(c.record, c.position ?? 0); f('draw').replaceChildren(...(draws.length ? draws.map(d => new Option(`Draw ${d.draw_index + 1} · ${d.label} · checkpoint ${d.checkpoint}`, String(d.draw_index))) : [new Option('No saved continuations at this checkpoint', '')]));
    fillPair(); updateVisibility(); updatePositionNote(true); if (!activeJob) status('Choose a lens and preview the exact token positions before running.');
  }};
}
