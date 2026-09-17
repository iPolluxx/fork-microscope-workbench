# Contributing

Fork Microscope turns checkpoint sampling into an inspectable workflow. Useful contributions make model attachment, reproducibility, evidence inspection, accessibility or deployment easier without obscuring what was measured.

## Development setup

Follow the [README](README.md) and use `./scripts/setup.sh cpu` for development without a GPU. Run one worker process per checkout/data directory. The frontend uses plain HTML, CSS and ES modules; no npm application install or frontend framework build is required.

All Python modules listed below are in `src/fork_microscope/`; all test files are in
`tests/`. Install editable before running the CLI outside the checkout.

- `live_model.py`, `model_preflight.py`: native model adapter and eligibility checks.
- `live_service.py`, `sampling.py`: job ownership, sampling, reconstruction and run records.
- `replay_trace.py`, `refinement.py`, `run_comparison.py`: exact-trace replay and evidence comparison.
- `investigation.py`, `lens_integration.py`: controlled text edits, exact-token internal readouts and bounded activation reuse.
- `workspace_store.py`: prompt sets and batch snapshots.
- `microscope_server.py`, `worker_connection.py`: worker API and access boundary.
- `public/fork-microscope/`: Setup → Explore → Inspect & Test, plus Workspace, Guide and compatibility pages.
- `docs/ARCHITECTURE.md`: components, ownership and measurement contracts.
- `vendor/forking-fast`: pinned upstream submodule; do not edit it as part of an unrelated change.

## Before opening a pull request

1. State the user problem and final behavior. Include a small reproduction for a bug.
2. Run the checks below. Add focused tests for changed scientific, storage or authentication behavior; avoid tests that merely duplicate a cosmetic edit.
3. Check desktop/mobile behavior when changing the interface. Keep core controls keyboard accessible.
4. Say what was actually tested. A fake adapter test is not GPU validation, a lower token budget is not measured savings, and a fitted boundary is not proof of a causal mechanism.

```bash
.venv/bin/python -m pytest -q
node --test tests/test_*.mjs
.venv/bin/fork-microscope verify-upstream
python3 scripts/build_dashboard.py
```

Keep model weights, tokens, provider credentials, full private runs and local agent state out of commits. Use small synthetic fixtures, or link to deliberately published evidence with its provenance. Bug reports should include the commit, OS, model/revision, relevant settings and sanitized errors.

Changing a model adapter requires a completion/tokenization check. Changing extraction, sampling, smoothing or comparison requires preserving or versioning the measurement contract. Do not silently alter old run records to match new semantics. Add a bounded CPU test where possible and clearly distinguish any GPU validation still needed.

## Attribution and release

Credit Goodfire's method where evidence is displayed, including recorded upstream revisions. Keep the project's MIT license and third-party notices in distributed artifacts. Contributions to this project's code are offered under its MIT license; that does not grant permissions for unrelated third-party code/data.

The pinned upstream dependency's distribution permissions remain unresolved. See [THIRD-PARTY.md](THIRD-PARTY.md) before publishing a bundled image. Public-source and package releases need an explicit maintainer decision; a merged contribution does not publish an image or provision compute.
