import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {
  COMPUTE_SCHEMA, validateComputeSnapshot, planApprovalState, progressSummary,
  safeExternalUrl, cleanupSummary, sessionContext, sessionProblem, jobProblem, needsLossAcknowledgment, createComputeController, renderCompute,
} from '../integrations/mcp/ui/compute.mjs';
import {createComputeBridge, computeToolData} from '../integrations/mcp/ui/compute-bridge.mjs';

const hash = 'a'.repeat(64);
const image = 'example/worker@sha256:' + 'd'.repeat(64);
const quote = (overrides = {}) => ({expires_at: 200, estimated_usd: .25, gpu_id: 'gpu', model_revision: 'exact-revision', revision: 'exact-revision', worker_image: image, ...overrides});
const plan = (overrides = {}) => ({
  id: 'plan-1', status: 'prepared', approval_digest: hash, config_hash: 'b'.repeat(64), expires_at: 200,
  config: {model: {model_id: 'model', revision: 'exact-revision'}, base: {prompt: '<script>not markup</script>', answers: ['yes', 'no']}, scan: {samples: 5}, refinement: {max_rounds: 0}, limits: {max_seconds: 100}},
  quote: quote(), storage_mode: 'device', usd_allowance: .5, max_duration_seconds: 600,
  ...overrides,
});
const snapshot = (overrides = {}) => ({
  schema: COMPUTE_SCHEMA, account: {connected: true, readiness: 'ready', balance: {status: 'unavailable'}, write_permissions_verified: false},
  setup_url: 'https://app.example/account', billing_url: 'https://console.runpod.io/billing', plan: plan(), session: null, job: null, artifacts: [], cleanup: {}, ...overrides,
});

