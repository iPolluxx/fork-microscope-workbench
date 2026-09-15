# Fork Microscope documentation

Start with the task you want to do. You do not need to read every document.

| I want to… | Start here |
| --- | --- |
| Understand the interface before connecting hardware | Open **Guide** from Workspace (or `/guide.html` on your running dashboard). It works without compute. |
| Connect my computer or a GPU VM and run one prompt | [Your first session](GETTING-STARTED.md) |
| Run a complete investigation from a CLI or agent | [Investigation workflow](INVESTIGATION-WORKFLOW.md) |
| Give a fresh agent the commands and context | [Agent operating guide](agents/OPERATING-GUIDE.md) → [every automated configuration field](CONFIGURATION.md) |
| Understand checkpoint counts, curves and comparisons | [Method reference](REFERENCE.md) |
| Inspect or collect a Jacobian lens readout | [Lens guide](JACOBIAN-LENS.md) |
| Choose NNsight capture or test an internal-state replacement | [Internal inspection and controlled patches](NNSIGHT-INTEGRATION.md) |
| Edit text and compare fresh continuations | [Interventions and investigations](INTERPRETING-RESULTS.md) |
| Host the interface or troubleshoot remote connections | [Hosted dashboard](HOSTED-DASHBOARD.md) |
| Check numerical validation and known limits | [Math validation](MATH-VALIDATION.md) and [validation record](../VALIDATION.md) |
| Contribute to the implementation | [Architecture](ARCHITECTURE.md) and [contributing](../CONTRIBUTING.md) |

## The shortest useful path

**One prompt:** Connect a machine → Configure model → Question and answer labels →
Generate and review → Scan → Explore. Expand advanced controls when your research
question needs them. You can also configure multiple prompts in Workspace.

**Existing results:** Start a CPU worker → Explore → Import evidence → select a
linked run → scan, compare paths, or open an existing lens artifact. You do not
need a loaded model. Importing a file does not generate missing data.

**Automation:** Follow the operating guide, validate a configuration, start with
an explicit request ID and resource limits, check status, then export. Automated
refinement uses a bounded selection heuristic; it is not a validated claim of
cost savings. The dashboard also accepts the same configuration file.

## Which file should I keep?

| File | Contains | Does not replace |
| --- | --- | --- |
| Prompt set | Reusable questions and outcome labels | Generated results |
| Investigation configuration | Model, prompt, sampling policy, lens settings and limits for a job | Evidence from that job |
| Single-run evidence export | One scan and its recorded data | A complete family of runs and lens artifacts |
| **Investigation bundle** | Linked runs, exact tokens, continuations, classifications, fits, lineage, collected lens artifacts and completed activation tests | Model weights or compute for new work |

Use **Export investigation** in Explore, or the CLI export command, for a portable
backup. Import the bundle through **Import evidence**. Each run remains
individually accessible within its investigation. Credentials and operational
paths are excluded, but original prompt and generated text are preserved: review
those before sharing a bundle.

## Three settings to learn first

- **Spacing**: where to measure along the original response. Smaller spacing adds checkpoints.
- **Draws per checkpoint**: how many new continuations to collect at each position.
- **New-token cap**: the maximum generated length of each continuation.

Concurrent continuations is a separate throughput/memory setting. The full
[automated configuration reference](CONFIGURATION.md) describes all
accepted fields and fixed behaviors; the manual interface exposes additional
controls documented in the method and lens guides.

## Interpretation in one paragraph

The graph shows sampled outcome frequencies and a statistical reconstruction,
not the probability of the next token. Interesting intervals are places to
investigate, not proof of an internal decision. Read the actual continuations and
check answer matching. J-lens maps selected internal states to vocabulary
rankings; layers do not emit those words, and a readout does not establish intent
or causation. A run with no contrasting outcomes is still a valid result.
