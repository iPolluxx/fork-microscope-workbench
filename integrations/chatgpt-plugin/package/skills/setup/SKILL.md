---
name: setup
description: Check the Fork Microscope connection and explain how to inspect saved evidence or connect existing model compute.
---

Check the connected Fork Microscope tools rather than assuming installation means connectivity. If `fork_microscope_compute_status` is available, use it with `refresh_account=false` for the authenticated managed-compute account. Use `fork_microscope_compute_setup` only for an explicit provider connection check. Otherwise call `fork_microscope_workflow_settings` for the private existing-worker adapter. Call `fork_microscope_list_investigations` for saved evidence. These checks do not start a model job.

For managed compute, use only the returned secure setup URL for RunPod key entry; never collect a key through chat or tool arguments. Explain observed read capabilities, optional prepaid balance and unverified write permissions. The `add_money` tool opens official RunPod billing for the user to complete payment; it neither charges nor confirms funding. Refresh account status afterward. A key connection does not authorize a GPU run. Prepare a complete bounded plan and obtain approval of its exact configuration/quote before provisioning. If these tools are absent, do not imply that the new hosted implementation is deployed.

Explain the current state in plain language:
- Tools reachable, saved bundles present: offer to inspect one. No GPU is needed.
- Tools reachable, no bundles: if `fork_microscope_import_investigation` is available, use its returned first-party import URL. The user chooses the original native JSON file there while signed in; never put file bytes, paths, remote URLs or credentials in chat/tool arguments. Otherwise explain the machine-host import for their private MCP server. Imported and synthetic evidence are labeled; import verifies file consistency rather than platform execution or cleanup.
- Worker absent: saved evidence still works; new generations need an existing Fork Microscope worker configured locally. An AI assistant is not the model worker.
- Tools unavailable: stop tool retries after one fresh attempt. Explain that the private MCP server/tunnel must be running on its host; installation does not start that host. Do not present cached evidence as a live response.

Never ask for API keys or worker tokens in the conversation. Local setup uses private token files. Local operators should follow `integrations/chatgpt-plugin/README.md` in their checkout; do not assume a particular machine path, app ID, or hosted tenant. In ordinary ChatGPT without local computer access, describe the operator action rather than claiming to perform it.

Show one next action matched to the state. Do not provision a VM, start/resume a workflow, or purchase compute during onboarding. Authorization for a specific new experiment must come from the user.
