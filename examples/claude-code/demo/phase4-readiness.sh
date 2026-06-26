#!/usr/bin/env bash
# Phase 4 readiness gate: validation + integration + profile benchmark.
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
DATA_DIR="$(mktemp -d)"
export GEM_SYSTEM_DATA_DIR="$DATA_DIR"
export MEMORY_SERVICE_VALIDATION_DATA_DIR="$DATA_DIR"
export MEMORY_SERVICE_VALIDATION_CONTROL_DIR="$DATA_DIR/control"
export MEMORY_SERVICE_VALIDATION_READY_FILE="$DATA_DIR/ready.json"
export MEMORY_SERVICE_VALIDATION_SCHEMA_MODE="fresh"
export MEMORY_SERVICE_VALIDATION_NAMESPACE_SEED="palimem"
export GEM_SYSTEM_LAUNCH_COMMAND="python3 app/run_stdio_server.py"
( cd "$REPO_ROOT" && "$PYTHON" tests/run_integration.py >/dev/null 2>&1 ) && pass "integration 8/8" || fail "integration"
rm -rf "$DATA_DIR"

echo "=== Phase 4 profile benchmark ==="
bash "$DEMO/phase4-profile-benchmark.sh" >/dev/null && pass "phase4-profile-benchmark" || fail "phase4-profile-benchmark"

echo "=== Artifact consistency ==="
"$PYTHON" -c "
import json, pathlib
comp = json.loads(pathlib.Path('$ROOT/tests/artifacts/latest-results.json').read_text())
passed = sum(1 for r in comp['results'] if r['status'] == 'pass')
assert passed == len(comp['results']) and comp['spec_version'] == '1.5.0'
integ = json.loads(pathlib.Path('$REPO_ROOT/tests/artifacts/latest-integration-results.json').read_text())
ip = sum(1 for r in integ if r['status'] == 'pass')
assert ip == len(integ) == 8
" && pass "artifacts green" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 4 READINESS CHECKS PASSED"
else
  echo "SOME PHASE 4 READINESS CHECKS FAILED"
  exit 1
fi
