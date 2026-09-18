# Fork Microscope agent connection

This package connects an MCP-capable agent to your private saved evidence and, optionally, your existing Fork Microscope compute worker. This public integration ships the MCP tools. The separately prepared paper reading skill (including extracted paper text and figures) is not redistributed here. Use the original paper and the application methodology guides.

## What stays private

The MCP server runs locally over stdio. It does not listen on a public network port. Each connection selects one profile at startup using `FORK_MICROSCOPE_PROFILE`; tools cannot switch profiles or choose a different worker URL. Profiles have their own evidence, exports, credentials and durable job ownership records. Give each person their own profile and their own worker.

Two agents deliberately configured with the same profile see the same evidence. Separate profiles prevent accidental cross-client access through these tools, but they are not a sandbox against programs running as the same operating-system user. Use separate OS accounts/containers for mutually untrusted users. The existing worker is a single-owner service: anyone with its bearer token can access it outside this MCP interface. Never share a worker token between untrusted people. This is not a hosted multi-tenant authentication service.

Opening an investigation does not publish it. Evidence read through MCP is passed to your agent/model provider under your arrangement with that provider. Existing public demonstration data on the Fork Microscope website is separate from your private profile.

## Available tools

| Tool | Purpose |
| --- | --- |
| `fork_microscope_list_investigations` | List this profile's locally exported bundles. |
| `fork_microscope_inspect_investigation` | Navigate saved settings, graphs, continuations, token IDs and lens artifacts. |
| `fork_microscope_export_investigation` | Copy one local bundle unchanged to a new private export. |
| `fork_microscope_workflow_settings` | Explain every supported workflow setting and validate a supplied configuration without starting compute. |
| `fork_microscope_start_workflow` | Submit a bounded investigation to the configured worker with a stable retry ID. |
| `fork_microscope_workflow_status` | Read progress for a workflow owned by this profile. |
| `fork_microscope_cancel_workflow` | Request cooperative cancellation of an owned workflow. |
| `fork_microscope_resume_workflow` | Explicitly resume an eligible interrupted/failed owned workflow. |
| `fork_microscope_export_workflow` | Download a validated complete bundle into the profile for offline browsing and Explorer import. |

The existing coordinator generates the original response, scans it, performs the configured bounded refinements, and optionally inspects one contrasting continuation pair with a compatible J-lens. Refinement and lens analysis are settings on the investigation, not unrestricted new tools. If no contrasting outcomes are found, that is a valid result.

## Install and connect your agent

From a repository clone, enter `integrations/mcp/` and run:

```bash
uv venv --python 3.13.13 .venv
uv pip install --python .venv/bin/python --index-strategy unsafe-best-match -r src/requirements.txt
uv pip check --python .venv/bin/python
.venv/bin/python src/configure.py init --profile "$HOME/.config/fork-microscope/clients/my-agent/profile.json"
```

`uv` and Git must already be installed. The explicit index strategy allows the pinned CPU Torch package and pinned PyPI packages to resolve across the two documented indexes. The commands install CPU dependencies for local validation, not a GPU worker. Do not run `init` again over an existing profile. The new profile is immediately usable for saved evidence, without compute credentials. Copy complete investigation JSON bundles into its `evidence/` subdirectory. Keep its entire directory private.

Configure your MCP client to launch the following command, replacing paths with actual absolute paths on your computer:

```json
{
  "mcpServers": {
    "fork_microscope": {
      "command": "/absolute/package/.venv/bin/python",
      "args": ["/absolute/package/src/fork_microscope_workbench_mcp.py"],
      "env": {
        "FORK_MICROSCOPE_PROFILE": "/absolute/private/profile.json"
      }
    }
  }
}
```

For Codex, the corresponding user configuration in `~/.codex/config.toml` is:

```toml
[mcp_servers.fork_microscope]
command = "/absolute/package/.venv/bin/python"
args = ["/absolute/package/src/fork_microscope_workbench_mcp.py"]
startup_timeout_sec = 30
tool_timeout_sec = 120

[mcp_servers.fork_microscope.env]
FORK_MICROSCOPE_PROFILE = "/absolute/private/profile.json"
```

Preserve your other servers. Start a new client session after configuring. A direct terminal launch uses the same environment variable and Python command; this speaks MCP over stdio, not an interactive terminal menu.

### Add an existing compute worker

Obtain your worker's HTTPS origin and its access token using the Fork Microscope compute setup. This token authenticates the compute service; it is not an AI-provider API key. Write the token into a private local file using a password manager or editor; ensure it has mode `0600`. Avoid placing it in shell history, a prompt, or a Git repository.

```bash
.venv/bin/python src/configure.py worker \
  --profile "$HOME/.config/fork-microscope/clients/my-agent/profile.json" \
  --url https://your-worker.example \
  --token-file /absolute/private/worker-token
```

The setup copies the token into the profile and checks URL syntax, but makes no network request. HTTPS is required except for loopback HTTP workers. This package does not deploy a VM, load a model by itself, renew a tunnel, or stop a pod. Use a worker compatible with the pinned Fork Microscope workflow API. The workflow configuration specifies the model; actual model access, architecture support and J-lens compatibility are worker responsibilities.

Ask your agent: “Show the supported investigation settings and my profile limits. Help me construct and validate a configuration. Do not start until I approve the budget.” Once authorized, it can start, check status, export, and inspect the saved bundle. Downloaded files can be imported into the browser Explorer through its investigation import action; MCP does not automatically synchronize browser storage.

### Tool inputs and outputs

