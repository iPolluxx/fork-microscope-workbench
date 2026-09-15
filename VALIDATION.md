# Validation scope

Validation separates the application tests, the pinned estimator's numerical checks and actual model inference. Passing CPU tests does not establish GPU performance or scientific reliability for an arbitrary model.

## Reproduce the checks

From the repository root:

```bash
./scripts/setup.sh cpu
.venv/bin/python -m pytest -q
node --test test_*.mjs
.venv/bin/fork-microscope verify-upstream
.venv/bin/python scripts/verify_math.py
.venv/bin/python scripts/build_dashboard.py
.venv/bin/python scripts/build_cloud_dashboard.py
```

These checks do not require private run archives or a GPU. The build commands produce local files only; they do not deploy a service. Generated audits and build artifacts are excluded from Git.

## Checked release candidate

On 2026-09-10, a fresh CPU setup on Linux x86-64 (Python 3.13.13, PyTorch 2.11.0+cpu, Transformers 5.16.1) passed 155 application Python tests, 10 JavaScript tests and 76 upstream tests. All 203 released-store checks and the standalone numerical audit passed. Both static dashboard build paths completed successfully. These are local checks, not a deployed-service acceptance test.

## Current main validation — 2026-09-12

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

The unreleased NNsight/patching integration was checked on 2026-09-13 with **273
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

The [numerical audit](docs/MATH-VALIDATION.md) checks 203 released stores, four bounded live-schema reconstruction cases against the pristine baseline, Gaussian/Dirichlet algebra and a small exhaustive PELT example. Upstream tests exercise the pinned implementation separately. The optional [grid benchmark](benchmarks/README.md) is not a full reproduction of the paper's evaluation or proof of cost savings.

## Inference and deployment boundary

Earlier development included CPU smoke runs and Muse GPU runs. Those private recordings are not included or required by this source distribution. The release-curation checks do not repeat GPU inference, measure throughput, rebuild a GPU image or certify every supported model/hardware combination. Use the [example profiles](configs/README.md) for your own smoke test, then inspect completed outputs before a larger run.

A model's architecture, native chat format, EOS handling, precision, generation defaults, available memory and token caps affect results. Exact saved IDs and settings improve traceability; cross-device bitwise reproducibility is not promised. Mention matching can misclassify rejected or incidental answer text. Statistical smoothing cannot correct invalid outcome labels.

The hosted interface is static; each connected worker is a single-owner service, not an account-isolated multi-tenant backend. See [Architecture](docs/ARCHITECTURE.md), [Security](SECURITY.md) and [Third-party notices](THIRD-PARTY.md) before deployment.
