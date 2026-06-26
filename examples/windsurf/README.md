# Windsurf integration

Register **memory-service** in Windsurf via MCP stdio transport.

Windsurf supports global MCP configuration only (`~/.codeium/windsurf/mcp_config.json`). Project-level MCP scoping is not available.

## Prerequisites

- [Windsurf](https://codeium.com/windsurf) with MCP support
- Node.js ≥ 18
- Repository cloned; dependencies installed (`cd app && npm install`)

## Quick connect

From repository root:

```bash
node app/scripts/ai-memory.js connect windsurf \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

This writes to `~/.codeium/windsurf/mcp_config.json`. Restart Windsurf to pick up the change.

## Options

| Flag | Purpose |
|------|---------|
| `--config PATH` | Target `mcp_config.json` (default: `~/.codeium/windsurf/mcp_config.json`) |
| `--project-root PATH` | Repo root for resolving `memory-service-mcp.js` (default: `$PWD`) |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` resolved to absolute before write (default: `.ai-memory/data`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged JSON without writing |

## Manual config

If you prefer to edit the file by hand, add the following to `~/.codeium/windsurf/mcp_config.json` under `mcpServers`. See [`mcp_config.json.sample`](./mcp_config.json.sample) for a complete example.

```json
{
  "mcpServers": {
    "memory-service": {
      "command": "node",
      "args": ["/absolute/path/to/your/repo/app/scripts/memory-service-mcp.js"],
      "env": {
        "MEMORY_SERVICE_DATA_DIR": "/absolute/path/to/your/repo/.ai-memory/data"
      }
    }
  }
}
```

Replace `/absolute/path/to/your/repo` with the actual absolute path to your cloned repository.

## Verify

1. Restart Windsurf.
2. In a Cascade chat session, ask the AI to call `memory_status`. You should see a response confirming the data directory and eleven available tools.

Expected tools: `memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Server not listed in MCP panel | Restart Windsurf after writing config |
| `ENOENT` on `memory-service-mcp.js` | Run `npm install` in `app/` first |
| Existing entry refused | Re-run with `--replace` |
| Tools show 9 instead of 11 | Update to spec v1.7.0 — `memory_query_temporal` and `memory_audit_export` added in v1.6.0 |

## Seed memory (optional)

```bash
python3 app/import_markdown.py \
  --data-dir .ai-memory/data \
  examples/markdown/USER.md.sample \
  examples/markdown/MEMORY.md.sample
```

## Related

- [Cursor integration](../cursor/README.md)
- [Copilot CLI integration](../copilot/README.md)
- [Codex integration](../codex/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
