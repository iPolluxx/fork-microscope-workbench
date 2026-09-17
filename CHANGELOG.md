# Changelog

## Documentation and bounded audit — 2026-09-17

- Reproducible single-case reconstruction audit with input/output arrays, pinned
  runtime source checks, selectable checkpoints/sample count and negative tests.
- Agent documentation distinguishes the repository CLI/API, separately packaged
  private MCP companion, and browser pairing. No public MCP endpoint is added.
- Corrected import/transfer instructions and current Setup navigation names.
- MCP companion validation is reported separately from this repository and from
  real-model experiments; see [its guide](docs/agents/MCP.md).

## Investigation workflow — 2026-09-15

- Setup → Explore → Inspect & Test with persistent investigation selection.
- Saved responses, bounded outcome search, manual operations, comparisons and
  conclusions; complete v3 bundles retain earlier v1/v2 compatibility.
- Offline evidence browsing with explicit transfer to compute before new work.

## Earlier implementation — package layout and machine pairing

- Python application in `src/fork_microscope/`; Python and JavaScript tests in `tests/`.
- Background machine startup, expiring one-time pairing and optional temporary HTTPS.
- Persistent outcome map, checkpoint context, contrasting-pair navigation and clearer compute status.
- Shared configuration reference and separated interpretation/workflow guides.
- Fresh public repository history: earlier checkouts migrate by new clone and evidence import, not force-pull.

## Earlier implementation — updates on main — internal inspection and controlled patches

- Optional, pinned NNsight capture on the existing model, with exact-token replay,
  temporary instrumentation, backend provenance and unchanged native defaults.
- Bounded donor/recipient activation patches with baseline and self-copy controls,
  explicit selection rationale, cancellation and saved continuation records.
- Progressive controls in Explore → Look inside, with compute availability and
  saved evidence kept distinct.
- Same-origin connection handoff between open tabs, clearer authentication errors,
  and local-library guidance for browsing saved runs in fresh windows.
- Version-2 investigation bundles include completed patch experiments; existing
  version-1 bundles remain supported.

These additions are tested on tiny random CPU models. Muse compatibility, NDIF
connections, vLLM sampling and compute savings are not established by this work.

## Updates on main — 2026-09-12

These changes are on `main`; the package remains `0.1.0b1` (no new tagged release).

- Original-response review and completion checks before scanning; explicit acceptance
  of ambiguous/unmatched completed responses and full-trace default checkpoint coverage.
- Weight-download and generation progress, clearer units and conditional remaining-time estimates.
- Retryable evidence downloads and preserved saved-run navigation.
- Exact-token Jacobian lens comparisons for two completed outcomes, original traces,
  saved draws and edit/control prefixes, with model/lens provenance checks.
- First-difference focus, expandable token/layer views, prompt and final-layer baselines,
  and restoration of saved inspection settings.
- Bounded model-session activation reuse, predicted and recorded forward work, and
  invalidation on model/parameter/device changes.
- Documentation and isolated installed-CLI validation for worker upgrades.

At that revision, activation patching, trained probes, automatic VM provisioning
and guaranteed compute savings were not implemented. Controlled patches were added
later, as recorded above; the other limitations remain. Update the worker as well as the dashboard.

## 0.1.0-beta.1 — 2026-09-10

First portfolio/source beta of Fork Microscope.

- Shared hosted dashboard with independently owned local/VM workers.
- Worker connection launcher that generates or reuses a token and prints the address.
- Native model inspection, explicit attachment and custom prompt/answer tracking.
- Saved prompt sets, sequential batches and independently configurable passes.
- Raw outcomes and Goodfire reconstruction, recorded text, exact-trace refinement and comparison checks.
- Portable prompt/evidence exports, CPU verification and static-only Cloud Run deployment.

Scope: Linux checkout installation; supported native model adapters. No public bundled GPU image while upstream distribution permissions are unresolved. No automatic VM provisioning, general model compatibility, executed token edits, activation interventions or automatic partial-draw resume.
