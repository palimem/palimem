#!/usr/bin/env bash
# Wire Palimem dogfood for this repo: deps, local data dir, seeded memory, MCP verify.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/app"
DATA_DIR="$ROOT/.ai-memory/data"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"

echo "== Palimem dogfood setup =="
echo "Repo: $ROOT"

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node not found on PATH (need Node.js 18+)" >&2
  exit 1
fi

echo "-- npm install (app/)"
(cd "$APP" && npm install)

echo "-- write optional local .cursor/mcp.json (not committed; reload Cursor after setup)"
mkdir -p "$ROOT/.cursor"
cat > "$ROOT/.cursor/mcp.json" <<'JSON'
{
  "mcpServers": {
    "memory-service": {
      "command": "node",
      "args": [
        "${workspaceFolder}/app/scripts/memory-service-mcp.js"
      ],
      "env": {
        "MEMORY_SERVICE_DATA_DIR": "${workspaceFolder}/.ai-memory/data"
      }
    }
  }
}
JSON

echo "-- seed memory from dogfood/*.md"
rm -rf "$DATA_DIR"
mkdir -p "$DATA_DIR"
"$PYTHON" "$APP/import_markdown.py" \
  --data-dir "$DATA_DIR" \
  --namespace palimem \
  "$ROOT/dogfood/USER.md" \
  "$ROOT/dogfood/MEMORY.md"

echo "-- verify MCP stdio (memory_status + 11 tools)"
MCP_SCRIPT="$APP/scripts/memory-service-mcp.js"
export MEMORY_SERVICE_DATA_DIR="$DATA_DIR"
"$PYTHON" - <<PY
import json, os, subprocess, sys

mcp_script = "$MCP_SCRIPT"
data_dir = "$DATA_DIR"
env = {**os.environ, "MEMORY_SERVICE_DATA_DIR": data_dir}

proc = subprocess.Popen(
    ["node", mcp_script],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
)

def send(msg):
    body = json.dumps(msg)
    header = f"Content-Length: {len(body)}\\r\\n\\r\\n"
    proc.stdin.write((header + body).encode())
    proc.stdin.flush()

def recv():
    header = b""
    while not header.endswith(b"\\r\\n\\r\\n"):
        ch = proc.stdout.read(1)
        if not ch:
            raise EOFError("MCP server closed stdout")
        header += ch
    length = int([l for l in header.decode().splitlines() if l.startswith("Content-Length:")][0].split(":")[1])
    return json.loads(proc.stdout.read(length))

send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "dogfood-setup", "version": "1.0"},
}})
assert "result" in recv(), "initialize failed"
send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_status", "arguments": {}}})
status = recv()
assert "result" in status, status
send({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
tools = {t["name"] for t in recv()["result"].get("tools", [])}
required = {
    "memory_remember", "memory_search", "memory_get", "memory_forget",
    "memory_status", "memory_consolidate", "memory_review",
    "memory_profile", "memory_reflect", "memory_query_temporal", "memory_audit_export",
}
missing = required - tools
assert not missing, missing
proc.terminate()
proc.wait(timeout=5)
print("dogfood verify: memory_status OK, 11 tools present")
PY

echo ""
echo "Dogfood ready."
echo "  Data:     $DATA_DIR"
echo "  MCP:      $ROOT/.cursor/mcp.json"
echo "  Reload Cursor, then try: memory_search scope=repository namespace=palimem query='release gate'"
echo "  Protocol: see dogfood/README.md"
