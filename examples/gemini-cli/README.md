# Gemini CLI integration

Register **memory-service** in the [Gemini CLI](https://github.com/google-gemini/gemini-cli) via `mcpServers` in `settings.json`.

**Full docs:** [palimem.com/docs/integrations/gemini-cli](https://palimem.com/docs/integrations/gemini-cli)

## Prerequisites

- Gemini CLI installed
- Node.js ≥ 18
- Python 3.10+ (stdlib only; used by the MCP server)

## Quick connect

**npx (no clone):**

```bash
npx @palimem/mcp ai-memory connect gemini \
  --project-root "$(pwd)" \
  --launcher npx \
  --data-dir .ai-memory/data
```

**From a clone:**

```bash
cd app && npm install && cd ..
node app/scripts/ai-memory.js connect gemini \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

Default target: `~/.gemini/settings.json`. Set `GEMINI_HOME` to override the config directory.

### Project-level config

```bash
npx @palimem/mcp ai-memory connect gemini \
  --project-root "$(pwd)" \
  --project-config .gemini/settings.json \
  --launcher npx \
  --data-dir .ai-memory/data
```

## Options

| Flag | Purpose |
|------|---------|
| `--config PATH` | User `settings.json` (default: `~/.gemini/settings.json`) |
| `--project-config PATH` | Also write project `.gemini/settings.json` |
| `--project-root PATH` | Workspace root for resolving data dir (default: `$PWD`) |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` (default: `.ai-memory/data`) |
| `--launcher local\|npx` | Use local `node` script or `npx @palimem/mcp` (default: `local`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged JSON without writing |

## Sample config

[`.gemini/settings.json.sample`](./.gemini/settings.json.sample) — use absolute paths, or let `ai-memory connect gemini` resolve them.

With `--launcher npx`, the merged entry uses:

```json
{
  "command": "npx",
  "args": ["-y", "@palimem/mcp"],
  "env": {
    "MEMORY_SERVICE_DATA_DIR": "/absolute/path/to/.ai-memory/data"
  }
}
```

## Verify

```bash
bash examples/gemini-cli/demo/gemini-smoke.sh
```

In Gemini CLI, invoke `memory_status` via MCP. Eleven tools should be available (including `memory_query_temporal` and `memory_audit_export`).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP server not listed | Restart Gemini CLI after writing settings |
| `ENOENT` on launcher | For local mode, run `npm install` in `app/`; for npx mode, ensure Node.js ≥ 18 |
| Existing entry refused | Re-run with `--replace` |
| Wrong config directory | Set `GEMINI_HOME` to the directory containing `settings.json` |

## Seed memory (optional)

```bash
python3 app/import_markdown.py \
  --data-dir .ai-memory/data \
  examples/markdown/USER.md.sample \
  examples/markdown/MEMORY.md.sample
```

## Related

- [Codex integration](../codex/README.md)
- [Connect helper source](../../app/connect_gemini.py)
