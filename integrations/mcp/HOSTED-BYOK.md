# Managed compute through ChatGPT

The managed adapter is separate from the local stdio server. It needs an authenticated hosted API, canonical OAuth/account mapping, independent controller, immutable worker image and durable private evidence library. Publishing a static dashboard alone does not enable it.

## Saved evidence

List investigations, then pin the returned bundle ID and file SHA-256 for reporting, inspection, rendering and byte retrieval. These reads need no worker or provider check. An empty library is a successful connection, not a completed experiment.

`fork_microscope_import_investigation` returns a secure first-party handoff. The user chooses original JSON bytes and explicitly submits them to the authenticated API. Bytes and credentials never belong in chat arguments. Imports validate format, size and hash, preserve retry identity, and retain `source=user_file`, `execution_verified=false`. Import does not certify execution or cleanup.

The pilot limits files to 16 MiB, up to 64 items/1 GiB raw evidence, and thirty-day retention for its own manifests. Generic volume-backed assembly requires its own confirmed persistence policy. Export a private backup before expiration.

## New investigation

| Step | Tool | Required evidence |
| --- | --- | --- |
| Setup | `fork_microscope_compute_setup` | Key entered only on authenticated setup page |
| Check | `fork_microscope_compute_status(refresh_account=true)` | Fresh provider read status; no implied pod write permission |
| Prepare | `fork_microscope_prepare_plan` | Exact config, pinned model/image, hardware, rates, duration, destination and expiry |
| Approve | `fork_microscope_approve_plan` | Explicit execution confirmation of unchanged ID/digest |
| Observe | `fork_microscope_compute_status` | Real phase/counts and controller warnings |
| Save | `fork_microscope_save_hosted_evidence` | Owned artifact, original hash/size/schema/identity and durable readback |
| Finish | `fork_microscope_finish_compute` | Delete request followed by provider-confirmed `cleanup.verified=true` |

Approval atomically queues one session and one job. Stable request identities protect uncertain retries; do not launch a duplicate after a timeout. Expired quotes need fresh review. Material price, hardware, permission or risk changes require reassessing approval. Runtime must leave startup/export reserves inside session duration.

Requested allowances, estimates and deadlines are not hard billing caps. `fork_microscope_add_money` is a user-completed official billing handoff, never an assistant payment API. Balance can be unavailable; spending history is not prepaid balance.

Device-mode evidence can be lost at the compute deadline. A verified private-server save is separate from a device download. Cancel cooperatively stops generation; it does not delete a GPU. Finish only the approved app-owned session, acknowledge unsaved loss when authorized, and supervise failed/unverified cleanup until provider absence is confirmed. This path creates no network volume and deletes no unrelated resources.

## Operator requirements

[Hosted service setup](../../deploy/hosted/README.md) covers authentication, private vault, Firestore, controller, scheduler and worker image. MCP additionally requires a real OAuth provider with issuer/audience/scope/expiry and authoritative revocation checks; canonical hosted identity mapping; exact HTTPS setup/resource URLs; explicit invited-user boundary; and durable storage verified independently of the deployment flag.

`scripts/build_pilot_context.py /path/to/new/empty/context` produces an allowlisted API/MCP source context. It performs no deployment and copies no credentials, evidence or model weights. The model worker is separate; public redistribution of bundled upstream code still requires resolving its license. [Architecture](../../docs/chatgpt/ARCHITECTURE.md), [third-party terms](../../THIRD-PARTY.md).

Run fake-provider, ownership, approval/recovery and real protocol tests before deployment. Test real OAuth/saved evidence separately from explicitly approved paid execution. The private pilot does not establish independent-user production acceptance. [Current validation scope](../../docs/chatgpt/VALIDATION.md).

The public `pilot_cloud.py` factory is locked to the example project `fork-microscope-pilot` and database `fork-hosted`. Review and set the approved private project binding in source before deploying your own pilot; this publication intentionally omits the operator's real project identifier. The fixed project/database guard remains fail-closed.
