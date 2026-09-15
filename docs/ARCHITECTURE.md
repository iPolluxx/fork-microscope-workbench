# Architecture

[Documentation index](README.md)

Fork Microscope connects an outcome-distribution graph to the generated text behind each observation. A user attaches a supported model to their own worker, generates a base response, samples continuations from selected token positions, and inspects or refines regions where the measured outcome distribution changes.

Python modules below live in `src/fork_microscope/`. All tests live in `tests/`.

## Components and responsibilities

| Component | Responsibility |
| --- | --- |
| `public/fork-microscope/` | Static dashboard: connection setup, prompt workspace, scan configuration, graphs, continuation reader and run comparison. |
| `microscope_server.py`, `worker_connection.py` | HTTP API, worker authentication and allowed browser origins. |
| `live_service.py`, `live_model.py` | Model lifecycle, base generation, sampling jobs, reconstruction and saved runs. |
| `model_preflight.py` | Architecture/tokenizer checks and hardware information before loading. Compatibility checks do not guarantee sufficient memory. |
| `sampling.py`, `outcome_readout.py` | Checkpoint mixture draws and versioned, inspectable answer classification. |
| `replay_trace.py`, `refinement.py` | Restore exact saved token IDs and collect a new, more closely spaced scan with source provenance. |
| `investigation.py`, `lens_integration.py` | Fresh edit/control experiments, raw activation capture, exact-token lens readouts and bounded session-local activation reuse. |
| `nnsight_inspection.py`, `activation_patching.py` | Optional temporary NNsight capture over the attached model, and separate bounded native donor/recipient interventions with fresh controls. |
| `run_comparison.py` | Check comparison eligibility and report distribution differences over supported positions. |
| `workspace_store.py`, `evidence_io.py` | Saved prompt sets and validated evidence import/export. |
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

The application does not provision cloud hardware, handle provider billing, mount storage automatically or terminate rented machines. Model weights, credentials, run archives and generated validation outputs are excluded from Git. Ephemeral machines require exporting results before deletion.

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

`investigation_bundle.py` packages and validates versioned JSON bundles. Import
stages all artifacts, refuses conflicts, then installs immutable run/lens files.
`investigation-bundles/` preserves imported handoff manifests. These private data
folders are excluded from Git and Docker build contexts. This implementation
supports one worker process per data directory, not distributed scheduling.

### Shared navigation

Every main page uses the same static header, styled by `app-navigation.css`.
Workspace, Configure, Explore, Compare and Guide stay in that order; page-specific
steps and tools sit below the global navigation. The logo always returns to
Workspace. Navigation remains usable without JavaScript.

Edit `scripts/sync_navigation.py` for shared markup, then run
`python3 scripts/sync_navigation.py`. Dashboard builds check that every page is
in sync, including the legacy explorer, so one page cannot silently ship a
different menu. Research panels keep their own layouts and controls.
