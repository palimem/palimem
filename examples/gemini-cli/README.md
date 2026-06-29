# Gemini CLI integration

Register **memory-service** in the [Gemini CLI](https://github.com/google-gemini/gemini-cli) via `mcpServers` in `settings.json`.

## Prerequisites

- Gemini CLI installed
- Node.js ≥ 18
- Repository cloned; dependencies installed (`cd app && npm install`)

## Quick connect

From repository root (user-level config):

```bash
node app/scripts/ai-memory.js connect gemini \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

Default target: `~/.gemini/settings.json`. Set `GEMINI_HOME` to override the config directory.

### Project-level config

```bash
node app/scripts/ai-memory.js connect gemini \
  --project-root "$(pwd)" \
  --project-config .gemini/settings.json \
  --data-dir .ai-memory/data
```

## Options

| Flag | Purpose |
|------|---------|
| `--config PATH` | User `settings.json` (default: `~/.gemini/settings.json`) |
| `--project-config PATH` | Also write project `.gemini/settings.json` |
| `--project-root PATH` | Repo root for resolving `memory-service-mcp.js` |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` (default: `.ai-memory/data`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged JSON without writing |

## Sample config

[`.gemini/settings.json.sample`](./.gemini/settings.json.sample)

## Verify

```bash
bash examples/gemini-cli/demo/gemini-smoke.sh
```

In Gemini CLI, invoke `memory_status` via MCP. Eleven tools should be available (including `memory_query_temporal` and `memory_audit_export`).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP server not listed | Restart Gemini CLI after writing settings |
| `ENOENT` on `memory-service-mcp.js` | Run `npm install` in `app/` first |
| Existing entry refused | Re-run with `--replace` |

## Related

- [Codex integration](../codex/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
