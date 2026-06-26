# OpenAI Codex CLI integration

Wire **memory-service** into Codex via MCP stdio. Codex stores MCP config in `~/.codex/config.toml` (global) or `.codex/config.toml` (project-scoped, trusted projects only).

## Quick connect (CLI helper)

From repository root:

```bash
node app/scripts/ai-memory.js connect codex \
  --project-root "$(pwd)" \
  --data-dir .ai-memory/data
```

This writes to `~/.codex/config.toml` (global). Use `--project-config .codex/config.toml` for project-scoped config.

## Options

| Flag | Purpose |
|------|---------|
| `--config PATH` | Target global `config.toml` (default: `~/.codex/config.toml`) |
| `--project-config PATH` | Write project `.codex/config.toml` instead of global |
| `--project-root PATH` | Repo root for resolving `memory-service-mcp.js` (default: `$PWD`) |
| `--data-dir PATH` | `MEMORY_SERVICE_DATA_DIR` resolved to absolute before write (default: `.ai-memory/data`) |
| `--replace` | Overwrite existing `memory-service` entry |
| `--dry-run` | Print merged TOML without writing |

## Manual `codex mcp add` (harness-native)

```bash
codex mcp add memory-service \
  --env MEMORY_SERVICE_DATA_DIR="$(pwd)/.ai-memory/data" \
  -- node app/scripts/memory-service-mcp.js
```

Verify:

```bash
codex mcp list
codex mcp get memory-service
```

In a Codex session, run `/mcp` to confirm eleven tools.

## Project-scoped config

Copy [`.codex/config.toml.sample`](./.codex/config.toml.sample) to your project `.codex/config.toml` and adjust paths. Project config overrides global for trusted workspaces.

## Manual `config.toml` snippet

```toml
[mcp_servers.memory-service]
enabled = true
command = "node"
args = ["/absolute/path/to/your/repo/app/scripts/memory-service-mcp.js"]

[mcp_servers.memory-service.env]
MEMORY_SERVICE_DATA_DIR = "/absolute/path/to/your/repo/.ai-memory/data"
```

Use absolute paths in `args` and `MEMORY_SERVICE_DATA_DIR`.

## Verify

After connecting, start a Codex session and run `/mcp`. You should see `memory-service` with eleven tools:

`memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`.

## Python direct (no Node wrapper)

```bash
codex mcp add memory-service \
  --env MEMORY_SERVICE_DATA_DIR="$(pwd)/.ai-memory/data" \
  -- python3 app/run_production_stdio_server.py
```

Set `cwd` to `app` if you use relative imports from that directory.

## npm wrapper (after `npm install` in `app/`)

```bash
cd app && npm install
codex mcp add memory-service \
  --env MEMORY_SERVICE_DATA_DIR="$(pwd)/.ai-memory/data" \
  -- node app/scripts/memory-service-mcp.js
```

## Seed memory (optional)

```bash
python3 app/import_markdown.py \
  --data-dir .ai-memory/data \
  examples/markdown/USER.md.sample \
  examples/markdown/MEMORY.md.sample
```

## Codex vs other harnesses

| Harness | Config location | Helper |
|---------|-----------------|--------|
| **Codex** | `~/.codex/config.toml` or `.codex/config.toml` | `ai-memory connect codex` |
| **Cursor** | `~/.cursor/mcp.json` or `.cursor/mcp.json` | `ai-memory connect cursor` |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | `ai-memory connect windsurf` |
| **Copilot CLI** | `~/.copilot/mcp-config.json` | `ai-memory connect copilot` |
| **Claude Code** | `.mcp.json` + hooks | [../claude-code/README.md](../claude-code/README.md) |

Codex does not use Claude Code lifecycle hooks. Use MCP tools directly, or run operator CLIs (`ai-memory consolidate`, `ai-memory review`) from cron or session scripts.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ENOENT` on `memory-service-mcp.js` | Run `npm install` in `app/` first |
| Existing entry refused | Re-run with `--replace` |
| Tools show 9 instead of 11 | Update to spec v1.7.0 — `memory_query_temporal` and `memory_audit_export` added in v1.6.0 |

## Related

- [Cursor integration](../cursor/README.md)
- [Windsurf integration](../windsurf/README.md)
- [Harness patterns](../../docs/02-harness-integration.md)
