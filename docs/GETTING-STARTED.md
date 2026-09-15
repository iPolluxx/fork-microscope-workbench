# Your first Fork Microscope session

[All documentation](README.md) · For a visual overview, open **Guide** from Workspace.

**Already have an investigation bundle?** Use the CPU setup below, start the worker, then open **Explore → Import evidence**. You can browse its saved runs and lens readouts without loading a model. To collect new evidence, follow the full sequence below.

Fork Microscope has two pieces:

```text
Fork Microscope website  ── private connection ──>  your worker  ──>  your model and GPU
      controls and graphs                                  runs the work       stay on your computer or VM
```

The website is the control panel. It does **not** contain a shared model or GPU.
Your **worker** is a small Fork Microscope program that you run on your own
computer or GPU VM. It loads the model, generates continuations, and saves the
results there. You remain in control of the hardware bill and the prompt data.

You do not need to understand the networking details before starting. The worker
starts in the background and prints a one-time pairing code. Paste it into the website’s
**Connect a machine** dialog. Manual address and token entry remains under Advanced.

## Before you start

Choose where the model will run:

| If you have… | Use it for… | Setup command |
| --- | --- | --- |
| A Linux computer with no NVIDIA GPU | Reading saved runs or trying a small model | `./scripts/setup.sh cpu` |
| A Linux computer or GPU VM with an NVIDIA GPU | Running checkpoint scans on models that fit its GPU | `./scripts/setup.sh cuda` |

