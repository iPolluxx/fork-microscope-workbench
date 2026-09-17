# Security and data ownership

This is a research workbench with one owner or trusted team per worker. It is not a multi-tenant inference service. A worker access token grants control over model attachment, generation, prompt sets and saved evidence on that worker.

## Deployment boundary

- Loopback is the default. Setting a worker token enables token authentication even on loopback.
- Network/origin access requires a URL-safe token of 32–512 characters. Hosted dashboards must be explicitly allowed by origin; wildcard origins are not accepted.
- Use an SSH tunnel or an authenticated HTTPS endpoint. Keep the plain worker HTTP port private, and put request/time/resource limits on a public reverse proxy.
- The hosted dashboard keeps the worker URL/token in tab session storage. Disconnect clears them. The operator of that website controls code that can access the token; use a host you trust.
- No provider API keys belong in the browser. Private-model authentication belongs on the worker. Keep runtime secrets out of Docker build arguments and Git.
- Model loading requires native safetensors and disables custom remote code. These constraints reduce exposure; they do not certify arbitrary model files or third-party libraries as safe.
- Model outputs and imported text are treated as data, not executable HTML or instructions. Exports can contain private prompt/response text; review them before sharing.
- Docker build context excludes host Git metadata, agent configuration, credentials, models and run data. Upstream source is fetched separately at the tested pin and content-verified in the image.

Use one worker process per data directory. Back up completed evidence before terminating disposable hardware. Cancellation is at operation/sampling boundaries; it is not instantaneous partial-run resumption.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting if it is enabled for the repository. If unavailable, open a minimal issue asking the maintainer for a private contact channel, without posting credentials, exploit details or private data. Never include a live worker token in a public report.

The project is a research beta. Automated tests and review do not constitute an independent security certification. Reports should describe the affected revision, deployment mode and a minimal sanitized reproduction.

## Machine pairing

`machine start` generates a private worker token and a single-use, ten-minute
pairing code. The code is a credential: exchanging it grants worker access, not
read-only viewing. Generating another code invalidates the prior unused code;
it does not rotate the persistent token or revoke already paired clients.
The optional temporary Cloudflare tunnel terminates TLS at Cloudflare. It is
not an end-to-end private relay or a permanent connection identity.

## Optional MCP companion

The [separate MCP companion](docs/agents/MCP.md) runs locally over stdio with a
fixed private profile. Tools cannot select another profile or worker. Agents
sharing a profile share its evidence. Profiles are not an OS sandbox: mutually
untrusted users need separate OS accounts/containers and separate workers/tokens.
The worker remains single-owner; its bearer token authorizes access outside MCP
too. No shared hosted authentication or tenant isolation is added to this app.

Evidence sent to an agent can be processed by that agent's model provider; local
storage does not mean the content never leaves the machine. Never commit profile
files, worker tokens, ownership ledgers or private evidence. Published demo
investigations are separate from private profiles.
