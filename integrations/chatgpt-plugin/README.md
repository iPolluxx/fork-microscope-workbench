# Fork Microscope plugin — private research beta

Ask ChatGPT or Codex to explain your saved investigations, inspect contrasting continuations, and export the complete evidence for Explorer. New experiments use your existing model worker. The assistant is the operator; the worker is the process that actually runs the model.

This package adds onboarding and investigation skills to the [private MCP adapter](../mcp/README.md). It does not copy investigations or credentials into the plugin. The local adapter does not provision a GPU or implement a public shared evidence service.

The same skills also work with the separate OAuth-authenticated **managed pilot** server (`integrations/mcp/src/tools/hosted.py`; [HOSTED-BYOK](../mcp/HOSTED-BYOK.md)). Users connect RunPod on the secure account page and review an immutable configuration, pinned model/image, hardware, rates, duration, destination and expiry before approving that digest. Evidence is saved to the authenticated library; exact bytes come from hash-pinned reads or the supported host file handoff. The private pilot has actual deployed saved-viewer acceptance. Fresh compute and independent-user acceptance are separate checks; this is not an unrestricted public service.

The compact/expanded MCP Apps viewer shows recorded checkpoint counts, contrasting continuations, provenance and verified original-byte export. Ask to show a listed saved investigation in the viewer. It needs no model worker. [Viewer validation](../mcp/VIEWER-VALIDATION.md).

## Start with saved evidence

Use the Python 3.13 environment installed according to [MCP USAGE](../mcp/USAGE.md). In the commands below, run from the repository root; `integrations/mcp/.venv/bin/python` is an example of that installed environment, not a bundled executable. Installation of the main workbench alone is insufficient.

```bash
integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py init \
  --profile "$HOME/.local/share/fork-microscope-chatgpt/profile.json"

integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py import \
  --profile "$HOME/.local/share/fork-microscope-chatgpt/profile.json" \
  --bundle public/fork-microscope/demo-portfolio.json

integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py doctor \
  --profile "$HOME/.local/share/fork-microscope-chatgpt/profile.json"
```

`init` creates private storage and an executable `launch-mcp.sh` beside the profile. `import` validates an investigation JSON using the workbench validator, preserves its bytes, names it by SHA-256, and safely reuses identical imports. Use Explorer's complete investigation export for your own evidence; a single-run export is a different format. The portfolio example is previously recorded Qwen evidence, not a new model run. Prompt and continuation text remain unredacted: review your own files before making them available to an assistant.

`doctor` checks dependencies, local profile permissions and available tool definitions. It does not prove the live MCP protocol, ChatGPT connection, GPU readiness or scientific validity. Missing dependencies or an invalid profile return a nonzero exit status. Existing profiles and launchers are never silently overwritten.

## Connect the private host

**Codex/local MCP client:** configure it to launch the absolute path to your generated `launch-mcp.sh` as a stdio MCP command. It keeps credentials out of the arguments and MCP stdout. The launcher binds exactly one private profile and removes ambient API keys before starting the server.

**ChatGPT:** use the official [Secure MCP Tunnel setup](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). Download the official `tunnel-client` from the link in those instructions. Associate a tunnel with your intended Platform organization and ChatGPT workspace. The runtime principal needs Tunnels Read + Use; creating/managing a tunnel requires separate permissions. Keep the runtime key private and outside this repository.

```bash
tunnel-client init --sample sample_mcp_stdio_local \
  --profile fork-microscope --tunnel-id YOUR_TUNNEL_ID \
  --mcp-command /absolute/path/to/launch-mcp.sh

tunnel-client doctor --profile fork-microscope --explain
tunnel-client run --profile fork-microscope
```

The official client's default key reference is `env:CONTROL_PLANE_API_KEY`. Supply it through your local secret manager or a hidden terminal prompt, following the client's documentation. Never paste the key into ChatGPT or embed it in the plugin. Quote the command according to `tunnel-client` guidance if its executable path contains spaces. The foreground `run` must stay active while ChatGPT uses tools; Ctrl+C disconnects it. For longer-lived operation, use the official client's supervised runtime commands, not an unmanaged background process. Local MCP launch does not need a public listening port.

