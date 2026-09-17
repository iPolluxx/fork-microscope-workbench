<!-- generated: Codex — documentation updated for the investigation revamp, 2026-09-15; authorized by Isaiah. -->
# Validation scope

Validation separates the application tests, the pinned estimator's numerical checks and actual model inference. Passing CPU tests does not establish GPU performance or scientific reliability for an arbitrary model.

## Reproduce the checks

From the repository root:

```bash
./scripts/setup.sh cpu
.venv/bin/python -m pytest -q
node --test tests/test_*.mjs
.venv/bin/fork-microscope verify-upstream
.venv/bin/python scripts/verify_math.py
.venv/bin/python scripts/build_dashboard.py
.venv/bin/python scripts/build_cloud_dashboard.py
```

These checks do not require private run archives or a GPU. The build commands produce local files only; they do not deploy a service. Generated audits and build artifacts are excluded from Git.

## Investigation revamp — 2026-09-15

The full local suite passed **303 Python tests (3 skipped)** and **48 JavaScript
tests**. A subsequent copy-only terminology cleanup passed 20 targeted JavaScript
tests and module syntax checks. Both static build paths are checked before release.
Tests cover exact response replay, safe retries, cancellation ownership, bundle
integrity/provenance, browser import/export and selection restoration.

A real local CPU SmolLM2-135M-Instruct workflow generated a response, scanned its
saved token sequence, exported version-3 evidence and imported it into the static
browser. The updated scan took 164.660 seconds in that local test; this is not a
GPU performance estimate. GUI transfer to an empty worker preserved the response
without loading a model. Saved Muse evidence supported browser/readout checks;
no new Muse GPU run was performed for the revamp.

The compatibility audit opened 19 historical runs without modifying 76 source
JSON files and round-tripped 12 supported families. Two earliest legacy runs lack
saved decoded continuation text and finish metadata: their curves remain readable,
but missing text is not manufactured. Synthetic intervention fixtures and saved
readouts do not constitute fresh on-model intervention validation.

A warm-cache fresh local clone installed successfully. This was not a cold remote
VM test. Agent browser checks found no mobile overflow at 390 pixels and preserved
selection through navigation/refresh. A new human participant has not yet tested
this workflow; usability acceptance remains unverified.

## Package-layout verification — 2026-09-14

The fresh public package layout passed **281 Python tests (3 skipped)** and
**31 JavaScript tests** locally. CLI discovery, the installed package outside the
checkout, both static build paths and background-worker start/stop were checked.
These checks used the existing pinned CPU dependencies through a local verification
environment; they were not a new GPU run or a clean dependency installation.
GitHub's separate CPU setup/build workflow subsequently passed for the initial
public commits `7a58967` and `a6e703d`.

No new model-quality, runtime-cost or lens-fit claims follow from the file-layout
refactor. Earlier numerical and model-specific validation remains historical.

## Checked release candidate

On 2026-09-10, a fresh CPU setup on Linux x86-64 (Python 3.13.13, PyTorch 2.11.0+cpu, Transformers 5.16.1) passed 155 application Python tests, 10 JavaScript tests and 76 upstream tests. All 203 released-store checks and the standalone numerical audit passed. Both static dashboard build paths completed successfully. These are local checks, not a deployed-service acceptance test.

## Historical validation — 2026-09-12

The implementation at `8e4176f` passed **214 Python tests and 17 JavaScript checks**.
Both dashboard build paths succeeded. Cloud Run revision `fork-microscope-00006-r7d`
served all traffic after deployment; ten fetched HTML/JS/CSS assets matched the
local build byte-for-byte. These statements describe that deployment, not a promise
that a later deployment or separately installed worker is identical.

Lens tests compare reused readouts with fresh forwards on tiny random CPU models,
check zero additional forwards for a cached narrower view, and exercise cache
bounds, changed prefixes/parameters, shared-prefix pairs and parity checks. An
isolated Python subprocess also verifies that the installed CLI can resolve the
lens backend outside the checkout. Browser checks covered focusing, expanding,
selecting the prompt baseline, opening old Muse artifacts and restoring settings.
No new full-Muse inference or GPU throughput benchmark was run for these changes.
See [lens validation and limits](docs/JACOBIAN-LENS.md).

## Coverage

The NNsight/patching integration was checked before release on 2026-09-13 with **273
application tests passing and 3 optional-runtime tests skipped** in the standard
environment, plus **23 JavaScript checks**. The three skipped tests were separately
executed using pinned NNsight 0.7.0 in an isolated dependency environment; the
combined inspection/workflow/lens suite passed all **69 tests** there. A subsequent
HTTP asset-serving regression was added and the affected release/bundle suite
passed **24 tests**. Both static builds succeeded. Nothing was deployed by these checks.

New validation includes real forward passes and generation on tiny randomly
initialized CPU Llama models, native-vs-NNsight readout equality, temporary
instrumentation cleanup, baseline/self-copy controls, one-time prefill patching,
caps/cancellation and bundle round trips. Synthetic scan and patch fixtures exercise
orchestration/import errors; they are not research findings. Desktop/mobile browser
checks used a copy of saved Muse scan evidence and an explicitly synthetic patch
artifact. No new Muse inference, GPU throughput comparison or NDIF/vLLM run was
performed. The pinned Goodfire audit still recomputes all 203 released stores and
matches the four checked live-schema fits exactly.

Application tests cover mixture collection and budgets, saved evidence, answer matching, completion gates, reconstruction, exact-prefix replay, refinement endpoints, run-comparison eligibility, workspace storage, worker connection controls, model preflight and packaging. JavaScript tests cover grid math and graph-evidence handling. Synthetic refinement fixtures are separate from runnable model examples.

The [numerical audit](docs/MATH-VALIDATION.md) checks 203 released stores, four bounded live-schema reconstruction cases against the pristine baseline, Gaussian/Dirichlet algebra and a small exhaustive PELT example. Upstream tests exercise the pinned implementation separately. The historical grid comparison was a separate experiment and is not shipped with this application; it did not establish cost savings.

## Inference and deployment boundary

Earlier development included CPU smoke runs and Muse GPU runs. Those private recordings are not included or required by this source distribution. The release-curation checks do not repeat GPU inference, measure throughput, rebuild a GPU image or certify every supported model/hardware combination. Use the [example profiles](configs/README.md) for your own smoke test, then inspect completed outputs before a larger run.

A model's architecture, native chat format, EOS handling, precision, generation defaults, available memory and token caps affect results. Exact saved IDs and settings improve traceability; cross-device bitwise reproducibility is not promised. Mention matching can misclassify rejected or incidental answer text. Statistical smoothing cannot correct invalid outcome labels.

The hosted interface is static; each connected worker is a single-owner service, not an account-isolated multi-tenant backend. See [Architecture](docs/ARCHITECTURE.md), [Security](SECURITY.md) and [Third-party notices](THIRD-PARTY.md) before deployment.

## Separate agent companion — 2026-09-17

A private MCP companion and reviewed paper skill were built and validated outside
this application checkout. They are not installed by the commands above or shipped
as part of this repository. See [availability, test scope and privacy](docs/agents/MCP.md).
Its simulated-worker lifecycle and saved-evidence tests do not constitute additional
GPU validation or change the application's single-owner worker boundary.
