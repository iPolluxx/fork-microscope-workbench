import { createComputeBridge } from './compute-bridge.mjs';

export const COMPUTE_SCHEMA = 'fork-microscope-compute-v1';
const HASH = /^[0-9a-f]{64}$/;
// Mirrors the controller's immutable_worker_image contract; tags such as :latest are not reviewable.
const IMAGE = /^[^\s@]+@sha256:[0-9a-f]{64}$/;
const TERMINAL_JOBS = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const nonnegative = value => finite(value) && value >= 0;
const hasId = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
const planId = plan => plan?.id ?? plan?.plan_id;
const connectionAllowsApproval = account => account?.connected === true && account.readiness === 'ready';
const quoteRevision = quote => quote?.model_revision ?? quote?.revision;
export const workerImage = plan => typeof plan?.quote?.worker_image === 'string' && IMAGE.test(plan.quote.worker_image) ? plan.quote.worker_image : null;

export function validateComputeSnapshot(snapshot) {
  if (!object(snapshot) || snapshot.schema !== COMPUTE_SCHEMA || !object(snapshot.account)) {
    throw new Error('The host did not return a supported managed-compute snapshot.');
  }
  for (const key of ['plan', 'session', 'job', 'cleanup', 'saved_evidence']) {
    if (snapshot[key] != null && !object(snapshot[key])) throw new Error(`Invalid compute ${key}.`);
  }
  if (snapshot.artifacts != null && !Array.isArray(snapshot.artifacts)) throw new Error('Invalid compute artifacts.');
  return snapshot;
}

export function planApprovalState(plan, nowSeconds = Date.now() / 1000) {
  if (!plan) return {allowed: false, reason: 'Ask for an investigation plan in chat to review a quote.'};
  if (plan.status !== 'prepared') return {allowed: false, reason: plan.status === 'approved' ? 'This plan has already been approved.' : 'This plan is not awaiting approval.'};
  if (!hasId(planId(plan)) || !HASH.test(plan.approval_digest || '') || !HASH.test(plan.config_hash || '') || !object(plan.config) || !object(plan.quote)) {
    return {allowed: false, reason: 'The plan is incomplete. Request a fresh quote before approval.'};
  }
  if (!finite(plan.expires_at) || !finite(plan.quote.expires_at) || Math.min(plan.expires_at, plan.quote.expires_at) <= nowSeconds) {
    return {allowed: false, reason: 'This quote expired. Ask for a fresh plan before approval.'};
  }
  if (!['device', 'drive'].includes(plan.storage_mode) || !nonnegative(plan.quote.estimated_usd) || !finite(plan.usd_allowance) || plan.usd_allowance <= 0 || plan.quote.estimated_usd > plan.usd_allowance || !finite(plan.max_duration_seconds) || plan.max_duration_seconds <= 0) {
    return {allowed: false, reason: 'The destination, estimate or allowance is incomplete. Request a fresh plan.'};
  }
  // The controller refuses unbound or changed images before provider effects; do not invite that approval.
  if (!workerImage(plan)) return {allowed: false, reason: 'This quote does not bind an immutable worker image digest. Request a fresh plan.'};
  const revision = plan.config.model?.revision;
  if (typeof revision !== 'string' || !revision || (quoteRevision(plan.quote) != null && quoteRevision(plan.quote) !== revision)) {
    return {allowed: false, reason: 'The model revision is missing or differs between configuration and quote. Request a fresh plan.'};
  }
  return {allowed: true, reason: 'Approval applies only to this exact configuration and quote.'};
}

export function progressSummary(job) {
  const progress = job?.progress ?? job?.worker_job?.progress ?? job?.worker_job;
  const phase = typeof progress?.phase === 'string' ? progress.phase : job?.status || 'Status unknown';
  if (!Number.isInteger(progress?.completed) || !Number.isInteger(progress?.total) || progress.completed < 0 || progress.total <= 0 || progress.completed > progress.total) {
    return {phase, label: 'Progress counts unavailable', completed: null, total: null};
  }
  const unit = typeof progress.progress_unit === 'string' ? ` ${progress.progress_unit}` : '';
  return {phase, label: `${progress.completed} / ${progress.total}${unit}`, completed: progress.completed, total: progress.total};
}

