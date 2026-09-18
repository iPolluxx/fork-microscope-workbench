# Source and dependencies

The tools call `fork_microscope.investigation_bundle.validate_bundle` from Fork Microscope Workbench, commit `5c0231c3d64bb8f167e41fea776e4ff2e578d048`:
https://github.com/iPolluxx/fork-microscope-workbench

Its lazy validation imports also require `forking-paths` from Goodfire's Forking Fast repository at commit `d32fed8d4162a4888291c4b3a38b059727c85a41`:
https://github.com/ericb-goodfire/forking-fast

Both are installed as pinned external dependencies. This archive does not redistribute their source or reference datasets. Consult each upstream repository for its license and terms; this wrapper does not grant additional rights to upstream work.

New wrapper code performs file confinement, source validation, pagination, and exact-byte export. It does not implement or alter Goodfire reconstruction, sampling, or lens calculations.

Conversion used the Paper2Agent/Paper2MCP workflow at commit `c5ce59cc726eddebd6623cc70ad2a80ae55c224e`, with separate source execution, implementation, and verification stages. Paper2Agent is not a runtime dependency.

Workflow tools additionally call the pinned `workflow_cli.Client`, `settings_reference` and `investigation_workflow.validate_config`. Execution remains on the configured worker; wrappers add private profile ownership, explicit resource ceilings and retry handling. No scientific algorithm is reimplemented.
