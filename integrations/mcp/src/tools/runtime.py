"""Real MCP runtime only. Offline adapter doubles live exclusively in tests."""
try:
    from fastmcp import FastMCP
except ModuleNotFoundError as exc:
    if exc.name != "fastmcp":
        raise
    raise RuntimeError(
        "FastMCP is not installed; this server cannot speak MCP. "
        "Run src/discovery.py for offline readiness and see USAGE.md."
    ) from exc
