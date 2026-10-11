import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { observedPoints, formatMass, checkpointIndex, validateView, createRequestGate, retrieveOriginalBytes, importedEvidenceNotice, createViewer } from '../ui/viewer.mjs';

const hash = 'a'.repeat(64);
test('trusted import notice visibly distinguishes synthetic files without execution or cleanup claims', () => {
  assert.equal(importedEvidenceNotice({bundle:{provenance:{source:'user_file',synthetic:true,execution_verified:false}}}), 'Imported synthetic evidence. This file does not verify a platform run or compute cleanup.');
  assert.equal(importedEvidenceNotice({bundle:{provenance:{source:'user_file',synthetic:false}}}), 'Imported saved evidence. This file does not verify a platform run or compute cleanup.');
  assert.equal(importedEvidenceNotice({bundle:{provenance:{source:'<script>unsafe</script>',synthetic:true}}}), null);
});
function view(overrides = {}) {
  return {
    schema: 'fork-microscope-view-v1', status: 'ready', bundle: { bundle_id: 'saved.json', file_sha256: hash },
    selection: { run_index: 0, pass_index: 0, token_position: 16 }, run: { categories: ['Left', 'Right', 'Other'] },
    checkpoints: [{ t: 16, counts_kind: 'raw_sampled_frequencies', n: 20, counts: { Left: 13, Right: 6, Other: 1 } }],
    selected: { continuations: { offset: 0, limit: 5, total: 20, items: [], next_offset: 5 } }, ...overrides,
  };
}

test('graph uses actual labels and observed raw counts, and never substitutes absent values', () => {
  const input = view();
  assert.deepEqual(observedPoints(input).map(point => [point.label, point.value]), [['Left', .65], ['Right', .3], ['Other', .05]]);
  input.checkpoints[0].counts.Other = undefined;
  assert.throws(() => observedPoints(input), /complete/);
  input.checkpoints[0].counts_kind = 'weighted_branch_estimates';
  assert.throws(() => observedPoints(input), /complete/);
  input.checkpoints[0].counts_kind = 'raw_sampled_frequencies';
  input.checkpoints[0].counts.Left = 21;
  assert.throws(() => observedPoints(input), /complete/);
});

test('near-one and tiny candidate masses are never rounded to certainty or zero', () => {
  assert.equal(formatMass(.9999740131), '>99.9%');
  assert.equal(formatMass(1 - .9999740131), '<0.1%');
  assert.equal(formatMass(.6055173452), '≈60.6%');
  assert.equal(formatMass(0), '0%');
  assert.equal(formatMass(1), '100%');
  assert.equal(formatMass(null), 'Not saved');
  assert.equal(formatMass(NaN), 'Not saved');
  assert.equal(formatMass(1.01), 'Not saved');
});

test('checkpoint keys follow the actual checkpoint list and wrap without inventing token positions', () => {
  assert.equal(checkpointIndex('ArrowRight', 1, 6), 2);
  assert.equal(checkpointIndex('ArrowDown', 5, 6), 0);
  assert.equal(checkpointIndex('ArrowLeft', 0, 6), 5);
  assert.equal(checkpointIndex('ArrowUp', 4, 6), 3);
  assert.equal(checkpointIndex('Home', 4, 6), 0);
  assert.equal(checkpointIndex('End', 1, 6), 5);
  assert.equal(checkpointIndex('Tab', 1, 6), -1);
  assert.equal(checkpointIndex('ArrowRight', 0, 0), -1);
});

test('view identity rejects changed or malformed evidence and accepts explicit empty bundles', () => {
  assert.equal(validateView(view(), hash).schema, 'fork-microscope-view-v1');
  assert.throws(() => validateView(view(), 'b'.repeat(64)), /Evidence changed/);
  assert.throws(() => validateView({ schema: 'fixture-only' }), /supported saved-evidence/);
  assert.throws(() => validateView(view({ selected: null })), /selected checkpoint/);
  assert.equal(validateView(view({ status: 'empty', run: null, selected: null, checkpoints: [] })).status, 'empty');
  assert.equal(validateView(view({ status: 'unsupported', selected: null, checkpoints: [] })).status, 'unsupported');
});

test('late checkpoint or page replies cannot become the latest selection', () => {
  const gate = createRequestGate();
  const page = gate.begin(), checkpoint = gate.begin();
  assert.equal(gate.current(page), false);
  assert.equal(gate.current(checkpoint), true);
  gate.invalidate();
  assert.equal(gate.current(checkpoint), false);
});

