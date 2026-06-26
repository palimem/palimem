#!/usr/bin/env bash
# Phase 2 readiness verification — historical phase gate (v1.3.0 era).
# For the current release gate use demo/phase4-readiness.sh (v1.5.0).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
APP="$ROOT/app"
DEMO="$(dirname "$0")"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
FAIL=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }

echo "=== Component validation (10 runs) ==="
for i in $(seq 1 10); do
  "$PYTHON" "$ROOT/tests/run_validation.py" >/dev/null 2>&1 || { fail "component validation run $i"; break; }
done
[ $FAIL -eq 0 ] && pass "component validation 10/10"

echo "=== Root integration (10 runs) ==="
(
  cd "$REPO_ROOT"
  for i in $(seq 1 10); do
    "$PYTHON" tests/run_integration.py >/dev/null 2>&1 || { fail "integration run $i"; break; }
  done
)
[ $FAIL -eq 0 ] && pass "integration 10/10"

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
node "$APP/scripts/ai-memory.js" consolidate --data-dir "$DATA" --scope repository --namespace readiness --dry-run 2>/dev/null | grep -q '"ok": true' \
  && pass "ai-memory consolidate dry-run" || fail "ai-memory consolidate dry-run"
node "$APP/scripts/ai-memory.js" review list --data-dir "$DATA" --namespace readiness 2>/dev/null | grep -q '"ok": true' \
  && pass "ai-memory review list" || fail "ai-memory review list"

echo "=== Copilot connect ==="
TMP_CFG="$(mktemp)"
echo '{"mcpServers":{}}' > "$TMP_CFG"
"$PYTHON" "$APP/connect_copilot.py" --project-root "$REPO_ROOT" --config "$TMP_CFG" --dry-run 2>/dev/null | grep -q '"memory-service"' \
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
"$PYTHON" -c "
import json, pathlib, sys
comp = json.loads(pathlib.Path('$ROOT/tests/artifacts/latest-results.json').read_text())
passed = sum(1 for r in comp['results'] if r['status'] == 'pass')
assert passed == len(comp['results']) == 62 and comp['spec_version'] == '1.4.1'
integ = json.loads(pathlib.Path('$REPO_ROOT/tests/artifacts/latest-integration-results.json').read_text())
ip = sum(1 for r in integ if r['status'] == 'pass')
assert ip == len(integ) == 8
" && pass "artifacts 62/62 + 8/8" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 2 READINESS CHECKS PASSED"
else
  echo "SOME CHECKS FAILED"
  exit 1
fi
