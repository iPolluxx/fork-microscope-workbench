<!-- generated: Codex — fork-microscope-revamp-ASTRA-BRIEF.md, Stage C workflow updates. -->
# One investigation, from compute to Explorer

[Documentation index](README.md)

An investigation coordinates one original response, an initial checkpoint scan,
bounded refinement, and an optional paired Jacobian lens inspection. The worker
runs the model. A CLI, an agent, or the dashboard can start the same job. Closing
the browser does not stop it. No cloud VM is provisioned or terminated by this API.

See the [investigation configuration reference](CONFIGURATION.md) for every field,
allowed values, effects, budget arithmetic, and manual-operation alternatives.

## Start in the browser

The interface has three working areas: **Setup → Explore → Inspect & Test**.
The header carries your investigation, model, compute state, budget and selected
checkpoint. Inspecting saved evidence never starts a model.

1. Open the example investigation, or import a complete investigation JSON bundle.
   A static build opens the attendance example with three related scans and saved
   Muse J-lens readouts. The example is existing model evidence, not a new run.
2. In Setup, name your question, enter the prompt and outcome rules, and test those
   rules on example text. Expand the input options for system/history messages.
   A disconnected draft is local to your browser; saving a worker investigation
   requires connected compute. Missing J-lens does not block scanning.
3. In Explore, generate one response, search for a target within an attempt limit,
   or select a saved response. Read its classification and completion status.
   Searching can legitimately find nothing. Selected responses retain exact IDs.
4. Scan the selected response: choose checkpoint spacing, samples per checkpoint,
   and a continuation length limit. Refine a selected region if useful. More
   samples improve an estimate; more checkpoints narrow its location.
5. Compare continuations and carry a selected path or pair into Inspect & Test.
   Existing lens, capture, edit and patch tools remain capability-gated. A change
   in an outcome curve, a textual divergence and an internal measurement are
   different observations; none alone establishes a causal decision point.
6. Write a conclusion and export the complete investigation. New v3 archives
   include responses, search history, comparisons, edits and captures as well as
   runs, saved fits, lens readouts and patches. Incomplete operations cannot be
   exported as complete evidence.

Browser imports stay in IndexedDB on that **browser and website origin**. They
are not an account, cloud sync or a backup. Keep the exported JSON; import it again
on another device. Browser annotations produce a new bundle version without
changing the stored model observations. **Use connected compute** leaves browsing
mode; transfer the exported bundle to the chosen worker before running new work.
Legacy standalone run files still use the worker importer; choose an investigation
bundle for worker-free browsing. Old v1/v2 bundles remain supported by the worker.
Historical checksums that predate browser-normalized numbers may require re-export
from the original worker before browser import.

## Start with connected compute

Install/start your personal worker using [Getting started](GETTING-STARTED.md).
Use a pinned model revision and hardware that can load it. This is a single-owner
worker: anyone with its access token can operate its compute and read its data.
It is not a shared multi-tenant job service.

Use `configs/investigation-example.json` as an editable example. It targets Muse
Glimmer and requires substantial GPU memory; it is not a free CPU demo or a proven
optimal configuration. Change the prompt, answers, scan region and budgets before
running. `scan.end: null` scans to the end of the completed original response.
A too-short or capped original response stops the pipeline with an explanation.
This automatic CLI configuration uses the existing `answer_text_anywhere_v1` rule: mentions may be
misclassified; multiple matches, unmatched and incomplete replies are Other.

Supply the worker token through `FORK_WORKER_TOKEN` in your shell environment.
Do not put tokens in JSON, command arguments, shared screenshots, or bundles.
`FORK_WORKER_URL` defaults to `http://127.0.0.1:8767`. Remote HTTP is rejected;
use HTTPS or an SSH tunnel. The client does not follow authentication redirects.

For `machine start` on its default port, set `FORK_WORKER_URL` to
`http://127.0.0.1:8768` (or its HTTPS address). On that machine, the private token
file is `~/.local/state/fork-microscope/machine-8768/token`; load it into the
`FORK_WORKER_TOKEN` environment variable without printing it. Browser pairing does
not automatically configure your CLI shell. Substitute your port if customized.


```bash
fork-microscope investigation start configs/investigation-example.json --request-id attendance-001
fork-microscope investigation list
fork-microscope investigation status JOB_ID
fork-microscope investigation cancel JOB_ID
fork-microscope investigation export JOB_ID investigation.json
```

An installed checkout also supports `.venv/bin/python -m fork_microscope.fork_cli investigation ...`. Commands for
remote requests need no local model weights; the worker still needs its normal
runtime dependencies. Replace JOB_ID with the returned 32-character ID.

