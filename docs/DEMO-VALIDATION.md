# Portfolio demonstration — prospective protocol, 2026-10-01

## Question and purpose
Does the attendance tradeoff yield different sampled choices as more of one original
response is fixed? This is a reproducible workflow demonstration, not a test of
rationality, internal intent, or generalized interpretability accuracy.

Use [the pinned configuration](../configs/portfolio-demonstration.json).
Qwen2.5-1.5B-Instruct is chosen for an inexpensive clean-worker acceptance test.
It is a different model from the existing Muse showcase; results cannot be pooled.

## Before looking at results
Generate one original response (seed 23), then sample 20 continuations per checkpoint
every 16 tokens across that response, using the existing Forking Fast reconstruction.
Keep incomplete/no-match/multiple-match replies as Other. Matching is literal text
and can misclassify mentions. Manually read any displayed contrasting examples.

Allow at most one refinement at spacing 4, 20 fresh draws per position, selected by
the existing largest eligible adjacent raw TVD policy with threshold 0.25.
This is post-selection exploration, not statistical significance.
No qualifying region or no contrasting choices is a valid result. Do not search
additional prompts/seeds to manufacture a visually attractive fork.

No J-lens is run: this test has no validated fitted lens for this model.

## Budget and operations
User-authorized RunPod ceiling: $5 total. Plan one RTX 4090, quoted $0.74/hour
before launch, ephemeral disk only. Independently delete after 60 minutes;
target completion well before that. Reserve remaining budget rather than
spending it. Record actual pod rate, duration, disk, and provider billing when available.
The configuration's 30-minute runtime is cooperative, not a VM billing cap.

Install the committed public checkout with pinned upstream and dependencies.
Exercise authenticated worker status, model loading, coordinator start/status,
safe replay of the same start request, export, and reconnect/read.
Export before deleting the pod and verify the pod is absent afterward.
If setup is broken, preserve diagnostics and stop; do not relax provenance.

## Evidence and reporting
Save exact config, code revision, model revision, hardware, original token IDs,
per-continuation outputs, outcome counts, reconstruction, refinement rationale,
and portable bundle. Validate import offline and compare exported counts.
Report capped replies, Other rates, missed controls, setup issues, and flat results.
Graphs describe outcome frequencies under this sampler; they do not locate a
proven causal decision or establish a cost advantage.

API/CLI acceptance does not substitute for a real uncoached browser usability test.
Publish only sanitized artifacts; exclude credentials and private connection data.

## Observed result — completed 2026-10-01
[Open this saved investigation](https://fork-microscope-wzyjs4vwsq-uc.a.run.app/observatory.html?demo=portfolio&run=35c70e0520c447e38f2c4468bde23393&pass=scan&checkpoint=16#scan).
No compute or credentials are needed. The original Muse demo remains available separately.

Worker code: `8100891`; local CLI transport fix: `2f04587`.
Python 3.13.12, PyTorch 2.11.0+cu128, Transformers 5.16.1, RTX 4090.
One 85-token original response selected A. The scan generated **120 continuations,
5,855 continuation tokens**, in **40.7 seconds** (sampling only, excluding setup,
model download/loading and reconstruction).

| Checkpoint | A | B | Other | Draws |
|---|---:|---:|---:|---:|
| 0 | 16 | 2 | 2 | 20 |
| 16 | 13 | 6 | 1 | 20 |
| 32 | 17 | 1 | 2 | 20 |
| 48 | 18 | 2 | 0 | 20 |
| 64 | 17 | 0 | 3 | 20 |
| 80 | 15 | 0 | 5 | 20 |

There are contrasting choices to inspect at checkpoint 16. A displayed A continuation
prioritizes guaranteed attendance; a B continuation accepts the uncertain option.
Their explanations are generated text, not verified accounts of internal mechanisms.

**13/120 draws were unresolved**, including one continuation at its length cap.
Do not drop Other or treat the remaining labels as an unbiased accuracy measure.
Candidate filtering retained between about 61% and 100% of next-token probability
mass across these positions. Frequencies refer to this configured sampling procedure.

The fitted reconstruction marks a candidate boundary between 32 and 48.
The separate adaptive selector found no adjacent raw TVD strictly above 0.25,
so it correctly skipped refinement. Those two policies answer different questions;
the plotted boundary is not a confirmed causal decision or a significant effect.
No threshold was lowered and no additional prompt/seed was tried.

### Acceptance and costs
Verified on real remote compute: unauthorized requests rejected; one-time pairing
code invalid after use; allowed dashboard CORS origin; idempotent duplicate start;
completed workflow; portable bundle validation and offline round trip; a fresh
pairing retrieved the same bundle checksum. Full raw files were also backed up
locally and their archive checksum matched the remote copy.

The fresh setup exposed and fixed Python 3.13.8 / pinned-PyTorch incompatibility
and RunPod proxy rejection of the CLI's default Python user-agent.
The API/CLI path was exercised; rendered browser interaction remains unverified.

Pod deleted after approximately nine minutes. A subsequent inventory returned zero
pods. GPU-time estimate **$0.11**, at $0.74/hour, plus minor disk charges;
provider billing had not yet posted. The approved ceiling was $5, not a spending
target. No persistent volume was created. No J-lens or new Muse execution occurred.
