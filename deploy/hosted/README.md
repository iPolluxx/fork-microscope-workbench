# Deploy the invited hosted beta

This is an optional backend, not a change to the statistical method. The existing static Cloud Run site remains usable without it. The checked-in `hosted-config.json` is disabled.

## Components and identities

Deploy the image built from `deploy/hosted/Dockerfile` twice:

- **Public API:** `uvicorn fork_microscope.hosted.bootstrap:create_public_app --factory --host 0.0.0.0 --port 8080`. Firebase user-token and invite authentication on researcher routes; scoped session-token authentication on worker routes.
- **Private controller:** use `create_controller_app` in the same command. No anonymous invoker. The public service account can invoke it; a distinct Scheduler account can invoke only reconciliation. App-level OIDC checks validate the exact audience, issuer, verified service-account email and operation.
- **Firestore:** metadata, idempotency, leases, OAuth state and artifact pointers. No permanent transcript/bundle library. Unacknowledged job configuration is private and is erased on acknowledgement or terminal cancellation.
- **Secret Manager:** provider keys, Drive refresh credentials and upload capabilities. The API can create/write/delete application secrets but cannot read payloads. The controller can read these secrets. Both may read only the separately mounted OAuth client secret. Grant metadata lookup needed for owner-label validation; do not grant broad project Editor.
- **Cloud Scheduler:** every minute, authenticated POST `{}` to `/internal/reconcile` on the private controller. Configure OIDC audience to the controller service origin. Keep it running independently of worker/browser lifecycle.

Use per-resource IAM grants/custom roles for Secret Manager: public writer permissions `secretmanager.secrets.create`, `secretmanager.secrets.get`, `secretmanager.secrets.delete`, `secretmanager.versions.add`, without `secretmanager.versions.access`; controller additionally needs payload access. Firestore server SDK permissions do not enforce user ownership; the application does. Restrict Firestore direct browser access. The public identity also needs `firebaseauth.users.get` (for example `roles/firebaseauth.viewer`), and the Identity Toolkit API enabled for token revocation checks. Verify runtime IAM with two real test accounts before inviting users.

## Configuration

All exact environment variables and validation are in the docstring of `src/fork_microscope/hosted/bootstrap.py` and its `sample_environment()` helper (test values only). Set at minimum:

- Both: `FM_HOSTED_ENV=production`, `FM_GCP_PROJECT`, `FM_PUBLIC_URL`, `FM_DASHBOARD_ORIGIN`, `FM_CONTROLLER_OIDC_AUDIENCE`, `FM_DRIVE_CLIENT_ID`, `FM_DRIVE_REDIRECT_URI`, and OAuth client secret through **one** of `FM_DRIVE_CLIENT_SECRET_FILE` / `FM_DRIVE_CLIENT_SECRET`.
- Public: `FM_FIREBASE_PROJECT_ID`, `FM_INVITED_EMAILS`, `FM_CONTROLLER_URL`, `FM_DRIVE_POST_AUTH_URL`.
- Controller: `FM_PUBLIC_SERVICE_ACCOUNT_EMAIL`, `FM_SCHEDULER_SERVICE_ACCOUNT_EMAIL`, immutable `FM_WORKER_IMAGE=registry/image@sha256:...`, `FM_DISK_USD_PER_GB_HOUR`, `FM_DISK_RATE_SOURCE`.

Optional settings bound the startup allowance, session duration, export reserve, disk size, and heartbeat timeout. Enrolled workers that have not received a job are cleaned up after five idle minutes. Prices must have an explicit current source. Validate with the pure `PublicConfig.from_env` and `ControllerConfig.from_env` functions before rollout. No fake authenticator is auto-selected in production.

Enable Google sign-in in Firebase/Identity Platform. Add the dashboard to authorized domains. Create a Google OAuth web client whose redirect is exactly:

```
https://YOUR-DASHBOARD/api/hosted/v1/connections/drive/callback
```

Request `drive.file` only, with offline consent. Use invited test accounts initially. OAuth Testing refresh tokens can expire after seven days; set appropriate production consent configuration and provide a privacy notice before claiming persistent connections for beta users. Revoked credentials must show a reconnect action.

## Same-origin dashboard/API routing

Serve the API under the dashboard's `/api/hosted/v1/` path so secure HttpOnly OAuth cookies work on mobile without third-party-cookie dependence.

The existing static Cloud Run context builder supports:

```
FM_HOSTED_API_ORIGIN=https://YOUR-PUBLIC-API.run.app python scripts/build_cloud_dashboard.py
```

It generates a fixed-upstream, TLS-verified Nginx proxy for that path only. No arbitrary URL proxy is exposed. API access logs are disabled at Nginx to avoid recording OAuth codes in callback queries; also configure platform request-log exclusions/redaction for the callback and never log authorization headers or payloads. Set `FM_PUBLIC_URL` to the dashboard origin so worker routes and OAuth use this proxy.

Set public `hosted-config.json` to `{ "enabled": true, "apiBase": "https://YOUR-DASHBOARD", "firebase": {...your Firebase web config...} }` before building. Firebase web config is public application metadata; never insert service-account keys, OAuth client secrets, RunPod keys or refresh tokens. The disabled default leaves the local/offline experience unchanged.

## Worker image

Build `docker/Dockerfile.hosted`, record its registry digest, and configure that immutable digest in the controller. It contains dependencies/app code, not model weights, keys, evidence or the unlicensed upstream research checkout. Startup fetches the tested upstream commit on the user's worker. A public upstream checkout is not a redistribution license; obtain permission before changing to a bundled distribution.

There is no SSH or general public execution API. Port 8780 is opened only for device-mode immutable bundle downloads, protected by per-artifact capabilities. Outbound worker polling handles commands. A worker uses `/workspace` for runs, response state, workflow journals, inspection artifacts and exports.

Build and publishing are operator actions; these files do not automatically build, push, deploy or rent hardware.

## Acceptance and operations

1. Run Python and JS tests with `requirements/hosted.txt` installed. Build the static dashboard and both containers.
2. Verify invited/uninvited Google sign-in and cross-account ID swapping against deployed services.
3. Verify Drive consent, interrupted upload, revoked access, checksum-confirmed save, and download after GPU deletion.
4. Verify an approved small RunPod session: clean boot, no model until an approved job, progress, evidence, termination, and empty managed-pod inventory. Record actual provider charges separately from the estimate.
5. Close the phone during a Drive run and interrupt the worker. Confirm the independent Scheduler still cleans up. Test deletion failures with the fake provider first.
6. Check account switching and device cache clearing on a real phone. Keep existing offline imports untouched.

Do not invite users if Scheduler is unhealthy. Alert on `terminating` sessions past deadline, reconciliation failures and stale heartbeats. On failure use the user's RunPod console to verify/delete the specific tagged pod; do not delete unrelated resources. A reported failed launch is not proof that no billable pod exists.

There is no persistent central evidence bucket. Users own Drive evidence and exported files; the operator still handles credentials and necessary private metadata. Metadata retention/Firestore TTL must be configured for OAuth states and terminal/idempotency records, retaining unresolved cleanup records until provider deletion is verified. Do not TTL-delete active sessions.
