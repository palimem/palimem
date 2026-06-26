# Cursor integration

Register **memory-service** in Cursor via MCP stdio transport.

## Prerequisites

- [Cursor](https://cursor.sh/) ≥ 0.40 (MCP support required)
- Node.js ≥ 18
- Repository cloned; dependencies installed (`cd app && npm install`)

## Quick connect

From repository root:

```bash
node app/scripts/ai-memory.js connect cursor \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

This writes to `~/.cursor/mcp.json` (global). Cursor picks up the entry on next restart.

## Options

| Flag | Purpose |
|------|---------|
| `--global-config PATH` | Target global `mcp.json` (default: `~/.cursor/mcp.json`) |
| `--project-config PATH` | Also write project `.cursor/mcp.json` |
| `--project-root PATH` | Repo root for resolving `memory-service-mcp.js` (default: `$PWD`) |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` resolved to absolute before write (default: `.ai-memory/data`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged JSON without writing |

## Project-level config

Cursor also supports a per-project `.cursor/mcp.json`. Use `--project-config` to write both:

```bash
node app/scripts/ai-memory.js connect cursor \
  --project-root "$(pwd)" \
  --project-config .cursor/mcp.json \
  --data-dir .ai-memory/data
```

A committed sample lives at [`.cursor/mcp.json`](./.cursor/mcp.json).

## Verify

1. Restart Cursor (or reload the MCP server list via the Cursor command palette).
2. In a chat session, ask the agent to call `memory_status`. You should see output confirming the data directory and eleven available tools.

Expected tools: `memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| No MCP server listed | Restart Cursor after writing config |
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

- [Copilot CLI integration](../copilot/README.md)
- [Windsurf integration](../windsurf/README.md)
- [Codex integration](../codex/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