async function exportFixture(size = 100100) {
  const bytes = Uint8Array.from({ length: size }, (_, index) => index % 251);
  const digest = new Uint8Array(await webcrypto.subtle.digest('SHA-256', bytes));
  const file_sha256 = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('');
  const bundle = { bundle_id: 'saved.json', bytes: size, file_sha256 };
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    const chunk = bytes.subarray(args.offset, args.offset + args.length);
    const end = args.offset + chunk.length;
    return { bundle: { ...bundle }, offset: args.offset, bytes: chunk.length, encoding: 'base64', data: Buffer.from(chunk).toString('base64'), next_offset: end < size ? end : null };
  };
  return { bytes, bundle, callTool, calls };
}

test('original export retrieves hash-pinned bounded chunks and verifies complete bytes', async () => {
  const fixture = await exportFixture();
  const progress = [];
  const downloaded = await retrieveOriginalBytes(fixture.callTool, fixture.bundle, { cryptoObject: webcrypto, onProgress: received => progress.push(received) });
  assert.deepEqual(downloaded, fixture.bytes);
  assert.equal(fixture.calls.length, 3);
  assert.deepEqual(progress, [49152, 98304, 100100]);
  for (const { name, args } of fixture.calls) {
    assert.equal(name, 'fork_microscope_read_investigation_bytes');
    assert.equal(args.length, 49152);
    assert.equal(args.expected_file_sha256, fixture.bundle.file_sha256);
  }
});

test('export rejects identity changes and missing or reordered chunks', async () => {
  const fixture = await exportFixture();
  for (const corrupt of [
    page => { page.bundle.file_sha256 = 'b'.repeat(64); },
    page => { page.bundle.bundle_id = 'different.json'; },
    page => { page.bundle.bytes++; },
    page => { page.offset++; },
    page => { page.next_offset = null; },
    page => { page.bytes--; },
    page => { page.data = '%bad-base64'; },
  ]) {
    await assert.rejects(retrieveOriginalBytes(async (...args) => {
      const page = await fixture.callTool(...args); corrupt(page); return page;
    }, fixture.bundle, { cryptoObject: webcrypto }), /Export stopped/);
  }
});

test('export rejects full-content hash mismatch even when every chunk identity claims the expected hash', async () => {
  const fixture = await exportFixture(50);
  await assert.rejects(retrieveOriginalBytes(async (...args) => {
    const page = await fixture.callTool(...args);
    const changed = Buffer.from(page.data, 'base64'); changed[0] ^= 255; page.data = changed.toString('base64');
    return page;
  }, fixture.bundle, { cryptoObject: webcrypto }), /complete file checksum/);
});

test('export fails before requesting bytes when size exceeds 64 MiB or hashing is unavailable', async () => {
  let called = false;
  const call = async () => { called = true; };
  await assert.rejects(retrieveOriginalBytes(call, { bundle_id: 'saved.json', file_sha256: hash, bytes: 64 * 1024 * 1024 + 1 }), /at most 64 MiB/);
  await assert.rejects(retrieveOriginalBytes(call, { bundle_id: 'saved.json', file_sha256: hash, bytes: 8 }, { cryptoObject: {} }), /cannot verify/);
  assert.equal(called, false);
});

