#!/usr/bin/env bash
# Phase 5 readiness gate: validation + integration + Phase 5 operator smoke.
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

echo "=== Phase 5 smoke ==="
bash "$DEMO/phase5-smoke.sh" >/dev/null && pass "phase5-smoke" || fail "phase5-smoke"

echo "=== Artifact consistency ==="
assert_validation_artifacts "1.7.0" >/dev/null && pass "artifacts green" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 5 READINESS CHECKS PASSED"
else
  echo "SOME PHASE 5 READINESS CHECKS FAILED"
  exit 1
fi
