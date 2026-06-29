#!/usr/bin/env bash
# Phase 3 host smoke: provider-level demo + validation-bridge protocol + OpenClaw bridge dispatch.
# Optional: set HERMES_BIN to a real `hermes` CLI for an additional host check.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DEMO="$(dirname "$0")"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
NODE="${MEMORY_SERVICE_NODE:-node}"
FAIL=0

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*"; FAIL=1; }
skip() { echo "SKIP: $*"; }

echo "=== Phase 3 provider smoke (phase3-smoke.sh) ==="
bash "$DEMO/phase3-smoke.sh" >/dev/null && pass "phase3-smoke" || fail "phase3-smoke"

echo "=== Hermes validation bridge host smoke ==="
"$PYTHON" "$ROOT/adapters/hermes/smoke-hermes-bridge.py" >/dev/null && pass "hermes-bridge" || fail "hermes-bridge"

echo "=== OpenClaw bridge dispatch host smoke (index.js argv path) ==="
"$NODE" "$ROOT/adapters/openclaw/smoke-openclaw-bridge.mjs" >/dev/null && pass "openclaw-bridge" || fail "openclaw-bridge"

echo "=== Optional Hermes CLI host smoke ==="
if [ -n "${HERMES_BIN:-}" ] && [ -x "$HERMES_BIN" ]; then
  WORKSPACE="$(mktemp -d)"
  export HERMES_HOME="$WORKSPACE/.hermes"
  mkdir -p "$HERMES_HOME/plugins/memory/ai-memory"
  echo 'from ai_memory_hermes import register' > "$HERMES_HOME/plugins/memory/ai-memory/__init__.py"
  export PYTHONPATH="$ROOT/adapters/hermes:$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
  if "$HERMES_BIN" memory setup --provider ai-memory --workspace "$WORKSPACE" >/dev/null 2>&1; then
    pass "hermes CLI memory setup"
  else
    fail "hermes CLI memory setup"
  fi
  rm -rf "$WORKSPACE"
elif command -v hermes >/dev/null 2>&1; then
  skip "hermes found on PATH but HERMES_BIN not set — export HERMES_BIN to enable CLI smoke"
else
  skip "hermes CLI not installed — bridge/provider smokes cover adapter contract"
fi

echo "=== Optional OpenClaw plugin SDK smoke ==="
if [ "${OPENCLAW_SMOKE_INSTALL:-}" = "1" ]; then
  (
    cd "$ROOT/adapters/openclaw"
    npm install openclaw --no-save >/dev/null 2>&1 || true
    if "$NODE" -e "import('openclaw/plugin-sdk/plugin-entry').then(()=>process.exit(0)).catch(()=>process.exit(1))" 2>/dev/null; then
      pass "openclaw plugin-sdk import"
    else
      fail "openclaw plugin-sdk import after npm install"
    fi
  )
else
  skip "set OPENCLAW_SMOKE_INSTALL=1 to npm install openclaw and verify plugin-sdk import"
fi

echo ""
if [ $FAIL -eq 0 ]; then
  echo "ALL PHASE 3 HOST SMOKE CHECKS PASSED"
else
  echo "SOME PHASE 3 HOST SMOKE CHECKS FAILED"
  exit 1
fi
