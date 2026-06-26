#!/usr/bin/env bash
# Phase 2 hooks smoke test: bash-error capture + pre-compact flush + recall.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HOOKS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app"
DATA_DIR="$(mktemp -d)"
export MEMORY_SERVICE_DATA_DIR="$DATA_DIR"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"

cleanup() { rm -rf "$DATA_DIR"; }
trap cleanup EXIT

echo "== simulate PostToolUseFailure (Bash) =="
printf '%s' '{
  "session_id": "demo-session",
  "cwd": "'"$ROOT"'",
  "hook_event_name": "PostToolUseFailure",
  "tool_name": "Bash",
  "tool_input": {"command": "python3 missing_module.py"},
  "tool_use_id": "demo-bash-fail-001",
  "error": "Command exited with non-zero status code 1\nModuleNotFoundError: No module named missing_module"
}' | node "$HOOKS_DIR/capture-tool-failure.js"

echo "== recall bash failure via hook_search =="
RESULT="$("$PYTHON" "$APP/hook_search.py" \
  --data-dir "$DATA_DIR" \
  --scope session \
  --namespace "$(basename "$ROOT")" \
  --query "ModuleNotFoundError missing_module" \
  --include-episodes)"
echo "$RESULT" | grep -q "demo-bash-fail-001"
echo "bash-error recall: OK"

echo "== simulate PreCompact flush =="
printf '%s' '{
  "session_id": "demo-session",
  "cwd": "'"$ROOT"'",
  "hook_event_name": "PreCompact",
  "trigger": "manual",
  "custom_instructions": "Preserve API contract for memory-service v1.3.0",
  "transcript_path": ""
}' | node "$HOOKS_DIR/pre-compact-flush.js"

REPO_NS="$(basename "$ROOT")-repo"
CHECKPOINT="$("$PYTHON" "$APP/hook_search.py" \
  --data-dir "$DATA_DIR" \
  --scope repository \
  --namespace "$REPO_NS" \
  --query "API contract memory-service" \
  --limit 3)"
echo "$CHECKPOINT" | grep -q "compaction_checkpoint"
echo "pre-compact flush: OK"

echo "== simulate Stop (transcript backfill + turn summary) =="
TRANSCRIPT="$(mktemp)"
cat > "$TRANSCRIPT" <<TXEOF
{"session_id":"demo-session","cwd":"$ROOT","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu-stop-001","is_error":true,"content":"NameError: name raise is not defined"}]}}
TXEOF
printf '%s' '{
  "session_id": "demo-session",
  "cwd": "'"$ROOT"'",
  "hook_event_name": "Stop",
  "transcript_path": "'"$TRANSCRIPT"'",
  "last_assistant_message": "I attempted to run the diagnostic script but the Bash tool failed with NameError. Next I will fix the import and rerun tests for memory-service v1.3.0."
}' | node "$HOOKS_DIR/stop-extract.js"

STOP_RECALL="$("$PYTHON" "$APP/hook_search.py" \
  --data-dir "$DATA_DIR" \
  --scope session \
  --namespace "$(basename "$ROOT")" \
  --query "tool_observation transcript" \
  --include-episodes)"
echo "$STOP_RECALL" | grep -q "transcript-tu-stop-001"
echo "stop transcript backfill: OK"

TURN_RECALL="$("$PYTHON" "$APP/hook_search.py" \
  --data-dir "$DATA_DIR" \
  --scope session \
  --namespace "$(basename "$ROOT")" \
  --query "diagnostic script memory-service" \
  --limit 5)"
echo "$TURN_RECALL" | grep -q "session_turn"
echo "stop turn summary: OK"
rm -f "$TRANSCRIPT"

echo "ALL HOOK SMOKE CHECKS PASSED"
