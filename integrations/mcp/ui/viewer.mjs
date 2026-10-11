import { createBridge } from './bridge.mjs';
import { buildMeasurementModel, measurementAt, supportedIntervals, localShifts, exceedsThreshold } from './measurements.mjs';

const VIEW_SCHEMA = 'fork-microscope-view-v1';
const VIEW_TOOL = 'fork_microscope_get_investigation_view';
const BYTE_TOOL = 'fork_microscope_read_investigation_bytes';
const MAX_EXPORT_BYTES = 64 * 1024 * 1024;
const CHUNK_BYTES = 49152;
const validHash = value => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const count = value => Number.isInteger(value) && value >= 0;

// Counts are raw observed labels only. A weighted estimate must never masquerade
// as a count, and absent values are not zeros.
export function observedPoints(view) {
  const model = buildMeasurementModel(view);
  return model.raw.flatMap(checkpoint => model.categories.map((label, index) => ({
    t: checkpoint.t, label, index, count: checkpoint.counts[label], n: checkpoint.n, value: checkpoint.probabilities[index],
  })));
}

export function formatMass(value) {
  if (!finite(value) || value < 0 || value > 1) return 'Not saved';
  if (value === 0) return '0%';
  if (value === 1) return '100%';
  if (value >= 0.9995) return '>99.9%';
  if (value < 0.0005) return '<0.1%';
  return `≈${(value * 100).toFixed(1)}%`;
}

export function checkpointIndex(key, index, length) {
  if (length < 1) return -1;
  if (key === 'Home') return 0;
  if (key === 'End') return length - 1;
  if (key === 'ArrowRight' || key === 'ArrowDown') return (index + 1) % length;
  if (key === 'ArrowLeft' || key === 'ArrowUp') return (index - 1 + length) % length;
  return -1;
}

export function validateView(view, expectedHash = '') {
  if (!view || view.schema !== VIEW_SCHEMA || !view.bundle || !validHash(view.bundle.file_sha256)) {
    throw new Error('The host did not return a supported saved-evidence view.');
  }
  if (expectedHash && view.bundle.file_sha256.toLowerCase() !== expectedHash.toLowerCase()) {
    throw new Error('Evidence changed since the requested hash. Reopen the investigation to use its new identity.');
  }
  if (typeof view.bundle.bundle_id !== 'string' || !view.selection ||
      (view.run !== null && (!Array.isArray(view.run?.categories) || !view.run.categories.every(x => typeof x === 'string'))) ||
      !Array.isArray(view.checkpoints) || !['ready', 'empty', 'unsupported'].includes(view.status)) {
    throw new Error('Saved-evidence view is missing required identity or selection fields.');
  }
  if (view.status === 'ready' && (!view.run || !view.selected || !view.selected.continuations)) {
    throw new Error('Saved-evidence view is missing its selected checkpoint.');
  }
  return view;
}

// This also invalidates pending page reads when a host result changes the source.
export function createRequestGate() {
  let generation = 0;
  return {
    begin: () => ++generation,
    current: ticket => ticket === generation,
    invalidate: () => ++generation,
  };
}

