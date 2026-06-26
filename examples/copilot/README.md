# GitHub Copilot CLI integration

Register **memory-service** in Copilot's MCP configuration.

## Quick connect

From repository root:

```bash
node app/scripts/ai-memory.js connect copilot \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

This merges into `~/.copilot/mcp-config.json` by default.

## Options

| Flag | Purpose |
|------|---------|
| `--config PATH` | Target file (default `~/.copilot/mcp-config.json`) |
| `--project-config PATH` | Also write project `.copilot/mcp-config.json` |
| `--project-root PATH` | Repo root for resolving `memory-service-mcp.js` (default: `$PWD`) |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` (resolved to absolute against `--project-root`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged JSON without writing |

## Python equivalent

```bash
python3 app/connect_copilot.py \
  --project-root "$(pwd)" \
  --project-config .copilot/mcp-config.json
```

## Verify

```bash
copilot mcp list
```

You should see `memory-service` with eleven tools when the CLI session starts the stdio server:
`memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`.

## Project-level config

Copilot CLI may also load workspace `.mcp.json`. This repo's Claude Code sample lives at `examples/claude-code/.mcp.json`; adapt the same `memory-service` block for Copilot if you prefer project-local registration.

## Smoke test

A non-interactive smoke test is available at [`demo/copilot-smoke.sh`](./demo/copilot-smoke.sh). It starts the MCP server via stdio and verifies `memory_status` responds with all eleven tools.

## Related

- [Cursor integration](../cursor/README.md)
- [Windsurf integration](../windsurf/README.md)
- [Codex integration](../codex/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
