# Fork Microscope — portfolio checklist

Updated 2026-10-01. Checked boxes below mean implemented or backed by existing
saved evidence, **not** that a human usability study has passed. These changes
are local and are not yet deployed to the public website.

## Front door / UX

- [x] Prompt and answers → **Run investigation**, backed by the existing coordinator.
- [x] Show the loaded model and immutable revision before generation.
- [x] Sampling, original-response and resource controls under Advanced.
- [x] Plain-language labels with technical terms where useful.
- [x] Sequential prompt → original response → scan → inspect progress; submitted
  prompt collapses into a summary while work runs.
- [ ] Visually check desktop/mobile layout in an available browser. Computer-use
  browser inventory was empty; DOM-double tests do not verify rendering.

## Compute

- [x] Persistent **Compute** control on each page, with model loading in Setup.
- [x] Disconnected / Connecting / Ready / Running, plus an honest connected-but-no-model state.
- [x] Optional trusted-browser URL/token persistence, session-only default,
  same-origin tab handoff, and disconnect clearing stored credentials.
- [x] Provider-neutral worker transport and model control.
- [x] Sampling/inspection use the connected worker rather than provider APIs.
- [ ] Fresh RunPod installation, pairing, reconnection and run acceptance on this
  version. Requires an explicit current budget; none spent in this pass.

## Investigation flow

- [x] Enter prompt and tracked answers.
- [x] Generate original response through a background worker job.
- [x] Scan the exact saved tokens with configured checkpoints and total samples.
- [x] Display the outcome timeline and candidate change intervals.
- [x] Select a checkpoint and inspect competing recorded continuations.
- [x] Refine manually; optional bounded automatic refinement is off by default.
- [x] Raw frequencies, counts, reconstruction limits and unresolved outcomes remain visible.
- [x] Explain each view's question; do not call a candidate interval a causal token.
- [x] Cancellation, durable status, export and idempotent recovery of lost start replies.

## Make the tool interpretable

- [x] One-sentence purpose on the first screen.
- [x] Settings explain checkpoints, total S and continuation length separately.
- [x] Timeline asks how often each outcome appeared from a saved prefix.
- [x] Observation is distinguished from causal interpretation.
- [x] Document outcome matching, truncation, exploratory selection and J-lens limitations.
- [x] No invented runtime, dollar estimate, confidence guarantee or guaranteed fork.

## One complete demonstration

- [x] Attendance trade-off question with no prescribed risk preference.
- [x] Existing real-model investigation: three linked scans and saved evidence.
- [x] Browser-only demo with recorded continuations and saved lens readouts.
- [x] Shareable [figure](images/attendance-evidence.png) derived from saved counts.
- [x] [Write-up](DEMO-ATTENDANCE.md): question, settings, observations, justified
  conclusion, unsupported conclusions and a short recording outline.
- [ ] Fresh end-to-end execution using this new front door. Existing evidence
  validates the demo, not today's new compute UX.
- [ ] Optional screen recording. A script and evidence figure are not a finished video.

## README / portfolio

- [x] Explain the problem and contribution before implementation details.
- [x] Demo near the top, with a screenshot and real-data figure.
- [x] Quick start and updated first-session guide.
- [x] Goodfire attribution and scientific limitations.
- [x] Public research beta label; no unsupported production/security claim.

## Actual-user test

- [x] Prepared [task card, observation sheet and invitation draft](USER-TEST.md).
- [ ] Give the candidate to 2–3 interested people. No messages have been sent.
- [ ] Observe without explaining the interface.
- [ ] Record hesitation, misunderstanding and abandonment.
- [ ] Fix repeated blockers, then retest the affected task.
- [ ] Stop polishing aspects without observed user problems.

## Then move forward

- [ ] Decide the v0.1 portfolio-ready release after outstanding acceptance work.
- [x] Keep the current scope bounded; no new interpreter or provider platform added.
- [ ] Move development attention to the next instrument once this handoff is done.
- [x] Keep reusable worker/model/run/bundle infrastructure separate from this UI;
  do not duplicate the generation loop for the quick-start screen.

## Verification record

- Python suite: **310 passed, 3 skipped** (optional NNsight runtime unavailable).
- JavaScript suite: **56 passed**, including simulated-worker start, lost-reply
  recovery without duplicate execution, cancellation and export.
- Existing saved-evidence tests validate import/export, parent links, raw counts,
  saved lens token provenance and malformed-bundle rejection.
- Static build and Python-worker asset routing pass; raw compute transport stays
  separate from offline evidence browsing.
- Figure counts are asserted against recorded observations; its rendered PNG was
  visually inspected. It reuses saved fits, not a newly estimated curve.
- These checks are CPU/saved/simulated-data checks. They are not a new Muse run,
  a live RunPod acceptance test, or human usability evidence.

No paid compute, public push or deployment occurred in this pass. Preserve this
record when marking the final remaining boxes; do not silently turn planned
acceptance into a completed claim.
