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