export function sessionContext(snapshot, nowSeconds = Date.now() / 1000) {
  const session = snapshot.session;
  if (!session) return '';
  if (session.observed_state === 'terminated' && session.cleanup_verified === true) {
    return `Historical session${finite(session.created_at) ? ' started ' + new Date(session.created_at * 1000).toLocaleString() : ''}. This record does not indicate a new run or current compute readiness.`;
  }
  if (finite(session.worker_last_heartbeat_at) && nowSeconds - session.worker_last_heartbeat_at > 90) {
    return 'Worker status is stale. Last reported progress may have stopped; refresh until the controller reports recovery or verified cleanup.';
  }
  if (['requested', 'provisioning', 'booting'].includes(session.observed_state)) return 'Waiting for worker enrollment. Queued work has not yet demonstrated model execution.';
  return '';
}

export function sessionProblem(session) {
  if (session?.observed_state === 'terminated' && session.cleanup_verified === true) return '';
  const failures = {
    stale_quote: 'The quote expired before provisioning. Review a new plan after cleanup is verified.',
    startup_timeout: 'The worker did not enroll before its startup deadline. Cleanup must still be verified.',
    credential_unavailable: 'The controller could not read the stored credential. Check this session in RunPod; billing may continue until deletion is verified.',
    create_outcome_manual_attention: 'The provider create outcome is uncertain. Check the app-owned session in RunPod before trying another run.',
    worker_image_review_required: 'The reviewed worker image no longer matches the controller configuration, so compute was not created for this plan. Review a fresh plan after cleanup is verified.',
    provider_unavailable:'The provider could not be reached. Observed status is incomplete; charges may continue until deletion is verified.',
  };
  if (session?.cleanup_status === 'credential_blocked') return 'The credential could not authorize cleanup. Check the app-owned pod in RunPod; charges may continue until deletion is verified.';
  if (session?.manual_attention_required) return failures[session.error_code] || 'Cleanup needs manual attention. Check the app-owned pod in RunPod; charges may continue until deletion is verified.';
  return failures[session?.error_code] || '';
}

export function jobProblem(job) {
  if (job?.error_code === 'insufficient_execution_time') return 'Startup used the reviewed execution allowance. This investigation was cancelled without shortening its configuration. Wait for verified cleanup, then prepare and review a fresh bounded plan.';
  if (job?.error_code === 'session_terminated') return 'The compute session ended before unfinished work completed. Save any available partial evidence; a new investigation requires a fresh reviewed plan.';
  return '';
}

// All links come from the authenticated snapshot. This panel never collects a key.
export function safeExternalUrl(value, billing = false) {
  if (typeof value !== 'string' || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search) return null;
    if (billing && (url.hostname !== 'console.runpod.io' || url.port || !/^\/billing\/?$/.test(url.pathname) || url.hash)) return null;
    return url.href;
  } catch { return null; }
}

export function cleanupSummary(snapshot) {
  const session = snapshot.session;
  if (!session) return 'No compute session in this snapshot.';
  if (session.cleanup_verified === true && session.observed_state === 'terminated') return 'Pod deletion verified by the controller.';
  if (session.desired_state === 'terminated') return 'Deletion requested; provider cleanup is not yet verified. Charges may continue.';
  return 'Compute may still be running and accruing charges.';
}

export function needsLossAcknowledgment(snapshot) {
  if (snapshot.cleanup?.requires_loss_acknowledgment === true || snapshot.cleanup?.requires_unsaved_loss_ack === true || snapshot.cleanup?.unsaved_artifact_ids?.length) return true;
  if (snapshot.job && !TERMINAL_JOBS.has(snapshot.job.status)) return true;
  return (snapshot.artifacts || []).some(artifact => artifact.storage_mode === 'device' && !artifact.received_at && !artifact.receipt_confirmed);
}

