# Jacobian lens readouts

[Documentation index](README.md)

The observatory can inspect original prompt/response tokens, a saved stochastic
continuation, two completed continuations with different recorded outcomes, or the control and edited prefixes from a saved edit investigation.
The output is a position-by-layer table of vocabulary readouts. It complements the
outcome-frequency graph; it does not replace continuation sampling or identify a
unique causal decision.

## Use the interface

1. In **Prompt**, connect your worker, load a compatible model, and generate a response.
   A saved scan can be explored without loading the model again.
2. In **Scan**, choose a dot or suggested region on the outcome map. The graph and
   its saved reconstruction remain available; coverage and uncertainty controls are
   under **Graph settings, coverage & limitations**.
3. Open **Compare paths** to read two completed continuations with distinct recorded
   labels at that checkpoint. Switch between final answers, generated text, and full
   responses. All draws, including capped and Other records, remain in the secondary
   reader. A checkpoint without an eligible pair does not invent a contrast.
4. **Look inside these two paths** carries the exact checkpoint and draw IDs to lens
   setup. Or choose **Inspect the original response instead**.
5. Attach the **same model revision** to your worker and choose its matching lens.
   Select a small response-token span (inclusive endpoints, maximum 64 positions).
   **Paths, layers & readout settings** exposes source selection, prompt coordinates,
   up to eight decoder layers, 2–30 ranked tokens per cell, and the optional
   [NNsight capture method](NNSIGHT-INTEGRATION.md). Standard capture remains the default. A checkpoint N
   preserves tokens before N. The default is one token before the pair's first
   unequal saved token through two tokens after it, clipped to both paths' lengths.
   This difference can occur later than N and is not a proven causal boundary.
   The preset selects up to five layers near 25%, 40%, 55%, 70% and 85% of depth
   (zero-based indices rounded down), excluding the final decoder block. This is
   an exploration preset, not an established workspace range for every model.
6. **Preview lens readout** checks exact tokens and compatibility and estimates
   the required model forwards and replayed prefix tokens. **Run** uses your
   attached model and downloads the selected lens if needed. It never provisions
   compute, loads a model, fits a lens, or generates a continuation automatically.
7. Open **Saved lens readouts**, click cells for rankings and baselines, and download
   their JSON before deleting an ephemeral worker. **Download JSON** prepares a
   file; click the **Save** link that appears and check your browser downloads.
   Saved readouts are labeled by
   source and token range, so original-response artifacts are not mistaken for pairs.

Use **Focus on first difference** to return to the compact pair view. Under
**Expand inspection or compare the baseline**, **Expand token window** adds up to
two positions per side (64 total maximum); **Add layer detail** fills gaps up to
eight layers. **Inspect prompt baseline** selects the final saved prompt token,
including the chat template. None of these controls starts compute. Preview again
before running. **Continue from these settings** restores a saved original/draw/pair
readout in its source pass; saved edit/control prefixes are selected in advanced settings.

Each row reads the state **after** its input token. The final-layer column predicts
the **next** token; intermediate J-lens words are vocabulary readouts, not tokens
actually emitted at each layer. Identical prefixes have the same deterministic
states. If the pair differs at response token 0, inspect the last prompt token for
the shared pre-divergence baseline. After divergence, equal numeric positions do
not guarantee equal meaning.

**How it works** opens a four-step walkthrough. Advanced text interventions and raw
activation capture live under **Test an edit or capture activation vectors** in step 4.

Outcome pairs require distinct, uniquely recorded draw IDs at the same checkpoint,
exact continuation token IDs, known completion, and different non-Other labels. The
worker reconstructs each prefix and records the draw index, outcome and completion
alongside its readout. They are two observational samples, not a controlled intervention.
Both use the same selected numeric range, bounded by the shorter trajectory; their
words after branching are not automatically aligned by meaning.

Different-length edits are compared at the same numeric positions, not automatically
aligned words. The edit/control source reads preserved prefixes; to inspect a freshly
sampled continuation use the recorded-draw source from a sampled run. Lens scores are
unnormalized readout logits and ranks, **not eventual-answer probabilities**. We display
unfiltered vocabulary ranks; punctuation, fragments and multilingual tokens are retained.

