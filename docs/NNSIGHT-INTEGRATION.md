# From a fork to an internal-state experiment

[Documentation index](README.md) · [Lens guide](JACOBIAN-LENS.md)

Start with **Scan → Compare paths → Look inside**. Read the actual continuations
and check their outcome labels first. A graph change selects a region worth testing;
it does not identify the cause of an answer. New inspection or intervention uses
the same model revision on your connected compute. Reading saved evidence only
needs a running CPU viewer worker, with no model loaded or GPU.

## Two different operations

| Operation | What it does | What the result means |
| --- | --- | --- |
| Jacobian lens readout | Replays saved tokens, captures selected internal states, and applies a matching fitted lens. | Vocabulary scores and ranks at particular layers and positions. No activation is changed. |
| Controlled activation patch | Replaces one recipient internal state with a donor state during a new generation, alongside controls. | Whether that particular replacement changes the sampled outcome frequencies. |

A J-lens is a fitted transformation, while NNsight is a way of accessing model
activations. Selecting NNsight does not improve a poorly fitted lens or make a
vocabulary word proof of hidden intent. The patch experiment uses the native
worker's controlled hooks; it does not require NNsight or a fitted J-lens.

## Choose a readout capture method

1. Open a saved run in **Explore**, select a checkpoint, then **Compare paths**.
2. Select two completed paths with different recorded outcomes and choose
   **Look inside these two paths**. If none exist, inspect the original response;
   absence of a contrast is a valid result.
3. Expand **Configure a new readout**. Choose the matching lens and a small token
   window. Read the text at those positions before expanding the window.
4. Under **Paths, layers & readout settings**, leave **Capture method** at
   **Standard capture**, or select **NNsight capture (experimental)** if the worker
   reports it available. Its status explains missing dependencies or compatibility
   problems. This is optional; saved artifacts remain readable either way.
5. **Preview lens readout** checks the request. **Run readout on connected compute**
   performs it. Changing settings requires a new preview. Stop preserves the
   partial data already written. Saved provenance records the capture backend.

Both methods replay exact saved token IDs through the already loaded model and
retain the existing J-lens transport, model-specific unembedding and final-layer
parity check. An explicitly requested NNsight capture fails clearly when unavailable;
it does not silently become Standard capture. This release does not capture readouts
during the original continuation generation.

The optional dependency is pinned in [requirements/nnsight.txt](../requirements/nnsight.txt).
Install it **in the compute worker environment**, from the repository root:

```bash
uv pip install --python .venv/bin/python -r requirements/nnsight.txt
```

Restart the worker using your normal [connection setup](GETTING-STARTED.md), attach
the source model, and reopen readout configuration to refresh its capabilities.
The package pin has been exercised with tiny randomly initialized CPU models;
that is not a certification of every model or a new Muse GPU benchmark. Keep the
standard capture method available while checking a new architecture.

## Test an activation patch

In **Look inside**, expand **Test an activation patch · advanced**. This is a
separate, optional experiment after reading the traces, not an automatic extra
step added to a scan. Select a donor continuation, a recipient continuation,
their token positions and up to four decoder layers (patched together). Review both texts in the preview:
equal numeric positions after a divergence need not have matching meaning.

Token positions are zero-based response indices, included in their respective
prefixes; fresh generation starts at the next token. The patch uses native
PyTorch hooks, inspired by the general activation-patching method. It is applied
once at the final selected recipient prefix state during prefix processing.
The model then generates fresh continuations. Compare the unpatched recipient,
the self-patch control, and the donor patch using their raw counts and completed
text. Samples and generation caps bound the job; a preview does not launch it.
Save the resulting artifact with the investigation before deleting temporary
compute. Its selection and settings are evidence, so keep them with the outcome
counts when sharing.

Two continuations with identical prefixes have the same deterministic internal
states before they diverge. Different eventual answers do not supply different
pre-divergence donor states. Choose genuinely different prefixes and an explicit
alignment. A patch tests that replacement in that recipient context; it does not
prove the replacement explains the original graph change. Post-selected positions,
small samples, capped replies and imperfect outcome matching limit conclusions.
Patch outcome frequencies are separate measurements from the Goodfire graph.

## For implementation agents

Discover capabilities through authenticated `GET /api/live/lens-options` and
`GET /api/live/patch-options`. Lens requests accept optional
`inspection_backend: "native" | "nnsight"` (`native` is the default). Use
`POST /api/live/lens-plan` before `POST /api/live/lens`.
Controlled patches use `POST /api/live/patch-plan` followed by
`POST /api/live/patch`; use the GUI preview as a concrete request example and the
[activation patch implementation](../src/fork_microscope/activation_patching.py) for accepted fields
and bounds. Poll the existing status endpoint and cancel with the job ID through
`POST /api/live/stop`. Saved readouts and patches are served by the existing
`investigations` / `investigation?id=…` endpoints.

Keep credentials in the established worker connection, never inside a request
artifact or exported bundle. Preview, inspect warnings, obtain the user's compute
scope, then run. Patching is not silently added to automated refinement. Follow the
[agent operating guide](agents/OPERATING-GUIDE.md) for budgets and artifact handoff.

## Related backends: possibilities, not connection choices

**NDIF:** NNsight supports remote activation work on NDIF's available models using
its own credentials and protocol. That protocol is not a Fork Microscope worker
address, and this integration does not add NDIF hosting or promise Muse availability.
A future adapter must preserve exact tokens, model revisions, authorization,
cancellation and artifact identity. [NNsight remote execution](https://nnsight.net/features/15_remote_execution/)

**vLLM:** NNsight documents selected activation capture and J-lens readouts during
generation. The example uses already fitted matrices; inference-only vLLM cannot
fit them with backward passes. A future sampling adapter needs numerical and
throughput checks before claiming faster or cheaper Fork scans.
[NNsight J-lens example](https://nnsight.net/vllm/examples/jacobian-lens/)

Its execution constraints differ from the Transformers backend: one prompt per
invoke, no cross-invocation barrier, and saved tensors need cloning because engine
buffers can be reused. Version and architecture checks matter.
[NNsight vLLM capabilities](https://nnsight.net/vllm/capabilities/)

For the intervention methodology, see NNsight's
[activation patching tutorial](https://nnsight.net/tutorials/tutorials/causal_mediation_analysis/activation_patching/).
The workbench exposes a bounded subset of that general method, with an explicit
preview and controls, rather than an arbitrary code execution interface.
