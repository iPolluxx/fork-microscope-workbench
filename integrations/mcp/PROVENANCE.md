# Source and dependencies

The adapter uses this checkout's native bundle validator, evidence identities, configuration validator and existing-worker client. Install the editable checkout from `integrations/mcp` as documented in [USAGE](USAGE.md); an older external pin can omit current safety behavior.

Real protocol checks used FastMCP 4.0.3, fastmcp-slim 4.0.3 and MCP 2.2.0. [protocol-runtime-lock.txt](protocol-runtime-lock.txt) records the isolated MCP runtime. Existing scientific dependencies were reused read-only; this is not a fresh-machine installation test. `src/requirements.txt` retains the historical complete requirement snapshot.

Forking Paths remains an external reference at `d32fed8d4162a4888291c4b3a38b059727c85a41` from [Goodfire's Forking Fast implementation](https://github.com/ericb-goodfire/forking-fast). No upstream source or reference datasets are copied into the plugin or lightweight API build context. Saved-evidence validation avoids eagerly loading model execution code. Worker dependency and license requirements still apply. [Third-party provenance](../../THIRD-PARTY.md).

Conversion used Paper2Agent/Paper2MCP at commit `c5ce59cc726eddebd6623cc70ad2a80ae55c224e`. Paper2Agent is not a runtime dependency. Fork Microscope's own source uses the repository MIT license; that grant does not relicense upstream code/data or model weights.

Wrappers add confinement, ownership, bounded reads, pagination, retries and byte-integrity checks. They do not reimplement Goodfire reconstruction or claim a new scientific algorithm. [Validation](../../docs/chatgpt/VALIDATION.md).
