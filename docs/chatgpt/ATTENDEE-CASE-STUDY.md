# Attendee choice: a reproducible first investigation

Which recommendation does a model make when expected attendance favors a gamble but reliability favors a guaranteed turnout? Both answers can be defensible, which makes this a useful behavioral inspection exercise.

## Published saved evidence

The repository's existing [portfolio bundle](../../public/fork-microscope/demo-portfolio.json) is deliberately public, previously recorded Qwen evidence. Importing or viewing it does not rerun the model.

| Identity | Recorded value |
| --- | --- |
| Model | `Qwen/Qwen2.5-1.5B-Instruct` |
| Model revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| Original file size | 238,407 bytes |
| Original file SHA-256 | `efd568b4c21ab4b37e2dde85299744f36e940a6b32770ef84e1600fe5308b61d` |
| Recorded continuations | 120: twenty at each of six checkpoints |

The recorded raw frequencies are:

| Original-response checkpoint | Plan A | Plan B | Other | Total |
| --- | ---: | ---: | ---: | ---: |
| 0 | 16 | 2 | 2 | 20 |
| 16 | 13 | 6 | 1 | 20 |
| 32 | 17 | 1 | 2 | 20 |
| 48 | 18 | 2 | 0 | 20 |
| 64 | 17 | 0 | 3 | 20 |
| 80 | 15 | 0 | 5 | 20 |

These counts describe sampled continuations conditioned on an output prefix and the experiment's retained next-token branches. They are neither an estimate over all possible generations nor proof of an internal decision token. Recorded answer-text matching and continuation caps affect classification. Read actual text before interpreting `Other` or a contrasting label. The saved run and its configuration are the authority for this historical experiment; the small config below is a separate first-run proposal.

Raw total variation between checkpoints 0 and 16 is 20%; between 16 and 32 it is 25%. The viewer uses a strict greater-than threshold, so an interval equal to a 20% threshold is excluded. A fitted curve is a descriptive estimate and can differ from raw checkpoint counts. No contrast or qualifying refinement is a valid outcome.

## Reproduce saved-data inspection

After [installing the private adapter](../../integrations/chatgpt-plugin/README.md), run from the repository root:

```bash
integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py init \
  --profile "$HOME/.local/share/fork-microscope-demo/profile.json"
integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py import \
  --profile "$HOME/.local/share/fork-microscope-demo/profile.json" \
  --bundle public/fork-microscope/demo-portfolio.json
integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py doctor \
  --profile "$HOME/.local/share/fork-microscope-demo/profile.json"
```

Connect your client to the generated private launcher. Ask it to list investigations, pin the returned file hash, report every page, select checkpoint 16, inspect contrasting continuations and retrieve the original bytes. Check the full-file SHA-256 after chunk reassembly. The public browser demo is also available through the root README.

## A new bounded experiment

The example [configuration](../../configs/attendee-chatgpt-small.json) asks:

> Choose between Plan A, which guarantees 70 attendees, and Plan B, which has a 50% chance of 40 attendees and a 50% chance of 110. Costs are equal. No risk preference or minimum attendance is specified. Explain your choice in at most 80 words. End with exactly CHOICE=A or CHOICE=B, and use these labels only on the final line.

It pins the same Qwen model revision, caps the base and each continuation at 256 tokens, scans at stride 16 over positions 0–112 with ten total draws per checkpoint, and permits at most 80 draws, 20,736 generated tokens and 600 seconds. A shorter base gives fewer checkpoints. Refinement and lens inspection are disabled.

Plan B has expected attendance of 75; Plan A guarantees 70. The question leaves risk preference unspecified. The scientific question is how sampled choices change as more of the original explanation is retained, rather than whether one recommendation is universally correct.

This JSON is a config, not an approved quote or proof of execution. A managed run separately requires fresh provider readiness, current rates, hardware/image bindings, storage review and explicit exact-plan approval. A planned twenty-minute compute session and a requested one-dollar allowance require validation by the deployment; they are not embedded provider billing guarantees. Save original evidence and verify deletion even if generation fails.
