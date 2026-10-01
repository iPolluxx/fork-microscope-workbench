#!/usr/bin/env bash
set -euo pipefail
cd /opt/fork-microscope
: "${FM_SESSION_ID:?Missing session}"
: "${FM_ENROLLMENT_TOKEN:?Missing one-use enrollment}"
: "${FM_CONTROL_PLANE_URL:?Missing controller}"
: "${FM_EXPIRES_AT:?Missing deadline}"
: "${FM_DASHBOARD_ORIGIN:?Missing dashboard origin}"
mkdir -p /workspace/live-runs /workspace/workspace-data /workspace/workflow-jobs /workspace/investigations /workspace/exports
# Upstream has no redistribution grant in its pinned checkout. Fetch the public
# revision on the user's worker instead of republishing it in our image.
readonly upstream_pin=d32fed8d4162a4888291c4b3a38b059727c85a41
if [[ ! -d vendor/forking-fast/.git ]]; then
  git clone --no-checkout https://github.com/ericb-goodfire/forking-fast.git vendor/forking-fast
fi
git -C vendor/forking-fast checkout --detach "$upstream_pin"
[[ "$(git -C vendor/forking-fast rev-parse HEAD)" == "$upstream_pin" ]]
python src/fork_microscope/upstream_snapshot.py vendor/forking-fast --revision "$upstream_pin" --write
uv pip install --python .venv/bin/python --no-deps ./vendor/forking-fast/otrecon ./vendor/forking-fast/forking_paths
exec python -m fork_microscope.hosted.worker
