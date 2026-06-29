# Research benchmarks

Informative benchmarks for recall quality, agent-task utility, and search latency. These complement the normative black-box validation suite in `tests/` but do **not** gate correctness on their own.

## Run locally

```bash
python3 benchmarks/run_benchmarks.py
```

Strict mode (fail on regression):

```bash
python3 benchmarks/run_benchmarks.py --strict
```

Results are written to `benchmarks/artifacts/latest-benchmark-results.json`.

## Suites

| Suite | Module | What it measures |
|-------|--------|------------------|
| **Persona recall** | `persona_recall.py` | Expanded USER.md vs profile+search treatment, noise, supersession |
| **Agent tasks** | `agent_tasks.py` | Task-shaped recall scenarios (resume session, supersession, episodes) |
| **Latency sweep** | `latency_sweep.py` | `memory_search` p50/p95 at corpus sizes 100 / 500 / 1000 |

Probe definitions live in `benchmarks/data/`.

## CI

GitHub Actions runs benchmarks in strict mode (`python3 benchmarks/run_benchmarks.py --strict`) as a blocking job alongside component validation, integration readiness, and the Phase 6 release gate.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `BENCHMARK_CORPUS_SIZES` | `100,500,1000` | Comma-separated corpus sizes for the latency sweep |

## Related

- Normative validation: `python3 tests/run_validation.py`
- Phase 4 exit benchmark: `bash examples/claude-code/demo/phase4-profile-benchmark.sh`
- Release readiness gate: `bash examples/claude-code/demo/phase6-readiness.sh`