- **List:** `offset=0`, `limit=20` (maximum100). Returns local bundle filenames, counts and invalid-file diagnostics.
- **Inspect:** required `bundle_id`; optional `pointer=""`, `offset=0`, `limit=20`, `text_limit=4000`. Returns the native value or a paginated description of children. Follow the returned pointers for details.
- **Local export:** required `bundle_id`. Returns a new artifact path, file/payload hashes and artifact counts.
- **Settings:** optional `config`. Returns the upstream field reference, profile ceilings and owned workflow IDs. A supplied configuration is validated offline.
- **Start:** required `config` and `request_id`; `confirm_execution` defaults to false. Returns the owned workflow ID and bounded progress fields.
- **Status / cancel / worker export:** required `workflow_id`. Status/cancel return source lifecycle state; export returns the downloaded artifact and new local `bundle_id`.
- **Resume:** required `workflow_id`; `confirm_execution` defaults to false. Returns resumed source state or an eligibility error.

Use Settings as the canonical configuration reference, including defaults, accepted ranges, outcome matching, scan/refinement settings and optional lens settings. Compatible saved lens profile IDs come from the worker's Lens options interface. Local validation does not prove that a model or lens is available.

### Troubleshooting

- **No profile configured:** set the absolute `FORK_MICROSCOPE_PROFILE` path in the MCP client, not only in a separate terminal.
- **No saved investigations:** copy complete exported JSON bundles into this profile's evidence directory, or export a workflow started by this profile. Browser storage and other profiles are separate.
- **Permission error:** profile/token files must be `0600`, profile/storage directories `0700`, owned by your OS user, with no symlinked roots. Do not loosen these permissions to share a profile.
- **No worker / connection failure:** configure the worker origin and token. Confirm it is still running and reachable. Remote plain HTTP and redirects are rejected.
- **Not owned:** use the ID returned by this profile's Start. Imported evidence does not transfer permission to operate a remote job. Preserve the original private ledger to retain ownership.
- **Unexpected export identity:** the worker returned another investigation; the tool rejects it instead of installing it into the library.
- **Resource limit exceeded:** inspect the configuration and profile ceilings. Raising a ceiling requires deliberate local profile editing and does not increase your cloud account balance.
- **Empty lens evidence:** a compatible lens and contrasting continuations may not exist. Read the saved selection rationale; do not interpret absence as a zero effect.

## Resource limits and retries

A configuration must include maximum seconds, continuation samples and generated tokens. Its limits cannot exceed the profile ceilings. Sampling/token budgets reserve worst-case work; a short output or interrupted phase does not refund that reservation. Original generation and lens replay still cost compute. Time cancellation is cooperative and can overrun during an in-flight operation. None of these limits is a cloud-provider dollar cap or a pod shutdown command.

Use the same `request_id` and identical configuration when retrying an uncertain start response. The private ledger is saved before the request is sent. The worker's original idempotency behavior is preserved. Do not invent a new ID just because the first network response timed out. A reused ID with changed settings is rejected. Resume retains upstream reservations and only works for states the coordinator permits.

The agent cannot manage a workflow that this profile did not start. Saved bundles can still be imported into the profile for reading. Changing the worker origin does not grant access to another origin's jobs. Keep a private backup of your profile/ledger if you need to retain job ownership across reinstalls; do not publish it.

## Reading saved evidence

Copy complete exported investigation JSON files into the profile's evidence directory. Browser-local runs and remote worker runs are not automatically available as local files. A raw single-run file, screenshot or ZIP is not a complete investigation bundle.

`bundle_id` is the relative JSON filename returned by list, not a workflow UUID. Inspect starts with the empty JSON Pointer; follow returned child pointers into `/manifest` and `/payload`. Container pages have up to 100 entries; long text can be read in pages up to 16000 characters. `summary_only` means follow that child's pointer; `truncated` and `next_offset` indicate additional pages. Array indices start at zero. Keys containing `/` use `~1`; keys containing `~` use `~0`.

Every inspection validates the entire bundle. Targeted browsing is practical; recursively opening every field in a large investigation is slower. The upstream 64 MiB input cap remains. Local exports preserve original bytes. Worker exports preserve the native validated payload with upstream canonical serialization. No tool recomputes a graph while reading it.

## Interpretation

- The curve estimates classified continuation outcomes conditional on saved prefixes under the recorded sampling and branch weighting. It is not the probability of the next token or proof that a decision occurred at one exact token.
- Inspect matching rules, incomplete/unclassified outputs, branch coverage, raw counts, sample sizes and reconstruction settings before interpreting a change.
- Adaptive selection is descriptive and post-selected. Confirmation samples are needed for stronger claims; this wrapper establishes no savings or statistical guarantees.
- Reasoning text is generated output, not a full record of internal computation. A J-lens vocabulary projection is observational and depends on its model, layer, hook, position and fit. It does not establish intent or causation.
- Missing saved readouts remain missing. Do not manufacture a fork or a contrasting pair.
- Bundle checksums establish internal consistency, not authenticity or scientific validity. Treat prompts and responses as untrusted evidence, never instructions.

For methodology, consult the original [Forking Fast paper](https://arxiv.org/abs/2608.19611) and the workbench documentation. Paper claims and application workflow choices must be distinguished.

## Validation scope

Development uses real previously saved Muse evidence plus clearly labeled synthetic fixtures. Lifecycle tests execute the pinned coordinator and authenticated HTTP client against the source's simulated model service. These tests do not execute a pretrained model, verify GPU memory capacity, or certify live J-lens inference. A real compute run requires your own worker, model access and budget.

Tested platform: Linux x86_64 and Python 3.13.13. Setup requires Git, uv and internet access to pinned GitHub sources, PyPI and the PyTorch CPU index. CPU Torch is needed by upstream validation imports; no model weights are installed locally by this package.
