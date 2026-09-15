#!/usr/bin/env bash
set -euo pipefail
cd /opt/fork-microscope
mkdir -p /workspace/workspace-data /workspace/live-runs /root/.ssh /run/sshd
chmod 700 /root/.ssh
# Official RunPod templates use PUBLIC_KEY; also accept the per-Pod override.
key="${SSH_PUBLIC_KEY:-${PUBLIC_KEY:-}}"
if [[ -n "$key" ]]; then
    printf '%s\n' "$key" >> /root/.ssh/authorized_keys
    sort -u /root/.ssh/authorized_keys -o /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
fi
ssh-keygen -A
/usr/sbin/sshd
if [[ ! -e live-runs ]]; then ln -s /workspace/live-runs live-runs; fi
if [[ ! -e workspace-data ]]; then ln -s /workspace/workspace-data workspace-data; fi
# Keep SSH available for diagnosis when preflight fails; do not load a model.
if ! fork-microscope doctor > /workspace/doctor.log 2>&1; then
    cat /workspace/doctor.log
    echo "Preflight failed. SSH remains available; see /workspace/doctor.log."
    exec sleep infinity
fi
cat /workspace/doctor.log
# Validate before requesting a model. Invalid configuration leaves SSH and UI available.
if python docker/autoload.py --print-config > /workspace/model-startup.json 2> /workspace/autoload.log; then
    python docker/autoload.py > >(tee -a /workspace/autoload.log) 2>&1 &
else
    cat /workspace/autoload.log
    echo "Automatic model configuration invalid; correct it in the dashboard or deployment settings."
fi
serve_args=(serve --port 8767 --host "${FORK_WORKER_HOST:-127.0.0.1}")
if [[ -n "${FORK_DASHBOARD_ORIGIN:-}" ]]; then
    serve_args+=(--allow-origin "$FORK_DASHBOARD_ORIGIN")
fi
exec fork-microscope "${serve_args[@]}"
