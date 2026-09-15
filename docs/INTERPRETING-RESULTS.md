# Investigating an unwanted outcome

[Documentation index](README.md)

The outcome map localizes **candidate changes in sampled behavior** along one fixed
response. It does not identify all decisions, establish reasoning faithfulness, or
prove that a highlighted token caused the original answer. An unwanted outcome also
needs a clear readout: a phrase match is not an alignment evaluation by itself.

## From a candidate region to an experiment

1. Open a saved run in **Explore evidence** and select a checkpoint near a change.
2. Open **Compare paths** to compare completed draws with different outcome labels.
   The original prefix, complete suffix and all draws are under **Shared prefix, all
   continuations & text edits**.
   Filter by outcome and completion. The generated reasoning text may omit causes
   or rationalize an answer; it is not a recording of all internal computation.
3. Use **Sample more closely** to collect additional checkpoints in the interval.
4. Open **Look inside → Test an edit or capture activation vectors**. Attach the exact source model
   revision on your worker, choose an instrument, and preview before running.

For a controlled internal-state replacement, use the separate **Look inside →
Test an activation patch** section. The [inspection and patching guide](NNSIGHT-INTEGRATION.md)
explains donor selection, exact positions, fresh controls and saved results.

## Edit versus fresh control

Select a half-open original-token span `[start, end)` and enter replacement text.
Empty text means deletion. The earlier prompt and response token IDs stay fixed.
The control retains original response IDs through `end`; the edited arm retains
response IDs through `start` and appends independently tokenized replacement IDs.
Both discard the later original suffix and generate new continuations. Preview
shows the exact decoded prefixes, including potentially surprising token seams.

Both arms use the same temperature and cap, full-vocabulary sampling (`top_p=1`,
`top_k=0`), one draw per batch, distinct recorded seeds, and alternating execution.
The original graph's retained-branch filter is **not** applied: use the fresh control
as the comparison, not old graph values. These are not paired-randomness trials.
Single-draw execution prioritizes auditable seeds over GPU throughput in this version.

The artifact saves token IDs, texts, model identity, readout results, caps, timing,
and sampling settings. The interface compares observed outcome counts and lets you
read each continuation. Multiple/no phrase matches and capped draws remain `Other`.
EOS is stripped by the upstream sampler; completion is inferred from length, with
exact-cap cases conservatively unresolved. No significance claim is computed.

A reproducible difference establishes an effect of **this text intervention under
these settings**, not a unique token-level mechanism. Edited length, grammatical
damage, explicit answer cues, and selection after seeing the source results are
possible explanations. Use neutral paraphrases, sham edits, independent confirmation
samples and additional prompts for stronger research conclusions.

## Read-only activation capture (experimental)

Choose up to 16 checkpoint positions and four zero-based decoder layers. Each
checkpoint replays `prompt_ids + base_ids[:checkpoint]` in a separate inference-mode
forward pass with caching disabled. At checkpoint zero, the selected position is
the last prompt token. Forward hooks copy only the final input position's decoder
block output, before final normalization, and are removed even after a failure.

Explicit architecture mappings exist for Llama, Mistral, Qwen2, Gemma2 and
Muse-Glimmer. Other architectures are rejected. Mapping a module path does **not**
establish numerical validation on every model: local parity tests cover a tiny,
randomly initialized Llama model, without downloaded weights. Muse and the other
architectures still need on-model parity/memory checks before research claims.

The artifact includes float32 vectors, layer/site, checkpoint, absolute position,
exact prefix IDs and hashes, model identity and runtime versions. Norms are displayed
only as magnitude summaries. Token identity, length and scale also change across
prefixes; vector differences are not semantic explanations.

For ranked vocabulary readouts beside these vectors, use the [Jacobian lens panel](JACOBIAN-LENS.md). It supports original prompt/response positions, saved draws and edit/control prefixes with a matching fitted lens.

This capture tool does **not** train probes or label hidden thoughts. The separate
[activation patch panel](NNSIGHT-INTEGRATION.md) implements bounded donor/recipient
replacement with fresh baseline and self-copy controls.
A later probe requires independent problem-level train/test splits and meaningful
label variation. Repeated continuations from the same prefix share one activation
example; they are not independent feature rows. Causal activation tests require
explicit donor/recipient alignment, sham controls, and correct cache handling.

## Storage and deployment

Investigations share the worker's existing single-job exclusion, authentication,
origin controls and cancellation mechanism. They do not change the loaded base or
the source run. Completed draws/prefix captures are saved incrementally to the
worker's `investigations/<id>.json`; stop/failure status is retained. An interrupted
process can leave a partial `running` artifact; automatic resume is not implemented.

Use **Refresh saved** after reconnecting and **Download artifact** to preserve data
locally before deleting a VM. These artifacts are separate from ordinary run exports
and are excluded from Git and Docker build contexts. The new dashboard assets and
worker must be deployed together. An older worker cannot execute these endpoints.

## Complete investigations

Run a bounded scan → refinement → optional J-lens workflow from the CLI, an agent, or the dashboard, then export related runs and readouts as one file. See [the investigation workflow guide](INVESTIGATION-WORKFLOW.md).