test('renderer has no HTML injection or external resource construction paths', async () => {
  const source = await readFile(new URL('../ui/viewer.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\b(?:innerHTML|outerHTML|insertAdjacentHTML|eval|fetch|XMLHttpRequest)\b/);
  assert.doesNotMatch(source.replaceAll('http://www.w3.org/2000/svg', ''), /https?:\/\//);
  assert.match(source, /textContent/);
  assert.match(source, /stop_reason_evidence/);
});

// A small event-capable DOM exercises the real renderer and bridge callbacks
// without adding a frontend runtime dependency. Browser layout is checked
// separately; this harness makes no claims about native accessibility behavior.
class TestNode {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = new Map(); this.listeners = new Map(); this.parentNode = null; this._text = ''; this.id = ''; this.className = ''; this.disabled = false; this.open = false; this.style = {}; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get childElementCount() { return this.children.length; }
  append(...children) { for (const child of children) { if (child.parentNode) child.remove(); child.parentNode = this; this.children.push(child); } }
  replaceChildren(...children) { if (typeof document !== 'undefined' && document.activeElement !== this && this.contains(document.activeElement)) document.activeElement = document.body; for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  remove() { if (!this.parentNode) return; const parent = this.parentNode; parent.children = parent.children.filter(child => child !== this); this.parentNode = null; }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  addEventListener(type, handler) { const list = this.listeners.get(type) ?? []; list.push(handler); this.listeners.set(type, list); }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  querySelectorAll(selector) { const tags = selector.split(',').map(tag => tag.toUpperCase()); return descendants(this).filter(node => tags.includes(node.tagName)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  focus(options = {}) { if (!this.disabled) { this.focusOptions = options; document.activeElement = this; } }
  dispatch(type, properties = {}) { if (this.disabled && type === 'click') return; const event = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties }; for (const handler of this.listeners.get(type) ?? []) handler(event); return event; }
  click() { this.dispatch('click'); }
}
function descendants(node) { return node.children.flatMap(child => [child, ...descendants(child)]); }
function rendererFixture(input = interactiveView(), bridgeExtras = {}) {
  const oldDocument = globalThis.document, oldNode = globalThis.Node;
  const body = new TestNode('body'), root = new TestNode('main'); root.clientWidth = 700; body.append(root);
  globalThis.Node = TestNode;
  globalThis.document = { body, documentElement: { dataset: {} }, activeElement: body, createElement: tag => new TestNode(tag), createElementNS: (_namespace, tag) => new TestNode(tag), getElementById: id => descendants(body).find(node => node.id === id) ?? null };
  let callbacks; const reads = [], modes = [], states = [];
  const viewer = createViewer(root, options => {
    callbacks = options;
    return { start() {}, resize() {}, saveState(state) { states.push(state); }, requestDisplayMode(mode) { modes.push(mode); }, callTool(name, args) { return new Promise((resolve, reject) => reads.push({ name, args, resolve, reject })); }, ...bridgeExtras };
  });
  callbacks.onResult(input);
  return { root, reads, modes, states, callbacks, viewer, byId: id => document.getElementById(id), find: predicate => descendants(root).find(predicate), all: predicate => descendants(root).filter(predicate), restore() { globalThis.document = oldDocument; globalThis.Node = oldNode; } };
}
function interactiveView({ t = 16, offset = 0, bundleHash = hash, bundleId = 'saved.json', runIndex = 0, passIndex = 0 } = {}) {
  const row = draw_index => ({ draw_index, label: draw_index % 2 ? 'Right' : 'Left', text: `Saved continuation ${draw_index + 1} at t=${t}`, text_pointer: `/t/${t}/draw/${draw_index}/text`, pointer: `/t/${t}/draw/${draw_index}`, label_source: 'saved matcher', stop_reason: 'eos', stop_reason_evidence: 'inferred from saved record', classification_status: 'completed', tok_id: 7, generated_tokens: 9 });
  const items = Array.from({ length: Math.min(5, 12 - offset) }, (_unused, index) => row(offset + index));
  return view({ bundle: { bundle_id: bundleId, file_sha256: bundleHash, bytes: 80, provenance: { source: 'user_file', synthetic: true, execution_verified: false } }, selection: { run_index: runIndex, pass_index: passIndex, token_position: t }, run: { categories: ['Left', 'Right', 'Other'], model_id: 'fixture-only', prompt: { text: 'Saved prompt', pointer: '/prompt' }, original_response: { text: 'Saved response', pointer: '/response' } }, checkpoints: [0, 16, 32].map(position => ({ t: position, counts_kind: 'raw_sampled_frequencies', n: 20, counts: { Left: 13, Right: 6, Other: 1 }, candidates: 4, retained_mass: .9999740131 })), selected: { shared_prefix: { text: 'Saved prefix', pointer: `/prefix/${t}` }, contrast: [row(0), row(1)], continuations: { offset, limit: 5, total: 12, items, next_offset: offset + items.length < 12 ? offset + items.length : null } }, readouts: { lens_count: 0, capture_count: 0, patch_count: 0 }, limitations: ['Fixture-only saved observations; no model execution.'] });
}
const settle = () => new Promise(resolve => setImmediate(resolve));
function openDisclosure(fixture, key) { const node = fixture.find(item => item.getAttribute('data-disclosure') === key); node.open = true; node.dispatch('toggle'); return node; }
function expandedFixture(input) { const fixture = rendererFixture(input); fixture.byId('explore-investigation').click(); return fixture; }

test('approved compact renderer orders graph, linked interval and optional evidence without synthetic fallback', () => {
  const fixture = rendererFixture();
  try {
    assert.match(fixture.root.textContent, /Where the answer forks/);
    assert.match(fixture.root.textContent, /Imported synthetic evidence/);
    assert.match(fixture.root.textContent, /not correctness or proof of a causal decision point/);
    assert.equal(fixture.byId('mode-smooth').disabled, true);
    assert.match(fixture.root.textContent, /Outcome mix · t=16 → 32/);
    assert.match(fixture.root.textContent, /Distribution difference · TVD/);
    assert.match(fixture.root.textContent, /does not track individual outcomes switching/);
    const classes = fixture.root.children[0].children.map(child => child.className);
    assert.ok(classes.indexOf('timeline-section') < classes.indexOf('selected-section'));
    assert.ok(classes.indexOf('selected-section') < classes.indexOf('evidence-area'));
    assert.equal(fixture.find(node => node.className === 'more-continuations').open, false);
    openDisclosure(fixture, 'provenance');
    assert.match(fixture.root.textContent, />99.9%/);
    assert.match(fixture.root.textContent, /<0.1%/);
    fixture.byId('explore-investigation').click();
    assert.equal(document.activeElement.id, 'selected-heading');
    fixture.byId('collapse-investigation').click();
    assert.equal(document.activeElement.id, 'explore-investigation');
    const noPair = interactiveView(); noPair.selected.contrast = []; noPair.selected.contrast_reason = 'No pair with distinct resolved labels was saved.';
    fixture.callbacks.onResult(noPair);
    assert.match(fixture.root.textContent, /No pair with distinct resolved labels was saved/);
    assert.doesNotMatch(fixture.root.textContent, /Two saved examples/);
    assert.equal(fixture.reads.length, 0);
  } finally { fixture.restore(); }
});

test('timeline pointer and keyboard requests pin identity, retain explicitly previous context and restore focus', async () => {
  const fixture = expandedFixture();
  try {
    fixture.find(node => node.getAttribute('class') === 'checkpoint-target').click();
    assert.equal(fixture.reads.length, 1);
    assert.deepEqual(fixture.reads[0].args, { bundle_id: 'saved.json', run_index: 0, pass_index: 0, token_position: 0, offset: 0, limit: 5, expected_file_sha256: hash });
    assert.equal(fixture.reads[0].name, 'fork_microscope_get_investigation_view');
    assert.equal(fixture.root.children[0].getAttribute('aria-busy'), 'true');
    assert.match(fixture.root.textContent, /Showing previously verified t=16/);
    assert.match(fixture.root.textContent, /Response around t=16 → 32/);
    assert.doesNotMatch(fixture.root.textContent, /Outcome mix · t=0 → 16/);
    assert.ok(fixture.all(node => ['BUTTON', 'INPUT'].includes(node.tagName)).every(node => node.disabled));
    fixture.find(node => node.getAttribute('class') === 'checkpoint-target').click();
    assert.equal(fixture.reads.length, 1);
    fixture.reads[0].resolve(interactiveView({ t: 0 })); await settle();
    assert.equal(fixture.byId('checkpoint-0').checked, true);
    assert.equal(document.activeElement.id, 'checkpoint-0');
    const event = fixture.byId('checkpoint-0').dispatch('keydown', { key: 'End' });
    assert.equal(event.defaultPrevented, true);
    assert.equal(fixture.reads[1].args.token_position, 32);
    fixture.reads[1].resolve(interactiveView({ t: 32 })); await settle();
    assert.equal(document.activeElement.id, 'checkpoint-32');
  } finally { fixture.restore(); }
});

test('pagination disclosure persists across reads and context rerenders, with disabled boundary focus fallback', async () => {
  const fixture = expandedFixture();
  try {
    openDisclosure(fixture, 'all-continuations');
    const provenance = openDisclosure(fixture, 'provenance');
    provenance.children[0].focus();
    fixture.callbacks.onContext({ theme: 'light' });
    assert.equal(document.activeElement.id, 'disclosure-provenance');
    assert.equal(fixture.find(node => node.getAttribute('data-disclosure') === 'provenance').open, true);
    fixture.byId('next-page').focus(); fixture.byId('next-page').click();
    assert.equal(fixture.reads[0].args.offset, 5);
    assert.equal(fixture.find(node => node.className === 'more-continuations').open, true);
    fixture.reads[0].resolve(interactiveView({ offset: 5 })); await settle();
    assert.equal(fixture.find(node => node.className === 'more-continuations').open, true);
    assert.equal(document.activeElement.id, 'next-page');
    fixture.byId('next-page').click(); fixture.reads[1].resolve(interactiveView({ offset: 10 })); await settle();
    assert.equal(fixture.byId('next-page').disabled, true);
    assert.equal(document.activeElement.id, 'page-heading');
    assert.match(fixture.root.textContent, /11–12 of 12/);
  } finally { fixture.restore(); }
});

test('disclosure state remains scoped to bundle identity, run and pass, and host results cancel late reads', async () => {
  const fixture = expandedFixture();
  try {
    for (const input of [interactiveView({ bundleHash: 'b'.repeat(64) }), interactiveView({ bundleHash: 'b'.repeat(64), runIndex: 1 }), interactiveView({ bundleHash: 'b'.repeat(64), runIndex: 1, passIndex: 1 })]) {
      openDisclosure(fixture, 'all-continuations');
      fixture.callbacks.onResult(input);
      assert.equal(fixture.find(node => node.className === 'more-continuations').open, false);
    }
    fixture.byId('checkpoint-0').checked = true; fixture.byId('checkpoint-0').dispatch('change');
    fixture.callbacks.onResult(interactiveView({ t: 32, bundleHash: 'c'.repeat(64), bundleId: 'replacement.json' }));
    fixture.reads[0].resolve(interactiveView({ t: 0, bundleHash: 'b'.repeat(64), runIndex: 1, passIndex: 1 })); await settle();
    assert.match(fixture.root.textContent, /Selected checkpoint · t=32/);
    assert.doesNotMatch(fixture.root.textContent, /Showing previously verified/);
    assert.equal(fixture.byId('checkpoint-32').checked, true);
  } finally { fixture.restore(); }
});

test('failed and mismatched page replies clear all previous evidence, and retry reuses the pinned request', async () => {
  for (const failure of ['host failure', 'selection mismatch', 'hash mismatch']) {
    const fixture = expandedFixture();
    try {
      openDisclosure(fixture, 'all-continuations'); fixture.byId('next-page').click();
      if (failure === 'host failure') fixture.reads[0].reject(new Error('Host timeout'));
      else fixture.reads[0].resolve(interactiveView(failure === 'selection mismatch' ? { offset: 5, t: 0 } : { offset: 5, bundleHash: 'b'.repeat(64) }));
      await settle();
      assert.match(fixture.root.textContent, /Previous evidence has been cleared/);
      assert.doesNotMatch(fixture.root.textContent, /Saved continuation|Distribution difference/);
      const retry = fixture.byId('retry-read'); retry.focus(); retry.click();
      assert.deepEqual(fixture.reads[1].args, fixture.reads[0].args);
      fixture.reads[1].resolve(interactiveView({ offset: 5 })); await settle();
      assert.match(fixture.root.textContent, /Response around t=16 → 32/);
      assert.equal(document.activeElement.id, 'page-heading');
      assert.equal(fixture.find(node => node.className === 'more-continuations').open, true);
    } finally { fixture.restore(); }
  }
});

test('new host evidence cancels an in-flight verified export without creating a download', async () => {
  const fixture = expandedFixture();
  try {
    fixture.byId('download-evidence').click();
    assert.equal(fixture.reads[0].name, 'fork_microscope_read_investigation_bytes');
    const bundle = { bundle_id: 'saved.json', file_sha256: hash, bytes: 80 };
    fixture.callbacks.onResult(interactiveView({ bundleId: 'replacement.json', bundleHash: 'b'.repeat(64) }));
    fixture.reads[0].resolve({ bundle, offset: 0, bytes: 80, encoding: 'base64', data: Buffer.alloc(80).toString('base64'), next_offset: null });
    await settle();
    assert.equal(fixture.all(node => node.tagName === 'A').length, 0);
    assert.equal(fixture.byId('download-evidence').disabled, false);
    assert.equal(fixture.byId('export-status').textContent, '');
  } finally { fixture.restore(); }
});

function withSavedReconstruction(input = interactiveView()) {
  input.run.token_count = 40; input.run.checkpoint_step = 8;
  input.run.token_context = { items: Array.from({length:40},(_v,t) => ({ t, text:` word${t}`, pointer:`/tokens/${t}`, text_truncated:false })), token_start:0, token_end:40, total_tokens:40, truncated:false, concatenates_to_response:false, pointer:'/tokens' };
  input.reconstruction = { available:true, reason:null, points:Array.from({length:33},(_v,t) => ({t, probabilities:[.75-t*.01,.2+t*.008,.05+t*.002],pointer:`/curve/${t}`})), provenance:{ method:'M5a_segkernel',parameters:{h:8,pen:1,variant:'linear'},tuning:'cv',fit_status:'complete',upstream_commit:'c'.repeat(40),otrecon_version:'1.0',pointer:'/curve' }, limitations:['Saved estimates are not additional observations.'] };
  return input;
}
function chooseRadio(fixture,id) { const node=fixture.byId(id); node.checked=true; node.dispatch('change'); }

test('mode and threshold radios preserve focus and smooth graph/context/mix use one exact saved interval', async () => {
  const fixture=rendererFixture(withSavedReconstruction());
  try {
    assert.equal(fixture.byId('mode-smooth').disabled,false);
    chooseRadio(fixture,'mode-smooth');
    assert.equal(document.activeElement.id,'mode-smooth');
    assert.match(fixture.root.textContent,/Response around t=16 → 24/);
    assert.match(fixture.root.textContent,/Outcome mix · t=16 → 24/);
    const highlighted=fixture.all(node=>node.tagName==='SPAN' && node.className.includes('token-selected')).map(node=>node.textContent);
    assert.deepEqual(highlighted,Array.from({length:8},(_v,index)=>` word${16+index}`));
    assert.match(fixture.root.textContent,/h=8/); assert.match(fixture.root.textContent,/pen=1/);
    fixture.byId('threshold-15').dispatch('keydown',{key:'End'});
    assert.equal(document.activeElement.id,'threshold-20');
    assert.equal(fixture.byId('threshold-20').checked,true);
    const select=fixture.byId('token-selection'); select.value='17'; select.dispatch('change');
    assert.equal(document.activeElement.id,'token-selection');
    assert.match(fixture.root.textContent,/Response around t=17 → 25/);
    assert.match(fixture.root.textContent,/Outcome mix · t=17 → 25/);
    assert.match(fixture.root.textContent,/Contrasting continuations · saved checkpoint t=16/);
    assert.equal(fixture.reads.length,0);
    openDisclosure(fixture,'all-continuations'); fixture.byId('next-page').click();
    fixture.reads[0].resolve(withSavedReconstruction(interactiveView({offset:5}))); await settle();
    assert.match(fixture.root.textContent,/Outcome mix · t=17 → 25/);
    assert.equal(document.activeElement.id,'next-page');
    chooseRadio(fixture,'mode-raw');
    assert.match(fixture.root.textContent,/Outcome mix · t=16 → 32/);
    assert.doesNotMatch(fixture.root.textContent,/Outcome mix · t=17/);
  } finally {fixture.restore();}
});

test('irregular raw intervals show actual positions, denominators and terminal missing endpoint', () => {
  const input=interactiveView({t:7}); input.checkpoints=[
    {t:0,counts_kind:'raw_sampled_frequencies',n:10,counts:{Left:8,Right:1,Other:1}},
    {t:7,counts_kind:'raw_sampled_frequencies',n:5,counts:{Left:3,Right:1,Other:1}},
    {t:20,counts_kind:'raw_sampled_frequencies',n:20,counts:{Left:13,Right:6,Other:1}},
  ];
  const fixture=rendererFixture(input);
  try {
    assert.match(fixture.root.textContent,/Outcome mix · t=7 → 20/);
    assert.match(fixture.root.textContent,/13-token interval/);
    assert.match(fixture.root.textContent,/Before · t=7 · n=5/);
    assert.match(fixture.root.textContent,/After · t=20 · n=20/);
    chooseRadio(fixture,'threshold-20');
    assert.match(fixture.root.textContent,/No measured local shift exceeds this threshold; this does not establish a globally steady response/);
    const terminal=structuredClone(input); terminal.selection.token_position=20; fixture.callbacks.onResult(terminal);
    assert.match(fixture.root.textContent,/No supported forward interval starts here/);
    assert.doesNotMatch(fixture.root.textContent,/Outcome mix · t=7 → 20/);
  } finally {fixture.restore();}
});

test('missing data and malformed count totals never render a synthetic curve', () => {
  const fixture=rendererFixture();
  try {
    const broken=interactiveView(); broken.checkpoints[0].counts={Left:6,Right:3,Other:1};
    fixture.callbacks.onResult(broken);
    assert.match(fixture.root.textContent,/Saved evidence could not be opened/);
    assert.doesNotMatch(fixture.root.textContent,/Where the answer forks|Saved continuation|Outcome mix/);
    fixture.callbacks.onResult(null);
    assert.match(fixture.root.textContent,/supported saved-evidence view/);
    assert.equal(fixture.reads.length,0);
  } finally {fixture.restore();}
});

test('smooth local selection resets with source identity and every pending select control is disabled', async () => {
  const fixture=rendererFixture(withSavedReconstruction());
  try {
    chooseRadio(fixture,'mode-smooth'); fixture.byId('token-selection').value='17'; fixture.byId('token-selection').dispatch('change');
    fixture.byId('checkpoint-0').dispatch('keydown',{key:'Home'});
    assert.ok(fixture.all(node=>['BUTTON','INPUT','SELECT'].includes(node.tagName)).every(node=>node.disabled));
    fixture.callbacks.onResult(withSavedReconstruction(interactiveView({t:16,bundleHash:'b'.repeat(64)})));
    assert.equal(fixture.byId('mode-raw').checked,true);
    assert.match(fixture.root.textContent,/Outcome mix · t=16 → 32/);
    fixture.reads[0].resolve(withSavedReconstruction(interactiveView({t:0}))); await settle();
    assert.equal(fixture.byId('checkpoint-16').checked,true);
  } finally {fixture.restore();}
});


test('adaptive TVD plot labels its actual range and scales peaks without changing measured values', () => {
  const input=interactiveView();
  input.checkpoints[0].counts={Left:16,Right:2,Other:2};
  input.checkpoints[1].counts={Left:13,Right:6,Other:1};
  input.checkpoints[2].counts={Left:17,Right:1,Other:2};
  const fixture=rendererFixture(input);
  try {
    assert.match(fixture.root.textContent,/Distribution difference · TVD · 0–30%/);
    const bars=fixture.all(node=>node.tagName==='RECT' && node.getAttribute('class')?.startsWith('signal-bar'));
    assert.equal(bars.length,2);
    assert.ok(Math.abs(Number(bars[1].getAttribute('height'))-50)<1e-9);
    assert.match(fixture.root.textContent,/Shift 1 · t=16 → 32 · 25(?:\.0)?%/);
    assert.match(fixture.root.textContent,/Distribution difference · TVD 25(?:\.0)?%/);
    const threshold=fixture.find(node=>node.getAttribute('class')==='threshold-line');
    assert.equal(Number(threshold.getAttribute('y1')),228);
    const fullRange=structuredClone(input);
    fullRange.checkpoints[0].counts={Left:20,Right:0,Other:0};
    fullRange.checkpoints[1].counts={Left:0,Right:20,Other:0};
    fixture.callbacks.onResult(fullRange);
    assert.match(fixture.root.textContent,/Distribution difference · TVD · 0–100%/);
    const fullBars=fixture.all(node=>node.tagName==='RECT' && node.getAttribute('class')?.startsWith('signal-bar'));
    assert.equal(Number(fullBars[0].getAttribute('height')),60);
    assert.match(fixture.root.textContent,/t=0 → 16 · 100%/);
  } finally {fixture.restore();}
});


async function resolveByteRead(fixture, bytesFixture) {
  const read=fixture.reads.at(-1); read.resolve(await bytesFixture.callTool(read.name,read.args));
  for (let index=0;index<20;index++) { await new Promise(resolve=>setTimeout(resolve,1)); if (fixture.byId('download-verified-json') || fixture.byId('export-status')?.textContent.includes('No device')) break; }
}

test('verified host export prepares exact bytes then requires a separate user download action', async () => {
  const bytesFixture=await exportFixture(80), input=interactiveView(); input.bundle={...input.bundle,...bytesFixture.bundle};
  const prepared=[],opened=[];
  const fixture=rendererFixture(input,{fileExportAvailable:()=>true,async prepareFileDownload(bytes,name,current) {assert(current());prepared.push({bytes,name});return 'https://files.example/fixture.json?signature=fixture';},async openFileDownload(url,current) {assert(current());opened.push(url);}});
  try {
    openDisclosure(fixture,'export'); fixture.byId('download-evidence').click();
    assert.equal(prepared.length,0); assert.equal(fixture.byId('download-verified-json'),null);
    await resolveByteRead(fixture,bytesFixture);
    assert.equal(prepared.length,1); assert.deepEqual(prepared[0].bytes,bytesFixture.bytes);
    assert.equal(prepared[0].name,`fork-investigation-${bytesFixture.bundle.file_sha256.slice(0,12)}.json`);
    assert.equal(opened.length,0); assert.equal(fixture.all(node=>node.tagName==='A').length,0);
    assert.doesNotMatch(fixture.root.textContent,/files\.example|signature=fixture/);
    fixture.byId('download-verified-json').click(); await settle();
    assert.equal(opened.length,1); assert.match(fixture.byId('export-status').textContent,/saving to your device has not been confirmed/);
    assert.equal(fixture.states.length,0);
    fixture.callbacks.onResult(interactiveView({bundleHash:'b'.repeat(64)}));
    assert.equal(fixture.byId('download-verified-json'),null);
  } finally {fixture.restore();}
});

test('host export rejects unverified bytes and stale prepared links without a premature file claim', async () => {
  const bytesFixture=await exportFixture(80), input=interactiveView(); input.bundle={...input.bundle,...bytesFixture.bundle};
  let prepare=0,resolvePrepared;
  const fixture=rendererFixture(input,{fileExportAvailable:()=>true,prepareFileDownload(_bytes,_name,current) {prepare++;return new Promise(resolve=>{resolvePrepared=()=>{assert.equal(current(),false);resolve('https://files.example/fixture.json');};});}});
  try {
    fixture.byId('download-evidence').click(); await resolveByteRead(fixture,bytesFixture);
    assert.equal(prepare,1); assert.equal(fixture.byId('download-verified-json'),null);
    assert.doesNotMatch(fixture.byId('export-status').textContent,/download is ready|saved to|created file/i);
    fixture.callbacks.onResult(interactiveView({bundleHash:'b'.repeat(64)})); resolvePrepared(); await settle();
    assert.equal(fixture.byId('download-verified-json'),null); assert.equal(fixture.byId('export-status').textContent,'');
    fixture.callbacks.onResult(input); fixture.byId('download-evidence').click();
    const read=fixture.reads.at(-1), page=await bytesFixture.callTool(read.name,read.args);page.data=Buffer.alloc(80).toString('base64');read.resolve(page);
    for(let index=0;index<20;index++)await new Promise(resolve=>setTimeout(resolve,1));
    assert.equal(prepare,1);assert.equal(fixture.byId('download-verified-json'),null);
    assert.match(fixture.root.textContent,/checksum did not match/);assert.doesNotMatch(fixture.root.textContent,/Outcome mix/);
  } finally {fixture.restore();}
});


test('host preparation and navigation failures report no confirmed device download and keep evidence usable', async () => {
  const bytesFixture=await exportFixture(80), input=interactiveView(); input.bundle={...input.bundle,...bytesFixture.bundle};
  let failPrepare=true;
  const fixture=rendererFixture(input,{fileExportAvailable:()=>true,async prepareFileDownload() {if(failPrepare)throw new Error('The host could not prepare a temporary file download. No download was requested.');return 'https://files.example/fixture.json';},async openFileDownload() {throw new Error('Host rejected navigation');}});
  try {
    fixture.byId('download-evidence').click();await resolveByteRead(fixture,bytesFixture);
    assert.equal(fixture.byId('download-verified-json'),null);
    assert.match(fixture.byId('export-status').textContent,/could not prepare/);
    assert.match(fixture.root.textContent,/Outcome mix/);
    failPrepare=false;fixture.byId('download-evidence').click();await resolveByteRead(fixture,bytesFixture);
    fixture.byId('download-verified-json').click();await settle();
    assert.equal(fixture.byId('download-verified-json'),null);
    assert.match(fixture.byId('export-status').textContent,/no device download has been confirmed/);
    assert.equal(fixture.byId('download-evidence').disabled,false);
    assert.equal(fixture.all(node=>node.tagName==='A').length,0);
  } finally {fixture.restore();}
});
