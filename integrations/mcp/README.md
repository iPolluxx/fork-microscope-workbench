# Fork Microscope MCP integration

Read saved investigations, open the interactive evidence viewer, and operate explicitly approved bounded workflows through an MCP-capable client.

- [Private local server](USAGE.md): one profile, stdio, optional Secure MCP Tunnel and an existing worker.
- [Managed pilot](HOSTED-BYOK.md): authenticated account, immutable plan, independent RunPod controller and private evidence library.
- [Viewer validation](VIEWER-VALIDATION.md): graph, token context, contrasting saved text and original-byte verification.
- [Portable plugin](../chatgpt-plugin/README.md): manifest, setup/investigation skills and private launcher/package helper.

The [showcase](../../docs/chatgpt/README.md) includes actual deployed saved-evidence screenshots and a reproducible public example. Viewing saved results makes no model calls. New paid runs need fresh provider readiness and exact-plan approval.

FastMCP is a separate runtime requirement; installing the workbench alone does not install this adapter. No credentials, profiles, private investigations or model weights are distributed. [Dependencies](PROVENANCE.md).

From repository root, with the pinned runtime and scientific dependencies installed:

```bash
PYTHONPATH=src:integrations/mcp/src python -m pytest \
  integrations/mcp/tests integrations/chatgpt-plugin/tests -q
node --test integrations/mcp/tests/test_*.mjs tests/test_compute_ui.mjs
```

Real protocol tests use FastMCP and subprocess stdio; offline doubles are tests-only. Report missing-runtime/optional-fixture skips separately. [Validation scope](../../docs/chatgpt/VALIDATION.md).
