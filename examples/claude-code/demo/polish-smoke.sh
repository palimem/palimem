#!/usr/bin/env bash
# Phase 2 polish smoke: copilot merge (dry-run), review export, consolidation CLI.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
APP="$ROOT/app"
DATA_DIR="$(mktemp -d)"
export MEMORY_SERVICE_DATA_DIR="$DATA_DIR"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"

cleanup() { rm -rf "$DATA_DIR"; }
trap cleanup EXIT

echo "== connect copilot dry-run =="
"$PYTHON" "$APP/connect_copilot.py" \
  --project-root "$REPO_ROOT" \
  --dry-run | grep -q '"memory-service"'

echo "== seed belief cluster for consolidation =="
for i in 1 2 3 4 5 6; do
  "$PYTHON" "$APP/hook_remember.py" --data-dir "$DATA_DIR" --quiet --stdin-json <<EOF
{"scope":"repository","namespace":"polish-demo-repo","memory_type":"belief","topic":"noise_cluster","field":"note_$i","value":"low salience note $i","provenance":{"source":"demo","tool":"polish","actor":"test","request_id":"n$i"}}
EOF
done

echo "== run consolidation + review export =="
REVIEW_FILE="$DATA_DIR/review.md"
"$PYTHON" "$APP/run_consolidation.py" \
  --data-dir "$DATA_DIR" \
  --scope repository \
  --namespace polish-demo-repo \
  --export-review "$REVIEW_FILE" \
  --quiet
test -f "$REVIEW_FILE"
echo "review export: OK"

echo "== ai-memory review list =="
node "$APP/scripts/ai-memory.js" review list --data-dir "$DATA_DIR" --namespace polish-demo-repo | grep -q '"ok": true'

echo "== ai-memory consolidate dry-run =="
node "$APP/scripts/ai-memory.js" consolidate --data-dir "$DATA_DIR" --scope repository --namespace polish-demo-repo --dry-run | grep -q '"ok": true'

echo "ALL POLISH SMOKE CHECKS PASSED"
