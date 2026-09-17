# Numerical validation

The audit checks that the application's reconstruction agrees with the pinned Goodfire implementation on released data. This is an implementation check, not a claim that every new model, prompt or sparse scan will produce an accurate curve.

## Reproduce without private runs or a GPU

From the repository root:

```bash
./scripts/setup.sh cpu
.venv/bin/fork-microscope verify-upstream
.venv/bin/python scripts/verify_math.py
```

The last command writes `outputs/math-checks.json`, which is ignored by Git. It requires the initialized submodule and installed dependencies but does not download model weights or use private recordings.

The expected upstream commit is `d32fed8d4162a4888291c4b3a38b059727c85a41`. The audit requires an unmodified checkout and forces the reference ruptures segmentation path with `OTRECON_FORCE_RUPTURES=1`.

For a single released case with exact input/output receipts and loaded-source
checks, see [Reproduce one reconstruction comparison](RECONSTRUCTION-AUDIT.md).

## What is checked

- SHA-256 checks for all 203 released stores and recomputation of their recorded weighted reference curves.
- Four released-data cases: Llama and DeepSeek rows 12 and 39, each reduced to at most 24 checkpoints and 20 mixture draws per checkpoint. These are deliberately bounded integration cases, not the full paper evaluation grid.
- Exact agreement between the live reconstruction and the pristine baseline on raw frequencies, selected cross-validation parameters and best score, fitted probabilities, marginal band arrays and breakpoint intervals. The selected CV candidate count is also checked; the entire score map is not compared.
- Independent calculation of the Gaussian pooling formula with the symmetric Dirichlet prior, agreeing within floating-point tolerance.
- The multinomial segment cost checked against its analytic expression, plus a small exhaustive legal-partition comparison with PELT.

For category `k`, the within-segment calculation is:

```text
alpha[t,k] = 1/K + sum_j exp(-(t - t_j)^2 / (2*h^2)) * counts[j,k]
p[t,k] = alpha[t,k] / sum_k alpha[t,k]
```

Only observations in the selected segment contribute. The prior term matters; this is not simply a moving average across the entire trace. Breakpoints between sparse observations indicate intervals, not a uniquely identified intervening token.

## What the check does not establish

The released-data audit transports outcome draws through the live record format to isolate reconstruction. It does not validate an actual newly loaded model's logits, tokenizer, generated continuations or answer semantics. Those require model-specific checks and inspection of saved outputs.

The live collector uses a direct position mixture with `S` total observations per checkpoint. This differs from collecting `S` continuations for every retained branch. It estimates outcomes conditional on the retained branch distribution, not the unrestricted model distribution. The [method reference](REFERENCE.md) describes the collection design and recorded omitted mass.

An independent dense reference permits empirical error measurement, but is finite and noisy. Model-based uncertainty bands are not a guarantee of coverage. A cap-hit gate is a diagnostic rule, not a statistical accuracy certificate. Post-hoc refinement is exploratory; confirming a discovered effect needs an independent evaluation design.

No cost-saving, universal fork-detection or causal-mechanism claim follows from matching the estimator.

See [VALIDATION.md](../VALIDATION.md) for the test commands and validation boundary of this release candidate.
