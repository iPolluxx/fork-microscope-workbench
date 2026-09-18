<!-- generated: Codex — documentation reviewed for current workflow and agent companion, 2026-09-17; authorized by Isaiah. -->
# Architecture

[Documentation index](README.md)

Fork Microscope connects an outcome-distribution graph to the generated text behind each observation. A user attaches a supported model to their own worker, generates a base response, samples continuations from selected token positions, and inspects or refines regions where the measured outcome distribution changes.

Python modules below live in `src/fork_microscope/`. All tests live in `tests/`.

## Components and responsibilities

| Component | Responsibility |
| --- | --- |
| `public/fork-microscope/` | Static dashboard: connection setup, prompt workspace, scan configuration, graphs, continuation reader and run comparison. |
| `microscope_server.py`, `worker_connection.py`, `machine_connection.py` | HTTP API, worker authentication, one-time pairing, background startup and allowed browser origins. |
| `live_service.py`, `live_model.py` | Model lifecycle, base generation, sampling jobs, reconstruction and saved runs. |
| `model_preflight.py` | Architecture/tokenizer checks and hardware information before loading. Compatibility checks do not guarantee sufficient memory. |
| `sampling.py`, `outcome_readout.py` | Checkpoint mixture draws and versioned, inspectable answer classification. |
| `replay_trace.py`, `refinement.py` | Restore exact saved token IDs and collect a new, more closely spaced scan with source provenance. |
| `investigation.py`, `lens_integration.py` | Fresh edit/control experiments, raw activation capture, exact-token lens readouts and bounded session-local activation reuse. |
| `nnsight_inspection.py`, `activation_patching.py` | Optional temporary NNsight capture over the attached model, and separate bounded native donor/recipient interventions with fresh controls. |
| `run_comparison.py` | Check comparison eligibility and report distribution differences over supported positions. |
| `workspace_store.py`, `evidence_io.py` | Saved prompt sets and validated evidence import/export. |
| `investigation_workflow.py`, `investigation_catalog.py`, `investigation_records.py` | Shared investigation records, durable responses/search, operation coordination and budget reservations. |
| `investigation_bundle.py` | Versioned portable bundles, integrity and provenance validation, safe conflict handling. |
| `selection.mjs`, `investigation-shell.mjs`, `investigation-workbench.mjs` | Persistent selection, three-area navigation and interactive investigation workflow. |
| `offline-evidence.mjs`, `evidence-import.mjs`, `classification.mjs` | Browser-only evidence storage/validation, explicit transfer and local outcome-rule preview. |
| `vendor/forking-fast/` | Pinned, unmodified Goodfire implementation and released data. |
| `fork_cli.py`, `scripts/`, `docker/` | Installation, command-line jobs, packaging and worker deployment. |

## A run from input to evidence

1. The worker inspects and loads a model revision with its tokenizer and native prompt format. It generates a base response and saves the prompt and response token IDs.
2. A scan selects checkpoints on that same response. At each checkpoint, it retains the original prefix and constructs a probability distribution over the retained next-token branches.
3. Each observation selects a branch before generating a continuation. The configured sample count is **total draws per checkpoint**, not per branch. Omitted branch probability is recorded; the distribution is conditional on the retained branches.
4. The answer matcher labels completed continuations. Matching text is not semantic verification: mentions and rejected alternatives can be misleading, and capped outputs become `Other`. The raw texts and matching evidence remain inspectable.
5. Eligible counts are passed to Goodfire's M5a segment-kernel estimator. Cross-validation selects smoothing parameters; completion and checkpoint-count gates can withhold a fitted curve. See [the method reference](REFERENCE.md) and [numerical validation](MATH-VALIDATION.md).
6. Refinement restores the saved IDs on the same model revision, then samples a smaller region into a separate run. It preserves the source rather than generating a replacement base. Editing text can produce an exported draft or a separately saved fresh edit/control experiment. Read-only decoder activation capture is also available; see [Investigations](INTERPRETING-RESULTS.md).

7. A user can select two completed draws with different labels and inspect their
   first differing saved token with a compatible lens. A small position/layer view
   expands on demand. Exact-prefix activation reuse can avoid repeated forwards;
   uncached regions still require full decoder execution. Readouts and execution
   counts are saved separately in `investigations/`. See [Jacobian Lens](JACOBIAN-LENS.md).

## Hosting and ownership

The hosted dashboard is static. Multiple visitors can use it while connecting directly to their own worker addresses; the static host does not run models or store their run archives. Worker credentials are held in browser session storage and sent to the selected worker. Remote workers require access protection, an appropriate allowed origin and a browser-reachable HTTPS endpoint; follow the [connection guide](HOSTED-DASHBOARD.md).

