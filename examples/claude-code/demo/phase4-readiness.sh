#!/usr/bin/env bash
# Phase 4 readiness gate: validation + integration + profile benchmark.
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

echo "=== Phase 4 profile benchmark ==="
bash "$DEMO/phase4-profile-benchmark.sh" >/dev/null && pass "phase4-profile-benchmark" || fail "phase4-profile-benchmark"

echo "=== Artifact consistency ==="
assert_validation_artifacts "1.7.0" >/dev/null && pass "artifacts green" || fail "artifact consistency"

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 4 READINESS CHECKS PASSED"
else
  echo "SOME PHASE 4 READINESS CHECKS FAILED"
  exit 1
fi
