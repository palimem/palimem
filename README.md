# Palimem

Local-first memory for coding agents. SQLite WAL storage, MCP stdio server, supersession, scoped recall, and audit export.

**Documentation:** [palimem.com/docs](https://palimem.com/docs/)  
**Spec:** [v1.7.0](spec/README.md) · **License:** [Apache-2.0](LICENSE)

## Install

```bash
git clone https://github.com/palimem/palimem.git
cd palimem/app && npm install && cd ..
```

Connect an editor (Cursor example):

```bash
node app/scripts/ai-memory.js connect cursor --project-root "$(pwd)"
```

See [getting started](https://palimem.com/docs/getting-started) for Claude Code, Copilot, Codex, and other integrations.

## Run

```bash
export MEMORY_SERVICE_DATA_DIR="$(pwd)/.ai-memory/data"
python3 app/run_production_stdio_server.py
```

Or:

```bash
node app/scripts/memory-service-mcp.js
```

## Validate

```bash
python3 tests/run_validation.py
bash integrations/run_readiness.sh
python3 benchmarks/run_benchmarks.py
```

## Layout

| Path | Purpose |
|------|---------|
| `app/` | MCP server and `ai-memory` CLI |
| `spec/` | Normative behavior contract |
| `tests/` | Black-box validation (138 behaviors) |
| `examples/` | Editor wiring examples |
| `integrations/` | Integration readiness smokes |
| `adapters/` | Hermes and OpenClaw plugins |