A request ID is an idempotency key: retrying with the same ID and config retrieves
the same job. Reusing it with different settings is rejected. Use a new ID for an
intentional new experiment. One investigation owns the worker until it finishes;
manual model jobs are rejected during that period.

In the dashboard's Setup page, open **Automate a complete investigation · advanced**, choose
the same configuration JSON, enter a request ID, review the summary and select
**Start on connected compute**. The job controls show progress, cancellation,
explicit resume, export, and an Explorer link. The advanced automatic route uses a configuration file; the standard manual route above uses forms. Not every adaptive-policy setting has a visual form.

## What adaptive means here

The initial scan uses the specified fixed interval and samples. After each run,
the coordinator considers adjacent checkpoints in a completed fit and chooses
the largest *raw* outcome-distribution total variation distance above `min_tvd`.
It collects fresh samples within that interval at the refinement stride and sample
count, preserving the exact original prompt and response IDs. It stops at the
round limit, when no eligible interval remains, or before exceeding its allowance.
It does not refine intervals already as narrow as the requested stride.

This is a deterministic, auditable exploratory policy, not sequential hypothesis
testing or a validated cost-saving algorithm. It can chase noise or miss changes
between initial checkpoints. Selection rationale and seeds are saved. Goodfire
reconstruction runs unchanged on each new pass. No significance or causal claim
is added by the coordinator.

If lens inspection is enabled, it searches completed runs from newest to oldest
for a checkpoint with completed, classified continuations reaching different
outcomes. It chooses the most balanced eligible checkpoint, breaks ties by token
position, and selects the first contrasting pair in draw order. The inspection
window surrounds that pair's first differing token, not necessarily its sampling
checkpoint. The automatic configuration pipeline collects at most one paired lens job; the interactive investigation can request additional bounded inspections.
If no pair exists, lens inspection is explicitly skipped. A missing/incompatible
lens yields an error with the already-collected runs retained for export.

Readouts describe activations *after* consuming each selected token. Vocabulary
ranks are not outcome probabilities, recovered hidden sentences, or proof of
causation. Numeric positions need not align semantically after divergence. Muse's
community lens reports an unmet convergence threshold; adapter parity does not
validate the learned lens. No lens fit is trained by this workflow.

## Limits and interruptions

Before a phase starts, the coordinator reserves its maximum samples and generated
tokens (draws × continuation cap; original generation also reserves its cap).
Reservations are conservative and are not refunded on interruption or short
outputs. Refinement includes both endpoints in its reservation. Lens replay and
model loading generate no new text, but use compute and count against time.

`max_seconds` covers the workflow, including loading and replay. Cancellation is
cooperative at worker checks: an in-flight GPU kernel, download or reconstruction
may take time to stop. It is **not** a strict dollar or wall-clock billing cap.
The tool does not know your provider's final bill and does not shut down a VM.

A restarted worker marks running investigations interrupted. Nothing resumes
paid work automatically. `fork-microscope investigation resume JOB_ID` resumes
from completed evidence, reconciles a completed pending operation by its saved
ID, and keeps previous reservations charged. An unfinished phase may run again;
it does not resume individual draws. Downtime can exhaust the original time
allowance. Cancelled/exhausted jobs cannot resume under the same budget; export
their completed evidence or submit a deliberate new request.

Keep `workflow-jobs/`, `live-runs/`, and `investigations/` on storage that survives
worker restart. Ephemeral VM deletion still deletes unexported evidence. Download
before shutdown. Filesystem/disk failure is not recoverable without backups.

## Export and open the entire investigation

Use Export investigation in Explorer to collect a saved run's available family:

```bash
fork-microscope investigation export-run RUN_ID investigation.json
fork-microscope investigation import investigation.json
```

The same file imports through Explorer's **Import run** button. The bundle contains
all included runs, parent links, exact token IDs, raw continuations, classifications,
fits, model and sampling provenance, selected readouts, and coordinator rationale
when exported from a workflow job. Explorer keeps each run individually navigable.
Current v3 exports include completed activation patches, exact-prefix edits and
activation captures, together with available response/search metadata. Existing
v1/v2 files remain readable. Unfinished artifacts are rejected from complete
exports with an error rather than silently omitted. Manual actions launched from
a selected investigation use its coordinator and resource ledger. A legacy action
without an investigation is labeled outside that ledger.

Saved bundles can be imported and browsed entirely in the browser without a
worker or GPU. New generation and internal measurements require compatible
compute. New cross-run statistical comparison still uses the existing worker API;
opening individual saved curves, continuations and readouts does not.

Bundles are versioned JSON with SHA-256 integrity checking, limited to 64 MB.
They contain no executable archive paths, model weights, caches or operational
credentials. Prompt and output text remain verbatim: review for private content
before sharing. Checksums detect corruption, not authorship or scientific truth.
Imports validate records against counts and validate lens source links. Missing
parents and conflicting IDs are rejected. Identical reimports are safe. Installation
is staged and existing evidence is never overwritten; retry after an interruption
can complete missing files. Local imports do not recompute fitted curves.