## Published profiles

| Profile | Model | Fitted file | Download size |
|---|---|---|---|
| `qwen35_4b` | `Qwen/Qwen3.5-4B` | `qwen3.5-4b/jlens/Salesforce-wikitext/Qwen3.5-4B_jacobian_lens_n1000.pt` | 406,332,644 bytes |
| `qwen36_27b` | `Qwen/Qwen3.6-27B` | `qwen3.6-27b/jlens/Salesforce-wikitext/Qwen3.6-27B_jacobian_lens_n1000.pt` | 3,303,032,772 bytes |

The two Qwen files are the selections in the [pinned upstream walkthrough](https://github.com/anthropics/jacobian-lens/blob/581d398613e5602a5af361e1c34d3a92ea82ba8e/walkthrough.ipynb).
We pin the Neuronpedia repository to `16a01f309fcec900fdcec3f4cd5b64f3d00e4d5a` and
check exact file sizes and SHA-256 digests in `lens_integration.py`. Generic filenames
and neighboring fit configs are not substituted for these specific artifacts.

**For Qwen, the exact fitted model revision is not published.** We record the loaded model's
revision separately and report the fitted revision as unknown. A named-model pairing,
matching dimensions, and final-layer parity are useful checks, but cannot establish
the missing fit provenance or semantic accuracy. Quantized or fine-tuned variants are
not accepted by these named profiles.

### Muse community profile

`muse_glimmer` selects [eyes-ml's community fit](https://huggingface.co/eyes-ml/Muse-Glimmer-30B_jacobian-lens/tree/71d8434fbd38c8b5d70e1ff1ff2095d5da926c34),
file `Muse-Glimmer-30B_jacobian_lens.pt` (4,518,854,369 bytes). It covers layers 0–50,
width 6656, targeting decoder block 51. Execution downloads and hash-verifies it.
The attached model must be `meta-models/Muse-Glimmer-30B` at
`a4e59da52a7bc87ae7251dd5545c0dd437c44b68`.

The publisher names `eyes-ml/Muse-Glimmer-30B` revision
`97e6fe0a8d8d221b100cd67f53fccf0744950abf` as the fit source. Its Hugging Face
weight shard and tokenizer content identities match our pinned Meta revision;
see [the provenance record](MUSE-LENS-PROVENANCE.json). This compares published
content identities, not a fresh local hash of the 60 GB model.

The publisher reports a text-only 900-prompt WikiText fit that did **not** meet its
convergence threshold. This is a usable candidate, not an independently validated
semantic interpretation. Our adapter preserves Muse's output multiplier before
its tanh soft cap and requires native final-logit parity on each new forward pass.
A full Muse run has produced 336 saved readout cells from two original-response
regions on an A100 80 GB, with zero maximum absolute error in each endpoint
final-logit parity check. This verifies execution, not semantic accuracy. The new
two-continuation handoff is tested on tiny CPU models; a paired full-Muse run has
not yet been collected. Controlled examples remain necessary to validate readout quality.

The published fits do not contain a matrix for the final decoder block. Select earlier
layers; the final-layer baseline is supplied automatically.

## Install and local lenses

`./scripts/setup.sh cpu` (or `cuda`) installs the pinned code dependency; the Docker
recipe includes it too. After pulling changes, stop the worker, refresh the editable
installation and lens dependency, then restart it. See the full
[upgrade procedure](GETTING-STARTED.md#updating-your-installation). Without changing other dependencies:

```bash
uv pip install --python .venv/bin/python --no-deps --editable .
uv pip install --python .venv/bin/python --no-deps -r requirements/lens.txt
```

This installs code only. To supply `/workspace/lenses/example.pt`, put the following
provenance manifest at `/workspace/lenses/example.pt.json` (replace every example value):

```json
{
  "model_id": "your/model",
  "resolved_revision": "the actual immutable model revision used for fitting",
  "lens_sha256": "the 64-character SHA-256 of example.pt",
  "site": "decoder_block_output_before_final_norm",
  "target_layer": 31
}
```

`target_layer` must be the last decoder block's zero-based index (31 for a 32-block
model). Upstream also supports intermediate-target fitting; those lenses cannot be
decoded directly through the final norm/head and are rejected here. The manifest is
a user declaration, not independent verification of the fit. It must match the loaded
model identity, including `local-sha256:...` identity and source fields for local snapshots.

Files must use the upstream `JacobianLens.save()` format, with `J`, `d_model`,
`n_prompts`, and layer matrices. Loading uses `weights_only=True`, CPU memory mapping,
and one selected matrix converted to float32 at a time. Do not use arbitrary pickle
files. The current format requires a filesystem-backed `.pt`; old non-zip serialization
is unsupported. Maximum local file size is 8 GiB.

## Exact-token implementation and validation

The convenience `lens.apply()` retokenizes and can truncate. The Hugging Face wrapper
constructor can change BOS and gradient settings. We bypass both: source token IDs
go directly to the existing model, bounded hooks copy selected decoder outputs, and
the upstream `JacobianLens.transport()` and unembedding implementation perform the
readout. The source site is zero-based block output, before final normalization.

Each uncached arm is cut at its last selected position and processed through the
full decoder with generation KV caching disabled, without gradients or interventions. Only selected rows are captured; the full layer×position×vocabulary
tensor is not saved. The final decoder output is independently unembedded and compared
with the model's native final logits at the last selected position. Failure stops the
readout. This checks plumbing, not the fitted lens's semantic quality. Raw scores,
three readouts, exact IDs/hashes, file identity, code pin, model revision, parity errors,
runtime versions and attention backend are saved with each artifact.

A separate, bounded **32 MiB CPU activation cache** belongs to the attached model
session. Keys include the complete causal token prefix and decoder layer; changed
model identity, parameter versions, dtype or device invalidate reuse. Length-dependent
rotary modes disable reuse. Repeating or narrowing a captured view can avoid a
model forward, while newly requested layers or positions can require another one.
Eviction and worker/model restarts lose this cache; saved readouts remain available.
This is not automatic resumption of an interrupted job or a persistent shared cache.

Lens-file checksum verification and vocabulary projections still run. The artifact's
`execution` object records `forward_passes`, `replay_tokens`, `reused_activation_rows`
and `captured_activation_rows`. Cached-only arms record parity as
`reused_activations_from_parity_checked_forward` with no new numerical error value;
only fresh forwards report a newly measured parity error. Reducing displayed cells
does not proportionally reduce the cost of a full decoder forward.

CPU tests use tiny randomly initialized models and synthetic lens matrices to check
identity and asymmetric transport, exact prompt/draw/edit IDs, causal position handling,
native final-logit parity, tokenizer/parameter stability, cleanup, provenance rejection,
and cancellation. No production weights or fitted Qwen/Muse lenses were downloaded
for those tests. The full Muse smoke test measured about 59.7 GB peak allocated GPU memory;
its two original-response readouts took about eight seconds after model loading.
These are one-run measurements, not performance guarantees for other configurations.

The worker preserves partial cells at layer boundaries and shares the existing single-job
lock, authentication and cancellation. Stopping during a Hub transfer takes effect when
that transfer returns; stop checks also run during file hashing and cell decoding.
Artifacts live under `investigations/`, separately from source run exports. Downloads are
the archival path; interrupted jobs are not automatically resumed.

## Research interpretation

The [paper](https://transformer-circuits.pub/2026/workspace/index.html) studies vocabulary
readouts of context-averaged internal effects. A token appearing in the lens may suggest
a hypothesis; it is not a complete hidden sentence, a semantic guarantee, or evidence
of intent on its own. Some relevant processing may be missed. Inspecting the original
prompt is supported because useful states may precede the generated response.

Use controlled text edits or separately validated internal interventions to test a
hypothesis, with neutral edits, independent samples and additional prompts. This
readout is observational. The separate [controlled activation-patch workflow](NNSIGHT-INTEGRATION.md)
tests a selected replacement alongside baseline and self-copy controls. Lens fitting,
trained probes and automatic concept labels are not implemented; neither readouts
nor patches by themselves establish causal novelty.
