# Example configurations

**Start with `cpu-custom.json` for a simple run, or `investigation-example.json` for a complete investigation.** The CPU smoke and Muse profiles are specialized examples, not additional onboarding steps.

These small profiles demonstrate the configuration format and pipeline wiring. Their sample counts, checkpoint coverage and token caps are deliberately small; they are not recommended research-quality settings.

| File | Purpose |
| --- | --- |
| `investigation-example.json` | Start here for the coordinated CLI/agent workflow; validate before running. |
| `cpu-smoke.json` | A small CPU model and the legacy four-choice answer format. |
| `cpu-custom.json` | The same CPU model with a free-form prompt and explicit answer-text tracking. |
| `muse-smoke.json` | A pinned Muse-Glimmer model on CUDA, illustrating the model-specific loading path. Requires suitable hardware and model access. |

From the repository root, after installing the appropriate CPU or CUDA environment:

```bash
.venv/bin/fork-microscope run configs/cpu-custom.json
```

This command loads weights and runs inference. Model revisions are pinned in each file. Change the model only after checking architecture/tokenizer support and available memory; see [Getting started](../docs/GETTING-STARTED.md).

Use the dashboard to generate a base and choose a larger checkpoint range, sample count and continuation cap interactively. Inspect completion rates, answer matches and retained branch mass before trusting a fitted graph. The [method reference](../docs/REFERENCE.md) explains every sampling dimension.

Synthetic records used by automated tests live in `tests/fixtures/`; they are not model-run presets. Personal prompts and historical run configurations are not distributed with the examples.
