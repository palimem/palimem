#!/usr/bin/env bash
# Phase 3 exit-criteria smoke: Hermes ai-memory setup + OpenClaw hybrid search on a sample workspace.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERMES_ADAPTER="$ROOT/adapters/hermes"
OPENCLAW_ADAPTER="$ROOT/adapters/openclaw"
APP="$ROOT/app"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
WORKSPACE="$(mktemp -d)"
HERMES_HOME="$WORKSPACE/.hermes-home"

cleanup() { rm -rf "$WORKSPACE"; }
trap cleanup EXIT

echo "== Hermes memory setup (ai-memory provider) =="
export PYTHONPATH="$HERMES_ADAPTER:$APP${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON" - <<PY
from pathlib import Path
import json
import sys
import time

workspace = Path("$WORKSPACE")
hermes_home = Path("$HERMES_HOME")
plugin_dir = hermes_home / "plugins" / "memory" / "ai-memory"
plugin_dir.mkdir(parents=True, exist_ok=True)
(plugin_dir / "__init__.py").write_text("from ai_memory_hermes import register\n", encoding="utf-8")

from ai_memory_hermes import AiMemoryProvider

provider = AiMemoryProvider()
assert provider.name == "ai-memory", provider.name

config = {
    "data_dir": str(workspace / ".ai-memory" / "data"),
    "namespace": workspace.name,
    "recall_mode": "hybrid",
    "mirror_builtin_memory": False,
    "prefetch_limit": 5,
    "sync_turn_enabled": True,
}
provider.save_config(config, str(hermes_home))
saved = json.loads((plugin_dir / "config.json").read_text(encoding="utf-8"))
assert saved["recall_mode"] == "hybrid"

provider.initialize(
    "phase3-smoke-session",
    hermes_home=str(hermes_home),
    workspace_root=str(workspace),
)
schemas = provider.get_tool_schemas()
assert schemas, "hybrid mode should expose provider tool schemas"
assert any(s.get("function", {}).get("name") == "memory_remember" for s in schemas)

remember = provider.handle_tool_call(
    "memory_remember",
    {
        "scope": "user",
        "namespace": workspace.name,
        "memory_type": "fact",
        "topic": "phase3_smoke",
        "field": "status",
        "value": "hermes-ready",
        "provenance": {
            "source": "smoke",
            "tool": "phase3",
            "actor": "test",
            "request_id": "hermes-1",
        },
    },
)
assert '"ok": true' in remember or '"ok":true' in remember.replace(" ", "")

prefetch = provider.prefetch("hermes-ready", session_id="phase3-smoke-session")
assert prefetch and "hermes-ready" in prefetch, prefetch[:200] if prefetch else "(empty)"

provider.sync_turn("remember Berlin office", "stored preference", session_id="phase3-smoke-session")
deadline = time.monotonic() + 2.0
while time.monotonic() < deadline:
    got = provider.handle_tool_call(
        "memory_get",
        {
            "scope": "session",
            "topic": "session_turn",
            "field": "turn",
            "memory_type": "episode",
        },
    )
    if '"ok": true' in got or '"ok":true' in got.replace(" ", ""):
        break
    time.sleep(0.1)
else:
    raise SystemExit("Hermes sync_turn episode not visible within timeout")

provider.shutdown()
print("hermes setup: OK")
PY

echo "== OpenClaw hybrid search on sample workspace =="
mkdir -p "$WORKSPACE/memory/project"
cat > "$WORKSPACE/MEMORY.md" <<'MD'
# Project memory

- Hybrid search smoke fixture for Phase 3.
MD
cat > "$WORKSPACE/memory/project/decisions.md" <<'MD'
# Decisions

We chose governed memory over ad-hoc markdown grep.
MD

SEARCH_JSON="$("$PYTHON" "$OPENCLAW_ADAPTER/bridge.py" \
  memory_search \
  --workspace-root "$WORKSPACE" \
  --namespace "$(basename "$WORKSPACE")" \
  --import-workspace-markdown \
  --payload '{"query":"governed memory","limit":5}')"

echo "$SEARCH_JSON" | grep -q '"results"'
echo "$SEARCH_JSON" | grep -qi 'governed'
echo "$SEARCH_JSON" | grep -q '"path"'
echo "$SEARCH_JSON" | grep -q '"snippet"'

GET_JSON="$("$PYTHON" "$OPENCLAW_ADAPTER/bridge.py" \
  memory_get \
  --workspace-root "$WORKSPACE" \
  --namespace "$(basename "$WORKSPACE")" \
  --payload '{"path":"memory/project/decisions.md"}')"

echo "$GET_JSON" | grep -qi 'governed memory'

echo "ALL PHASE 3 SMOKE CHECKS PASSED"
