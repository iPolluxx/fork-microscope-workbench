# One investigation, from compute to Explorer

[Documentation index](README.md)

An investigation coordinates one original response, an initial checkpoint scan,
bounded refinement, and an optional paired Jacobian lens inspection. The worker
runs the model. A CLI, an agent, or the dashboard can start the same job. Closing
the browser does not stop it. No cloud VM is provisioned or terminated by this API.

See the [agent configuration reference](CONFIGURATION.md) for every field,
allowed values, effects, budget arithmetic, and manual-operation alternatives.

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
Answer matching uses the existing `answer_text_anywhere_v1` rule: mentions may be
misclassified; multiple matches, unmatched and incomplete replies are Other.

Supply the worker token through `FORK_WORKER_TOKEN` in your shell environment.
Do not put tokens in JSON, command arguments, shared screenshots, or bundles.
`FORK_WORKER_URL` defaults to `http://127.0.0.1:8767`. Remote HTTP is rejected;
use HTTPS or an SSH tunnel. The client does not follow authentication redirects.

```bash
fork-microscope investigation start configs/investigation-example.json --request-id attendance-001
fork-microscope investigation list
fork-microscope investigation status JOB_ID
fork-microscope investigation cancel JOB_ID
fork-microscope investigation export JOB_ID investigation.json
```

A checkout also supports `python3 -m fork_microscope.fork_cli investigation ...`. Commands for
remote requests need no local model weights; the worker still needs its normal
runtime dependencies. Replace JOB_ID with the returned 32-character ID.

A request ID is an idempotency key: retrying with the same ID and config retrieves
the same job. Reusing it with different settings is rejected. Use a new ID for an
intentional new experiment. One investigation owns the worker until it finishes;
manual model jobs are rejected during that period.

In the dashboard's Configure page, open **Run a complete investigation**, choose
the same configuration JSON, enter a request ID, review the summary and select
**Start on connected compute**. The job controls show progress, cancellation,
explicit resume, export, and an Explorer link. This first UI uses a configuration
file; it does not yet provide a visual form for every policy setting.

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
checkpoint. Only one paired lens job is collected per investigation in v1.
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
Family exports also include completed activation-patch experiments in a version-2
bundle, with donor/recipient prefixes, controls and selection rationale. Version-1
bundles remain readable. Partial patches remain standalone artifacts and are not
included in a complete-evidence bundle. Manual patching is not an automatic
coordinator step; export the run family after adding manual experiments.
Viewing saved evidence requires a reachable local worker/storage server but **no
GPU or loaded model**. Static hosted pages alone do not store imported evidence.

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
