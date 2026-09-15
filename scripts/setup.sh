#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-cuda}"
case "$mode" in
  cpu) wheel_index="https://download.pytorch.org/whl/cpu" ;;
  cuda) wheel_index="https://download.pytorch.org/whl/cu128" ;;
  *) echo "Usage: ./scripts/setup.sh [cuda|cpu]" >&2; exit 2 ;;
esac
if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi
git submodule update --init --recursive
uv venv --python 3.13 --allow-existing .venv
uv pip sync --python .venv/bin/python --index "$wheel_index" \
  --index-strategy unsafe-best-match "requirements/$mode.lock"
uv pip install --python .venv/bin/python --no-deps \
  ./vendor/forking-fast/otrecon ./vendor/forking-fast/forking_paths
uv pip install --python .venv/bin/python --no-deps --editable .
uv pip install --python .venv/bin/python --no-deps -r requirements/lens.txt
.venv/bin/fork-microscope doctor
echo "Ready. Run .venv/bin/fork-microscope serve --port 8767, then open http://127.0.0.1:8767/."
