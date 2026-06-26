# Hermes adapter (`ai-memory`)

This package implements the Hermes `MemoryProvider` contract for `memory-service` v1.6.0.

- Registration name: `ai-memory`
- Config key: `memory.provider: ai-memory`
- Backing store: in-process `MemoryService` imported from `app/memory_service/`
- Default data dir: `<workspace>/.ai-memory/data` (or `MEMORY_SERVICE_DATA_DIR`)
- Recall modes: `hybrid`, `context`, `tools`

## What it does

- `initialize(session_id, **kwargs)` opens the local governed memory store and caches workspace/session identity.
- `system_prompt_block()` emits a short usage hint plus a bounded user-profile shelf when available.
- `prefetch()` searches `user` and `session` scopes and formats a bounded context block.
- `sync_turn()` persists completed turns as `episode` records on a background daemon thread.
- `get_tool_schemas()` / `handle_tool_call()` expose Hermes tool-mode access to the same Section 10 MCP envelopes.
- Optional hooks:
  - `on_session_end()` logs `memory_consolidate` dry-run stats
  - `on_pre_compress()` writes repository compaction-checkpoint facts
  - `on_memory_write()` mirrors built-in `MEMORY.md` / `USER.md` writes when enabled

## Install for local development

From the repository root:

```bash
python3 -m pip install -e adapters/hermes
```

Hermes discovers memory providers from `~/.hermes/plugins/memory/<provider>/`. Create a tiny shim:

```bash
mkdir -p ~/.hermes/plugins/memory/ai-memory
cat > ~/.hermes/plugins/memory/ai-memory/__init__.py <<'PY'
from ai_memory_hermes import register
PY
```

If you prefer not to use `cat`, create the same file manually with the single import line shown above.

## `hermes memory setup`

The provider implements `get_config_schema()` and `save_config()`, so `hermes memory setup` can persist native config to:

```text
~/.hermes/plugins/memory/ai-memory/config.json
```

Accepted keys:

| Key | Default | Notes |
|-----|---------|-------|
| `data_dir` | `<workspace>/.ai-memory/data` | Overridden by `MEMORY_SERVICE_DATA_DIR` when set |
| `namespace` | workspace basename | Repository namespace prefix |
| `recall_mode` | `hybrid` | `hybrid`, `context`, or `tools` |
| `mirror_builtin_memory` | `false` | Import `USER.md` on start and mirror built-in memory writes |
| `prefetch_limit` | `5` | Total prefetched results per turn |
| `sync_turn_enabled` | `true` | Queue completed turns as `episode` records |
| `profile_engine_enabled` | `false` | Opt-in background profile extraction (Section 17.1); triggers `run_profile_engine` on session end when enabled |

Sample config: [`ai-memory.config.sample.json`](./ai-memory.config.sample.json)

Example `config.yaml` snippet:

```yaml
memory:
  provider: ai-memory
```

## Manual smoke test

Provider-level smoke (simulates `hermes memory setup`):

```bash
bash examples/claude-code/demo/phase3-smoke.sh
```

Validation-bridge host smoke:

```bash
python3 adapters/hermes/smoke-hermes-bridge.py
```

Optional real Hermes CLI smoke: set `HERMES_BIN` and run `phase3-host-smoke.sh`.

For the current release readiness gate (validation + integration + Phase 4 profile benchmark), use `bash examples/claude-code/demo/phase4-readiness.sh`.

The provider can also be exercised without a full Hermes runtime:

```bash
PYTHONPATH="adapters/hermes:app" \
python3 - <<'PY'
from pathlib import Path
import json
import tempfile

from ai_memory_hermes import AiMemoryProvider

workspace = Path(tempfile.mkdtemp(prefix="ai-memory-hermes-workspace-"))
hermes_home = workspace / ".hermes-home"
provider = AiMemoryProvider()
provider.initialize("session-1", hermes_home=str(hermes_home), workspace_root=str(workspace))
print(provider.system_prompt_block())
print(provider.handle_tool_call("memory_remember", {
    "scope": "repository",
    "topic": "adapter_smoke",
    "field": "status",
    "memory_type": "fact",
    "value": "ready"
}))
print(provider.handle_tool_call("memory_get", {
    "scope": "repository",
    "topic": "adapter_smoke",
    "field": "status",
    "memory_type": "fact"
}))
provider.sync_turn("remember this", "stored", session_id="session-1")
provider.shutdown()
PY
```

## Validation startup contract notes

Black-box adapter tests should treat this package as the public startup surface instead of constructing private launch commands:

- Import path: `ai_memory_hermes.AiMemoryProvider`
- Required startup call: `initialize(session_id, hermes_home=<path>, workspace_root=<path>)`
- Optional environment: `MEMORY_SERVICE_DATA_DIR`
- The adapter opens the same SQLite-backed governed store as production MCP and writes turn-sync events asynchronously on a daemon thread.
- Tests should verify behavior by calling provider methods (`prefetch`, `sync_turn`, `get_tool_schemas`, `handle_tool_call`, `shutdown`) and observing persisted state through the same public tool calls.
