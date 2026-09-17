<!-- generated: Codex — Paper2Agent-guided method verification, 2026-09-17. -->
# Reproduce one reconstruction comparison

This is a bounded scientific-code check: given identical outcome samples, does
Fork Microscope reconstruct the same curve as the pinned upstream baseline?
It uses public released evidence and CPU computation, with no model loading,
worker connection, credentials or GPU.

## Run it

After the normal CPU installation and submodule initialization:

```bash
.venv/bin/python scripts/verify_math.py \
  --case llama:12 \
  --output outputs/paper2agent/llama12.json
```

The default selected case uses at most 24 evenly spaced observed checkpoints and
20 mixture samples per checkpoint. For a changed-input check:

```bash
.venv/bin/python scripts/verify_math.py \
  --case deepseek:39 --checkpoints 8 --samples 40 --seed 43000002 \
  --output outputs/paper2agent/deepseek39.json
```

Supported cases are `llama:12`, `llama:39`, `deepseek:12`, and `deepseek:39`.
Repeat `--case` to choose several. `--checkpoints` accepts 4–24; `--samples`
accepts 20 or 40 so five-fold CV has equal-sized folds; `--seed` must be nonnegative.
These bounds keep this verification small; they are not recommended production
sampling settings. Omitting `--case` retains the broader existing audit of all
203 released stores and four reconstruction cases.

Require a successful process exit and use a fresh output filename for a new
comparison. A failed run must not be confused with a previously saved report.
Outputs are local and ignored by Git.

## What the JSON contains

`schema: fork-reconstruction-audit-v1` identifies the report format.

- `settings` and `scope`: which stores, samples, seed and checkpoint subset were used.
- `upstream_commit`, `source_hashes`, `runtime_sources`: source provenance. Imported
  `otrecon.data`, `otrecon.models` and `otrecon.cv` must match pinned source bytes,
  even if Python imports them from an installed package in another environment.
- `cases[].source_sha256`: original compressed public-store identity.
- `categories`, `positions`, `draws`, `counts`: the exact input arrays used by both paths.
- `application` and `upstream`: raw proportions, selected CV parameters and best
  score, support, fitted probabilities, marginal bands and breakpoint intervals.
- `checks`: comparison errors and the separate small analytic likelihood/PELT check.
- `limitations`: the scope of the claims supported by the run.

The application calls its existing `live_service.reconstruct`. The reference
calls the pinned repository's isolated `otrecon/baseline` implementation directly.
No scientific estimator is reimplemented in a production wrapper. Independent
Gaussian pooling algebra remains a verification-only check in the audit script.

## Interpretation and boundaries

Agreement means that the checked integration reproduces the reference computation
on these exact inputs. It does **not** mean the curve is ground truth, every
checkpoint is accurate, or a suggested interval is causal.

The audit constructs a synthetic live-record adapter from public outcome labels.
Its completion and retained-mass metadata enable an estimator test; they are not
new measurements of generated text or the live collector. Mixture draws must match
the baseline and use no exhaustion fallback. They consume recorded branch labels
in order; this is not an independent verification that fresh model sampling is IID.

The audit forces the reference ruptures segmentation path. It does not validate
every optimized execution path. Only the selected/best CV score and candidate count
are compared, not the entire candidate score map. Bands match the reference but
are not calibrated by this test. Small exhaustive partition verification covers
one multinomial example, not every fitted segmentation variant.

## Paper2Agent's role

The development workflow used Paper2Agent's source-reuse and independent-verifier
principles to inspect and extend an existing audit. Reviewed skill revision:
`c5ce59cc726eddebd6623cc70ad2a80ae55c224e` from
[jmiao24/Paper2Agent](https://github.com/jmiao24/Paper2Agent).
Paper2Agent is optional developer tooling, not a Fork Microscope runtime dependency.

This audit is a reproducible comparison with inspectable inputs and outputs, not
a scientific certification. A separate Paper2MCP conversion and reviewed paper
skill were subsequently prepared; see [the MCP companion guide](agents/MCP.md)
for their delivery scope. Their transport tests do not extend this audit into a
full-paper replication or validate new model inference. See
[Numerical validation](MATH-VALIDATION.md) for the wider existing audit.
