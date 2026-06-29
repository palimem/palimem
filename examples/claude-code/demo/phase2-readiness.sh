#!/usr/bin/env bash
# Phase 2 readiness verification — hooks, operator CLI, and portability smokes.
# For the current release gate use demo/phase4-readiness.sh (v1.7.0).
set -euo pipefail

DEMO="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_readiness_lib.sh
source "$DEMO/_readiness_lib.sh"
resolve_repo_paths "$DEMO"
APP="$ROOT/app"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
NODE="${MEMORY_SERVICE_NODE:-node}"
FAIL=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }

echo "=== Component validation (10 runs) ==="
for i in $(seq 1 10); do
  "$PYTHON" "$ROOT/tests/run_validation.py" >/dev/null 2>&1 || { fail "component validation run $i"; break; }
done
[ $FAIL -eq 0 ] && pass "component validation 10/10"

echo "=== Smoke demos ==="
bash "$DEMO/hooks-phase2-smoke.sh" >/dev/null && pass "hooks-phase2-smoke" || fail "hooks-phase2-smoke"
bash "$DEMO/polish-smoke.sh" >/dev/null && pass "polish-smoke" || fail "polish-smoke"

echo "=== Operator CLI ==="
DATA="$(mktemp -d)"
trap 'rm -rf "$DATA"' EXIT
for i in 1 2 3 4 5 6; do
  "$PYTHON" "$APP/hook_remember.py" --data-dir "$DATA" --quiet --stdin-json <<EOF >/dev/null
{"scope":"repository","namespace":"readiness","memory_type":"belief","topic":"cluster","field":"n$i","value":"note $i","provenance":{"source":"test","tool":"readiness","actor":"test","request_id":"r$i"}}
EOF
done
"$NODE" "$APP/scripts/ai-memory.js" consolidate --data-dir "$DATA" --scope repository --namespace readiness --dry-run 2>/dev/null | grep -q '"ok": true' \
  && pass "ai-memory consolidate dry-run" || fail "ai-memory consolidate dry-run"
"$NODE" "$APP/scripts/ai-memory.js" review list --data-dir "$DATA" --namespace readiness 2>/dev/null | grep -q '"ok": true' \
  && pass "ai-memory review list" || fail "ai-memory review list"

echo "=== Copilot connect ==="
TMP_CFG="$(mktemp)"
echo '{"mcpServers":{}}' > "$TMP_CFG"
"$PYTHON" "$APP/connect_copilot.py" --project-root "$ROOT" --config "$TMP_CFG" --dry-run 2>/dev/null | grep -q '"memory-service"' \
  && pass "copilot dry-run" || fail "copilot dry-run"
rm -f "$TMP_CFG"

echo "=== JSONL export/import roundtrip ==="
PORT_DATA="$(mktemp -d)"
PORT2="$(mktemp -d)"
"$PYTHON" "$APP/hook_remember.py" --data-dir "$PORT_DATA" --quiet --stdin-json <<'EOF' >/dev/null
{"scope":"user","namespace":"port-test","memory_type":"fact","topic":"prefs","field":"editor","value":"vim","provenance":{"source":"test","tool":"port","actor":"test","request_id":"p1"}}
EOF
"$PYTHON" "$APP/export_memory.py" --data-dir "$PORT_DATA" --jsonl "$PORT_DATA/out.jsonl" >/dev/null
"$PYTHON" "$APP/import_markdown.py" --data-dir "$PORT2" "$PORT_DATA/out.jsonl" >/dev/null
"$PYTHON" "$APP/hook_search.py" --data-dir "$PORT2" --scope user --namespace port-test --query "editor" 2>/dev/null | grep -qi vim \
  && pass "jsonl roundtrip recall" || fail "jsonl roundtrip recall"
rm -rf "$PORT_DATA" "$PORT2"

echo "=== Artifact consistency ==="
assert_validation_artifacts "1.7.0" >/dev/null && pass "artifacts green" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 2 READINESS CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
  exit 1
fi
