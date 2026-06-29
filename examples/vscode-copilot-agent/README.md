# VS Code + Copilot Agent integration

Register **memory-service** in VS Code via the project `.vscode/mcp.json` MCP stdio transport (Copilot Agent and related VS Code MCP clients).

## Prerequisites

- [VS Code](https://code.visualstudio.com/) with MCP support (Copilot Agent or compatible extension)
- Node.js ≥ 18
- Repository cloned; dependencies installed (`cd app && npm install`)

## Quick connect

From repository root:

```bash
node app/scripts/ai-memory.js connect vscode \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

This writes to `.vscode/mcp.json` in the project. Reload the window or restart VS Code so MCP servers are picked up.

## Options

| Flag | Purpose |
|------|---------|
| `--project-config PATH` | Target `.vscode/mcp.json` (default: `<project-root>/.vscode/mcp.json`) |
| `--project-root PATH` | Repo root for resolving `memory-service-mcp.js` (default: `$PWD`) |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` resolved to absolute before write (default: `.ai-memory/data`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged JSON without writing |

## Sample config

A committed sample lives at [`.vscode/mcp.json.sample`](./.vscode/mcp.json.sample).

## Verify

```bash
bash examples/vscode-copilot-agent/demo/vscode-copilot-smoke.sh
```

In VS Code, ask Copilot Agent to call `memory_status`. You should see the data directory and eleven available tools.

Expected tools: `memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP server not listed | Reload VS Code after writing config |
| `ENOENT` on `memory-service-mcp.js` | Run `npm install` in `app/` first |
| Existing entry refused | Re-run with `--replace` |

## Related

- [Copilot IDE integration](../copilot-ide/README.md) — same `.vscode/mcp.json` surface
- [Cursor integration](../cursor/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
