#!/usr/bin/env bash
# copilot-ide-smoke.sh — same MCP surface as VS Code Copilot Agent (.vscode/mcp.json).
set -euo pipefail

SMOKE_NAME=copilot-ide-smoke \
  bash "$(cd "$(dirname "$0")/../../vscode-copilot-agent/demo" && pwd)/vscode-copilot-smoke.sh"
