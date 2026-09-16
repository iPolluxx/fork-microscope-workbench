<!-- generated: Codex — documentation updated for the investigation revamp, 2026-09-15; authorized by Isaiah. -->
# Cloud Run static dashboard

This is the web interface only. The nginx container contains the explicitly built dashboard assets and license notices. The curated attendance demo includes its saved prompt and evidence. No worker API, inference library, model weights, other private run archives, cloud credentials or upstream Goodfire Python/data enter the deployment. The image runs as the nginx user and listens on port 8080.

## First deployment prerequisites

Use your own billed GCP project with Cloud Run, Artifact Registry and IAM APIs enabled, plus permission to deploy a public service. Create a regional Docker repository named `fork-dashboard` and a service account named `fork-dashboard-web`. Do not grant that runtime account project roles: the website does not call GCP APIs.

```bash
gcloud artifacts repositories create fork-dashboard --project=PROJECT_ID \
  --location=us-central1 --repository-format=docker
gcloud iam service-accounts create fork-dashboard-web --project=PROJECT_ID
```

## Deploy or update

```bash
scripts/deploy_cloud_dashboard.sh PROJECT_ID us-central1
```

Set `GCLOUD_BIN` if the CLI is not on PATH. Git publication is not needed for this deployment. The script builds a fresh static manifest, creates an isolated `dist/cloud-run/` context, builds locally, pushes a versioned image to Artifact Registry using temporary Docker credentials, and deploys the `fork-microscope` service. Only generated static files enter its build context. Source changes are not automatically deployed.

Default settings: `us-central1`, public HTTPS, request-based billing, 1 CPU, 256 MiB, concurrency 80, minimum 0 and maximum 2 instances, no startup CPU boost, 30-second request timeout. The maximum is a scaling setting, not a dollar spending cap. Free allowances are shared across the billing account; image storage, requests and egress may incur charges. See [Cloud Run pricing](https://cloud.google.com/run/pricing).

The service's default HTTPS URL is printed after deployment. Connect a worker through the interface and allow that exact origin on the worker. A different domain needs its own allowed origin. See [worker connection instructions](../../docs/HOSTED-DASHBOARD.md).

## Verify and maintain

- Open the demo, Setup, Explore, Inspect & Test, and Workspace at the returned URL without Google login.
- `/fork-microscope-ready` returns `ok`; `/api/live/status` returns 404 because compute belongs on the separately owned worker.
- Browser tokens stay in tab session storage and requests go directly to the selected worker. No reverse proxy to arbitrary worker URLs is provided.
- The service uses revalidation for static assets so a new deployment does not retain stale unversioned JavaScript. Browser modules use the JavaScript MIME type. Gzip is enabled for JS, CSS and JSON.
- No restrictive upgrade-insecure-requests policy is set: allowed loopback worker connections use HTTP. Browser local-network restrictions still apply.
- For rollback, find a prior ready revision with `gcloud run revisions list --service=fork-microscope --project=PROJECT_ID --region=us-central1`, then send traffic to it using `gcloud run services update-traffic fork-microscope --to-revisions=REVISION=100 --project=PROJECT_ID --region=us-central1`.
- Cloud Run scales to zero when idle; delete the service and its dedicated images/repository when retiring the website. That action is separate from shutting down any user's GPU worker.

Check the ready revision and asset hashes for your own deployment. Hosting a website does not publish its source repository or a GPU worker image.
