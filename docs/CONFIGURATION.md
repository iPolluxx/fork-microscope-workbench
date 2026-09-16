# Investigation configuration reference

Read this before proposing or running an automated investigation. It describes
`fork-microscope investigation start`, not every lower-level manual operation.

The interactive Setup/API additionally supports structured conversation history,
final-marker rules, saved response selection and bounded outcome search. Those are
documented in [Investigation workflow](INVESTIGATION-WORKFLOW.md),
not accepted as extra fields by this automatic CLI configuration.

## Discover settings without compute

```bash
fork-microscope investigation settings
fork-microscope investigation validate configs/investigation-example.json
```

Both commands work offline and do not load a model, contact a worker, or start
compute. `settings` returns structured JSON an agent can read. `validate` checks
syntax; success does not establish that a model fits in memory, a lens matches,
or a prompt completes within its cap.

All six top-level keys and the fields below are required, except
`lens.inspection_backend`, which defaults to `native`. Set `lens` to `null` to disable lens inspection and
`refinement.max_rounds` to `0` to disable refinement. The checked-in example is
one proposed configuration, not an optimal or universally safe preset.

## model

| Field | Accepted values | Meaning and tradeoff |
|---|---|---|
| `model.model_id` | nonempty string | Hub repository or worker-local native model directory. A locator, not a cloud API endpoint. |
| `model.revision` | nonempty string; main/master/latest rejected | Use an immutable Hub commit or the expected local content identity. Other moving branch names are not detected by syntax validation; the agent must pin them. |
| `model.device` | auto  /  cpu  /  cuda | Device for loading. auto chooses an available device; it does not guarantee enough memory. cuda requires a CUDA worker. |
| `model.batch_size` | integer 1–128 | Number of continuations processed concurrently. Controls memory/throughput, not total samples or statistical confidence. |

## base

| Field | Accepted values | Meaning and tradeoff |
|---|---|---|
| `base.prompt` | 1–16000 characters after trimming | The user question/context supplied to the model. Actual token count depends on the tokenizer and chat template. |
| `base.answers` | 1–32 nonempty single-line strings, ≤200 characters each; unique ignoring case/whitespace; Other reserved | Outcome texts to recognize in completed replies. Multiple matches, unmatched or incomplete replies are Other. Matching normalizes case/whitespace and uses word edges. It is not correctness grading. |
| `base.mode` | chat  /  base | chat applies the model chat template; base tokenizes the supplied text with special tokens, without a chat template. Inspect saved prompt IDs for exact conditioning. |
| `base.max_tokens` | integer 8–4096 | Maximum newly generated tokens for the original response. A capped original response stops scanning; it is not silently treated as complete. |
| `base.seed` | integer 0–2147483647 | Seed recorded for original-response generation, which is greedy in this workflow; changing it is not a way to sample alternative base responses. Reproducibility also depends on model, runtime, device and settings. |

## scan

| Field | Accepted values | Meaning and tradeoff |
|---|---|---|
| `scan.start` | integer 0–4095 | First checkpoint on the GENERATED RESPONSE, not the user prompt. At checkpoint t, preserve the first t response tokens and branch at response index t. |
| `scan.end` | null or integer start+1–4095 | Upper bound clamped to the last available response index. null means through the response. Initial scan includes only regular-grid positions; an off-grid endpoint is not appended. |
| `scan.stride` | integer 1–128 | Distance between checkpoints. Smaller spacing gives finer coverage at greater cost. At least two actual checkpoints are required. |
| `scan.samples` | integer 5–512 | Total fresh continuation draws per checkpoint, allocated across retained branch tokens; not samples per branch. More draws reduce sampling uncertainty but do not improve position spacing. |
| `scan.cont_max` | integer 1–4096 | Maximum generated continuation tokens after the forced branch token per draw. Longer caps cost more but reduce truncation. This is not number of layers or checkpoints. |
| `scan.temperature` | finite number 0.05–2 | Temperature for continuation generation after the selected branch. Branch selection itself uses temperature-1 next-token probabilities. |
| `scan.top_k` | integer 1–50 | Maximum next-token branch candidates considered at each checkpoint before applying the probability threshold. This is NOT J-lens display top-k or continuation-generation top-k. |
| `scan.threshold` | finite number 0–1 | Minimum next-token probability for a candidate branch. Retained candidates are renormalized; excluded probability mass is not measured. A threshold leaving no candidates prevents collection. |
| `scan.seed` | integer 0–2147483647 | Seed for branch allocation and continuation sampling. Source records preserve derived seeds. Separate from base.seed. |