In [ChatGPT Plugins](https://chatgpt.com/), add a custom MCP server, choose **Tunnel**, select your tunnel, review the access warning and create the private plugin. Workspace availability and permissions vary. Test a fresh chat with:

> Use Fork Microscope to list my saved investigations. Inspect the portfolio investigation, explain the recorded outcome counts and contrasting continuations, and export it. Do not start any new model job.

Tool discovery and calls must work before claiming connection success. Installation alone does not start the laptop, tunnel or GPU. If your host sleeps, ChatGPT cannot retrieve new local evidence until it wakes and reconnects.

## Add the packaged skills

Copy the technical `plugin_asdk_app_...` ID from your own registered connection, then build:

```bash
integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py build \
  --app-id YOUR_PLUGIN_ASDK_APP_ID \
  --output "$HOME/.local/share/fork-microscope-plugin-package"
```

Choose an empty private output directory. The result contains:
- `plugins/fork-microscope/`: portable manifest, registered app mapping, onboarding and investigation skills.
- `.agents/plugins/marketplace.json`: a local marketplace referencing that package.
- `fork-microscope-plugin.zip`: the same allowlisted package files, without local host paths, credentials, evidence or caches.
- `build.json`: archive and per-file checksums.

Where the Codex plugin CLI is installed, register this local source with `codex plugin marketplace add /absolute/path/to/the/output/directory`. Then follow the [official local plugin installation instructions](https://developers.openai.com/plugins/build/plugins) for your desktop surface, install **Fork Microscope**, and run its setup in a fresh chat. ChatGPT also exposes **Plugins → Add → Upload plugin archive**. Upload the generated ZIP there where supported; automated Chrome uploads need the ChatGPT extension's local-file permission, which should be changed only with the operator's approval. A ZIP is an artifact, not proof of installation or a publicly submitted plugin. Some surfaces do not support local marketplaces. The raw private MCP connection remains usable independently of the skills package.

Each operator builds the mapping for their own registered app. An app ID is not a password and does not grant permission to someone else's connection. This is one private profile per MCP process, with local OS file permissions and tunnel access controls. It is not adversarial multi-tenant authentication for unrelated users sharing one server/Unix account. Do not distribute an operator's app mapping as a universal connection.

## Optional: run an investigation

Connect an existing Fork Microscope worker using its HTTPS origin and a private local token file:

```bash
integrations/mcp/.venv/bin/python integrations/chatgpt-plugin/plugin.py worker \
  --profile "$HOME/.local/share/fork-microscope-chatgpt/profile.json" \
  --url https://YOUR_WORKER_HOST \
  --token-file /absolute/private/path/worker-token
```

This copies the token into private profile storage and saves the origin; it does not contact the worker or launch a VM. Restart the stdio/tunnel connection after configuration changes. Ask the assistant for a validated config and explain the limits before authorizing execution. The thirteen tools include saved-evidence rendering and browsing, settings/validation, start, status, cancel, eligible resume, and workflow export; see [tool reference](../mcp/USAGE.md). Dollar estimates are not enforceable spend caps. Cancelling a workflow does not stop GPU VM billing. Managed RunPod provisioning is not bridged through this local adapter; it belongs to the browser application and the separate managed pilot server described above.

## What the evidence means

Outcome frequencies concern sampled continuations from saved output prefixes. A fitted fork region is a useful inspection candidate, not proof of where an internal decision occurred. Classification rules, truncation, sample size, coverage and post-selection affect interpretation. Saved J-lens vocabulary projections are not generated reasoning or a causal explanation; missing lens data stays missing. The skills require hash-pinned inspection and clearly separate observations from interpretation. Export preserves original validated bytes, including unresolved results. A local export path is not automatically downloadable in ChatGPT; use bounded byte retrieval with file-writing support or retrieve it from the host and import it into Explorer.

## Verification

```bash
integrations/mcp/.venv/bin/python -m pytest integrations/chatgpt-plugin/tests -q
```

The tests cover deterministic allowlisted packaging, per-app mapping, private permissions, malformed/symlink/tampered imports, duplicate imports, credential-free child environment, and real stdio discovery → saved-data report → byte-identical export/chunk retrieval through the generated launcher. The stdio test requires FastMCP; inspect skips rather than treating missing runtime tests as acceptance. No new model inference or provider provisioning occurs. See [VALIDATION.md](VALIDATION.md) for the recorded result and remaining surface-specific checks.
