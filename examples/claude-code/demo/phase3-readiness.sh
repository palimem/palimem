#!/usr/bin/env bash
# Phase 3 readiness gate — provider smokes + integration catalog.
# For the current release gate use demo/phase4-readiness.sh (v1.7.0).
set -euo pipefail

DEMO="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_readiness_lib.sh
source "$DEMO/_readiness_lib.sh"
resolve_repo_paths "$DEMO"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
FAIL=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }

echo "=== Component validation ==="
"$PYTHON" "$ROOT/tests/run_validation.py" >/dev/null 2>&1 && pass "component validation" || fail "component validation"

echo "=== Integration readiness ==="
bash "$ROOT/integrations/run_readiness.sh" >/dev/null 2>&1 && pass "integration readiness" || fail "integration readiness"

echo "=== Phase 3 smokes ==="
bash "$DEMO/phase3-smoke.sh" >/dev/null && pass "phase3-smoke" || fail "phase3-smoke"
bash "$DEMO/phase3-host-smoke.sh" >/dev/null && pass "phase3-host-smoke" || fail "phase3-host-smoke"

echo "=== Artifact consistency ==="
assert_validation_artifacts "1.7.0" >/dev/null && pass "artifacts green" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 3 READINESS CHECKS PASSED"
else
  echo "SOME PHASE 3 READINESS CHECKS FAILED"
  exit 1
fi
