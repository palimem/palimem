# Claude Code marketplace plugin

Self-contained plugin for distributing **memory-service** through Claude Code's plugin marketplace.

## Layout

```
claude-code-plugin/
├── .claude-plugin/plugin.json    # Plugin manifest
├── .mcp.json                     # MCP stdio registration (vendored paths)
├── hooks/hooks.json              # Lifecycle hooks (vendored paths)
├── vendor/                       # Generated — run scripts/vendor-plugin.sh
│   ├── hooks/claude-code/        # Hook JavaScript
│   └── app/                      # Python MCP server + hook CLIs
├── scripts/vendor-plugin.sh      # Refresh vendor/ from repository sources
└── README.md
```

Hook and MCP paths use `${CLAUDE_PLUGIN_ROOT}/vendor/...` so the plugin works when installed from a marketplace without sibling example paths.

## Refresh vendored files

After changing hook scripts or the Python app:

```bash
bash examples/claude-code-plugin/scripts/vendor-plugin.sh
git add examples/claude-code-plugin/vendor/
```

## Install (from GitHub)

```bash
claude plugin marketplace add palimem/palimem
claude plugin install memory-service@palimem
```

The repo root [`.claude-plugin/marketplace.json`](../../.claude-plugin/marketplace.json) catalogs this plugin via `git-subdir`.

## Install (local development)

```bash
claude plugin marketplace add /path/to/palimem/examples/claude-code-plugin
claude plugin install memory-service@palimem
```

## Optional SessionEnd consolidation

```bash
export MEMORY_SERVICE_RUN_CONSOLIDATION_ON_SESSION_END=1
export MEMORY_SERVICE_REVIEW_EXPORT_PATH=.ai-memory/review.md
```

## Publish checklist

1. Run `scripts/vendor-plugin.sh` and commit `vendor/`.
2. Bump `version` in `.claude-plugin/plugin.json` with component spec semver.
3. Bump `metadata.version` in repo-root `.claude-plugin/marketplace.json` if needed.
4. Tag release; users run `claude plugin marketplace update palimem`.

## Related

- Hook sources (edit here, then re-vendor): [../claude-code/README.md](../claude-code/README.md)
- Operator CLI: `node ../../app/scripts/ai-memory.js`
- Codex wiring: [../codex/README.md](../codex/README.md)
