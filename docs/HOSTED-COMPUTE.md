# Hosted compute beta

Fork Microscope can coordinate a separate RunPod worker for each invited user. The implementation is **opt-in and restricted to invited accounts**. It is not enabled merely by publishing the static site. Real Firebase, Firestore, Secret Manager, Drive, and RunPod acceptance is a separate deployment check.

## For a researcher

1. Open **Compute** and sign in with your invited Google account.
2. Connect a dedicated RunPod API key once. Your own RunPod account pays for compute. The app stores the key in a server vault, not in browser storage or the worker.
3. Return to **Investigations → New investigation**, select **Managed RunPod session**, and choose **Google Drive** or **This device** for evidence. Drive asks for access to files this app creates; it can save while your phone is closed. Device mode is attended: keep the browser open until evidence is received. IndexedDB is a convenience cache, not a permanent backup. Download the portable file too.
4. Choose the supported model, enter a prompt and at least two answer markers, then adjust sampling under Advanced if needed.
5. Request a quote. Review the exact model revision, GPU, maximum session duration, estimated price and destination. Approve to request paid compute and queue the investigation.
6. The worker generates an original response, samples continuations, reconstructs the outcome graph, and optionally refines eligible intervals using the existing workflow. It does not invent contrasting outcomes.
7. Open **Investigations**, retrieve the account artifact, then open it in Explorer. Drive evidence remains available after the GPU is gone. Device evidence is verified and saved into your account's browser library before the app acknowledges receipt and requests deletion. Export a file before signing out: signing out clears this account's hosted browser cache.

No SSH, worker address, pairing code, or model-control bearer token is needed in this path. Manual workers and offline imports still work separately.

## What the states mean

- **Requested / provisioning:** the controller is allocating your worker. An uncertain provider response is reconciled; it is not blindly retried.
- **Booting:** the container is starting. A provider showing Running is not evidence that the app is ready.
- **Ready / running:** the worker enrolled and is accepting or executing the approved investigation.
- **Saving:** evidence is being exported. This is not a scientific confidence statement.
- **Terminating:** deletion was requested. Billing may continue until the provider removes the resource.
- **Terminated:** provider removal was verified. Drive evidence and downloaded files survive.
- **Interrupted:** execution could not be safely resumed. Completed evidence is preserved when recoverable; the app does not automatically regenerate an uncertain paid operation.

## Boundaries

The hosted catalog initially contains Qwen2.5-1.5B-Instruct at the tested immutable revision. Arbitrary models and J-lens fits remain available through compatible manual workers; hosted lens requests are rejected unless an explicitly approved profile is in the catalog quote. Adding a model requires compatibility and memory testing, not just entering its name.

A dollar allowance constrains the accepted estimate. Absolute runtime, startup, idle and lease deadlines govern cleanup. Provider outages, billing delays and deletion failures mean this is **not a guaranteed dollar cap**. The controller runs independently of the phone and worker. A local watchdog stops worker execution, but only provider deletion stops VM billing.

Google Drive full/revoked/unavailable: reconnect before running. If export fails during a run, retries are bounded by the compute deadline. There is no operator-hosted permanent evidence backup. Device evidence can be lost if the browser cannot receive it before termination. Never describe an unfinished upload as saved.

The graph estimates downstream outcome distributions under recorded sampling and classification rules. Selected intervals are exploratory; refinement is not a validated cost-savings guarantee. Vocabulary readouts do not establish intent or causation.

## Operator setup

See [deployment instructions](../deploy/hosted/README.md). The API and controller use separate runtime identities. Do not enable the front door until sign-in, ownership, scheduled cleanup, storage, and one budgeted real run pass acceptance.

## Verification

`tests/test_hosted_*.py` covers account isolation, Firestore-interface transactions with a memory adapter, request retries, worker fencing, simulated provider lifecycle, Google OAuth/upload failures, saved-bundle export/import, and cleanup. `tests/test_hosted_client.mjs` covers credential handling, request identity, account changes, bounded downloads and checksums. These are simulated cloud/provider tests and saved evidence replay, not new model measurements.

### Local implementation check — 2026-10-01

411 Python tests passed (3 optional tests skipped); 71 JavaScript tests passed. Chromium at a 390×844 mobile viewport exercised disabled entry, mocked Google sign-in, quote approval, saved-evidence delivery, an interrupted receipt followed by reload/retry, reopening after simulated pod deletion, and the actual Explorer graph. No JavaScript page errors or horizontal overflow occurred in the hosted screen. The API/controller container built locally and both environment validators passed inside it. The GPU worker container builds and its startup dependencies pass a local CPU import check. Deployed Google sign-in, dedicated-database isolation, credential write/read separation, two-account run isolation, and idempotent credential storage have also been checked against real services with disposable test records. A real Qwen GPU run generated 96 tokens, reached its original-response cap, and correctly stopped before scanning. Its partial bundle was checksum-verified, imported into the browser library, and backed up locally; provider deletion was verified. A provider-rejected retry exposed a cleanup-fence bug, now fixed with a regression test: definite rejection can finish cleanup after absence is verified, while uncertain creation remains fenced. A subsequent real scan completed with 8 checkpoints at stride 8, 5 samples per checkpoint (40 continuations), 1,057 continuation tokens, and 29.7 seconds of collection. Four continuations were unresolved by the outcome matcher. The bundle was downloaded and reopened in Explorer after provider deletion, showing the reconstructed graph. Its two fitted candidate intervals are exploratory, not validated significant forks. All test pods were deleted and the provider list verified empty. Google Drive delivery remains unverified pending user consent. The account-isolation tests used disposable records; the GPU generation used the real model.

Follow-up verification: 103 targeted Python tests and 12 hosted-client JavaScript tests passed. The enabled Cloud Run build accepts a configured API upstream and rejects a missing upstream. These tests complement, rather than replace, the real hosted run above.

## Unified interface verification (2026-10-02)

The investigation form is shared with the direct-worker path. Connections and session lifecycle live in Compute; artifact retrieval lives in Investigations. Browser tests of the reorganization use saved evidence and a simulated local worker, not a new paid GPU acceptance. Existing real-run evidence above predates this UI change. A provider account connection, job completion, verified delivery and confirmed provider deletion are distinct states.
