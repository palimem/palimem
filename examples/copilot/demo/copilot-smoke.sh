#!/usr/bin/env bash
# copilot-smoke.sh — non-interactive smoke test for the Copilot CLI MCP integration.
# Verifies that memory-service starts via stdio and responds to memory_status.
# Exit 0 on success, 1 on failure.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
APP="$ROOT/app"
PYTHON="${MEMORY_SERVICE_PYTHON:-python3}"
WORKSPACE="$(mktemp -d)"

cleanup() { rm -rf "$WORKSPACE"; }
trap cleanup EXIT

DATA_DIR="$WORKSPACE/.ai-memory/data"

echo "== Copilot smoke: memory_status via stdio MCP =="

MCP_SCRIPT="$APP/scripts/memory-service-mcp.js"
if [ ! -f "$MCP_SCRIPT" ]; then
  echo "ERROR: $MCP_SCRIPT not found — run npm install in $APP" >&2
  exit 1
fi

"$PYTHON" - <<PY
import json
import subprocess
import sys
import os

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
    header = f"Content-Length: {len(body)}\r\n\r\n"
    proc.stdin.write((header + body).encode())
    proc.stdin.flush()

def recv():
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        ch = proc.stdout.read(1)
        if not ch:
            raise EOFError("MCP server closed stdout unexpectedly")
        header += ch
    length = int([l for l in header.decode().splitlines() if l.startswith("Content-Length:")][0].split(":")[1])
    return json.loads(proc.stdout.read(length))

send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "copilot-smoke", "version": "1.0"},
}})
init_resp = recv()
assert "result" in init_resp, f"initialize failed: {init_resp}"

send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
    "name": "memory_status",
    "arguments": {},
}})
status_resp = recv()
assert "result" in status_resp, f"memory_status failed: {status_resp}"
content = status_resp["result"].get("content", [])
text = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
assert text, f"memory_status returned empty content: {status_resp}"

send({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
tools_resp = recv()
assert "result" in tools_resp, f"tools/list failed: {tools_resp}"
tool_names = {t["name"] for t in tools_resp["result"].get("tools", [])}
required = {
    "memory_remember", "memory_search", "memory_get", "memory_forget",
    "memory_status", "memory_consolidate", "memory_review",
    "memory_profile", "memory_reflect", "memory_query_temporal", "memory_audit_export",
}
missing = required - tool_names
assert not missing, f"Missing tools: {missing}"

proc.terminate()
proc.wait(timeout=5)
print("copilot-smoke: OK — memory_status responded, all 11 tools present")
sys.exit(0)
PY

echo "== COPILOT SMOKE PASSED =="
