# Method and experiment reference

[Documentation index](README.md)

Commands below run from the repository root. The [main README](../README.md) contains installation and release status.

<!-- generated: Codex — fork-microscope-revamp-ASTRA-BRIEF.md, canonical vocabulary. -->
## Interface vocabulary

| Name | Meaning |
|---|---|
| Investigation | A question and its connected responses, scans, comparisons and measurements. |
| Prompt set | Reusable input configurations, not a backup of generated evidence. |
| Response | One exact generated output token sequence selected for analysis. |
| Outcome rule | A versioned matching rule, not automatically a correctness judgment. |
| Scan | Collected checkpoint evidence along one fixed response. |
| Pass | One independently configured checkpoint grid and fit within a scan. |
| Checkpoint N | Keep the first N response tokens; sample the alternative at index N. |
| Continuation | The forced branch token and newly generated suffix after a checkpoint. |
| Samples per checkpoint | Total alternative continuations across retained branch tokens. |
| Branch token | The next-token candidate forced before its sampled suffix. |
| Continuation length limit | Maximum newly generated suffix tokens after the branch token. |
| Observed proportion | Recorded matching outcomes divided by samples at that checkpoint. |
| Fitted estimate | A reconstruction based on recorded observations and model assumptions. |
| Candidate change interval | A region worth investigating, not a certified causal boundary. |
| Refinement | Fresh observations at additional positions on the same exact response. |
| Path comparison | Comparing two saved continuations. |
| Scan comparison | Comparing recorded estimates from two compatible scans. |
| Compute connection | Connection to the model-running worker service, not an AI agent. |
| Inspection | A read-only internal measurement, such as a lens readout or capture. |
| Intervention | A controlled edit or activation patch with recorded controls. |

Tokens use zero-based response coordinates unless a control explicitly says prompt
or absolute coordinates. Lens positions describe the state **after** the selected
input token; a checkpoint describes a prefix of N tokens. These conventions differ
by one at the boundary and must not be silently interchanged.

## What a data point means (schema 2)

At checkpoint t, retain the prompt plus response tokens before t. Enumerate retained next-token branches using the recorded temperature-1 probabilities, top-k setting and minimum branch probability (the base token is always retained). Normalize over retained branches; omitted mass is excluded and saved for inspection.

For each of S draws at that checkpoint, choose a branch by its normalized probability BEFORE generating its continuation. Group draws by branch for execution, but save their original mixture order. Every generated outcome is used exactly once in the histogram and in the reconstruction input. **S means total draws per checkpoint, not draws per branch.** Increasing retained branch count does not multiply the draw budget.

A marker is the frequency of each observed answer among those S draws. Goodfire's M5a segment-kernel estimator is fitted to those same multinomial counts. Cross-validation uses those same draw sequences; there is no second subsampling step. Nominal 90% bands are model-based, exclude exactly 0 and 1, and have no guaranteed frequentist coverage.

Continuations use the chosen temperature, top_p=1 and top_k=0. Branch-selection probabilities always use temperature 1; the UI warns when the continuation temperature differs. Inherited generation defaults and effective sampling settings are saved separately.

New custom-prompt runs match 1–32 expected answer texts anywhere in a completed reply. The answer list is tracking metadata and is not appended to the prompt. Matching normalizes Unicode (NFKC), case and repeated whitespace, with word boundaries on word-like ends. It does not recognize semantic equivalents or negation. One distinct match gets that label; multiple distinct matches, no match or a capped output become Other. `Other` is reserved; answer strings must be unique after normalization. An answer such as `56` will not match `156`, but “I reject 56” will match. Inspect the saved matched answers and text before interpreting the curves.

For generic models, an explicit `<think>…</think>` prefix is excluded; untagged reasoning remains part of the searchable response. No universal reasoning-channel parser is claimed. Legacy question/choices configurations keep the original A–D regex. Neither path forces a logit fallback. Muse only reads a completed `to=user` channel; both `<|eot|>` and `<|end_of_text|>` stop generation, while `<|eom|>` only ends a channel. Upstream strips the terminal EOS, so stop reason is inferred from the forced token or stripped length and the exact removed EOS identity is not fabricated.

## Product workflow (local beta)

Open `/observatory.html` to explore saved evidence without loading a model. The completed-run library includes **Import run** and **Export evidence**. An imported JSON archive is checked for valid settings, shared source IDs, complete mixture records, agreement between response labels and graph counts, and bounded curve coordinates. Existing evidence is never overwritten. The saved fitted curve is preserved, not recomputed during import, and is labeled accordingly. The browser importer accepts exports up to 64 MB; larger run folders can be copied into `live-runs`.

