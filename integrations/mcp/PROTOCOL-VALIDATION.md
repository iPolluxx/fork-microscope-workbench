# Model-free MCP protocol checks

Real tests use FastMCP 4.0.3 and MCP 2.2.0 with the public `Client` and subprocess `PythonStdioTransport`. Checks cover automatic/legacy negotiation, discovery, schemas, saved-data inspection/reporting, UI-resource metadata and byte-identical export. Offline adapter doubles are explicitly tests-only.

The local runtime reused existing scientific dependencies read-only; [protocol-runtime-lock.txt](protocol-runtime-lock.txt) is not a complete fresh-machine scientific installation result. See [USAGE](USAGE.md) and [current branch checks](../../docs/chatgpt/VALIDATION.md).

`src/discovery.py` is an offline inventory/profile/dependency check. Its `protocol_verified=false` correctly distinguishes it from a real connection. Report missing-runtime skips separately. Supported local subprocess pipes suffice; no public listener, provider request or model inference is needed. Keep profile permissions and host sandbox restrictions intact.
