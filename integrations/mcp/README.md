# Private MCP integration

Connect an MCP-capable agent to your own exported investigations and compute worker.

Start with [USAGE.md](USAGE.md) for installation, private profiles, all nine tools, and execution limits. See [PROVENANCE.md](PROVENANCE.md) for pinned dependencies and conversion attribution.

This is an optional separately installed integration. Installing the main workbench does not install MCP. No model weights, private investigations, credentials, or extracted paper figures are distributed here.

The standalone runtime intentionally pins the previously validated workbench revision. Updating that pin requires compatibility testing. Paper2Agent was used to create this integration; it is not a runtime dependency.

## Tests

With the integration environment installed, install pytest into that environment and run:

```bash
.venv/bin/python -m pytest tests -q
```

Tests use temporary empty profiles and an in-process MCP client. No paid compute or pretrained model execution occurs. Earlier lifecycle/round-trip validation is described in USAGE.md; those full conversion fixtures are not shipped here.
