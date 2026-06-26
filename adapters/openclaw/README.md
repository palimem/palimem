# OpenClaw adapter (`ai-memory`)

This adapter exposes `memory-service` through OpenClaw's memory plugin slot.

- Plugin id: `ai-memory`
- Plugin kind: `memory`
- Tool surface: `memory_search`, `memory_get`
- Backing implementation: Node plugin entry (`index.js`) + Python bridge (`bridge.py`) that imports the in-process `MemoryService`
- Default data dir: `<workspace>/.ai-memory/data`

## What it does

- `memory_search` delegates to `memory_service.memory_search` and returns OpenClaw-style results:
  - `path`
  - `snippet`
  - `score`
  - `startLine`
  - `endLine`
- `memory_get` supports:
  - direct subject-key lookup (`scope`, `namespace`, `topic`, `field`, `memory_type`)
  - path aliases such as `memory/<topic>/<field>.md`
  - bounded reads of real sandboxed files under `MEMORY.md`, `memory/**`, and `.ai-memory/**`
- Optional workspace import hashes `MEMORY.md` and `memory/*.md` so repeated activation only re-imports changed files.

## Workspace sandbox

Allowed read paths:

- `<workspace>/MEMORY.md`
- `<workspace>/memory/**`
- `<workspace>/.ai-memory/**`

Anything outside the configured workspace root is rejected with `invalid_request`.

## Install

Link or copy this directory into your OpenClaw plugins location according to your local OpenClaw installation pattern. The package entrypoint is `index.js`.

If you manage plugins from the repository directly, point OpenClaw at:

```text
adapters/openclaw
```

## Configuration

Legacy memory slot:

```json
{
  "plugins": {
    "entries": {
      "ai-memory": {
        "data_dir": ".ai-memory/data",
        "namespace": "workspace-name",
        "import_workspace_markdown": true
      }
    },
    "slots": {
      "memory": "ai-memory"
    }
  }
}
```

Newer OpenClaw builds with memory role-slots can also point `memory.recall` at `ai-memory`.

Supported plugin config keys:

| Key | Default | Notes |
|-----|---------|-------|
| `data_dir` | `.ai-memory/data` | Relative to workspace root unless absolute |
| `namespace` | workspace basename | Repository namespace used for repository-scope reads |
| `import_workspace_markdown` | `false` | Import `MEMORY.md` and `memory/*.md` on activation when changed |

Sample config: [`openclaw.config.sample.json`](./openclaw.config.sample.json)

## Manual smoke test

Provider + bridge dispatch smokes:

```bash
bash examples/claude-code/demo/phase3-smoke.sh
node adapters/openclaw/smoke-openclaw-bridge.mjs
```

Optional OpenClaw plugin SDK check: `OPENCLAW_SMOKE_INSTALL=1 bash examples/claude-code/demo/phase3-host-smoke.sh`

For the current release readiness gate (validation + integration + Phase 4 profile benchmark), use `bash examples/claude-code/demo/phase4-readiness.sh`.

Search against an empty store:

```bash
python3 adapters/openclaw/bridge.py \
  memory_search \
  --workspace-root /tmp/openclaw-ai-memory-demo \
  --payload '{"query":"project decisions"}'
```

Subject-key lookup after seeding one fact:

```bash
python3 - <<'PY'
from pathlib import Path
import tempfile

import sys
sys.path.insert(0, "app")
from memory_service.service import MemoryService

workspace = Path(tempfile.mkdtemp(prefix="openclaw-ai-memory-"))
data_dir = workspace / ".ai-memory" / "data"
service = MemoryService(data_dir, "auto", None)
service.memory_remember({
    "scope": "repository",
    "namespace": workspace.name,
    "topic": "adapter_smoke",
    "field": "status",
    "memory_type": "fact",
    "value": "ready",
    "provenance": {
        "source": "smoke",
        "tool": "seed",
        "actor": "tester",
        "request_id": "seed-1"
    }
})
service.close()
print(workspace)
PY
```

Then call:

```bash
python3 adapters/openclaw/bridge.py \
  memory_get \
  --workspace-root <workspace-path-from-previous-step> \
  --payload '{"topic":"adapter_smoke","field":"status","memory_type":"fact"}'
```

## Validation startup contract notes

Black-box tests should use the adapter-owned startup surface instead of reaching into private internals:

- Node plugin entrypoint: `adapters/openclaw/index.js`
- Bridge entrypoint: `adapters/openclaw/bridge.py`
- Required startup input: `--workspace-root <path>`
- Optional config inputs: `--data-dir`, `--namespace`, `--import-workspace-markdown`, `--session-key`
- The bridge opens the same SQLite-backed governed store as production MCP, so restart/persistence checks should run by invoking the bridge multiple times against the same `data_dir`.