function harness() {
  const sent = [], results = [], errors = [];
  let listener;
  const env = {
    parent: {postMessage(message, origin) { sent.push({message, origin}); }},
    addEventListener(type, fn) { if (type === 'message') listener = fn; }, removeEventListener() { listener = null; }, setTimeout, clearTimeout,
    document: {body: {scrollHeight: 450}, documentElement: {clientWidth: 390}},
  };
  const emit = (data, source = env.parent, origin = 'https://host.example') => listener?.({data, source, origin});
  const bridge = createComputeBridge({env, timeoutMs: 50, onResult: value => results.push(value), onError: error => errors.push(error)});
  const init = async () => {
    const initialized = bridge.start();
    emit({jsonrpc: '2.0', id: sent[0].message.id, result: {protocolVersion: '2026-01-26', hostContext: {theme: 'dark'}}});
    await initialized;
  };
  return {sent, results, errors, env, emit, bridge, init};
}

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this._text = ''; }
  append(...children) { this.children.push(...children); }
  set textContent(value) { this._text = String(value); }
  get textContent() { return this._text + this.children.map(child => child.textContent ?? child).join(''); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const fakeDocument = {createElement: tag => new Element(tag)};
const descendants = root => [root, ...root.children.flatMap(child => child instanceof Element ? descendants(child) : [])];
const noopHandlers = {refresh() {}, checkAccount() {}, approve() {}, cancel() {}, finish() {}, save() {}, viewSaved() {}};

// Bind configuration review to fresh server identities, including the quote's own expiry.
test('approval rejects incomplete, changed-state or expired plans before any action', () => {
  assert.equal(planApprovalState(plan(), 100).allowed, true);
  for (const item of [null, plan({status: 'approved'}), plan({approval_digest: 'bad'}), plan({config_hash: ''}), plan({expires_at: 100}), plan({quote: {expires_at: 99, estimated_usd: .25}}), plan({quote: {expires_at: 200, estimated_usd: .6}}), plan({storage_mode: 'unknown'}), plan({usd_allowance: -1})]) {
    assert.equal(planApprovalState(item, 100).allowed, false);
  }
});

// Production lifecycle refuses unbound/changed images before provider effects, so review must show and require the digest.
test('approval requires a visible immutable worker image and a quote revision matching the config', async () => {
  for (const worker_image of [undefined, 'example/worker:latest', 'example/worker@sha256:' + 'D'.repeat(64), ' example/worker@sha256:' + 'd'.repeat(64)]) {
    const unbound = plan({quote: quote({worker_image})});
    assert.match(planApprovalState(unbound, 100).reason, /immutable worker image/);
    const rendered = renderCompute({snapshot: snapshot({plan: unbound}), now: 100}, noopHandlers, fakeDocument);
    assert.match(rendered.textContent, /Not bound to an immutable digest/);
    assert.equal(descendants(rendered).find(item => item.tagName === 'button' && item.textContent === 'Approve this plan and start').disabled, true);
  }
  for (const changed of [plan({quote: quote({model_revision: 'other', revision: 'other'})}), plan({config: {...plan().config, model: {model_id: 'model'}}})]) {
    assert.match(planApprovalState(changed, 100).reason, /model revision/);
  }
  const rendered = renderCompute({snapshot: snapshot({plan: plan({quote: quote({gpu_count: 1, cloud: 'SECURE', gpu_usd_per_hour: 1.5, disk_gb: 30, disk_usd_per_gb_hour: .0001, startup_allowance_seconds: 300, export_reserve_seconds: 120})})}), now: 100}, noopHandlers, fakeDocument);
  for (const text of [image, 'exact-revision', 'gpu · 1 GPU · SECURE', 'GPU $1.50/hour', '5 min startup and 2 min export reserve', 'Save verified evidence before the session ends', 'Quote expires']) assert(rendered.textContent.includes(text), text);
  let calls = 0;
  const controller = createComputeController({now: () => 100, callTool: async () => {calls++; return snapshot();}});
  controller.accept(snapshot({plan: plan({quote: quote({worker_image: undefined})})}));
  assert.equal(await controller.approve(), false); assert.equal(calls, 0);
  assert.match(sessionProblem({error_code: 'worker_image_review_required'}), /compute was not created/);
});

test('snapshot and visible progress reject invented counts and retain actual units', () => {
  assert.equal(validateComputeSnapshot(snapshot()).schema, COMPUTE_SCHEMA);
  assert.throws(() => validateComputeSnapshot({schema: 'other'}), /supported/);
  assert.throws(() => validateComputeSnapshot(snapshot({artifacts: {}})), /artifacts/);
  assert.deepEqual(progressSummary({progress: {phase: 'scanning', completed: 7, total: 20, progress_unit: 'samples'}}), {phase: 'scanning', label: '7 / 20 samples', completed: 7, total: 20});
  for (const progress of [{completed: 21, total: 20}, {completed: 0, total: 0}, {completed: null, total: 20}, {completed: 2.5, total: 20}]) assert.equal(progressSummary({progress}).completed, null);
});

test('safe handoff permits only HTTPS without credentials or query; billing stays on official page', () => {
  assert.equal(safeExternalUrl('https://app.example/account'), 'https://app.example/account');
  assert.equal(safeExternalUrl('https://console.runpod.io/billing', true), 'https://console.runpod.io/billing');
  for (const url of ['javascript:alert(1)', 'http://app.example/account', 'https://user:private@app.example/', 'https://app.example/?api_key=secret']) assert.equal(safeExternalUrl(url), null);
  for (const url of ['https://runpod.io/billing', 'https://console.runpod.io.evil.example/billing', 'https://console.runpod.io/pods', 'https://console.runpod.io:8443/billing']) assert.equal(safeExternalUrl(url, true), null);
});

test('uncertain approval retries retain identity, reject duplicate clicks, and bind exact digest', async () => {
  const calls = []; let release, failed = true;
  const controller = createComputeController({now: () => 100, uuid: () => 'stable-request', callTool: (name, args) => {
    calls.push({name, args});
    return new Promise((resolve, reject) => { release = () => failed ? reject(new Error('transport timeout')) : resolve(snapshot({plan: plan({status: 'approved'})})); });
  }});
  controller.accept(snapshot());
  const first = controller.approve();
  assert.equal(await controller.approve(), false);
  assert.equal(calls.length, 1);
  release(); assert.equal(await first, false);
  assert.match(controller.state().error, /timeout/);
  failed = false; const second = controller.approve(); release(); assert.equal(await second, true);
  assert.deepEqual(calls[1], calls[0]);
  assert.deepEqual(calls[0].args, {plan_id: 'plan-1', approval_digest: hash, confirm_execution: true, request_id: 'compute-stable-request'});
  assert.equal(await controller.approve(), false);
  assert.equal(calls.length, 2);
});

test('changed plan identity gets a new retry identity and disconnected/expired approval never calls host', async () => {
  const calls = []; let count = 0, clock = 100;
  const controller = createComputeController({now: () => clock, uuid: () => String(++count), callTool: async (name, args) => {calls.push({name, args}); throw Error('uncertain');}});
  controller.accept(snapshot()); await controller.approve();
  controller.accept(snapshot({plan: plan({id: 'plan-2', approval_digest: 'c'.repeat(64)})})); await controller.approve();
  assert.notEqual(calls[0].args.request_id, calls[1].args.request_id);
  clock = 201; assert.equal(await controller.approve(), false);
  controller.accept(snapshot({account: {connected: false}})); clock = 100; assert.equal(await controller.approve(), false);
  assert.equal(calls.length, 2);
});

test('progress refresh makes no provider read; connection check is explicit', async () => {
  let called;
  const controller = createComputeController({callTool: async (name, args) => {called = {name, args}; return snapshot();}});
  await controller.refresh();
  assert.deepEqual(called, {name: 'fork_microscope_compute_status', args: {refresh_account: false}});
  await controller.checkAccount();
  assert.deepEqual(called, {name: 'fork_microscope_compute_status', args: {refresh_account: true}});
});

test('new authoritative notification cannot be overwritten by a late status response', async () => {
  let release;
  const controller = createComputeController({callTool: () => new Promise(resolve => {release = resolve;})});
  controller.accept(snapshot());
  const refreshing = controller.refresh();
  controller.accept(snapshot({plan: plan({id: 'newer-plan'})}));
  release(snapshot({plan: plan({id: 'stale-plan'})})); await refreshing;
  assert.equal(controller.state().snapshot.plan.id, 'newer-plan');
});

test('cancel does not pretend cleanup succeeded and delete requires loss acknowledgment', async () => {
  const calls = [];
  const active = snapshot({session: {id: 'session-1', observed_state: 'ready', desired_state: 'running'}, job: {id: 'job-1', status: 'running'}});
  const controller = createComputeController({uuid: () => 'one', callTool: async (name, args) => {calls.push({name, args}); return active;}});
  controller.accept(active);
  assert.equal(needsLossAcknowledgment(active), true);
  assert.equal(await controller.finish(), false); assert.equal(calls.length, 0);
  await controller.cancel(); assert.equal(calls[0].name, 'fork_microscope_cancel_investigation');
  assert.match(cleanupSummary(controller.state().snapshot), /still be running/);
  await controller.finish(true);
  assert.deepEqual(calls[1].args, {session_id: 'session-1', confirm_delete: true, acknowledge_unsaved_loss: true, request_id: 'compute-one'});
  assert.match(cleanupSummary(snapshot({session: {...active.session, desired_state: 'terminated'}})), /not yet verified/);
  assert.match(cleanupSummary(snapshot({session: {...active.session, observed_state: 'terminated', cleanup_verified: true}})), /deletion verified/);
});

test('save is owner-snapshot scoped and viewer request pins verified bytes without replacing compute view', async () => {
  const saved = snapshot({artifacts: [{id: 'artifact-1', sha256: hash}], saved_evidence: {bundle_id: 'bundle.json', file_sha256: hash, server_saved: true, receipt: {status: 'acknowledged'}}});
  const calls = [];
  const controller = createComputeController({uuid: () => 'one', callTool: async (name, args) => {calls.push({name, args}); return name.endsWith('render_investigation') ? {schema: 'fork-microscope-view-v1'} : saved;}});
  controller.accept(saved);
  assert.equal(await controller.save('other-owner-artifact'), false); assert.equal(calls.length, 0);
  await controller.save('artifact-1'); await controller.viewSaved();
  assert.deepEqual(calls[1].args, {bundle_id: 'bundle.json', expected_file_sha256: hash});
  assert.equal(controller.state().snapshot.schema, COMPUTE_SCHEMA);
  assert.match(controller.state().notice, /Viewer requested/);
});

test('rendered controls are accessible, show exact prompt as text and cannot silently delete unsaved work', () => {
  const rendered = renderCompute({snapshot: snapshot({session: {id: 's', observed_state: 'ready'}, job: {id: 'j', status: 'running', progress: {phase: 'scanning', completed: 2, total: 10}}}), now: 100}, noopHandlers, fakeDocument);
  const all = descendants(rendered);
  assert(all.some(item => item.tagName === 'pre' && item.textContent === '<script>not markup</script>'));
  assert(all.some(item => item.attributes['aria-live'] === 'polite'));
  assert(all.some(item => item.tagName === 'progress' && item.value === 2 && item.max === 10));
  const finish = all.find(item => item.tagName === 'button' && item.textContent === 'Finish and delete compute');
  const checkbox = all.find(item => item.tagName === 'input' && item.type === 'checkbox');
  assert.equal(finish.disabled, true); checkbox.checked = true; checkbox.listeners.change(); assert.equal(finish.disabled, false);
  assert.match(rendered.textContent, /not hard dollar caps/);
  assert.match(rendered.textContent, /preserved volumes/);
  const disabled = renderCompute({snapshot: snapshot(), now: 201}, noopHandlers, fakeDocument);
  assert.equal(descendants(disabled).find(item => item.tagName === 'button' && item.textContent === 'Approve this plan and start').disabled, true);
  const pending = renderCompute({snapshot: snapshot(), now: 100, pending: 'Approving'}, noopHandlers, fakeDocument);
  assert(descendants(pending).filter(item => item.tagName === 'button').every(item => item.disabled));
});

test('saved receipt describes controller acknowledgment and never implies a user download', () => {
  for (const [status, text] of [['acknowledged', 'Acknowledged by the controller'], ['pending_retry', 'Pending; refresh or retry save'], ['not_required', 'Not required for this destination']]) {
    const rendered = renderCompute({snapshot: snapshot({saved_evidence: {bundle_id: 'bundle.json', file_sha256: hash, server_saved: true, receipt: {status}, user_download_confirmed: false}}), now: 100}, noopHandlers, fakeDocument);
    assert.match(rendered.textContent, new RegExp(text));
    assert.match(rendered.textContent, /does not confirm a download to your device/);
  }
});

test('failed readiness is visible, blocks approval, and real negative balance remains visible', async () => {
  const current = snapshot({account: {connected: true, readiness: 'invalid_key', balance: {status: 'available', amount_usd: -2.5}}});
  const rendered = renderCompute({snapshot: current, now: 100}, noopHandlers, fakeDocument);
  assert.match(rendered.textContent, /RunPod rejected this key/);
  assert.match(rendered.textContent, /−\$2.50/);
  assert.equal(descendants(rendered).find(item => item.tagName === 'button' && item.textContent === 'Approve this plan and start').disabled, true);
  const controller = createComputeController({now: () => 100, callTool: async () => {throw Error('must not call host');}});
  controller.accept(current); assert.equal(await controller.approve(), false);
  assert.match(controller.state().error, /current read checks/);
});

test('missing balance is never displayed as zero and unsafe setup links are omitted', () => {
  const rendered = renderCompute({snapshot: snapshot({account: {connected: false, balance: {status: 'unavailable', amount_usd: 0}}, setup_url: 'https://app.example/?api_key=secret'}), now: 100}, noopHandlers, fakeDocument);
  assert.match(rendered.textContent, /Unavailable — check RunPod billing/);
  assert.match(rendered.textContent, /secure account page is not configured/);
  assert.equal(descendants(rendered).filter(item => item.tagName === 'a').length, 1);
});

test('bridge pins parent origin, blocks other tools and does not auto-execute notifications', async () => {
  const h = harness(); await h.init();
  const notification = {jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: {structuredContent: snapshot()}};
  h.emit(notification, {}); h.emit(notification, h.env.parent, 'https://other.example'); assert.equal(h.results.length, 0);
  h.emit(notification); assert.equal(h.results.length, 1);
  assert.equal(h.sent.filter(item => item.message.method === 'tools/call').length, 0);
  await assert.rejects(h.bridge.callTool('fork_microscope_start_workflow', {}), /only permits/);
  const request = h.bridge.callTool('fork_microscope_compute_status'); await Promise.resolve();
  const sent = h.sent.at(-1); assert.equal(sent.origin, 'https://host.example');
  h.emit({jsonrpc: '2.0', id: sent.message.id, result: {structuredContent: snapshot()}}); assert.equal((await request).schema, COMPUTE_SCHEMA);
  h.bridge.dispose();
});

test('bridge timeouts and teardown never imply cancellation or cleanup', async () => {
  const h = harness(); await h.init();
  await assert.rejects(h.bridge.callTool('fork_microscope_finish_compute', {}), /may have completed/);
  const pending = h.bridge.callTool('fork_microscope_compute_status'); await Promise.resolve();
  h.emit({jsonrpc: '2.0', id: 90, method: 'ui/resource-teardown', params: {}});
  await assert.rejects(pending, /closed/);
  assert(h.sent.some(item => item.message.id === 90));
  assert.throws(() => computeToolData({isError: true, content: [{type: 'text', text: 'Not authorized'}]}), /Not authorized/);
});

test('panel has no key collection, browser persistence, network transport or HTML injection', async () => {
  const source = await readFile(new URL('../integrations/mcp/ui/compute.mjs', import.meta.url), 'utf8');
  const bridge = await readFile(new URL('../integrations/mcp/ui/compute-bridge.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source + bridge, /\b(?:localStorage|sessionStorage|indexedDB|setWidgetState|innerHTML|outerHTML|insertAdjacentHTML|eval|fetch|XMLHttpRequest)\b/);
  assert.match(source, /textContent/);
  assert.doesNotMatch(source, /type\s*=\s*['"](?:password|text)['"]/);
  const css = await readFile(new URL('../integrations/mcp/ui/compute.css', import.meta.url), 'utf8');
  assert.match(css, /max-width: 520px/); assert.match(css, /focus-visible/); assert.match(css, /prefers-reduced-motion/);
});


test('historical sessions, stale progress and credential-blocked cleanup remain explicit', () => {
  assert.match(sessionContext(snapshot({session:{created_at:1,observed_state:'terminated',cleanup_verified:true}}), 1000), /Historical session/);
  assert.match(sessionContext(snapshot({session:{worker_last_heartbeat_at:1,observed_state:'ready'}}), 1000), /stale/);
  assert.match(sessionContext(snapshot({session:{observed_state:'booting'}}), 1000), /enrollment/);
  assert.match(sessionProblem({cleanup_status:'credential_blocked'}), /charges may continue/);
  const rendered = renderCompute({snapshot:snapshot({session:{id:'s',observed_state:'ready',worker_last_heartbeat_at:1,cleanup_status:'credential_blocked'},job:{id:'j',status:'running',cancel_requested:true}}),now:1000},noopHandlers,fakeDocument);
  assert.match(rendered.textContent,/has not yet confirmed it stopped/);
  assert.match(rendered.textContent,/status is stale/);
  assert.match(rendered.textContent,/charges may continue/);
});

test('unknown or refresh-required readiness cannot approve paid compute', async () => {
  let calls=0;
  const controller=createComputeController({now:()=>100,callTool:async()=>{calls++;return snapshot();}});
  for(const readiness of [undefined,'refresh_required','unavailable']) {
    const current=snapshot({account:{connected:true,readiness,write_permissions_verified:false}});
    controller.accept(current);
    assert.equal(await controller.approve(),false);
    const rendered=renderCompute({snapshot:current,now:100},noopHandlers,fakeDocument);
    assert.equal(descendants(rendered).find(item=>item.tagName==='button'&&item.textContent==='Approve this plan and start').disabled,true);
    assert.match(rendered.textContent,/Read checks do not prove permission/);
  }
  assert.equal(calls,0);
});

test('existing evidence import uses an authenticated page handoff without network or bundle arguments', () => {
  const rendered=renderCompute({snapshot:snapshot({import_url:'https://app.example/hosted.html#saved-import'}),now:100},noopHandlers,fakeDocument);
  const link=descendants(rendered).find(item=>item.tagName==='a'&&item.textContent==='Import a saved investigation file');
  assert.equal(link.href,'https://app.example/hosted.html#saved-import');
  assert.match(rendered.textContent,/requires no GPU or model call/);
});


test('delayed startup does not silently shorten an approved investigation', () => {
  assert.match(jobProblem({error_code:'insufficient_execution_time'}),/without shortening/);
  const rendered=renderCompute({snapshot:snapshot({session:{id:'s',observed_state:'terminated',cleanup_verified:true,manual_attention_required:true,cleanup_status:'credential_blocked'},cleanup:{manual_attention_required:true},job:{status:'cancelled',error_code:'insufficient_execution_time'}}),now:100},noopHandlers,fakeDocument);
  assert.match(rendered.textContent,/prepare and review a fresh bounded plan/);
  assert.doesNotMatch(rendered.textContent,/cleanup needs attention|credential could not authorize cleanup/);
});