## refinement

| Field | Accepted values | Meaning and tradeoff |
|---|---|---|
| `refinement.max_rounds` | integer 0–8 | Maximum follow-up runs after the initial scan. 0 disables refinement. It may stop earlier if no eligible gap remains or the budget is insufficient. |
| `refinement.stride` | integer 1–128 | Spacing within the selected interval. Both endpoints are included, even if off-grid. Only gaps wider than this stride are eligible. |
| `refinement.samples` | integer 5–512 | Fresh draws at each refinement checkpoint, including repeated endpoints. These are additional observations; earlier counts are not reused as new samples. |
| `refinement.min_tvd` | finite number 0–1 | Minimum adjacent raw-distribution total variation distance to consider. This is a descriptive selection threshold, NOT a p-value, confidence level, or guarantee of a fork. |

## lens

| Field | Accepted values | Meaning and tradeoff |
|---|---|---|
| `lens.profile` | string matching a compatible named worker profile | Query GET /api/live/lens-options for profile IDs, model compatibility, installation and layer count. Profiles are model-specific; arbitrary filenames and local profiles are not accepted by this automatic workflow. |
| `lens.layers` | 1–16 unique integers, each 0–999 | Zero-based decoder layer indices. The loaded model and fitted lens impose tighter bounds; the final decoder block is not a fitted source layer. More layers increase capture/projection work. |
| `lens.before` | integer 0–16 | Inspection positions before the selected pair’s FIRST DIFFERING TOKEN. Clipped at zero. Not an offset from the sampling checkpoint. |
| `lens.after` | integer 0–16 | Inspection positions after the first differing token. Clipped to the shorter path. The divergence position itself is included. |
| `lens.top_k` | integer 2–30 | Number of ranked vocabulary tokens displayed per readout. Changes output detail; does not sample more continuations or add layers. |
| `lens.inspection_backend` | optional `native` or `nnsight`; default `native` | Capture implementation for the same read-only J-lens inspection. `nnsight` requires the pinned optional worker package and fails before model loading or sampling if unavailable. It does not connect NDIF, change the reconstruction method, or establish better readout quality or speed. Query `/api/live/lens-options` for availability. |

## limits

| Field | Accepted values | Meaning and tradeoff |
|---|---|---|
| `limits.max_seconds` | integer 1–1000000000 | Time allowance including loading, generation and lens replay. Stopping is cooperative; an in-flight operation may overrun. Restart downtime can consume allowance. NOT a provider billing cap. |
| `limits.max_samples` | integer 1–1000000000 | Maximum reserved continuation draws across scan and refinement. Original-response generation and lens replay are not continuation draws. |
| `limits.max_generated_tokens` | integer 1–1000000000 | Maximum reserved generated-token allowance: original cap plus each phase’s draws × continuation cap. Reservations are not refunded for short output or interrupted attempts. Does not count replayed/prompt tokens, which still cost compute. |

## What is fixed rather than configurable here

- Original generation is greedy; continuation generation is stochastic.
- Outcome matching is `answer_text_anywhere_v1`. Inspect completed replies to check
  that the matching rule captures the intended outcomes. The generic parser excludes
  explicit `<think>` prefixes; Muse reads its completed user-facing channel.
- Reconstruction uses the existing cross-validated Goodfire implementation.
  Dense reference collection is off. `reference_samples=5` is an internal unused
  placeholder while dense collection is off, not an extra sampling charge.
- Branch probabilities use temperature 1. Continuation generation has top-p 1 and
  top-k 0; `scan.temperature` controls its temperature.
