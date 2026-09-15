# Third-party provenance

Fork Microscope’s own code is covered by the root MIT LICENSE. That license does not relicense the materials below.

## Forking Fast

Source: https://github.com/ericb-goodfire/forking-fast

Pinned Git revision: `d32fed8d4162a4888291c4b3a38b059727c85a41`.

Fetched as a Git submodule, not copied into the parent repository's source tree. Its source, datasets and original notices remain upstream. No LICENSE file was present at this revision; this project does not grant a license to that material or treat public availability as permission to relicense it. Confirm upstream permissions before a public redistribution or release with bundled upstream code/data.

## Jacobian lens

Source: https://github.com/anthropics/jacobian-lens

The setup script and Docker recipe install Anthropic's Apache-2.0 reference implementation
at Git commit `581d398613e5602a5af361e1c34d3a92ea82ba8e`. Its original license and notices
remain in the dependency. Fork Microscope reuses its transport and unembedding math;
the exact-token adapter and interface are this project's code. The upstream repository
describes itself as unmaintained reference code.

Method: [Verbalizable Representations Form a Global Workspace in Language Models](https://transformer-circuits.pub/2026/workspace/index.html).
Curated fitted lenses are fetched on explicit execution from
[`neuronpedia/jacobian-lens`](https://huggingface.co/neuronpedia/jacobian-lens), at revision
`16a01f309fcec900fdcec3f4cd5b64f3d00e4d5a`, with SHA-256 verification. No fitted lenses or
model weights are bundled. Artifact provenance and limitations are in [the lens guide](docs/JACOBIAN-LENS.md).

## Plotly.js

`public/fork-microscope/plotly.min.js` is the Plotly.js v4.0.0 distribution from the installed Plotly package. Its copyright/license header is retained. Plotly.js is MIT licensed; the full license is in `licenses/plotly-MIT.txt`.

## Model weights

No model weights are included. Models are downloaded from Hugging Face or read from a user's local directory. Their individual licenses and access conditions apply independently. Model source revisions are pinned in the example configurations and recorded in run metadata.

## Muse community Jacobian lens

Optional Apache-2.0 artifact from [eyes-ml](https://huggingface.co/eyes-ml/Muse-Glimmer-30B_jacobian-lens),
pinned to `71d8434fbd38c8b5d70e1ff1ff2095d5da926c34`. Downloaded separately on execution,
never bundled. See the lens guide for provenance and validation limitations.
