# Claude Code integration examples

These files show how to wire `memory-service` into Claude Code for local, stdio-based MCP access.

## Files

| File | Purpose |
|------|---------|
| [`.mcp.json`](./.mcp.json) | Project MCP server registration |
| [`hooks.json`](./hooks.json) | Lifecycle hooks: SessionStart, PostToolUseFailure, Stop, PreCompact |
| [`session-start-manifest.js`](./session-start-manifest.js) | SessionStart — inject Markdown memory manifest |
| [`capture-tool-failure.js`](./capture-tool-failure.js) | PostToolUseFailure (Bash) — write tool-failure episodes |
| [`stop-extract.js`](./stop-extract.js) | Stop — transcript backfill + turn summary |
| [`pre-compact-flush.js`](./pre-compact-flush.js) | PreCompact — flush user snippets to repository facts |
| [`hook-lib.js`](./hook-lib.js) | Shared paths, stdin JSON, `hook_remember.py` bridge |
| [`session-end-consolidate.js`](./session-end-consolidate.js) | SessionEnd — optional consolidation job |
| [`demo/polish-smoke.sh`](./demo/polish-smoke.sh) | Smoke test for operator CLIs |
| [`demo/run-consolidation-cron.sh`](./demo/run-consolidation-cron.sh) | Cron-friendly consolidation wrapper |
| [`demo/phase4-readiness.sh`](./demo/phase4-readiness.sh) | Phase 4 readiness gate — validation + integration + profile benchmark |
| [`demo/phase5-readiness.sh`](./demo/phase5-readiness.sh) | Phase 5 readiness gate — validation + integration + temporal/audit smoke |
| [`demo/phase6-readiness.sh`](./demo/phase6-readiness.sh) | **Current** release gate — validation + integration + host smokes + benchmarks |
| [`demo/phase4-profile-benchmark.sh`](./demo/phase4-profile-benchmark.sh) | Phase 4 profile-engine benchmark (invoked by readiness gates) |

Hook CLIs (no MCP required): `app/hook_remember.py`, `app/hook_search.py`.

## Quick start

1. Copy `.mcp.json` to your project root (merge if you already have MCP servers configured).
2. Merge [`hooks.json`](./hooks.json) into your Claude Code hooks settings (project or `.claude/settings.json`).
3. Ensure Python 3.10+ and Node.js are available.
4. Optional: seed memory from markdown samples:

```bash
python3 app/import_markdown.py \
  --data-dir .ai-memory/data \
  examples/markdown/USER.md.sample \
  examples/markdown/MEMORY.md.sample
```

5. Start Claude Code in the project. Data defaults to `.ai-memory/data`.

## Hooks pack (Phase 2)

| Hook | When | What it does |
|------|------|----------------|
| **SessionStart** | Session begins | Exports bounded Markdown manifest (`<session-memory-manifest>`) |
| **PostToolUseFailure** | Bash command fails | Writes `episode` with `observation.kind=tool_failure` (session scope) |
| **Stop** | Claude finishes turn | Backfills transcript tool errors; stores turn summary fact |
| **PreCompact** | Before `/compact` or auto-compact | Flushes recent user snippets + custom instructions to repository facts |
| **SessionEnd** | Session ends | Optional consolidation when `MEMORY_SERVICE_RUN_CONSOLIDATION_ON_SESSION_END=1` |
| **Stop** | Claude finishes turn | Transcript backfill for missed Bash errors + turn summary fact |

### Operator CLI (`ai-memory`)

```bash
node app/scripts/ai-memory.js connect copilot --project-root "$(pwd)"
node app/scripts/ai-memory.js review export --data-dir .ai-memory/data --output .ai-memory/review.md
node app/scripts/ai-memory.js consolidate --data-dir .ai-memory/data --export-review .ai-memory/review.md
```

See also [../copilot/README.md](../copilot/README.md), [../codex/README.md](../codex/README.md), and [../claude-code-plugin/README.md](../claude-code-plugin/README.md).

### Bash-error recall demo

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEMORY_SERVICE_DATA_DIR` | `.ai-memory/data` | SQLite data directory (same as MCP) |
| `MEMORY_SERVICE_NAMESPACE` | workspace folder basename | Hook namespace for session scope |
| `MEMORY_SERVICE_PYTHON` | `python3` / `py -3` | Python for hook CLIs |
| `MEMORY_SERVICE_HOOK_DEBUG` | unset | `1` logs hook ingest counts to stderr |
| `MEMORY_SERVICE_BLOCK_AUTO_COMPACT` | unset | `1` + `MEMORY_SERVICE_COMPACTION_SAFE_FILE` can veto auto PreCompact |

Repository-scope flush records use namespace `{namespace}-repo`.

### Bash-error recall demo

```bash
bash examples/claude-code/demo/hooks-phase2-smoke.sh
```

Or manually search after a failed Bash tool:

```bash
python3 app/hook_search.py \
  --data-dir .ai-memory/data \
  --scope session \
  --namespace "$(basename "$PWD")" \
  --query "ModuleNotFoundError" \
  --include-episodes
```

## npm wrapper alternative

```bash
cd app
npm install
```

Point `.mcp.json` at `node app/scripts/memory-service-mcp.js`.

## Related

- Production MCP: `app/run_production_stdio_server.py`
- Harness patterns: [../../docs/02-harness-integration.md](../../docs/02-harness-integration.md)
- Roadmap Phase 2: [../../docs/06-product-roadmap.md](../../docs/06-product-roadmap.md)