- Each refinement chooses one eligible interval in the latest run. The largest raw
  adjacent TVD above the threshold wins, with ties broken by earlier position.
  An eligible gap must be wider than the refinement stride. The fit must be complete.
  Refinement inherits continuation and branch settings; seeds are derived from the
  previous pass seed and round number and are recorded. Repeated endpoints use
  fresh draws. This is not adaptive confidence-interval stopping.
- Lens selection searches newest runs first, chooses the most balanced eligible
  checkpoint in that run, then the first contrasting draws in draw order. It collects
  at most one paired readout per workflow. No eligible pair means an explicit skip.
- With a fixed refinement stride, subsequent rounds may stop immediately after one
  refinement has filled all eligible gaps. `max_rounds` is a ceiling, not a promise.

## Three settings that are easy to confuse

- **Spacing** (`stride`): how closely you cover positions in the original output.
- **Samples** (`samples`): how many independent continuation draws estimate each
  checkpoint's outcome distribution.
- **Continuation length** (`cont_max`): how far each draw may generate after its
  forced branch token. A short cap may prevent a final answer from appearing.

`model.batch_size` changes execution concurrency. `scan.top_k` limits branch
candidates. `lens.top_k` changes the number of displayed vocabulary readouts.
They are separate controls with separate effects.

For a checkpoint at `t=132`, the first 132 original response tokens are preserved,
a branch token is forced at index 132, and subsequent tokens are newly generated.
If two selected paths first differ at 153, `lens.before=1` and `lens.after=5`
inspect indices 152–158, clipped to valid positions. They do not inspect 131–137.
Readouts are states **after** consuming a token; they are not the hidden sentence
that caused that token. The final-layer baseline predicts the next token.

## Budget arithmetic

For an initial scan, let `last` be the final available original-response index and
`end` be the smaller of `scan.end` (or `last` when null) and `last`.
`N = floor((end - start) / stride) + 1` when end ≥ start. At least two checkpoints
are required. The phase reserves `N × samples` draws and
`N × samples × cont_max` generated tokens before it begins. Original generation
also reserves `base.max_tokens`. Refinement additionally includes an off-grid
final endpoint, if necessary. Prompt/prefix replay and lens computation cost time
but do not spend the generated-token allowance.

Example: checkpoints 0, 16, 32, 48, 64 with 20 samples reserve 100 draws. At a
512-token continuation cap this reserves 51,200 generated tokens, plus the original
response cap. This is a worst-case reservation, not measured usage or a dollar quote.
An interval 0–10 refined at stride 4 samples 0, 4, 8, 10, including the endpoint.

Before suggesting settings, tell the user what question they answer, what coverage
may be missed, how completion will be checked, and the conservative reservation.
Do not claim universal “sweet spots” or validated savings for the adaptive policy.

## Manual controls and runtime discovery

The automatic workflow intentionally exposes a bounded subset. For other tasks:

| Need | Where to look |
|---|---|
| Multiple explicit passes, offsets, irregular positions, dense reference, fixed tuning | [Technical reference](REFERENCE.md), `sampling.pass_plan`, manual run/estimate APIs |
| Exact saved-trace refinement | [Workflow guide](INVESTIGATION-WORKFLOW.md), `refinement.build_plan` |
| Manual prompt/response lens coordinates, original or individual draw inspection, local lens files | [Jacobian lens guide](JACOBIAN-LENS.md), `lens_integration.build_plan` |
| Text edits and activation capture | [Investigation tools](INTERPRETING-RESULTS.md), `investigation.build_plan` |
| Actual loaded model, device, context limits and active work | `GET /api/live/status` |
| Installed lens support, named profile IDs, model pairing and layer count | `GET /api/live/lens-options` |
| Authentication, commands, job states, cancellation/resume, bundle format | [Workflow guide](INVESTIGATION-WORKFLOW.md) |

Do not invent profile IDs or copy Muse's layer indices to another model. Published
profiles have model-specific dimensions and fit provenance; adapter parity does not
validate a lens's scientific quality. A local lens path requires the manual API;
it cannot be passed as an extra field in the automatic config.

The configuration validator and runtime checks are authoritative if documentation
and a worker version differ. Do not silently remove budget or compatibility checks
to get a request accepted. There is no provider deployment or shutdown setting in
this workflow.
