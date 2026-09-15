# Hosted dashboard, personal compute

[Documentation index](README.md)

Fork Microscope separates a static website from an independently owned worker. The website serves HTML, JavaScript and plotting assets. Each visitor connects their own worker, which loads the model, generates continuations and stores prompt sets and evidence. Hosting the website does not provide GPU compute or a central user account system.

## Publish the static interface

From the repository:

```bash
python3 scripts/build_dashboard.py
```

The build also creates `dist/fork-dashboard.zip` containing the same explicit assets.

Publish **only `dist/dashboard/`** to your static HTTPS host. Its index is the Workspace. This build uses an explicit asset list: model weights, personal run records, prompt sets, environment files and credentials are excluded. Keep the dashboard and worker on the same release; protocol compatibility across future versions is not promised.

For GCP, this folder is the frontend deployment artifact. A static host or a web-server container behind HTTPS can serve it; cloud deployment is separate from the GPU worker. This change does not provision GCP infrastructure or guarantee a free bill. Check the chosen service's current storage, request and egress terms before publishing. Do not deploy the inference worker as a shared public API.

For the prepared Google Cloud Run deployment, see [the static deployment guide](../deploy/cloud-run/README.md). It builds a small web-only image; the GPU worker Dockerfile is not used.

## Connect a local computer

The simplest installation remains:

```bash
.venv/bin/fork-microscope serve --port 8767
```

Open `http://127.0.0.1:8767`. It uses that local worker without a separate connection step.

The easiest hosted connection is `.venv/bin/fork-microscope connect --dashboard-origin https://YOUR-DASHBOARD-DOMAIN`. It generates (or reuses) a token and prints the details. See [getting started](GETTING-STARTED.md).

To configure the same settings manually, generate a worker token and allow the **exact** dashboard origin:

```bash
export FORK_WORKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/fork-microscope serve --port 8767 --allow-origin https://YOUR-DASHBOARD-DOMAIN
```

Enter `http://127.0.0.1:8767` and that worker token in **Connect compute** on the hosted page. The token belongs to this Fork Microscope worker; it is not a RunPod, GCP or Hugging Face API key. Display it locally when you need to copy it; do not put it into shared notebooks or screenshots.

Hosted-to-local connections depend on browser local-network permissions. If the browser blocks this connection, open the local dashboard URL instead. The identical workflow is served by the worker.

## Connect a GPU VM with any provider

Install the same application or launch its GPU container. Connect through either method:

1. **SSH tunnel:** run the worker on loopback with your hosted origin allowed, then forward its port: `ssh -L 8767:127.0.0.1:8767 USER@YOUR-VM`. Enter the localhost URL and worker token in the dashboard. SSH authentication remains with your SSH client.
2. **HTTPS endpoint:** put the worker behind a TLS reverse proxy. Start it with `--host 0.0.0.0 --allow-origin https://YOUR-DASHBOARD-DOMAIN` and `FORK_WORKER_TOKEN` set. Expose only the HTTPS proxy publicly; keep the plain HTTP worker port private. Enter the HTTPS origin and worker token. Do not use a raw public HTTP URL.

The container accepts `FORK_WORKER_HOST`, `FORK_DASHBOARD_ORIGIN`, and `FORK_WORKER_TOKEN`. Default binding remains loopback and model autoload is off. If using an HTTPS provider proxy, bind the internal worker to `0.0.0.0`, expose port 8767 through that proxy, and configure its access restrictions appropriately. No provider API key is needed in the website. The app does not provision or terminate your VM.

## Data and permissions

- The worker token grants access to that worker's model controls, filesystem model paths, jobs and saved evidence. Use a separate worker per person or trusted team. This is not tenant isolation inside one worker.
- Allowed origins are explicit; wildcard access is refused. API requests require a bearer token when network/origin access is enabled or a worker token is explicitly set. CLI calls without an Origin still require the token.
- The browser retains the selected worker URL and token in **tab session storage**, so page navigation keeps working. Disconnect removes them. They are not included in run or prompt-set exports.
- Lens and text/activation investigations live in `investigations/` and must be exported separately from run evidence. The current GPU container does not link this directory into `/workspace`; see [container storage](../docker/README.md#persistent-or-disposable-storage).
- Run records live in `live-runs/`; sets and batch snapshots live in `workspace-data/`. Both map into `/workspace/` in the GPU container. Use a persistent mount or export before terminating an ephemeral VM.
- Prompt batches execute sequentially on one attached model. A model load or another batch cannot begin while a job runs. Stop acts on the selected job ID. Completed results survive cancellation; incomplete sampling is retained but not automatically resumed.
- The dashboard has no central accounts or shared gallery. Switching workers switches the workspace being viewed. A hosted frontend operator controls the code that can access the token in that page; only connect from a dashboard host you trust.

## Research workflow

1. Connect your worker and inspect a Hugging Face model ID or local safetensors directory.
2. Attach the model; choose a saved prompt or create a prompt set.
3. Generate and review the original response, then set checkpoint spacing, continuations per point and continuation token cap, then run.
4. Open a completed run in Explore. Inspect raw outcomes alongside the saved Goodfire reconstruction.
5. Choose a candidate interval, or the entire trace, then **Reference preset**. Review its continuation budget before launching. The exact original trace is restored.
6. Open Compare, select the exploratory and reference runs. Comparability checks precede any metrics. Reference measurements are finite samples, not ground truth.
7. Optionally compare completed paths and inspect them with a compatible [Jacobian lens](JACOBIAN-LENS.md). Export evidence, prompt sets and separate investigation JSON before changing or terminating hardware.

A website deployment does not upgrade workers. Follow [Updating your installation](GETTING-STARTED.md#updating-your-installation), then refresh and reconnect.

## Current limits

Native Transformers text generation is the supported attachment route. Ordinary chat APIs, GGUF/Ollama and arbitrary custom remote-code repositories are not interchangeable adapters. Eligible architecture metadata is not evidence that every model has passed GPU tests. The Muse family has a special parser; generic models still need inspection of EOS and answer extraction. The graph measures generated continuations. A separate compatible Jacobian lens can inspect internal vocabulary readouts; it does not extract a complete private chain of thought.

Sampling, saved-trace replay and the audited estimator are real. Fresh edit/control runs and experimental activation capture require the updated worker and dashboard; see [Investigations](INTERPRETING-RESULTS.md). Optional NNsight readouts and controlled native activation patches likewise need both updated components; see [internal inspection](NNSIGHT-INTEGRATION.md). Automatic cloud provisioning, partial-draw resume and central multi-tenant hosting are not implemented. Public-release claims should name these limits.