export async function retrieveOriginalBytes(callTool, bundle, options = {}) {
  const cryptoObject = options.cryptoObject ?? globalThis.crypto;
  if (!validHash(bundle?.file_sha256) || !count(bundle?.bytes) || bundle.bytes > MAX_EXPORT_BYTES || !bundle.bundle_id) {
    throw new Error('Export requires a valid pinned file identity of at most 64 MiB.');
  }
  if (!cryptoObject?.subtle) throw new Error('This host cannot verify a SHA-256 download. Export is unavailable.');
  const bytes = new Uint8Array(bundle.bytes);
  let offset = 0;
  do {
    const page = await callTool(BYTE_TOOL, {
      bundle_id: bundle.bundle_id, offset, length: CHUNK_BYTES, expected_file_sha256: bundle.file_sha256,
    });
    const identity = page?.bundle;
    if (!identity || identity.file_sha256 !== bundle.file_sha256 || identity.bundle_id !== bundle.bundle_id || identity.bytes !== bundle.bytes) {
      throw new Error('Export stopped: the evidence identity changed.');
    }
    if (page.encoding !== 'base64' || page.offset !== offset || !count(page.bytes) || page.bytes > CHUNK_BYTES || typeof page.data !== 'string' || page.data.length > 4 * Math.ceil(CHUNK_BYTES / 3)) {
      throw new Error('Export stopped: the byte response was invalid.');
    }
    let chunk;
    try { chunk = Uint8Array.from(atob(page.data), character => character.charCodeAt(0)); }
    catch { throw new Error('Export stopped: invalid base64 byte data.'); }
    if (chunk.length !== page.bytes || offset + chunk.length > bytes.length || (!chunk.length && bytes.length)) {
      throw new Error('Export stopped: the byte response length was invalid.');
    }
    bytes.set(chunk, offset);
    offset += chunk.length;
    const expectedNext = offset < bytes.length ? offset : null;
    if (page.next_offset !== expectedNext) throw new Error('Export stopped: a byte chunk was missing or out of order.');
    options.onProgress?.(offset, bytes.length);
  } while (offset < bytes.length);
  const digest = new Uint8Array(await cryptoObject.subtle.digest('SHA-256', bytes));
  const hash = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
  if (hash !== bundle.file_sha256.toLowerCase()) throw new Error('Export stopped: the complete file checksum did not match.');
  return bytes;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function svgElement(tag, attributes = {}, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function button(text, handler, className = '', disabled = false) {
  const node = element('button', className, text);
  node.type = 'button';
  node.disabled = disabled;
  node.addEventListener('click', handler);
  return node;
}
function detail(title, content, key = '', className = '') {
  const node = element('details', className);
  const summary = element('summary', '', title);
  if (key) {
    node.setAttribute('data-disclosure', key);
    summary.id = `disclosure-${key}`;
  }
  node.append(summary, content);
  return node;
}
function definitions(entries) {
  const dl = element('dl');
  for (const [label, value] of entries) {
    const dd = element('dd');
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value === null || value === undefined || value === '' ? 'Not saved' : String(value);
    dl.append(element('dt', '', label), dd);
  }
  return dl;
}
function shapeInfo(label, index) {
  const other = label.toLowerCase() === 'other';
  return { shape: other ? 'diamond' : ['', 'square', 'triangle', 'diamond'][index % 4], color: `color-${other ? 4 : index % 4}` };
}
function labelKey(label, index) {
  const { shape, color } = shapeInfo(label, index);
  const wrapper = element('span', 'legend-item');
  const marker = element('span', `shape ${shape} ${color}`);
  marker.setAttribute('aria-hidden', 'true');
  wrapper.append(marker, element('span', '', label));
  return wrapper;
}
function savedText(value, missingMessage) {
  const wrapper = element('div', 'stack');
  if (!value || value.text === null || value.text === undefined || value.missing || value.text_missing) {
    wrapper.append(element('p', 'notice', missingMessage));
  } else {
    const text = element('div', 'text source-text', value.text);
    text.tabIndex = 0;
    wrapper.append(text);
    if (value.truncated || value.text_truncated) wrapper.append(element('p', 'small muted', 'Preview truncated. The complete saved evidence is available in the verified export.'));
  }
  if (value?.pointer) wrapper.append(element('code', 'small muted', value.pointer));
  return wrapper;
}

const percent = value => `${(value * 100).toFixed(value * 100 % 1 ? 1 : 0)}%`;
const intervalText = interval => `t=${interval.t0} → ${interval.t1}`;

function outcomeChart(model, mode, horizon, threshold, selectedT, onSelect, width = 720) {
  const points = mode === 'smooth' ? model.smooth : model.raw;
  if (!points.length) return element('p', 'notice', 'No saved outcome distribution is available.');
  const intervals = supportedIntervals(model, horizon, mode);
  const height = width < 500 ? 260 : 286, left = 42, right = 18, top = 14, plotBottom = 158;
  const signalTop = 198, signalBottom = height - 28;
  const first = points[0].t, last = points.at(-1).t;
  const x = t => left + (last === first ? .5 : (t - first) / (last - first)) * (width - left - right);
  const y = p => top + (1 - p) * (plotBottom - top);
  // Scale only the plotted axis, never the saved TVD values or classification.
  const largestSignal = intervals.reduce((largest, interval) => Math.max(largest, interval.score), threshold);
  const signalMax = Math.min(1, Math.max(.3, Math.ceil(largestSignal * 10) / 10));
  const signalY = p => signalBottom - p / signalMax * (signalBottom - signalTop);
  const svg = svgElement('svg', { class: 'chart', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${mode === 'smooth' ? 'Saved reconstructed' : 'Raw observed'} outcome shares and total variation distance over actual saved intervals. The score compares distributions, not individual outcomes or causality.` });
  const selected = measurementAt(model, selectedT, horizon, mode);
  if (selected) svg.append(svgElement('rect', { x: x(selected.t0), y: top, width: Math.max(1, x(selected.t1) - x(selected.t0)), height: signalBottom - top, class: 'selected-region', rx: 4 }));
  for (const value of [0, .5, 1]) {
    svg.append(svgElement('line', { x1: left, x2: width - right, y1: y(value), y2: y(value), class: 'grid-line' }));
    svg.append(svgElement('text', { x: left - 8, y: y(value) + 4, 'text-anchor': 'end' }, `${value * 100}%`));
  }
  for (const point of model.raw) svg.append(svgElement('line', { x1: x(point.t), x2: x(point.t), y1: top, y2: plotBottom, class: 'checkpoint-guide' }));
  model.categories.forEach((label, index) => {
    const color = shapeInfo(label, index).color;
    // In Raw, connecting lines are explicitly visual guides between observations.
    const path = points.map((point, i) => `${i ? 'L' : 'M'}${x(point.t)} ${y(point.probabilities[index])}`).join(' ');
    svg.append(svgElement('path', { d: path, class: `outcome-line ${mode === 'raw' ? 'raw-guide' : ''} ${color}` }));
    for (const point of points) {
      if (mode === 'smooth' && !model.raw.some(raw => raw.t === point.t)) continue;
      const dot = svgElement('circle', { cx: x(point.t), cy: y(point.probabilities[index]), r: 3, class: `outcome-dot ${color}` });
      dot.append(svgElement('title', {}, `t=${point.t}; ${label}: ${percent(point.probabilities[index])}${mode === 'raw' ? ` (${point.counts[label]} of ${point.n})` : ' saved reconstruction'}`));
      svg.append(dot);
    }
  });
  svg.append(svgElement('text', { x: left, y: signalTop - 10, class: 'signal-label' }, `Distribution difference · TVD · 0–${percent(signalMax)}`));
  svg.append(svgElement('line', { x1: left, x2: width - right, y1: signalBottom, y2: signalBottom, class: 'grid-line' }));
  svg.append(svgElement('line', { x1: left, x2: width - right, y1: signalY(threshold), y2: signalY(threshold), class: 'threshold-line' }));
  svg.append(svgElement('text', { x: width - right, y: signalTop - 10, 'text-anchor': 'end' }, `${percent(threshold)} threshold`));
  if (mode === 'smooth' && intervals.length) {
    const d = `M${x(intervals[0].t0)} ${signalBottom} ` + intervals.map(interval => `L${x(interval.t0)} ${signalY(interval.score)}`).join(' ') + ` L${x(intervals.at(-1).t0)} ${signalBottom} Z`;
    svg.append(svgElement('path', { d, class: 'signal-area' }));
    const clip = svgElement('clipPath', { id: 'signal-hot-clip' });
    clip.append(svgElement('rect', { x: left, y: signalTop, width: width - left - right, height: signalY(threshold) - signalTop }));
    svg.append(clip, svgElement('path', { d, class: 'signal-area above-threshold', 'clip-path': 'url(#signal-hot-clip)' }));
  } else for (const interval of intervals) {
    const bar = svgElement('rect', { x: x(interval.t0) + 1, y: signalY(interval.score), width: Math.max(1, x(interval.t1) - x(interval.t0) - 2), height: signalBottom - signalY(interval.score), class: `signal-bar ${exceedsThreshold(interval.score, threshold) ? 'above-threshold' : ''}` });
    bar.append(svgElement('title', {}, `${intervalText(interval)}: ${percent(interval.score)} total variation distance`)); svg.append(bar);
  }
  const stride = Math.max(1, Math.ceil(model.raw.length / 6));
  model.raw.forEach((point, i) => {
    if (i % stride === 0 || i === model.raw.length - 1) svg.append(svgElement('text', { x: x(point.t), y: height - 7, 'text-anchor': i === 0 ? 'start' : i === model.raw.length - 1 ? 'end' : 'middle' }, i === 0 ? `token ${point.t}` : point.t));
  });
  if (finite(selectedT)) svg.append(svgElement('line', { x1: x(selectedT), x2: x(selectedT), y1: top - 4, y2: signalBottom, class: 'selection-rule' }));
  // Pointer targets are paired with native checkpoint radios below the graph.
  const targetPoints = mode === 'smooth' ? intervals.map(interval => ({t: interval.t0})) : model.raw;
  targetPoints.forEach((point, i) => {
    const before = i ? (x(targetPoints[i - 1].t) + x(point.t)) / 2 : left;
    const after = i + 1 < targetPoints.length ? (x(point.t) + x(targetPoints[i + 1].t)) / 2 : width - right;
    const hit = svgElement('rect', { x: before, y: top, width: Math.max(1, after - before), height: signalBottom - top, class: 'checkpoint-target', fill: 'transparent', 'aria-hidden': 'true', focusable: 'false' });
    hit.append(svgElement('title', {}, `Select saved ${mode === 'smooth' ? 'reconstruction interval' : 'checkpoint'} t=${point.t}`));
    hit.addEventListener('click', () => onSelect(point.t)); svg.append(hit);
  });
  return svg;
}

function continuation(row, categories, headingPrefix = 'Continuation', key = '') {
  const node = element('article', 'continuation');
  const header = element('header');
  const label = row.label ?? 'Label not saved';
  header.append(labelKey(label, Math.max(0, categories.indexOf(label))), element('span', 'small muted', `${headingPrefix} ${count(row.draw_index) ? row.draw_index + 1 : '—'}`));
  node.append(header);
  const text = element('div', 'text', row.text === null || row.text === undefined ? 'Continuation text was not saved.' : row.text);
  text.tabIndex = 0;
  if (key) text.id = `continuation-text-${key}`;
  text.setAttribute('aria-label', `${headingPrefix} text, ${label}`);
  node.append(text);
  if (row.text_truncated) node.append(element('p', 'notice', 'Preview truncated; use the verified export for the full saved text.'));
  const provenance = definitions([
    ['Label source', row.label_source], ['Classification status', row.classification_status],
    ['Stop reason', row.stop_reason], ['Stop reason evidence', row.stop_reason_evidence], ['Generated tokens', row.generated_tokens],
    ['Forced token ID', row.tok_id], ['Original next token', row.is_base === true ? 'Yes' : row.is_base === false ? 'No' : null],
    ['Record pointer', row.pointer], ['Text pointer', row.text_pointer],
  ]);
  node.append(detail('Continuation provenance', provenance, key ? `continuation-${key}` : ''));
  return node;
}

export function importedEvidenceNotice(view) {
  const source = view?.bundle?.provenance;
  if (source?.source !== 'user_file') return null;
  return source.synthetic === true
    ? 'Imported synthetic evidence. This file does not verify a platform run or compute cleanup.'
    : 'Imported saved evidence. This file does not verify a platform run or compute cleanup.';
}

function provenance(view) {
  const content = element('div', 'stack');
  content.append(definitions([
    ['Saved bundle', view.bundle.bundle_id], ['File SHA-256', view.bundle.file_sha256], ['Payload SHA-256', view.bundle.payload_sha256],
    ['Evidence source', view.bundle.provenance?.source === 'user_file' ? 'User-imported original file' : 'Saved evidence'],
    ['Synthetic evidence', view.bundle.provenance?.source === 'user_file' ? (view.bundle.provenance.synthetic === true ? 'Yes' : 'No') : null],
    ['Run ID', view.selection.run_id], ['Response ID', view.selection.response_id], ['Pass', view.selection.pass_id],
    ['Model', view.run?.model_id], ['Model revision', view.run?.model_revision], ['Sampling design', view.run?.sampling_design],
    ['Matching rule', typeof view.run?.matching_rule === 'string' ? view.run.matching_rule : JSON.stringify(view.run?.matching_rule ?? null)],
    ['Reconstruction method identified by application contract', view.reconstruction?.provenance?.method],
    ['Saved reconstruction parameters', view.reconstruction?.provenance?.parameters ? JSON.stringify(view.reconstruction.provenance.parameters) : null],
    ['Reconstruction tuning', view.reconstruction?.provenance?.tuning],
    ['Reconstruction fit status', view.reconstruction?.provenance?.fit_status],
    ['Forking Fast upstream revision', view.reconstruction?.provenance?.upstream_commit ?? view.run?.upstream_commit],
    ['otrecon version', view.reconstruction?.provenance?.otrecon_version],
    ['Reconstruction source pointer', view.reconstruction?.provenance?.pointer],
    ['Estimator name recorded in source', view.reconstruction?.provenance?.method_recorded === true ? 'Yes' : view.reconstruction?.provenance?.method_recorded === false ? 'No' : null],
    ['Method identification basis', view.reconstruction?.provenance?.method_basis],
    ['Upstream revision pointer', view.reconstruction?.provenance?.upstream_pointer ?? view.run?.upstream_pointer],
  ]));
  const table = element('table');
  table.append(element('caption', '', 'Saved candidate probability mass. ≈ and inequality signs preserve rounding; this is distinct from observed outcome frequency.'));
  const head = element('thead');
  const headerRow = element('tr');
  for (const title of ['Checkpoint', 'Candidates', 'Retained mass', 'Omitted mass']) {
    const th = element('th', '', title); th.scope = 'col'; headerRow.append(th);
  }
  head.append(headerRow); table.append(head);
  const body = element('tbody');
  for (const checkpoint of view.checkpoints) {
    const row = element('tr');
    const retained = checkpoint.retained_mass;
    for (const value of [`t=${checkpoint.t}`, count(checkpoint.candidates) ? checkpoint.candidates : 'Not saved', formatMass(retained), formatMass(finite(retained) ? 1 - retained : null)]) row.append(element('td', '', value));
    body.append(row);
  }
  table.append(body);
  const wrap = element('div', 'table-wrap'); wrap.append(table); content.append(wrap);
  const limits = element('ul', 'limits small muted');
  for (const value of [...(view.limitations ?? []), ...(view.reconstruction?.limitations ?? [])]) limits.append(element('li', '', value));
  if (limits.childElementCount) content.append(limits);
  content.append(element('p', 'small muted', 'Checksums establish file consistency, not scientific truth. Sampled outcome differences do not establish a causal mechanism or hidden intent.'));
  return detail('Provenance and sampling limits', content, 'provenance');
}

export function createViewer(root, bridgeFactory = createBridge) {
  let view = null, expanded = false, loading = false, error = null, retryArgs = null;
  let exportState = '', exporting = false, readyExport = null, currentContext = {}, exportGeneration = 0, pendingArgs = null;
  let disclosureScope = '', resetFocus = false, model = null, selectedT = null, mode = 'raw', threshold = .15;
  const disclosures = new Map();
  const gate = createRequestGate();
  let chartBucket = root.clientWidth > 0 && root.clientWidth < 520 ? 'narrow' : 'wide';
  let widthObserver = null;
  const bridge = bridgeFactory({
    onResult: receive,
    onContext(context) {
      currentContext = { ...currentContext, ...context };
      if (context.theme === 'light' || context.theme === 'dark') document.documentElement.dataset.theme = context.theme;
      if (context.displayMode === 'fullscreen') expanded = true;
      render();
    },
    onError(cause) {
      gate.invalidate(); exportGeneration++; readyExport = null; loading = false; pendingArgs = null; view = null; error = cause; exporting = false;
      render();
    },
  });

  function acceptView(next) {
    const scope = JSON.stringify([next.bundle.bundle_id, next.bundle.file_sha256, next.selection.run_index, next.selection.pass_index, next.selection.run_id, next.selection.response_id, next.selection.pass_id]);
    if (scope !== disclosureScope) {
      disclosures.clear();
      disclosureScope = scope;
      exportGeneration++; readyExport = null; exportState = ''; exporting = false;
      resetFocus = true; mode = 'raw'; threshold = .15; selectedT = next.selection.token_position;
    }
    model = next.status === 'ready' ? buildMeasurementModel(next) : null;
    if (mode === 'smooth' && !model?.modes.smooth.available) mode = 'raw';
    view = next;
  }

  function receive(data) {
    gate.invalidate(); exportGeneration++; readyExport = null;
    loading = false; pendingArgs = null; error = null; exportState = ''; exporting = false;
    try { acceptView(validateView(data)); selectedT = view.selection.token_position; retryArgs = requestArgs(view); }
    catch (cause) { view = null; error = cause; }
    render();
  }

  function requestArgs(data, overrides = {}) {
    return {
      bundle_id: data.bundle.bundle_id, run_index: data.selection.run_index, pass_index: data.selection.pass_index,
      token_position: data.selection.token_position, offset: data.selected?.continuations?.offset ?? 0,
      limit: data.selected?.continuations?.limit ?? 5, expected_file_sha256: data.bundle.file_sha256, ...overrides,
    };
  }

  async function request(args, focus = '') {
    const ticket = gate.begin();
    retryArgs = args; pendingArgs = args; loading = true; error = null;
    render();
    try {
      const next = validateView(await bridge.callTool(VIEW_TOOL, args), args.expected_file_sha256);
      if (!gate.current(ticket)) return;
      if (next.bundle.bundle_id !== args.bundle_id || next.selection.run_index !== args.run_index || next.selection.pass_index !== args.pass_index ||
          (args.token_position !== null && next.selection.token_position !== args.token_position) ||
          (next.status === 'ready' && next.selected.continuations.offset !== args.offset)) {
        throw new Error('The returned evidence does not match the requested selection.');
      }
      const changedCheckpoint = next.selection.token_position !== view?.selection.token_position;
      acceptView(next); if (changedCheckpoint) selectedT = next.selection.token_position; loading = false; pendingArgs = null;
      bridge.saveState?.({ ...next.selection, bundle_id: next.bundle.bundle_id, file_sha256: next.bundle.file_sha256, expanded });
      render();
      if (focus) {
        const target = document.getElementById(focus);
        (target?.disabled ? document.getElementById('page-heading') : target)?.focus({ preventScroll: true });
      }
    } catch (cause) {
      if (!gate.current(ticket)) return;
      // Do not leave an old graph visible after a stale-source or page failure.
      view = null; exportGeneration++; readyExport = null; exporting = false; loading = false; pendingArgs = null; error = cause; render();
    }
  }

  function selectCheckpoint(t) {
    if (!view || loading) return;
    if (t === view.selection.token_position) {
      selectedT = t;
      render();
      document.getElementById(`checkpoint-${t}`)?.focus({ preventScroll: true });
      return;
    }
    request(requestArgs(view, { token_position: t, offset: 0 }), `checkpoint-${t}`);
  }

  async function expand() {
    expanded = true; render();
    document.getElementById('selected-heading')?.focus({ preventScroll: true });
    if (currentContext.availableDisplayModes?.includes('fullscreen')) {
      try { await bridge.requestDisplayMode?.('fullscreen'); } catch { /* Inline expansion remains usable. */ }
    }
  }

  async function collapse() {
    expanded = false; render();
    document.getElementById('explore-investigation')?.focus({ preventScroll: true });
    try { if (currentContext.displayMode === 'fullscreen') await bridge.requestDisplayMode?.('inline'); } catch { /* Card stays usable. */ }
  }

  async function exportOriginal() {
    if (!view || exporting || loading) return;
    const bundle = { ...view.bundle }, ticket = ++exportGeneration;
    exporting = true; readyExport = null; exportState = 'Retrieving original saved bytes…'; render();
    try {
      const bytes = await retrieveOriginalBytes(bridge.callTool.bind(bridge), bundle, {
        onProgress(received, total) {
          if (ticket !== exportGeneration) throw new Error('Export cancelled because the investigation changed.');
          exportState = `Verifying export · ${received.toLocaleString()} / ${total.toLocaleString()} bytes`;
          updateExportStatus();
        },
      });
      if (ticket !== exportGeneration) return;
      if (bridge.fileExportAvailable?.()) {
        exportState = 'Checksum verified. Preparing a temporary host download…'; updateExportStatus();
        const current = () => ticket === exportGeneration && view?.bundle.bundle_id === bundle.bundle_id && view?.bundle.file_sha256 === bundle.file_sha256;
        const downloadUrl = await bridge.prepareFileDownload(bytes, `fork-investigation-${bundle.file_sha256.slice(0, 12)}.json`, current);
        if (!current()) return;
        readyExport = { downloadUrl, ticket, bundleId: bundle.bundle_id, hash: bundle.file_sha256 };
        exportState = 'Checksum verified. The download is ready; select Download verified JSON. No device download has been confirmed.';
        return;
      }
      const blobUrl = URL.createObjectURL(new Blob([bytes], { type: 'application/json' }));
      const link = element('a');
      link.href = blobUrl;
      link.download = `fork-investigation-${bundle.file_sha256.slice(0, 12)}.json`;
      link.hidden = true; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      exportState = 'Checksum verified. Download requested; this host may ask you where to save it.';
    } catch (cause) {
      if (ticket !== exportGeneration) return;
      readyExport = null; exportState = `${cause.message || 'Export failed.'} No device download has been confirmed.`;
      if (/evidence (?:identity )?changed|checksum/i.test(cause.message || '')) {
        gate.invalidate(); exportGeneration++; readyExport = null; view = null; error = cause; exporting = false; render();
      }
    } finally {
      if (ticket === exportGeneration) { exporting = false; render(); }
    }
  }

  async function openVerifiedDownload() {
    const prepared = readyExport;
    const current = () => prepared && prepared === readyExport && prepared.ticket === exportGeneration &&
      view?.bundle.bundle_id === prepared.bundleId && view?.bundle.file_sha256 === prepared.hash;
    if (!current() || loading || exporting) return;
    exporting = true; exportState = 'Opening the verified download through the host…'; render();
    try {
      await bridge.openFileDownload(prepared.downloadUrl, current);
      if (current()) exportState = 'Checksum verified. The host was asked to open the download; saving to your device has not been confirmed.';
    } catch {
      if (current()) { readyExport = null; exportState = 'The host could not open the verified download. Prepare it again; no device download has been confirmed.'; }
    } finally {
      if (prepared.ticket === exportGeneration) { exporting = false; render(); }
    }
  }

  function updateExportStatus() {
    const status = document.getElementById('export-status');
    if (status) status.textContent = exportState;
  }

  function selectPosition(t, focus = '') {
    if (loading || !model) return;
    const interval = measurementAt(model, t, model.N, mode);
    const target = mode === 'raw' && interval ? interval.t0 : t;
    if (model.raw.some(point => point.t === target)) { selectCheckpoint(target); return; }
    selectedT = target; render();
    document.getElementById(focus || 'selected-heading')?.focus({ preventScroll: true });
  }

  function group(name, items, value, onChange, className = 'segmented') {
    const fieldset = element('fieldset', `control-group ${className}`);
    fieldset.append(element('legend', '', name));
    const controls = element('div', 'segments');
    const enabled = items.filter(item => !item.disabled);
    for (const item of items) {
      const label = element('label', `segment ${item.disabled ? 'disabled' : ''}`);
      const radio = element('input'); radio.type = 'radio'; radio.name = name;
      radio.id = item.id; radio.value = String(item.value); radio.checked = item.value === value; radio.disabled = item.disabled === true;
      radio.setAttribute('aria-label', item.label); if (item.reason) radio.title = item.reason;
      radio.addEventListener('change', () => { if (radio.checked) { onChange(item.value); render(); document.getElementById(item.id)?.focus({ preventScroll: true }); } });
      radio.addEventListener('keydown', event => {
        const i = enabled.findIndex(option => option.value === item.value), next = checkpointIndex(event.key, i, enabled.length);
        if (next < 0 || i < 0) return;
        event.preventDefault(); onChange(enabled[next].value); render(); document.getElementById(enabled[next].id)?.focus({ preventScroll: true });
      });
      label.append(radio, element('span', '', item.label)); controls.append(label);
    }
    fieldset.append(controls); return fieldset;
  }

  function header() {
    const node = element('header', 'viewer-header');
    const copy = element('div', 'header-copy');
    const eyebrow = element('div', 'eyebrow');
    const logo = svgElement('svg', { viewBox: '0 0 18 18', 'aria-hidden': 'true' });
    logo.append(svgElement('path', { d: 'M2 9h5.5M7.5 9C10 9 11 4.5 15.5 4.5M7.5 9c2.5 0 3.5 4.5 8 4.5', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6 }), svgElement('circle', { cx: 7.5, cy: 9, r: 1.9, fill: 'currentColor' }));
    eyebrow.append(logo, element('span', '', 'Fork Microscope'), element('span', 'model-name', view.run?.model_id || 'Model not saved'));
    copy.append(eyebrow, element('h1', '', 'Where the answer forks'));
    const prompt = view.run?.prompt;
    if (prompt?.text && !prompt.missing) copy.append(element('p', 'question muted', prompt.text));
    const toolbar = element('div', 'viewer-toolbar');
    toolbar.append(element('span', `pill ${view.bundle.provenance?.synthetic === true ? 'warning' : ''}`, view.bundle.provenance?.synthetic === true ? 'Synthetic evidence' : 'Saved evidence'));
    const control = button(expanded ? 'Collapse' : 'Expand', expanded ? collapse : expand, 'size-control');
    control.id = expanded ? 'collapse-investigation' : 'explore-investigation'; toolbar.append(control);
    node.append(copy, toolbar); return node;
  }

  function graphSection() {
    const section = element('section', 'timeline-section');
    const shifts = localShifts(model, { horizon: model.N, threshold, mode });
    const take = element('p', 'takeaway');
    take.append(element('strong', '', `${shifts.length} ${shifts.length === 1 ? 'interval' : 'intervals'} above the ${percent(threshold)} threshold.`));
    take.append(element('span', '', shifts.length ? ' Select a local distribution difference to inspect the saved evidence.' : ' No measured local shift exceeds this threshold; this does not establish a globally steady response.'));
    section.append(take);
    const toolbar = element('div', 'chart-toolbar');
    toolbar.append(group('View', [{ value: 'raw', label: 'Raw', id: 'mode-raw', disabled: !model.modes.raw.available }, { value: 'smooth', label: 'Smoothed', id: 'mode-smooth', disabled: !model.modes.smooth.available, reason: model.modes.smooth.reason }], mode, value => { mode = value; selectedT = view.selection.token_position; }));
    toolbar.append(group('Threshold', [10,15,20].map(value => ({value: value / 100, label: `${value}%`, id: `threshold-${value}`})), threshold, value => { threshold = value; }));
    section.append(toolbar);
    section.append(element('p', 'chart-help muted', mode === 'raw' ? 'Raw = saved counts ÷ the actual samples at each checkpoint. Lines guide the eye between observations; they do not supply per-token evidence.' : `Smoothed = the saved reconstruction. Each change score compares t → t+${model.N}; no curve is fitted in this viewer.`));
    if (!model.modes.smooth.available) section.append(element('p', 'small muted', `Smoothed unavailable: ${model.modes.smooth.reason}`));
    const legend = element('div', 'legend'); model.categories.forEach((label, index) => legend.append(labelKey(label, index))); section.append(legend);
    section.append(outcomeChart(model, mode, model.N, threshold, selectedT, t => selectPosition(t), root.clientWidth > 0 && root.clientWidth < 520 ? 360 : 720));
    const checkpoints = element('fieldset', 'checkpoint-group'); checkpoints.append(element('legend', '', 'Saved checkpoints · select with chart, touch or arrow keys'));
    const radios = element('div', 'checkpoints');
    model.raw.forEach((checkpoint, index) => {
      const label = element('label', 'checkpoint'); const radio = element('input');
      radio.type = 'radio'; radio.name = 'checkpoint'; radio.value = String(checkpoint.t); radio.id = `checkpoint-${checkpoint.t}`;
      radio.checked = checkpoint.t === view.selection.token_position; radio.setAttribute('aria-label', `Saved checkpoint t=${checkpoint.t}`);
      radio.addEventListener('change', () => { if (radio.checked) selectCheckpoint(checkpoint.t); });
      radio.addEventListener('keydown', event => { const next = checkpointIndex(event.key, index, model.raw.length); if (next < 0) return; event.preventDefault(); selectCheckpoint(model.raw[next].t); });
      label.append(radio, element('span', '', `t=${checkpoint.t}`)); radios.append(label);
    }); checkpoints.append(radios); section.append(checkpoints);
    const ranked = shifts.slice().sort((a,b) => b.score - a.score);
    const chips = element('div', 'shift-chips'); chips.setAttribute('aria-label', 'Largest measured local shifts');
    for (const [index, interval] of ranked.slice(0,3).entries()) {
      const chip = button(`Shift ${index + 1} · ${intervalText(interval)} · ${percent(interval.score)}`, () => selectPosition(interval.t0, `shift-${interval.t0}`), `shift-chip ${interval.t0 === selectedT ? 'on' : ''}`); chip.id = `shift-${interval.t0}`; chips.append(chip);
    }
    section.append(chips);
    if (ranked.length > 3) {
      const list = element('div', 'shift-chips');
      for (const interval of ranked.slice(3)) { const chip = button(`${intervalText(interval)} · ${percent(interval.score)}`, () => selectPosition(interval.t0, `shift-more-${interval.t0}`), 'shift-chip'); chip.id = `shift-more-${interval.t0}`; list.append(chip); }
      section.append(detail(`Show ${ranked.length - 3} more measured intervals`, list, 'all-shifts', 'shift-disclosure'));
    }
    if (view.checkpoints_truncated) section.append(element('p', 'notice', `Showing ${view.checkpoints.length} of ${view.checkpoints_total} saved checkpoints. The original export contains the complete investigation.`));
    return section;
  }

  function selectedSection() {
    const section = element('section', 'selected-section');
    const interval = measurementAt(model, selectedT, model.N, mode);
    const context = element('div', 'token-panel');
    const heading = element('h2', '', interval ? `Response around ${intervalText(interval)}` : `Selected checkpoint · t=${selectedT}`); heading.id = 'selected-heading'; heading.tabIndex = -1; context.append(heading);
    const tokenContext = model.tokenContext;
    const tokens = tokenContext?.items?.filter(token => token.t >= selectedT - 14 && token.t <= (interval?.t1 ?? selectedT) + 14) ?? [];
    if (tokens.length) {
      const text = element('p', 'token-context');
      for (const token of tokens) {
        const selected = interval && token.t >= interval.t0 && token.t < interval.t1;
        const span = element('span', selected ? 'token-selected' : '', token.text); span.title = `Saved token t=${token.t}`;
        if (token.t === selectedT) span.className += ' token-start'; text.append(span);
      }
      context.append(text, element('p', 'small muted', 'Highlight marks the selected comparison interval in the saved token trace.'));
      if (tokenContext.concatenates_to_response === false) context.append(element('p', 'small muted', 'Saved token strings do not exactly concatenate to the original response; the original text remains available in evidence details.'));
      if (tokenContext.truncated || tokens.some(token => token.text_truncated)) context.append(element('p', 'small muted', 'Token context is bounded or truncated; the original export retains the saved source.'));
      const candidates = tokens.filter(token => measurementAt(model, token.t, model.N, mode)?.t0 === token.t);
      if (mode === 'smooth' && candidates.length) {
        const select = element('select'); select.id = 'token-selection'; select.setAttribute('aria-label', 'Select a saved reconstructed interval start');
        for (const token of candidates) { const option = element('option', '', `t=${token.t} · ${token.text.trim() || '(space)'}`); option.value = String(token.t); option.selected = token.t === selectedT; select.append(option); }
        select.addEventListener('change', () => selectPosition(Number(select.value), 'token-selection')); context.append(select);
      }
    } else context.append(element('p', 'notice', 'A token-aligned response trace was not saved. Text boundaries are not reconstructed from words.'));
    const mix = element('div', 'mix-panel');
    mix.append(element('h2', '', interval ? `Outcome mix · ${intervalText(interval)}` : 'No forward comparison'));
    if (interval) {
      for (const [name, t, probabilities, n] of [['Before', interval.t0, interval.before, interval.n_before], ['After', interval.t1, interval.after, interval.n_after]]) {
        const row = element('div', 'mix-row'); row.append(element('span', 'small muted', `${name} · t=${t}${mode === 'raw' ? ` · n=${n}` : ''}`));
        const bar = element('div', 'mix-bar'); bar.setAttribute('aria-hidden', 'true');
        probabilities.forEach((value,index) => { const segment = element('span', shapeInfo(model.categories[index], index).color); segment.style.flexGrow = String(value); bar.append(segment); }); row.append(bar); mix.append(row);
      }
      const table = element('table', 'mix-table'); const caption = element('caption', 'sr-only', 'Outcome shares before and after the selected interval'); table.append(caption);
      const head = element('thead'); const tr = element('tr'); ['Outcome','Before','After','Δ points'].forEach(label => { const th = element('th','',label); th.scope = 'col'; tr.append(th); }); head.append(tr); table.append(head);
      const body = element('tbody'); model.categories.forEach((label,index) => { const tr = element('tr'); const name = element('th'); name.scope = 'row'; name.append(labelKey(label,index)); tr.append(name,element('td','',percent(interval.before[index])),element('td','',percent(interval.after[index])),element('td','',`${interval.after[index] > interval.before[index] ? '+' : ''}${((interval.after[index] - interval.before[index])*100).toFixed(1)}`)); body.append(tr); }); table.append(body); mix.append(table);
      const total = element('div', 'difference-total'); total.append(element('span','', 'Distribution difference · TVD'),element('strong','',percent(interval.score))); mix.append(total);
      mix.append(element('p','small muted', `${interval.gap}-token interval · ${exceedsThreshold(interval.score, threshold) ? 'above' : 'at or below'} the ${percent(threshold)} threshold. TVD compares distributions; it does not track individual outcomes switching.`));
    } else mix.append(element('p','notice','No supported forward interval starts here. Select an earlier saved checkpoint or supported reconstruction position; no missing endpoint is inferred.'));
    if (mode === 'raw') mix.append(element('p','small muted','Raw counts are conditional on the retained candidate set. Other includes unresolved labels; it is not a competing answer.'));
    section.append(context,mix); return section;
  }

  function contrastSection() {
    const section = element('section', 'comparison-section stack');
    section.append(element('h2', '', `Contrasting continuations · t=${view.selection.token_position}`));
    const hasPair = view.selected.contrast?.length === 2;
    section.append(element('p', 'small muted', `${hasPair ? 'Two saved examples with different resolved labels. These examples do not identify why an outcome changed. ' : ''}Stored continuation text starts after the forced token; its exact token ID is retained in provenance.`));
    section.append(detail('Shared original-response prefix', savedText(view.selected.shared_prefix, 'Shared prefix text is unavailable in this saved evidence.'), 'shared-prefix'));
    if (hasPair) {
      const pair = element('div', 'pair');
      view.selected.contrast.forEach((row, index) => pair.append(continuation(row, view.run.categories, 'Continuation', `contrast-${view.selection.token_position}-${index}-${row.draw_index}`)));
      section.append(pair);
    } else section.append(element('p', 'notice', view.selected.contrast_reason || 'No pair with two different resolved outcome labels was saved at this checkpoint. Other is not substituted as a competing answer.'));
    return detail(`Contrasting continuations · saved checkpoint t=${view.selection.token_position}`, section, 'contrasting-continuations', 'continuation-disclosure');
  }

  function pageSection() {
    const section = element('section');
    const page = view.selected.continuations;
    const heading = element('div', 'row');
    const start = page.total ? page.offset + 1 : 0;
    const title = element('h2', '', 'Saved continuations'); title.id = 'page-heading'; title.tabIndex = -1;
    heading.append(title, element('span', 'small muted', `${start}–${Math.min(page.offset + page.items.length, page.total)} of ${page.total}`));
    section.append(heading);
    const list = element('div', 'page-list');
    if (!page.items.length) list.append(element('p', 'notice', 'No continuation records on this page.'));
    for (const row of page.items) list.append(continuation(row, view.run.categories, 'Continuation', `page-${view.selection.token_position}-${page.offset}-${row.draw_index}`));
    section.append(list);
    const navigation = element('nav', 'actions'); navigation.setAttribute('aria-label', 'Continuation pages');
    const previous = button('Previous page', () => request(requestArgs(view, { offset: Math.max(0, page.offset - page.limit) }), 'previous-page'), '', page.offset === 0);
    previous.id = 'previous-page';
    const next = button('Next page', () => request(requestArgs(view, { offset: page.next_offset }), 'next-page'), '', page.next_offset === null);
    next.id = 'next-page';
    navigation.append(previous, next); section.append(navigation);
    return detail(`Browse all ${page.total} saved continuations · t=${view.selection.token_position}`, section, 'all-continuations', 'more-continuations');
  }

  function evidenceDetails() {
    const section = element('section', 'evidence-section stack');
    section.append(detail('Original prompt and response', (() => {
      const content = element('div', 'stack');
      content.append(element('h3', '', 'Prompt'), savedText(view.run?.prompt, 'Prompt text was not saved.'), element('h3', '', 'Original response'), savedText(view.run?.original_response, 'Original response text was not saved.'));
      return content;
    })(), 'original-text'));
    if (model) {
      const interval = measurementAt(model, selectedT, model.N, mode);
      const selected = view.checkpoints.find(point => point.t === view.selection.token_position);
      const source = definitions([['Selected graph position', selectedT], ['Loaded continuation checkpoint', view.selection.token_position], ['Interval', interval ? intervalText(interval) : 'No forward interval'], ['Measurement', mode === 'raw' ? 'Observed counts / actual samples' : 'Saved reconstruction'], ['Before source pointer', interval?.pointers.before], ['After source pointer', interval?.pointers.after], ['Before counts pointer', interval?.pointers.counts_before], ['After counts pointer', interval?.pointers.counts_after], ['Before sample-count pointer', interval?.pointers.n_before], ['After sample-count pointer', interval?.pointers.n_after], ['Candidate retained mass at loaded checkpoint', formatMass(selected?.retained_mass)], ['Candidate omitted mass at loaded checkpoint', formatMass(finite(selected?.retained_mass) ? 1 - selected.retained_mass : null)]]);
      const content = element('div','stack'); content.append(source);
      for (const point of model.smooth.filter(point => point.t === interval?.t0 || point.t === interval?.t1)) if (point.low && point.high) content.append(definitions([[`Saved uncertainty bands at t=${point.t}`, JSON.stringify({low:point.low,high:point.high})],['Lower band pointer',point.bands_pointer?.low],['Upper band pointer',point.bands_pointer?.high]]));
      content.append(element('p','small muted','Candidate probability mass is distinct from outcome frequency and distribution difference. Omitted mass is not an outcome category. Saved uncertainty values are retained as supplied; this viewer does not infer missing bands.'));
      section.append(detail('Selected interval evidence', content, 'interval-evidence'));
    }
    section.append(provenance(view));
    const readouts = view.readouts ?? {};
    const counts = [readouts.lens_count, readouts.capture_count, readouts.patch_count];
    const internal = element('div', 'stack');
    if (counts.every(value => value === 0)) internal.append(element('p', 'notice', 'No saved lens, capture or patch artifacts are present. This investigation provides outcome evidence, with no saved internal readout or causal intervention to inspect.'));
    else {
      internal.append(definitions([['Saved lenses', readouts.lens_count], ['Saved captures', readouts.capture_count], ['Saved patches', readouts.patch_count]]));
      internal.append(element('p', 'small muted', 'These saved artifacts are included in the original export. This viewer does not interpret their contents or infer causality from their presence.'));
    }
    section.append(detail('Saved internal readouts', internal, 'internal-readouts'));
    return section;
  }

  function footer() {
    const node = element('footer', 'stack');
    const actions = element('div', 'actions');
    const download = button(exporting ? 'Verifying export…' : 'Download original evidence', exportOriginal, '', exporting);
    download.id = 'download-evidence'; actions.append(download);
    if (readyExport) {
      const ready = button('Download verified JSON', openVerifiedDownload, 'primary', exporting);
      ready.id = 'download-verified-json'; actions.append(ready);
    }
    const status = element('p', 'export-status muted', exportState); status.id = 'export-status'; status.setAttribute('role', 'status');
    actions.append(status); node.append(actions);
    node.append(element('p', 'small muted', 'Export preserves the original JSON, including unredacted prompt and response text. Review before sharing. No model execution is started by this viewer.'));
    node.append(element('p', 'small muted', 'Sampling and reconstruction build on Goodfire’s Forking Fast.'));
    return detail('Original evidence export', node, 'export', 'export-disclosure');
  }

  function methodFooter() {
    const node = element('footer', 'method-footer');
    node.append(element('span', '', `${view.checkpoints_total ?? view.checkpoints.length} saved checkpoints`));
    if (model?.N) node.append(element('span','', `Saved step N=${model.N}`));
    const method = model?.reconstruction?.provenance;
    if (method?.method) node.append(element('span','', method.method));
    if (method?.parameters) for (const [name,value] of Object.entries(method.parameters)) if (['h','pen','variant'].includes(name)) node.append(element('span','', `${name}=${value}`));
    node.append(element('span','', 'Method: Goodfire’s Forking Fast'));
    return node;
  }

  function render() {
    const focusedId = !resetFocus && root.contains(document.activeElement) ? document.activeElement?.id : '';
    const panel = element('article', `panel ${expanded ? 'expanded' : 'compact'}`);
    panel.setAttribute('aria-busy', loading ? 'true' : 'false');
    if (error) {
      const block = element('div', 'error stack');
      block.setAttribute('role', 'alert');
      block.append(element('div', 'state-eyebrow', 'Fork Microscope'));
      const heading = element('h2', 'state-title', 'Saved evidence could not be opened'); heading.id = 'error-heading'; heading.tabIndex = -1;
      block.append(heading, element('p', 'muted', error.message || 'The host returned an error.'));
      block.append(element('p', 'small muted', 'Previous evidence has been cleared so it cannot be mistaken for a current result. If the file changed, reopen the investigation from the conversation.'));
      if (retryArgs) {
        const retry = button('Retry pinned read', () => request(retryArgs, retryArgs.offset ? 'page-heading' : 'selected-heading'), 'primary');
        retry.id = 'retry-read'; block.append(retry);
      }
      panel.append(block);
    } else if (!view) {
      const block = element('div', 'loading stack'); block.setAttribute('role', 'status');
      block.append(element('div', 'state-eyebrow', 'Fork Microscope'), element('h2', 'state-title', loading ? 'Reading saved evidence…' : 'Waiting for saved evidence'), element('p', 'muted small', 'Waiting for the host to provide a validated saved-evidence result.'));
      panel.append(block);
    } else {
      panel.append(header());
      if (loading) {
        const target = pendingArgs?.token_position;
        const page = pendingArgs?.offset ? `, continuation page starting at ${pendingArgs.offset + 1}` : '';
        const status = element('p', 'notice pending-status', `Reading saved checkpoint t=${target ?? 'requested'}${page}. Showing previously verified t=${view.selection.token_position ?? 'unknown'} until the read completes; controls are disabled.`);
        status.setAttribute('role', 'status');
        panel.append(status);
      }
      const importedNotice = importedEvidenceNotice(view);
      if (importedNotice) panel.append(element('p', 'notice', importedNotice));
      panel.append(element('p', 'small muted method-notice', 'Outcome labels are not correctness or proof of a causal decision point. This viewer checks saved evidence; it does not check model readiness or independently reproduce the run.'));
      if (view.status === 'ready') {
        panel.append(graphSection(), selectedSection());
        const evidence = element('section', 'evidence-area');
        evidence.append(contrastSection(), pageSection(), evidenceDetails(), footer()); panel.append(evidence);
      } else {
        const section = element('section', 'stack');
        const heading = element('h2', '', view.status === 'empty' ? 'No saved checkpoint evidence' : 'This saved pass needs a different evidence view');
        heading.id = 'selected-heading'; heading.tabIndex = -1; section.append(heading);
        section.append(element('p', 'notice', view.status === 'empty' ? 'This investigation has no compatible saved observations to display.' : 'Raw observed frequencies are unavailable for this sampling design. Weighted values are not shown as observed counts.'));
        panel.append(section);
        const evidence = element('section','evidence-area'); evidence.append(evidenceDetails(),footer()); panel.append(evidence);
      }
      panel.append(methodFooter());
    }
    for (const node of panel.querySelectorAll('details')) {
      const key = node.getAttribute('data-disclosure');
      if (!key) continue;
      node.open = disclosures.get(key) === true;
      node.addEventListener('toggle', () => {
        if (!root.contains(node)) return;
        if (loading) { node.open = disclosures.get(key) === true; return; }
        disclosures.set(key, node.open);
        bridge.resize?.();
      });
      const summary = node.querySelector('summary');
      if (loading) {
        summary.setAttribute('aria-disabled', 'true');
        summary.tabIndex = -1;
        summary.addEventListener('click', event => event.preventDefault());
        summary.addEventListener('keydown', event => { if (event.key === ' ' || event.key === 'Enter') event.preventDefault(); });
      }
    }
    if (loading) for (const control of panel.querySelectorAll('button,input,select')) control.disabled = true;
    root.replaceChildren(panel);
    resetFocus = false;
    if (focusedId) {
      const target = document.getElementById(focusedId);
      if (target && !target.disabled && target.getAttribute('aria-disabled') !== 'true') target.focus({ preventScroll: true });
      else if (loading) document.getElementById(focusedId.includes('page') ? 'page-heading' : 'selected-heading')?.focus({ preventScroll: true });
      else if (error) document.getElementById('error-heading')?.focus({ preventScroll: true });
    }
    bridge.resize?.();
  }
  if (typeof ResizeObserver === 'function') {
    widthObserver = new ResizeObserver(() => {
      const bucket = root.clientWidth > 0 && root.clientWidth < 520 ? 'narrow' : 'wide';
      if (bucket !== chartBucket) { chartBucket = bucket; render(); }
    });
    widthObserver.observe(root);
  }
  bridge.start();
  return { receive, render, destroy() { exportGeneration++; readyExport = null; widthObserver?.disconnect(); bridge.dispose?.(); } };
}

if (typeof document !== 'undefined' && document.getElementById('app')) createViewer(document.getElementById('app'));
