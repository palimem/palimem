#!/usr/bin/env bash
# Phase 3 readiness gate — historical phase gate (v1.4.x era).
# For the current release gate use demo/phase4-readiness.sh (v1.5.0).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
DEMO="$(dirname "$0")"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
FAIL=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }

echo "=== Component validation ==="
"$PYTHON" "$ROOT/tests/run_validation.py" >/dev/null 2>&1 && pass "component validation" || fail "component validation"

echo "=== Root integration ==="
( cd "$REPO_ROOT" && "$PYTHON" tests/run_integration.py >/dev/null 2>&1 ) && pass "integration 8/8" || fail "integration"

echo "=== Phase 3 smokes ==="
bash "$DEMO/phase3-smoke.sh" >/dev/null && pass "phase3-smoke" || fail "phase3-smoke"
bash "$DEMO/phase3-host-smoke.sh" >/dev/null && pass "phase3-host-smoke" || fail "phase3-host-smoke"

echo "=== Artifact consistency ==="
"$PYTHON" -c "
import json, pathlib
comp = json.loads(pathlib.Path('$ROOT/tests/artifacts/latest-results.json').read_text())
passed = sum(1 for r in comp['results'] if r['status'] == 'pass')
assert passed == len(comp['results']) == 62 and comp['spec_version'] == '1.4.1'
integ = json.loads(pathlib.Path('$REPO_ROOT/tests/artifacts/latest-integration-results.json').read_text())
ip = sum(1 for r in integ if r['status'] == 'pass')
assert ip == len(integ) == 8
" && pass "artifacts 62/62 + 8/8" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 3 READINESS CHECKS PASSED"
else
  echo "SOME PHASE 3 READINESS CHECKS FAILED"
  exit 1
fi
