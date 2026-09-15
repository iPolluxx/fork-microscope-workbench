# GPU worker container

The image contains the CUDA environment, dashboard, worker and pinned upstream checkout. It starts **without a selected model and without downloading weights**. Use Configure to inspect and attach a native model.

## Build and start

Build from the application source directory:

```bash
docker build -t fork-microscope:worker .
```

Choose hardware for your model and continuation context. The earlier Muse profile ran on an A100 80GB; this is evidence for that profile, not a universal hardware requirement. Native model eligibility and weight-memory estimates are available in Configure. See [validation scope](../VALIDATION.md) for tested behavior and GPU limitations.

The entrypoint configures SSH from `SSH_PUBLIC_KEY` (or RunPod's `PUBLIC_KEY`), runs the environment check, links result and workspace storage, and serves on loopback port 8767. For a provider VM, expose SSH and forward the dashboard port:

```bash
ssh -N -L 8767:127.0.0.1:8767 -p VM_SSH_PORT USER@VM_IP
```

Open `http://127.0.0.1:8767`. For a separately hosted dashboard, configure its allowed origin and a worker token as described in [hosted dashboard setup](../docs/HOSTED-DASHBOARD.md). HTTPS provider proxies require internal network binding and should not expose the worker's plain HTTP port directly.

| Runtime variable | Meaning |
| --- | --- |
| `AUTO_LOAD_MODEL` | Default `0`; `1` explicitly enables model attachment at startup. |
| `FORK_MODEL_ID` | Native Hugging Face ID or existing model directory on this worker. |
| `FORK_MODEL_REVISION` | Hub ref, default `main` for a directly selected ID; attachment resolves a commit. For saved local models, the content fingerprint is checked. |
| `FORK_MODEL_PROFILE` | Optional JSON load configuration; no profile is selected by default. |
| `FORK_MODEL_DEVICE` | `auto`, `cpu`, or `cuda`. |
| `FORK_MODEL_BATCH_SIZE` | Concurrent continuations, 1–128; affects memory. |
| `FORK_WORKER_HOST` | Default `127.0.0.1`. Use `0.0.0.0` only behind your authenticated HTTPS access path. |
| `FORK_DASHBOARD_ORIGIN` | Exact allowed website origin, such as `https://dashboard.example.org`. |
| `FORK_WORKER_TOKEN` | Worker access secret, required for network/origin access. Never a provider API key. |
| `HF_TOKEN` | Optional worker-side model access; do not put in image build arguments or browser settings. |

Explicit startup attachment never starts prompt generation or sampling. The build fetches the pinned public upstream source, records SHA-256 hashes, and removes its Git metadata. The environment check validates those hashes in the container. Host Git history, agent files, credentials, models and personal results are excluded.

Changing model metadata at build time can reuse dependency layers; model weights are never baked into this image. No cold-start savings are claimed without measurement.

## Persistent or disposable storage

- `/workspace/huggingface`: model cache.
- `/workspace/live-runs`: raw observations, run manifests and results.
- `/workspace/workspace-data`: prompt sets and immutable batch snapshots.

Lens and other investigation artifacts live in `/opt/fork-microscope/investigations/`
inside the container. The current entrypoint does **not** link it into `/workspace`. Export those
JSON artifacts separately or explicitly mount that directory too; mounting
`/workspace` alone does not preserve lens readouts.

Mount `/workspace` if you want the three directories above to survive replacing the container. Otherwise export evidence and prompt sets in the dashboard, or copy both data directories before terminating a disposable VM. Cache download, completed evidence and unexecuted drafts are distinct artifacts.

## Distribution status

The pinned Goodfire checkout has no LICENSE file. [THIRD-PARTY.md](../THIRD-PARTY.md) records the unresolved public-redistribution question. No public bundled GPU image is provided. Build locally only as appropriate under upstream terms; this project's MIT license does not relicense the dependency. The static dashboard excludes upstream Python code and datasets.