## API

All routes use the existing worker Bearer authentication and allowed-origin rules.
Use one owner per worker; transport connects existing compute only.

| Method | Route under `/api/live/` | Body / result |
|---|---|---|
| POST | `workflow-create` | `{request_id, context, limits}` → draft investigation |
| POST | `workflow-update` | `{id, record_revision, conclusion?, selected_response_id?, comparison?, context?}` |
| POST | `classifier-preview` | `{rule, text, complete}`; same classifier as execution |
| POST | `workflow-search` | `{id, request_id, target, max_attempts, max_tokens, seed, temperature}` |
| GET | `responses?investigation_id=ID` | saved response catalog |
| GET | `response?id=ID` | exact IDs, raw text, classification and provenance |
| POST | `workflow-operation` | `{id, request_id, action, payload}`; scan/refine/lens/investigate/patch |
| POST | `workflow-start` | `{request_id, config}` → saved job |
| GET | `workflows` | list jobs |
| GET | `workflow?id=ID` | job and active worker progress |
| POST | `workflow-cancel` | `{id}` |
| POST | `workflow-resume` | `{id}` |
| GET | `workflow-export?id=ID` | versioned complete-evidence bundle |
| GET | `bundle-export?id=RUN_ID` | saved run family, lens readouts and completed patches |
| POST | `import` | bundle or existing single-run export |

Polling is sufficient; streaming events, provider provisioning, multiple concurrent
models, and automatic internal activation interventions are not implemented.

## Validation of this implementation

The coordinator is tested with a simulated model service, including positive
refinement and lens paths, no contrasting pair, sample-budget stopping,
cancellation, safe retries, authenticated HTTP calls, and restart reconciliation.
Those tests do not establish real-model performance or adaptive-policy accuracy.

A separate saved-evidence check exported three existing Muse runs and two completed
J-lens artifacts, imported them into empty storage through the same API used by the
GUI, re-exported an identical portable bundle, and opened a readout in Explorer
without loading a model. No new GPU experiment was launched for this implementation.

Run checks with `.venv/bin/python -m pytest -q` and `node --test tests/test_*.mjs`.
After upgrading a checkout with new modules, rerun
`uv pip install --python .venv/bin/python --no-deps -e .` before restarting its
installed CLI. The full installation guide handles dependency upgrades separately.

### Browser-safe integrity checks

New bundle checksums normalize integral JSON numbers (`1.0` and `1`) before
hashing, so browser export/import preserves validity. Nonintegral values and
large integers are not rounded. Untouched earlier v1 bundles remain accepted.
If a bundle from an earlier browser download fails its checksum, re-export from
the original worker; do not remove or manually replace the checksum.

### Manual API contract and recovery

`context` contains `name`, `question`, `input` and `outcome_rule`, with optional
model identity. Input uses `schema: "fork-input-v1"`, `prompt`, `mode` (`chat` or
`base`), and optional prior `messages` (`role`/`content`) in chat mode. The final
prompt is appended after the prior messages. Model chat-template support is checked
at execution; different templates can reject otherwise valid roles.

An outcome rule uses `schema: "fork-outcome-rule-v1"`, `method: "text_match"` or
`"final_marker"`, and `answers`. Final-marker rules also record `marker` (default
`"Final answer:"`). Multiple competing matches, unfinished replies and unmatched
responses are Other. Matching is not semantic judgment. Inspect the evidence text.

A search with `target: null`, `max_attempts: 1`, `temperature: 0` generates one
response. Targeted searches require positive temperature. Each attempt is persisted
before the next; interruption inside upstream generation cannot recover a partial
response that the model adapter has not returned.

For a scan, use `action: "scan"` and
`payload: {"response_id": "ID", "settings": {...}}`. Settings use the existing
sampling contract in [Configuration](CONFIGURATION.md); checkpoint samples are
**total samples across retained branches**, not samples for each branch.
Other actions use their existing previewed request as payload. Reuse the same
request ID only to retry identical settings. After an interrupted manual operation,
inspect retained evidence before choosing a new operation ID; the coordinator does
not silently regenerate potentially completed work. Reserved allowances stay charged.

`limits` requires positive integer `max_seconds`, `max_samples` and
`max_generated_tokens`; `max_attempts` is optional. Worst-case generated tokens and
samples are reserved before operations. Lens replay is bounded by its selected
positions, layers and runtime, and is not captured by a generated-token counter.
Runtime cancellation is cooperative, including possible in-flight forward overruns.
Estimated dollars are not a provider billing cap.
