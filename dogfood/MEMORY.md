# Project memory (Palimem dogfood)

## Repository layout

- Flat product repo: MCP server and CLI live under `app/`
- Normative spec: `spec/README.md` v1.7.0 (143 black-box behaviors)
- Integration catalog: `spec/integrations.yaml` (11 tier A/B harnesses)

## Validation

- Component validation: `python3 tests/run_validation.py` (143 behaviors, spec v1.7.0)
- Integration smokes: `bash integrations/run_readiness.sh` (10 harness smokes including Hermes and OpenClaw adapters)
- Research benchmarks: `python3 benchmarks/run_benchmarks.py --strict`
- **Current release gate:** `bash examples/claude-code/demo/phase6-readiness.sh` — use this before release pushes

## Connect CLI

- Entrypoint: `node app/scripts/ai-memory.js connect <harness>`
- Supported harnesses: copilot, cursor, windsurf, codex, vscode, gemini
- P1 integrations delivered at tier B: vscode-copilot-agent, copilot-ide, gemini-cli

## Integration smokes

- Tier A/B smokes need Node.js 18+ and `npm install` in `app/` (MCP stdio via `memory-service-mcp.js`)

## Documentation

- Public docs: https://palimem.com/docs/
- Website repo is separate: palimem/website on GitHub

## Constraints

- Do not commit `.ai-memory/data` (local SQLite WAL)
- Benchmark artifacts under `benchmarks/artifacts/` are generated, not committed