The tested installation is Linux x86-64 with Git and
[uv](https://docs.astral.sh/uv/getting-started/installation/). macOS and Windows
are not yet tested release paths. Muse-Glimmer-30B previously required an A100
80 GB; do not try it on an ordinary laptop just to see the interface.

## 1. Install Fork Microscope where the model will run

Open a terminal **on the computer or VM that has the hardware**. Then run:

```bash
git clone --recurse-submodules https://github.com/iPolluxx/fork-microscope-workbench.git
cd fork-microscope-workbench
./scripts/setup.sh cuda
```

Use `cpu` instead of `cuda` when that machine has no NVIDIA GPU.

This creates a Python environment and checks the installation. It does **not**
download a model. An already-running rented VM may be billed while you install and configure software. The repository checkout is required;
a source ZIP does not include the upstream submodule.

## 2. Start and pair your machine

On the machine with the evidence or GPU, run this from the installed repository:

```bash
.venv/bin/fork-microscope machine start \
  --dashboard-origin https://fork-microscope-wzyjs4vwsq-uc.a.run.app
```

For your own hosted dashboard, set `FORK_DASHBOARD_ORIGIN` or pass
`--dashboard-origin`; the explicit flag takes precedence over the environment.
With neither set, the default is the public Fork Microscope website.

It starts a background worker on port **8768** and prints a **pairing code** beginning
with `FM1.`. Copy the entire code. In the website choose **Connect a machine →
One-time pairing code → Pair this machine**. You no longer need to copy an address
and long-lived token separately. The code is private, valid for ten minutes and
usable once. It is a copy-and-paste code, not a six-digit relay code.

Connecting does not download a model or launch GPU work. On Linux with a user
systemd session, the worker runs as a managed background service. On a Linux VM
without that session, it runs as a detached process. Both survive closing the
terminal; neither setup promises startup after reboot. Keep the machine online.

### Phone access or a remote VM

Install **cloudflared** from [Cloudflare's official installation guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/),
then add `--share` to the startup command:

```bash
.venv/bin/fork-microscope machine start --share \
  --dashboard-origin https://fork-microscope-wzyjs4vwsq-uc.a.run.app
```

Run it on the machine holding your model or evidence. It creates an authenticated
worker behind a temporary Cloudflare HTTPS tunnel and prints the same kind of
pairing code. No port forwarding or cloud-provider API key is needed. Cloudflare
carries the connection and terminates its public TLS; do not treat it as an
end-to-end private relay. The tunnel has no uptime guarantee and its address changes
after a restart. The supervisor retries a failed tunnel; **generate a new pairing
code afterward**. There is no permanent hosted relay or automatic VM provisioning.

Already have HTTPS forwarding to the worker? Use `--public-url https://YOUR-WORKER`
instead of `--share`. The endpoint must forward to loopback port 8768; configure
that outside this command. No model compatibility is implied by successful pairing.

### Manage the background worker

```bash
.venv/bin/fork-microscope machine status
.venv/bin/fork-microscope machine pair   # new code; invalidates an unused old code
.venv/bin/fork-microscope machine stop
```

If you started with a different `--port`, supply it to every management command.
Stopping a worker does **not** terminate a rented VM or stop its bill. Export your
investigation before deleting temporary compute. Status shows the log location;
for managed services use `journalctl --user -u fork-machine-8768`.

Connections remain in session storage, shared with other open tabs of the same
website. Closing all tabs, using another browser, or changing the worker address
requires pairing again. Runs stay on the worker: changing machines changes your
visible library. Import a saved bundle to browse it on another machine without a GPU.

## 3. Advanced or existing workers

Existing `fork-microscope connect` and `serve` commands still work. In **Connect a
machine → Advanced: worker URL and access token**, enter their existing details.
The worker token is not a RunPod or Hugging Face key.

For an SSH connection, forward the worker port from your browsing computer:

```bash
ssh -N -L 8768:127.0.0.1:8768 USER@VM_HOST
```

A localhost address works only from that computer or SSH tunnel, not directly
from your phone. If your browser blocks a hosted website's localhost access, use
the local dashboard and allow that dashboard's exact origin in the startup command.

## 4. Load a model

After connection, the app opens **Configure → Model**.

1. Enter a Hugging Face model ID, such as `organization/model-name`, or choose
   **Local directory on my worker** and enter a path on the worker.
2. Click **Inspect model requirements**. This checks the model format and shows
   what the worker can tell you about its hardware.
3. Click **Load model** after inspection succeeds.
4. Continue to **Question**.

The model runs where the worker runs. A local directory is a path on that worker,
not an upload from your browser. For a private or gated Hugging Face model, sign
in to Hugging Face on the worker before loading; never paste a Hugging Face token
into Fork Microscope's connection form.

Fork Microscope currently supports native Transformers safetensors text models
and a specialized Muse adapter. GGUF/Ollama, ordinary chat APIs, custom remote
code, and pre-quantized checkpoints are not supported attachment routes.

## 5. Run a small first scan

In **Question**, write a prompt and provide answer texts that are easy to spot in
a completed reply. A good first prompt asks the model to end with exactly one
short marker, for example `DECISION=LAUNCH` or `DECISION=DELAY`.

Click **Generate original response**. That one response becomes the fixed path
that Fork Microscope will inspect. Stay in Question to read the response and its
matched outcome. An unfinished response cannot be scanned: increase its base
token cap or revise the prompt, then generate again. If no outcome or several
outcomes match, adjust the answer texts or explicitly choose to explore `Other`.
Choose **Response reviewed · choose scan settings** when you are ready.

In **Scan**, the initial region covers the full original response. Start small:
widely spaced checkpoints, 5–20 draws per checkpoint,
and a continuation cap that fits the kind of reply you asked for. Check the budget
before you click **Run checkpoint scan**.

- **Checkpoint spacing** means how far apart the sampled positions are in the
  original response.
- **Draws per checkpoint** means how many alternate continuations are generated
  from each sampled prefix.
- **Continuation cap** is the maximum new-token length of each continuation.

The answer matcher looks for literal text. It is not a truth or quality judge.
Replies that match multiple labels, no labels, or do not finish are placed in
`Other` so you can read them yourself.

Progress separates continuations saved from the token currently being generated.
A rough remaining-time estimate appears after two continuations finish. Different
continuation lengths can change that estimate. Model loading reports weight
download progress where available, then memory loading as a separate stage.

## 6. Follow the evidence

When the scan finishes, open **Explore**:

1. **Scan** the graph for checkpoints or candidate intervals where recorded
   outcomes change.
2. **Compare paths** to read two completed continuations from the same checkpoint
   that have different recorded outcomes.
3. **Look inside** to send that exact pair into a compatible Jacobian lens readout
   on your worker. This is evidence to investigate, not proof of a causal model
   mechanism.

The lens view starts around the pair’s **first different saved token**, which can
be later than the graph checkpoint. Its preceding token is the shared baseline;
if the paths differ at response token 0, **Inspect prompt baseline** selects the
last saved prompt token instead. Each cell describes the state **after** its
input token. The final-layer column predicts the **next** token. A text difference
does not establish the location or cause of an internal decision.

Start with the five-layer exploration preset, then use **Expand token window**
or **Add layer detail**. These controls only change the selection; preview and run
remain separate. **Continue from these settings** restores a saved original or
continuation readout so you can expand it. Layers are sampled broadly, without
assuming that a fixed percentage of Muse’s depth is its workspace.

Preview estimates model forwards and prefix tokens to replay. The worker reuses
previously captured activations for identical causal prefixes and layers in the
same loaded model session, with a 32 MiB CPU cache. Repeating or narrowing a view
can avoid another model forward; requesting uncaptured positions or layers may
still require one. The resulting artifact records actual forward counts and
reused rows. Lens-file verification and vocabulary projections still run. Cache
eviction, model reloads or parameter changes require fresh computation; reuse is
disabled for length-dependent rotary embedding modes. Saved JSON readouts remain
available independently of this temporary cache.

Use **Sample more closely** when you want denser checkpoints in a promising
interval. Export your evidence before terminating an ephemeral VM. A reference
run is still a finite sample, not ground truth.

**Export evidence** includes the original trace, token IDs, continuations,
settings, and computed results. Wait for the file to be prepared and click the
**Save** link that appears. Your browser handles the download location. Confirm
the file is on your computer before deleting your
worker. **Import run** restores that file to another worker without resampling.

## Updating your installation

Wait for the active job to finish, export any evidence you need, and stop the
worker with Ctrl+C in its terminal. From your existing checkout:

```bash
git pull --ff-only
git submodule update --init --recursive
uv pip install --python .venv/bin/python --no-deps --editable .
uv pip install --python .venv/bin/python --no-deps -r requirements/lens.txt
.venv/bin/fork-microscope doctor
.venv/bin/fork-microscope connect \
  --dashboard-origin https://fork-microscope-wzyjs4vwsq-uc.a.run.app
```

The editable reinstall updates the CLI's module registration, including new worker
modules. If the dependency lockfiles changed, run `./scripts/setup.sh cpu` or
`./scripts/setup.sh cuda` before `doctor` instead of the two `uv pip install`
commands above. A container-based worker needs an image rebuilt from the new
checkout and a replacement container; preserve its evidence first.

Refresh the website and reconnect with the token printed by the restarted worker.
If `FORK_WORKER_TOKEN` is set, the launcher reuses it; otherwise it generates a new
one. Reload your model when needed. Saved files stay on the worker, but the loaded
model, unsaved original response and temporary activation cache do not survive a
restart. Export lens/investigation JSON separately: a normal run export does not
include those artifacts. See [storage details](INTERPRETING-RESULTS.md#storage-and-deployment).

## Need help?

[Open an issue](https://github.com/iPolluxx/fork-microscope-workbench/issues/new/choose)
with your OS, model ID and revision, hardware, the step that failed, and sanitized
errors. Never include a worker access token, private model credential, or private
prompt/result data.

## Complete investigations

Run a bounded scan → refinement → optional J-lens workflow from the CLI, an agent, or the dashboard, then export related runs and readouts as one file. See [the investigation workflow guide](INVESTIGATION-WORKFLOW.md).

For optional NNsight capture and controlled internal-state experiments, follow
[From a fork to an internal-state experiment](NNSIGHT-INTEGRATION.md) after your
first scan. These controls reuse your connected compute; they do not connect an AI agent.

## Saved runs in a new tab

Saved runs live on the worker, not inside a browser tab. For your local library,
start the default loopback viewer with `.venv/bin/fork-microscope serve --port 8767`
and open `http://127.0.0.1:8767/observatory.html`. A plain local viewer needs no
model, GPU, or token unless you explicitly configure `FORK_WORKER_TOKEN`. Do not
reuse network-worker credentials merely to browse your local files.

Authenticated connections are shared between open tabs of the same website,
without storing tokens in localStorage. A different browser or website origin,
or reopening after all tabs close, may need **Connect compute** again. The top
bar identifies the evidence source. An authentication error means reconnect; it
does not mean the files disappeared. Network workers still require their token.

## Keep your place while inspecting

In Explore, choose a saved run and checkpoint. The outcome map remains available
as a collapsible ribbon while you compare paths or inspect saved internal readouts.
Raw counts and intervals describe sampled outcomes; a fitted change point is a
candidate region to investigate, not proof of a hidden decision or its cause.

In Workspace, collapse prompts you are not editing. Unsaved changes are marked;
Ctrl+S (Cmd+S) saves the set to the connected worker. Without a reachable worker,
the save action offers a portable file instead. It does not claim that file is
stored on your worker. You can hide and reopen the introductory help.

Configure reports the hardware the worker actually exposes. A CPU warning is
not a measured runtime estimate, and GPU availability is not a guarantee that
your selected model fits. Model inspection and loading remain separate actions.