Choose **New prompt** for the guided **Model → Question → Scan** flow. Hardware status describes the machine running this server. Model inspection checks native architecture support, tokenizer metadata and available memory information before attachment. CPU execution is available for eligible smaller models. The interface does not promise that every model fits merely because a GPU exists.

The observatory keeps original prompts and less-used settings collapsed, shows completed versus capped continuations separately, and lets you filter recorded text by outcome and completion. Candidate regions open a refinement panel with exact endpoints, tighter checkpoint spacing, an editable continuation count and a projection based on the source run's measured speed. Runtime projections exclude loading/replay and depend on comparable hardware and settings.

The source-model setup link retains the original model revision and a route back to the same saved run. Refinement replays exact saved IDs. Edited passages can be exported as drafts or tested against fresh control continuations in the investigation panel; see [Investigations](INTERPRETING-RESULTS.md). The hosted dashboard supports one-time machine pairing or Advanced URL/token entry. Cloud provisioning, HF authentication and storage/VM lifecycle remain external. Interrupted coordinator jobs support explicit conservative recovery; partial sampling is not automatically resumed.

Mathematical validation and release limitations are documented in [the validation scope](../VALIDATION.md) and [the numerical audit guide](MATH-VALIDATION.md).

## Dashboard workflow

1. **Setup & sample:** attach a compatible HF model ID or server-local model directory, write your exact prompt, and enter expected answers (one per line). Generate and explicitly review the completed base trace and its matched outcome, then choose checkpoint region, spacing, draws and continuation cap. Add passes only when needed.
2. **Results:** select a saved run, choose an outcome, and inspect its observed frequencies and eligible fitted curves. Clicking an observed point opens its evidence.
3. **Continuations:** navigate pass → all checkpoints or one checkpoint → draw. Filter by outcome, completion or ambiguity and search the saved text. The paginated list opens a reader with new continuation text, full response, matcher input and token IDs. Old runs remain browsable with missing fields labeled unavailable.

**Length versus count:** at checkpoint 60, retain original tokens 0–59, choose one branch token at 60, and generate up to `cont_max` new tokens after it. EOS can stop earlier. Each draw starts from the same checkpoint prefix, not the preceding draw. Spacing 4 visits every fourth position; 20 draws collects 20 continuations at each visited position. The cap limits each continuation’s length, not the checkpoint spacing or number of draws.

For internal exploration, use **Compare paths → Look inside** and the [lens workflow](JACOBIAN-LENS.md). The first unequal saved token is a text coordinate, not proof of an internal causal boundary.

## Inspect and interpret

Click an observed point or use the continuation viewer's pass/checkpoint selectors. New records contain decoded full-response and continuation text, label source, channel reached, stop reason/evidence, IDs, branch probabilities and original draw indices. Model text is rendered literally, not interpreted as instructions or HTML.

- Above 10% continuation-cap hits, reconstruction and comparison metrics are withheld; the actual outcome markers and text remain visible. This is an engineering diagnostic gate, not a scientific reliability guarantee.
- Fewer than four checkpoints disables CV and change-point segmentation; only explicitly labeled fixed smoothing is available if the completion gate passes.
- Small sample counts, incomplete base responses, unparsed completed answers and substantial omitted branch mass generate warnings.
- The base panel shows each token's position and top-token probability. Do not assume the first few tokens contain a meaningful decision; Muse may restate the question.
- No valid dense reference means no measured reconstruction error. A dense reference is sampled independently and remains noisy; excessive cap hits withhold comparisons.

Offsets shift checkpoint positions, not text or positional embeddings. For each pass, checkpoints are `range(start + offset, end + 1, stride)`. At least two must fit. Overlapping positions across passes incur independent draws; they are not deduplicated. Add a pass with the same settings and a different seed to collect a repeat. Adding/removing/reordering passes can change the seed namespace; the recorded seeds are the source of truth.

The cost panel reports selected draws and maximum continuation-token allowances. It does not present a stride ratio as measured savings. Runtime/dollar projections require user-supplied throughput and hourly rate and exclude loading/prefix-processing overhead. Cross-branch KV reuse, adaptive sample stopping and a combined-pass estimator are not implemented.

## CLI profiles and records

```bash
.venv/bin/fork-microscope run configs/muse-smoke.json
.venv/bin/fork-microscope run configs/muse-smoke.json --prepare-only
```

