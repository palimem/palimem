# Palimem

Local-first memory for coding agents. SQLite WAL storage, MCP stdio server, supersession, scoped recall, and audit export.

**Documentation:** [palimem.com/docs](https://palimem.com/docs/)  
**Spec:** [v1.7.0](spec/README.md) · **License:** [Apache-2.0](LICENSE)

## Install

**Quick start (npx — no clone required):**

```bash
npx github:palimem/palimem ai-memory connect cursor \
  --project-root "$(pwd)" \
  --launcher npx \
  --data-dir .ai-memory/data
```

Requires Node.js ≥ 18 and Python 3.10+. The connect helper writes an MCP entry that runs `npx -y github:palimem/palimem palimem-mcp`.

Once published to npm, `npx @palimem/mcp` is equivalent.

**From a clone (contributors / offline):**

```bash
git clone https://github.com/palimem/palimem.git
cd palimem/app && npm install && cd ..
node app/scripts/ai-memory.js connect cursor --project-root "$(pwd)"
```

**Claude Code plugin:**

```bash
/plugin marketplace add palimem
/plugin install memory-service@palimem
```

See [getting started](https://palimem.com/docs/getting-started) for Copilot, Codex, Gemini CLI, and other integrations.

## Run

```bash
export MEMORY_SERVICE_DATA_DIR="$(pwd)/.ai-memory/data"
python3 app/run_production_stdio_server.py
```

Or:

```bash
npx github:palimem/palimem palimem-mcp
```

## Benchmarks

Research benchmarks (informative, not normative) measure persona recall, agent-task utility, and search latency. Run locally:

```bash
python3 benchmarks/run_benchmarks.py --strict
```

Latest CI run on spec v1.7.0 (strict mode):

| Suite | Result | Notes |
|-------|--------|-------|
| Persona recall | pass | Expanded profile+search meets USER.md-only baseline; supersession probes pass |
| Agent tasks | pass | 7/7 task-shaped recall scenarios |
| Latency sweep | pass | `memory_search` p95 ≤ 10.1 ms at corpus sizes 100 / 500 / 1000 |

Full artifact: `benchmarks/artifacts/latest-benchmark-results.json`. See [benchmarks/README.md](benchmarks/README.md) for suite details.

## Validate

```bash
python3 tests/run_validation.py
bash integrations/run_readiness.sh
bash examples/claude-code/demo/phase6-readiness.sh
python3 benchmarks/run_benchmarks.py --strict
bash dogfood/run_automated.sh   # optional maintainer dogfood probes
```

## Layout

| Path | Purpose |
|------|---------|
| `app/` | MCP server and `ai-memory` CLI |
| `spec/` | Normative behavior contract |
| `tests/` | Black-box validation (143 behaviors) |
| `examples/` | Editor wiring examples |
| `integrations/` | Integration readiness smokes |
| `dogfood/` | Optional maintainer dogfood probes and sample memory |
| `adapters/` | Hermes and OpenClaw plugins |