const canonical = value => JSON.stringify(value, (_, item) => object(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
export function createComputeController({callTool, onChange = () => {}, now = () => Date.now() / 1000, uuid = () => globalThis.crypto.randomUUID()} = {}) {
  let snapshot = null, pending = '', error = '', notice = '', generation = 0;
  const intents = new Map();
  const state = () => ({snapshot, pending, error, notice, now: now()});
  const emit = () => onChange(state());
  function accept(value) {
    snapshot = validateComputeSnapshot(value); generation++; error = ''; notice = ''; emit();
  }
  async function invoke(action, name, args = {}, {mutation = false, update = true} = {}) {
    if (pending) return false;
    const ticket = generation;
    if (mutation) {
      const key = canonical({name, args});
      if (!intents.has(key)) intents.set(key, `compute-${uuid()}`);
      args = {...args, request_id: intents.get(key)};
    }
    pending = action; error = ''; notice = ''; emit();
    try {
      const result = await callTool(name, args);
      if (ticket === generation) {
        if (update) snapshot = validateComputeSnapshot(result);
        else notice = 'Viewer requested for the verified saved bundle. If the host does not open it, ask in chat to show that saved investigation.';
      }
      return true;
    } catch (caught) {
      if (ticket === generation) error = caught?.message || 'The action failed. Refresh status before taking a different action.';
      return false;
    } finally { pending = ''; emit(); }
  }
  function reject(message) { error = message; emit(); return Promise.resolve(false); }
  return {
    state, accept, refreshClock: emit,
    setError(caught) { error = caught?.message || String(caught); emit(); },
    refresh() { return invoke('Refreshing stored status', 'fork_microscope_compute_status', {refresh_account: false}); },
    checkAccount() { return invoke('Checking RunPod read access', 'fork_microscope_compute_status', {refresh_account: true}); },
    approve() {
      const plan = snapshot?.plan, approval = planApprovalState(plan, now());
      if (!approval.allowed) return reject(approval.reason);
      if (!connectionAllowsApproval(snapshot.account)) return reject('Connect RunPod and use Check RunPod connection to obtain current read checks before approval.');
      return invoke('Approving this plan', 'fork_microscope_approve_plan', {plan_id: planId(plan), approval_digest: plan.approval_digest, confirm_execution: true}, {mutation: true});
    },
    cancel() {
      const job = snapshot?.job;
      if (!hasId(job?.id) || TERMINAL_JOBS.has(job.status) || job.cancel_requested) return reject('There is no cancellable investigation in this snapshot.');
      return invoke('Requesting investigation cancellation', 'fork_microscope_cancel_investigation', {job_id: job.id}, {mutation: true});
    },
    finish(acknowledgeUnsavedLoss = false) {
      const session = snapshot?.session;
      if (!hasId(session?.id) || (session.observed_state === 'terminated' && session.cleanup_verified)) return reject('There is no compute session awaiting deletion.');
      if (needsLossAcknowledgment(snapshot) && acknowledgeUnsavedLoss !== true) return reject('Save available evidence first, or acknowledge that deleting compute can lose unsaved work.');
      return invoke('Requesting compute deletion', 'fork_microscope_finish_compute', {session_id: session.id, confirm_delete: true, acknowledge_unsaved_loss: acknowledgeUnsavedLoss === true}, {mutation: true});
    },
    save(artifactId) {
      if (!(snapshot?.artifacts || []).some(item => item.id === artifactId)) return reject('This artifact is not in the current snapshot. Refresh status.');
      return invoke('Saving and verifying evidence', 'fork_microscope_save_hosted_evidence', {artifact_id: artifactId}, {mutation: true});
    },
    viewSaved() {
      const saved = snapshot?.saved_evidence;
      if (!hasId(saved?.bundle_id) || !HASH.test(saved?.file_sha256 || '')) return reject('A verified saved bundle is required before opening the viewer.');
      return invoke('Requesting saved-evidence viewer', 'fork_microscope_render_investigation', {bundle_id: saved.bundle_id, expected_file_sha256: saved.file_sha256}, {update: false});
    },
  };
}

function money(value, signed = false) { return finite(value) && (signed || value >= 0) ? `${value < 0 ? '−' : ''}$${Math.abs(value).toFixed(Math.abs(value) < 1 ? 4 : 2)}` : 'Unavailable'; }
function minutes(value) { return finite(value) ? `${Math.ceil(value / 60)} min` : 'Unavailable'; }
function timestamp(value) { return finite(value) ? new Date(value * 1000).toLocaleString() : 'Unavailable'; }
function print(value) { return value == null ? 'Unavailable' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value); }

export function renderCompute(state, handlers, doc = globalThis.document) {
  const {snapshot, pending, error, notice, now} = state;
  const node = (tag, className = '', text) => {
    const result = doc.createElement(tag);
    if (className) result.className = className;
    if (text != null) result.textContent = String(text);
    return result;
  };
  const button = (label, action, className = '', disabled = false) => {
    const result = node('button', className, label); result.type = 'button'; result.disabled = Boolean(pending || disabled);
    result.addEventListener('click', action); return result;
  };
  const definitions = entries => {
    const result = node('dl');
    for (const [label, value] of entries) result.append(node('dt', '', label), node('dd', '', print(value)));
    return result;
  };
  const detail = (label, child) => { const result = node('details'); result.append(node('summary', '', label), child); return result; };
  const link = (label, value, billing = false) => {
    const href = safeExternalUrl(value, billing);
    if (!href) return null;
    const result = node('a', 'button-link', label); result.href = href; result.target = '_blank'; result.rel = 'noopener noreferrer'; return result;
  };
  const section = title => { const result = node('section', 'stack'); result.append(node('h2', '', title)); return result; };
  const panel = node('article', 'compute-panel'); panel.setAttribute('aria-busy', pending ? 'true' : 'false');
  const header = node('header', 'row');
  const heading = node('div'); heading.append(node('p', 'eyebrow', 'Fork Microscope'), node('h1', '', 'Managed compute'));
  header.append(heading, button('Refresh status', () => handlers.refresh())); panel.append(header);
  const live = node('p', 'sr-only', pending || error || notice || (snapshot ? `${snapshot.job?.status || 'Status updated'}. ${cleanupSummary(snapshot)}` : 'Awaiting status'));
  live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite'); live.setAttribute('aria-atomic', 'true'); panel.append(live);
  if (error) { const alert = node('section', 'notice error', error); alert.setAttribute('role', 'alert'); panel.append(alert); }
  if (pending || notice) panel.append(node('section', 'notice', pending || notice));
  if (!snapshot) { panel.append(node('section', 'notice', 'Waiting for account and compute status from the host.')); return panel; }
  if (typeof snapshot.message === 'string') panel.append(node('p', 'notice', snapshot.message));
  if (typeof snapshot.evidence_warning === 'string') panel.append(node('p', 'notice warning', snapshot.evidence_warning));
  const account = section('Account');
  account.append(node('p', snapshot.account.connected === true ? 'pill success' : 'pill', snapshot.account.connected === true ? 'RunPod connected' : snapshot.account.connected === false ? 'RunPod not connected' : 'RunPod connection status unavailable'));
  const readinessText = {ready: 'Catalog and pod reads verified.', refresh_required: 'The saved connection has not been checked in this snapshot. Check RunPod connection before approving paid compute.', invalid_key: 'RunPod rejected this key. Update the secure connection.', permission_denied: 'RunPod denied a required read permission. Review the key permissions.', unavailable: 'RunPod could not be reached or verified. Refresh before approving.', credential_unavailable: 'The controller could not access the credential. Check the secure connection.'}[snapshot.account.readiness];
  if (readinessText) account.append(node('p', 'small muted', readinessText));
  if (object(snapshot.account.capabilities)) account.append(detail('Connection checks', definitions([['Catalog reads', snapshot.account.capabilities.catalog_read], ['Pod reads', snapshot.account.capabilities.pods_read], ['Pod writes', snapshot.account.capabilities.pods_write]])));
  const balance = snapshot.account.balance || {};
  account.append(definitions([
    ['Credit balance', balance.status === 'available' && finite(balance.amount_usd) ? money(balance.amount_usd, true) : 'Unavailable — check RunPod billing'],
    ['Current spend / hour', nonnegative(balance.spend_per_hour_usd) ? money(balance.spend_per_hour_usd) : 'Unavailable'],
  ]));
  if (balance.deprecated) account.append(node('p', 'small muted', 'The balance uses RunPod’s legacy balance interface; billing remains the source of truth.'));
  if (snapshot.account.write_permissions_verified !== true) account.append(node('p', 'small muted', 'Read checks do not prove permission to create or delete pods. Those actions are checked when requested.'));
  const accountActions = node('div', 'actions');
  const setupLink = link(snapshot.account.connected ? 'Manage secure connection' : 'Connect RunPod securely', snapshot.setup_url);
  const billingLink = link('Add money in RunPod', snapshot.billing_url || snapshot.account.billing_url, true);
  if (setupLink) accountActions.append(setupLink);
  if (billingLink) accountActions.append(billingLink);
  accountActions.append(button('Check RunPod connection', () => handlers.checkAccount(), '', snapshot.account.connected !== true));
  if (finite(snapshot.funding?.requested_amount_usd) && snapshot.funding.requested_amount_usd > 0) account.append(node('p', 'notice', `Requested funding: ${money(snapshot.funding.requested_amount_usd)}. Complete payment yourself on the RunPod billing page.`));
  account.append(accountActions, node('p', 'small muted', 'Enter your key only on the secure account page. Funding is completed in RunPod billing; check the RunPod connection afterward. Refresh status only reads stored progress.'));
  if (!setupLink && !snapshot.account.connected) account.append(node('p', 'notice', 'The secure account page is not configured for this deployment.'));
  panel.append(account);

  const imports = section('Existing saved investigations');
  const importLink = link('Import a saved investigation file', snapshot.import_url);
  if (importLink) imports.append(importLink);
  imports.append(node('p', 'small muted', importLink ? 'Choose a JSON file on the secure account page to save it in your private hosted library. Existing evidence requires no GPU or model call. Do not paste the file or credentials into chat.' : 'Ask in chat to list saved investigations. Local-file import requires the secure account page to be configured.'));
  panel.append(imports);

  const plan = snapshot.plan;
  if (plan) {
    const review = section('Review this investigation');
    const approval = planApprovalState(plan, now);
    const metrics = node('div', 'metrics');
    for (const [value, label] of [[money(plan.quote?.estimated_usd), 'Estimated compute'], [money(plan.usd_allowance), 'Estimate allowance'], [minutes(plan.max_duration_seconds), 'Session deadline']]) {
      const metric = node('div', 'metric'); metric.append(node('strong', '', value), node('span', '', label)); metrics.append(metric);
    }
    review.append(metrics, definitions([
      ['Model', plan.config?.model?.model_id], ['Pinned revision', plan.config?.model?.revision],
      ['Worker image', workerImage(plan) || 'Not bound to an immutable digest — approval unavailable'],
      ['GPU', plan.quote?.gpu_id == null ? null : `${plan.quote.gpu_id} · ${plan.quote.gpu_count ?? 1} GPU${plan.quote.cloud ? ' · ' + plan.quote.cloud : ''}`],
      ['Rates', finite(plan.quote?.gpu_usd_per_hour) ? `GPU ${money(plan.quote.gpu_usd_per_hour)}/hour${finite(plan.quote.disk_gb) && finite(plan.quote.disk_usd_per_gb_hour) ? ` · disk ${plan.quote.disk_gb} GB × $${plan.quote.disk_usd_per_gb_hour}/GB/hour` : ''}` : null],
      ['Deadline includes', finite(plan.quote?.startup_allowance_seconds) && finite(plan.quote?.export_reserve_seconds) ? `${minutes(plan.quote.startup_allowance_seconds)} startup and ${minutes(plan.quote.export_reserve_seconds)} export reserve` : null],
      ['Evidence destination', plan.storage_mode === 'drive' ? 'Connected Google Drive' : plan.storage_mode === 'device' ? 'Save verified evidence before the session ends' : 'Unavailable'],
      ['Quote expires', timestamp(Math.min(plan.expires_at ?? NaN, plan.quote?.expires_at ?? NaN))],
    ]));
    review.append(node('h3', '', 'Prompt'));
    const prompt = node('pre', 'prompt', print(plan.config?.base?.prompt)); prompt.tabIndex = 0; review.append(prompt);
    review.append(definitions([
      ['Outcome answers', plan.config?.base?.answers], ['Sampling', plan.config?.scan], ['Refinement', plan.config?.refinement], ['Run limits', plan.config?.limits],
    ]));
    const exact = node('pre', '', JSON.stringify({config: plan.config, quote: plan.quote, storage_mode: plan.storage_mode, usd_allowance: plan.usd_allowance, max_duration_seconds: plan.max_duration_seconds, approval_digest: plan.approval_digest, config_hash: plan.config_hash}, null, 2)); exact.tabIndex = 0;
    review.append(detail('Complete configuration and quote', exact));
    review.append(node('p', 'notice warning', 'Approval starts paid compute for this exact plan. Estimates and deadlines are not hard dollar caps; startup and cleanup delays can add charges.'));
    if (plan.storage_mode === 'device') review.append(node('p', 'small muted', 'Save evidence before the deadline or deleting compute. Unsaved device evidence can be lost.'));
    review.append(node('p', 'small muted', approval.reason), button('Approve this plan and start', () => handlers.approve(), 'primary', !approval.allowed || !connectionAllowsApproval(snapshot.account)));
    panel.append(review);
  } else if (!snapshot.session || (snapshot.session.observed_state === 'terminated' && snapshot.session.cleanup_verified)) panel.append(node('section', 'notice', 'For a new investigation, describe the prompt, outcome answers, model, sampling limits, duration, estimate allowance and evidence destination in chat. Review a fresh exact plan before paid compute starts.'));

  if (snapshot.session) {
    const progress = section('Investigation and compute');
    const context = sessionContext(snapshot, now);
    if (context) progress.append(node('p', 'notice', context));
    const problem = sessionProblem(snapshot.session) || jobProblem(snapshot.job);
    if (problem) progress.append(node('p', 'notice warning', problem));
    const summary = progressSummary(snapshot.job);
    progress.append(definitions([
      ['Investigation', snapshot.job?.status || 'Not queued'], ['Phase', summary.phase], ['Progress', summary.label],
      ['Compute state', snapshot.session.observed_state || 'Unknown'], ['Started', timestamp(snapshot.session.created_at)], ['Last observed', timestamp(snapshot.observed_at)], ['Session deadline', timestamp(snapshot.session.expires_at)],
    ]));
    if (summary.completed !== null) { const bar = node('progress'); bar.max = summary.total; bar.value = summary.completed; bar.setAttribute('aria-label', `${summary.phase}: ${summary.label}`); progress.append(bar); }
    if (snapshot.job && !TERMINAL_JOBS.has(snapshot.job.status)) {
      progress.append(button(snapshot.job.cancel_requested ? 'Cancellation requested' : 'Cancel investigation', () => handlers.cancel(), '', snapshot.job.cancel_requested));
    }
    if (snapshot.job?.cancel_requested && !TERMINAL_JOBS.has(snapshot.job.status)) progress.append(node('p', 'notice', 'Cancellation requested; the worker has not yet confirmed it stopped. In-flight work may finish.'));
    progress.append(node('p', 'small muted', 'Cancellation stops investigation work cooperatively. Check compute cleanup separately; cancellation alone does not confirm pod deletion.'));
    panel.append(progress);
  }

  if (snapshot.artifacts?.length || snapshot.saved_evidence) {
    const evidence = section('Save evidence');
    for (const artifact of snapshot.artifacts || []) {
      const item = node('div', 'artifact stack');
      item.append(definitions([['Artifact', artifact.id], ['SHA-256', artifact.sha256], ['Destination', artifact.storage_mode]]));
      const receipt = Boolean(artifact.received_at || artifact.receipt_confirmed);
      item.append(button(receipt ? 'Verify saved evidence again' : 'Save and verify evidence', () => handlers.save(artifact.id), '', !hasId(artifact.id)));
      evidence.append(item);
    }
    if (snapshot.saved_evidence) {
      const saved = snapshot.saved_evidence;
      evidence.append(definitions([['Saved bundle', saved.bundle_id], ['SHA-256', saved.file_sha256], ['Save receipt', saved.receipt?.status === 'acknowledged' ? 'Acknowledged by the controller' : saved.receipt?.status === 'not_required' ? 'Not required for this destination' : 'Pending; refresh or retry save']]));
      if (saved.server_saved === true || saved.saved === true) evidence.append(node('p', 'small muted', 'Verified evidence is saved in the private evidence library. This does not confirm a download to your device.'));
      evidence.append(button('View saved evidence', () => handlers.viewSaved(), 'primary', !hasId(saved.bundle_id) || !HASH.test(saved.file_sha256 || '')));
    }
    panel.append(evidence);
  }

  if (snapshot.session) {
    const cleanup = section('Compute cleanup');
    cleanup.append(node('p', 'notice', cleanupSummary(snapshot)));
    if (snapshot.cleanup?.manual_attention_required && !(snapshot.session.observed_state === 'terminated' && snapshot.session.cleanup_verified === true)) cleanup.append(node('p', 'notice warning', 'The controller reports that cleanup needs attention. Check the app-owned pod in RunPod and refresh until deletion is verified.'));
    if (!(snapshot.session.observed_state === 'terminated' && snapshot.session.cleanup_verified)) {
      let ack = null;
      const needsAck = needsLossAcknowledgment(snapshot);
      const finishButton = button(snapshot.session.desired_state === 'terminated' ? 'Retry deletion request' : 'Finish and delete compute', () => handlers.finish(ack?.checked === true), 'danger', needsAck);
      if (needsAck) {
        const label = node('label', 'loss-ack'); ack = node('input'); ack.type = 'checkbox'; ack.disabled = Boolean(pending);
        ack.addEventListener('change', () => { finishButton.disabled = Boolean(pending || !ack.checked); });
        label.append(ack, node('span', '', 'I understand deleting compute can lose unsaved evidence or unfinished work.'));
        cleanup.append(label);
      }
      cleanup.append(finishButton);
    }
    cleanup.append(node('p', 'small muted', 'Deletion is complete only after the controller verifies the pod is absent. Independently preserved volumes can continue to incur storage charges. This panel does not offer pause.'));
    panel.append(cleanup);
  }
  panel.append(node('footer', 'small muted', 'Refresh status to check worker progress, evidence delivery and verified cleanup.'));
  return panel;
}

if (typeof document !== 'undefined' && document.getElementById('compute-app')) {
  const mount = document.getElementById('compute-app');
  let expiryTimer;
  const controller = createComputeController({
    callTool: (name, args) => bridge.callTool(name, args),
    onChange: state => {
      // Preserve keyboard focus across status updates; avoid assigning evidence to markup.
      const focusedLabel = mount.contains(document.activeElement) ? document.activeElement?.textContent : null;
      mount.replaceChildren(renderCompute(state, controller));
      if (focusedLabel) [...mount.querySelectorAll('button, a, summary')].find(item => item.textContent === focusedLabel && !item.disabled)?.focus();
      clearTimeout(expiryTimer);
      const plan = state.snapshot?.plan;
      if (planApprovalState(plan, state.now).allowed) expiryTimer = setTimeout(() => controller.refreshClock(), Math.max(1, Math.min(2147483647, (Math.min(plan.expires_at, plan.quote.expires_at) - Date.now() / 1000) * 1000 + 10)));
      bridge.resize();
    },
  });
  const bridge = createComputeBridge({
    onResult: result => {
      if (result.schema === COMPUTE_SCHEMA) {
        try { controller.accept(result); } catch (error) { controller.setError(error); }
      }
    },
    onContext: context => {
      if (['dark', 'light'].includes(context.theme)) document.documentElement.dataset.theme = context.theme;
    },
    onError: error => controller.setError(error),
  });
  bridge.start();
}
