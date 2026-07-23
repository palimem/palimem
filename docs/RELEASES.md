# Releases

Palimem uses two version lines:

| Line | What it tracks | Current |
|------|----------------|---------|
| **Spec** | Normative behavior contract ([spec/README.md](../spec/README.md)) | **1.7.0** |
| **Package** | npm `@palimem/mcp`, MCP `serverInfo`, Docker label, Claude plugin manifest | **1.7.2** |

Patch bumps on the package line (docs, install path, metadata) do not change spec behavior unless [spec/README.md](../spec/README.md) is revised.

---

## Package releases

### v1.7.2

- Aligns component `serverInfo`, `app/` package metadata, Docker label, and plugin manifests with the 1.7.1 release line
- Documentation: governed-memory positioning, `npx @palimem/mcp` as primary install path
- **Spec:** unchanged at 1.7.0 (143 validation behaviors)
- [GitHub release](https://github.com/palimem/palimem/releases/tag/v1.7.2) · [npm](https://www.npmjs.com/package/@palimem/mcp)

### v1.7.1

- npm README and package description sync (`npx @palimem/mcp`, governed-memory copy)
- No spec or MCP tool semantic changes from v1.7.0
- [GitHub release](https://github.com/palimem/palimem/releases/tag/v1.7.1)

### v1.7.0

- Spec 1.7.0: Phase 6 integrations, 143 validation behaviors, 11 MCP tools
- npx install path (`@palimem/mcp` / `github:palimem/palimem`)
- Claude Code plugin, connect helpers for Cursor, Copilot, Codex, Windsurf, VS Code, Gemini CLI
- [GitHub release](https://github.com/palimem/palimem/releases/tag/v1.7.0)

---

## Verify a release

```bash
python3 tests/run_validation.py
bash integrations/run_readiness.sh
python3 benchmarks/run_benchmarks.py --strict
```

User docs: [palimem.com/docs](https://palimem.com/docs/)
