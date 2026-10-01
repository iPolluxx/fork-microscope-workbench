# First-user test — 15 minutes, no coaching

Use the locally verified release candidate first; record its Git revision and any
uncommitted changes. The hosted site does not change until deployment. This
protocol is prepared, not evidence that people have already completed it.

## Recruit 2–3 people

Ask people interested in open-model research, ideally with different levels of
interpretability experience. Obtain permission before recording their screen or
voice. Do not record credentials. The invitation below is a draft, not a sent message.

> I built Fork Microscope to explore how sampled model outcomes change along a
> response. Could you try a 15-minute saved-data investigation and tell me where
> you get stuck? No GPU or payment is needed. I’m testing whether the interface
> explains itself, so I’ll avoid guiding you while you use it.

## Task card — give only this to the participant

1. Open Fork Microscope and find an example you can browse without compute.
2. Explain the question that example investigates.
3. Find a checkpoint with two different answers. Read one continuation of each.
4. Explain what a graph point measures and one thing the graph cannot establish.
5. Export the investigation and locate the file. Open a fresh browser profile or
   another browser, import it and find the same checkpoint.
6. Show where you would connect your own compute and what you would need to run
   a new prompt. Do not rent hardware or enter credentials for this task.

## Observer sheet

Participant alias: ___  Date: ___  Build: ___  Prior experience: ___

| Task | Completed without help? | Time | Exact hesitation / misunderstanding | Intervention needed |
| --- | --- | --- | --- | --- |
| Find demo | | | | |
| Identify question | | | | |
| Find contrasting continuations | | | | |
| Interpret point and limitation | | | | |
| Export and reimport | | | | |
| Identify compute setup | | | | |

Let them think aloud. Record what happens rather than explaining the interface.
If they are completely blocked, note the failure before helping them continue.
Ask afterward: “What did you expect here?” and “Would this help with a question
you actually investigate?” Do not lead them toward a positive answer.

## Separate compute acceptance

With a willing tester and their approved budget: install on their RunPod VM, pair,
load a supported model, run a short prompt, inspect, export, disconnect/reconnect,
then stop or delete the VM themselves after verifying the export. Record model,
revision, hardware, cost and each failure. A local mock is not a RunPod acceptance
result. Do not assume the account balance is a spending authorization.

## Decide what to fix

Fix blocking failures and repeated misunderstandings first. Record each change
beside the observation that motivated it. Three sessions cannot establish broad
usability, but they can expose obvious blockers. Once the same core task works,
publish the bounded beta and stop cosmetic rebuilding without user evidence.
