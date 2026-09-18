# Private agent connection (MCP companion)

[Documentation index](../README.md) · [Agent operating guide](OPERATING-GUIDE.md)

## Availability: repository versus companion

This repository includes the optional [MCP companion](../../integrations/mcp/README.md) created with Paper2Agent. It supplies nine tools for private saved evidence and bounded worker workflows. Follow its [installation guide](../../integrations/mcp/USAGE.md).

A clone includes the code, but `pip install .` and browser pairing do not install or configure MCP. Install its separate pinned runtime and initialize a private profile. There is no public MCP URL on the hosted dashboard. The separately prepared full paper reading skill is not redistributed here; consult the original paper and workbench method guides.

## What the companion does

| Tool | Action |
| --- | --- |
| `fork_microscope_list_investigations` | List this profile's local investigation bundles. |
| `fork_microscope_inspect_investigation` | Read saved data through paginated JSON pointers: prompts, token IDs, continuations, fits and collected readouts. |
| `fork_microscope_export_investigation` | Export a local bundle without changing its bytes. |
| `fork_microscope_workflow_settings` | Discover supported fields and profile ceilings; validate a complete configuration offline. |
| `fork_microscope_start_workflow` | Start the existing bounded automatic scan/refinement/optional lens workflow on an already configured worker. |
| `fork_microscope_workflow_status` | Read an owned workflow's progress. |
| `fork_microscope_cancel_workflow` | Request cooperative cancellation. |
| `fork_microscope_resume_workflow` | Explicitly recover an eligible interrupted or failed owned workflow. |
| `fork_microscope_export_workflow` | Download a validated owned investigation into the profile's local library for offline inspection and Explorer import. |

The companion delegates scientific computation to existing Fork Microscope code.
It does not expose every interactive operation as a tool: manual outcome search,
text edits, activation patches and arbitrary capture jobs remain on the app/API.
Its automated configuration supports bounded refinement and at most one paired
J-lens job. Compatible lens profile IDs must come from the worker's Lens options.
Missing or incompatible lenses cannot be repaired by a paper skill.

## How a new user connects

1. **Choose offline or compute.** Offline browsing only needs complete exported
   investigation JSON files. New generation needs a running, compatible worker.
2. **Install the companion and initialize a private profile** using its guide.
   Configure the MCP client to start its Python server over stdio with the
   absolute `FORK_MICROSCOPE_PROFILE` path. No public port is opened.
3. **Add evidence or configure compute.** Copy exported bundles into that
   profile's `evidence/` directory. For compute, use the companion's
   `configure.py worker` command with a worker origin and a private token file.
   HTTPS is required except for loopback HTTP. A pairing code is not the worker
   bearer token, and browser pairing does not configure an MCP profile.
4. **Start a new agent session** and ask it to list saved investigations or show
   workflow settings. Settings discovery and syntax validation do not load a model.
5. **Review an execution configuration and limits**, then authorize a start.
   Preserve the returned workflow ID and reuse the same request ID and unchanged
   configuration after an uncertain network response.
6. **Export the result**, then use Explorer's **Import evidence** action to open
   the downloaded bundle. The MCP library and browser storage do not synchronize
   automatically. Saved evidence can be browsed without keeping the GPU running.

The connection chain is: **agent → local MCP process → your compute worker**.
The dashboard is another client of that worker, not the place the model executes.
The paper skill supplies methodology knowledge; it neither runs compute nor gives
access to anyone's investigations.

## Ownership and limits

Each profile has separate evidence, exports, worker credentials and durable job
ownership records. Tools cannot switch profiles or supply another worker URL.
Status/cancel/resume/export reject jobs not started by that profile on its current
worker before making a network request. Importing a bundle gives read access to
that file; it does not grant control over its original remote job.

Two agents using the same profile intentionally share access. This is local
profile isolation, **not hosted multi-tenancy or an OS security sandbox**. Use
separate OS accounts/containers and separate workers/tokens for mutually untrusted
users. Anyone holding a worker token can access that worker outside the companion.
Evidence read by an agent may be sent to its model provider. See [Security](../../SECURITY.md).

Start and resume require `confirm_execution: true` after user authorization.
Time/sample/generated-token limits must stay within local profile ceilings.
Cancellation is cooperative; these are not provider-dollar caps. No tool provisions
or stops a VM. Back up the private profile and ownership ledger as well as exported
evidence; do not commit either to Git. A lost ledger is not reconstructed by
importing bundles.

## Validation and scientific interpretation

The companion was independently checked on 2026-09-17 with 26 module tests and
16 real stdio acceptance cases across all nine tools in development, a clean
runtime, and an extracted ZIP. Saved-evidence exports were compared byte-for-byte;
worker exports were compared with native coordinator payloads. Those full conversion tests are separate from this checkout. Portable profile/privacy and nine-tool discovery smoke tests ship in `integrations/mcp/tests/`.

Lifecycle tests used the real pinned coordinator and an authenticated local HTTP
fixture with the upstream simulated model service. They did **not** execute a
pretrained model, spend GPU credit, validate live Muse lens quality, or certify
multi-tenant security. The companion pins app revision
`5c0231c3d64bb8f167e41fea776e4ff2e578d048`; later app changes require compatibility
checks and a new companion validation. Its Linux/Python runtime requirements and
verification reports belong to that delivery.

The reviewed paper skill is a reading aid with documented source ambiguities,
not a reproduction of the paper. Neither MCP transport tests nor paper extraction
establish statistical calibration, cost savings, hidden intent or causation.
Use [method interpretation](../REFERENCE.md), inspect raw continuations and
matching rules, and retain [lens limitations](../JACOBIAN-LENS.md).