A worker is a **single-owner compute service**, with shared state for its attached model, jobs and files. It is not a multi-tenant backend with per-user accounts, independent authorization or isolated job storage. Giving several people the same worker token gives them access to that worker. One owner per worker is the supported deployment boundary.

The application does not provision cloud hardware, handle provider billing, mount storage automatically or terminate rented machines. Model weights, credentials, arbitrary local run archives and generated validation outputs are excluded from Git. The curated attendance demo is the explicitly included saved-evidence exception. Ephemeral machines require exporting results before deletion.

## Reproducibility and limits

Saved evidence records model identity, IDs, generation settings, branch probabilities, draw order, labels and completion diagnostics. Exact-prefix replay reduces accidental changes between scans; it does not promise identical floating-point results across devices. An independently sampled dense reference remains a noisy estimate.

A change in outcome frequencies identifies a region worth inspecting. It does not establish that displayed reasoning is faithful, locate a unique causal token, or expose an internal activation mechanism. Trained activation probes are not implemented. Text interventions have separate fresh controls. Experimental native activation patches compare unmodified, self-copy and donor-patched continuations at a single selected prefill position; these are separate experiments from the scan. Optional NNsight capture replays exact tokens through the already loaded model and preserves the existing J-lens readout. Both new paths have local tiny-model CPU validation, not on-model Muse certification. See [internal inspection and controlled patches](NNSIGHT-INTEGRATION.md).

The dependency and redistribution boundaries are documented in [THIRD-PARTY.md](../THIRD-PARTY.md). Development planning records and personal run histories are not required to use or validate the software.

### Durable investigation jobs

`investigation_workflow.py` coordinates existing `LiveService` actions under an
exclusive workflow owner. Stable operation IDs are journaled before execution;
completed artifacts remain in their existing stores. `workflow-jobs/` holds job
state and conservative reservations. A process restart marks running jobs
interrupted for explicit recovery. `workflow_cli.py` is an authenticated HTTP
client; `workflow-panel.mjs` uses the same endpoints.

Version-2 workflow records extend the existing coordinator with context, responses,
search attempts, manual operations and conclusions. Version-1 jobs remain readable;
explicit upgrades preserve a backup. Stable browser request IDs survive uncertain
retries. Manual recovery reconciles existing artifacts rather than silently
repeating interrupted model calls.

`investigation_bundle.py` packages and validates versioned JSON bundles. Version 3
adds complete responses, edits, captures and investigation metadata; earlier bundle
readers remain supported. The browser validates the same portable shape before
IndexedDB storage. Safe annotation updates re-export a new integrity-checked bundle.
Compute is blocked in saved-evidence mode until an explicit worker handoff. Import
stages all artifacts, refuses conflicts, then installs immutable run/lens files.
`investigation-bundles/` preserves imported handoff manifests. These private data
folders are excluded from Git and Docker build contexts. This implementation
supports one worker process per data directory, not distributed scheduling.

### Shared navigation

Every main page uses the same static header plus the investigation shell.
**Setup / Explore / Inspect & Test** are the primary working areas; Guide remains
help and the logo returns to Workspace. Existing HTML URLs remain compatible.
Typed selection state carries exact run/pass/checkpoint/continuation references
through navigation and refresh. The outcome map stays mounted during inspection;
response selection and advanced controls collapse independently.

Edit `scripts/sync_navigation.py` for shared markup, then run
`python3 scripts/sync_navigation.py`. Dashboard builds check that every page is
in sync, including the released-data explorer, so one page cannot silently ship a
different menu. Research panels keep their own layouts and controls.

### Paths and package installation

Python is a `src` package installed with `uv pip install --python .venv/bin/python --no-deps --editable .`. The console entry point is
`fork_microscope.fork_cli:main`. Resource paths resolve to the checkout root, not
the shell’s current directory. This remains a checkout-based application, not a
standalone wheel containing UI and upstream resources.

Each checkout has its own ignored runtime directories. A fresh checkout does not
automatically inherit an older checkout’s library; use evidence bundle import.
Machine service credentials live outside the checkout under the user’s private
`~/.local/state/fork-microscope/machine-PORT/` directory. Stop an existing service
before using the same port from another checkout.

### Optional private agent companion

The optional MCP companion in `integrations/mcp/` uses local stdio → the existing
`workflow_cli.Client` → an authenticated worker. Saved-file navigation uses the
native `investigation_bundle.validate_bundle` and does not contact compute. Its
profile stores evidence, exports, credentials and job ownership separately from
browser storage. No MCP server is started by `fork-microscope serve`, and no
companion implementation is bundled in this checkout. See [MCP scope](agents/MCP.md).
