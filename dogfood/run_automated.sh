#!/usr/bin/env bash
# Automated dogfood: setup + MCP/static/none probe matrix (no manual Cursor chat).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
TRANSPORT="${DOGFOOD_TRANSPORT:-python}"
STRICT="${DOGFOOD_STRICT:-1}"

echo "== Automated dogfood =="

bash "$ROOT/dogfood/setup.sh"

echo ""
echo "== Probe matrix (mcp + static + none) transport=$TRANSPORT =="
ARGS=(--mode all --transport "$TRANSPORT" --output "$ROOT/dogfood/artifacts/latest-probe-results.json")
if [ "$STRICT" = "1" ]; then
  ARGS+=(--strict)
fi
"$PYTHON" "$ROOT/dogfood/automated_probe.py" "${ARGS[@]}"

if [ "${DOGFOOD_DOCKER:-0}" = "1" ]; then
  echo ""
  echo "== Docker MCP transport =="
  docker build -t palimem-mcp:local -f "$ROOT/Dockerfile" "$ROOT"
  DOGFOOD_DOCKER_IMAGE=palimem-mcp:local DOGFOOD_TRANSPORT=docker \
    "$PYTHON" "$ROOT/dogfood/automated_probe.py" --mode mcp --transport docker --strict
fi