The original CPU and Muse smoke profiles use one pass and deliberately retain small caps to test wiring and completion warnings, not to produce scientific results. `cpu-custom.json` exercises free-form prompts and numeric text matching with a 64-token continuation cap; it is also a small pipeline check. Dashboard defaults use longer caps (base 512, continuation 768), which still require completion checks. `--prepare-only` loads weights and generates a base; another CLI run starts anew. Use the dashboard to sample the same in-memory base interactively.

A custom `base` object uses `prompt` (up to 16,000 characters), `answers` (1–32 strings, each up to 200 characters), `mode` (`chat` or `base`), `max_tokens`, and `seed`. See `configs/cpu-custom.json` for a runnable example. The legacy `question` plus four `choices` object remains supported.

New JSON run configuration contains `passes` plus shared `cont_max`, `temperature`, `top_k`, `threshold`, `dense`, `reference_samples`, and `tuning`. Each pass requires `id`, `label`, `start`, `end`, `stride`, `offset`, `samples`, `seed`. The independent dense reference spans the earliest through latest actual selected checkpoints. Legacy `samples/stride/shift/start/end/seed` configurations still parse as two passes, but new collection always uses per-checkpoint mixture sampling.

Every job saves `live-runs/<id>/manifest.json`, one `<pass-id>.json` per pass, optional `dense.json`, and `result.json`. Pass IDs must be unique safe names. Results/weights/credentials are excluded from Git. Completed branches are atomically saved during collection; partial-run resumption is not implemented.

Historical schema-1 results remain readable and visibly labeled legacy. Their markers used all per-branch data but fits used a subset; their older generic extractor could label truncated outputs by logit fallback. Missing decoded text and stop metadata are shown as unavailable, never invented. Original records are not rewritten.

## CPU validation checks

```bash
./scripts/setup.sh cpu
.venv/bin/python -m pytest -q
node --test tests/test_*.mjs
.venv/bin/fork-microscope run configs/cpu-smoke.json
.venv/bin/fork-microscope verify-upstream
```


The Goodfire submodule stays unmodified at `d32fed8d4162a4888291c4b3a38b059727c85a41`. Upstream tests and released-data checks exercise its original method. The strict readout, direct live mixture collection, multi-pass orchestration and viewer are our additions. Applying this workflow to Muse is a new-model experiment, not reproduction of the paper's model-specific benchmark. GPU coverage is summarized separately from CPU verification. See [VALIDATION.md](../VALIDATION.md) and [THIRD-PARTY.md](../THIRD-PARTY.md).


### Refining a saved trace

`replay_trace.replay_saved_trace` teacher-forces the saved prompt and response token IDs through the same resolved model revision and captures next-token scores before forcing each saved token. It rejects model/revision or tokenizer text mismatches and verifies every replayed ID. It does not generate a new base or tokenize the decoded text again. The refinement worker additionally compares recovered endpoint probabilities with the original saved candidates.

Pass configurations can include an ordered, unique `positions` list with both `start` and `end` included and `offset: 0`. This supports irregular endpoint coverage (339, 347, …, 451, 452) without combining independent fits. The scan form constructs regular grids; the observatory refinement form adds the exact final endpoint, including when it is off-grid. Replay verification and source provenance live with the run session.

### Run → inspect → refine

In the observatory, click a fitted interval on the graph or choose one in **Refine a region**. Adjust endpoints, spacing, draws per point and continuation cap. The preview includes both endpoints and shows exact positions, draw count and maximum output tokens. It also reports source-region truncations and flags changes to the original cap.

**Restore & sample on attached model** requires the exact source model revision on the same worker, an idle worker, and the saved source run on that worker. It replays the original IDs and writes a new run with a parent-run link and source token checksum. It never generates a replacement base. Starting a refinement does not replace its source run.

**Export refinement job** downloads a self-contained JSON bundle with original prompt/response IDs, model revision and sampling settings. On a GPU machine running this updated checkout, execute `fork-microscope refine job.json`; it loads the pinned model, validates the bundle, replays the saved trace and writes results into `live-runs`. This command does not provision or stop cloud infrastructure. Explorer can import a complete investigation bundle for offline browsing, then explicitly **Transfer to connected compute** before further work. That investigation transfer is distinct from this refinement-job CLI file. Neither path provisions a GPU automatically.

Refinement selects a region after inspecting the source, so treat it as exploratory. Separately inspect decision labels and completion/truncation; a change in Other counts is not evidence of a decision switch.
