# Memory Service App

Validation entrypoint:

```powershell
py -3 components/memory-service/app/run_stdio_server.py
```

Required validation environment:

- `MEMORY_SERVICE_VALIDATION_READY_FILE`
- `MEMORY_SERVICE_VALIDATION_CONTROL_DIR`
- `MEMORY_SERVICE_VALIDATION_DATA_DIR`
- `MEMORY_SERVICE_VALIDATION_SCHEMA_MODE`
- `MEMORY_SERVICE_VALIDATION_NAMESPACE_SEED`
- `MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE` when schema mode is `upgrade_from_v1_0_1`

Validation contract notes:

- The durable source of truth is a SQLite-backed append-only WAL under the validation data directory.
- Governed semantic versions are derived and stored separately from disposable TF-IDF search indexes.
- Search indexes can be rebuilt or marked unavailable without corrupting current-state lookup or historical audit.
- `memory_search` accepts an optional `subject` object containing `topic`, `field`, or both. When supplied, the service validates that shape publicly, applies those keys as equality filters on the stored subject key before ranking, and includes the applied subject constraint in each result's `match_reason`.
- Consolidation and review are explicit opt-in operations in v1.3.0. `memory_consolidate` runs non-destructive `safe_merge` (dedupe + summarize low-salience beliefs) without mutating WAL authority; `memory_review` lists, accepts, or rejects promotion proposals before they become governed current state.
- `memory_get` accepts optional `depth` (`full` default, `summary` truncates value to 256 Unicode scalars).
- `memory_remember` accepts optional `expires_at`, `blocks_actions`, and episode `observation` metadata.
- `memory_remember` accepts optional `legal_hold`; when `true`, `memory_forget` and retention eviction are blocked for the current version until a later write clears the hold.
- Expired governed records are excluded from default current-state recall and search but remain available through historical `as_of` evaluation before expiry.
- Two Phase 5 tools are published in addition to the v1.5.0 tool set: `memory_query_temporal` for topic-scoped belief trajectories and `memory_audit_export` for JSON/JSONL audit export.
- Phase 5 governance features are operator-controlled through the validation control plane: `set_pii_scan`, `set_audit_logging`, `set_retention_policy`, `run_retention`, `set_fleet_mode` / `set_fleet_status` / `set_fleet_config`, `fleet_push`, `fleet_pull`, and `rebuild_graph`.
- The graph layer is derivation-only: it projects topic entities, field edges, and direct `extends -> depends_on` edges from governed semantic units and can be rebuilt without changing WAL authority.
- `memory_status` now reports `pii_scan`, `retention`, and `fleet` metadata for the requested namespace.
- Validation-mode `fleet_replica` supports a minimal local-authoritative stub: configure a file-backed backend with `backend_path` or `backend_id`, let writes best-effort push committed WAL segments, and use `fleet_pull` for catch-up sync on another replica.
- The validation control plane watches `requests/` and writes matching responses into `responses/` beneath the configured control directory.

Production entrypoint:

```powershell
py -3 components/memory-service/app/run_production_stdio_server.py
```

Optional production environment:

- `MEMORY_SERVICE_DATA_DIR` defaults to `/data` in containers and may be set to any writable durable directory.

Production contract notes:

- The production entrypoint starts the same MCP stdio server and the same eleven tools as validation mode (`memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`), but it does not require the validation control-plane or ready-file environment.
- Production readiness is process readiness: startup succeeds only after the SQLite store opens, the schema bootstrap completes, and indexes are rebuilt from durable state. There is no network listener, port, or sidecar health endpoint.
- Fresh-create and in-place upgrade use the same durable bootstrap path. On startup the service opens `memory_service.sqlite3` inside `MEMORY_SERVICE_DATA_DIR`, enables SQLite WAL mode, and applies idempotent `CREATE TABLE IF NOT EXISTS` schema initialization without wiping existing state.
- Existing WAL history, semantic versions, and namespace status survive restarts because production mode uses schema mode `auto` rather than the validation-only fixture modes.
- Normal stdio shutdown (EOF on stdin) closes the service cleanly. Container stop may terminate the process directly; committed SQLite WAL state remains durable in the mounted data directory.
- `fleet_primary` remains unsupported; the v1.6.0 fleet stub stays local-authoritative and does not treat any remote backend as the write authority.

Container packaging:

- Dockerfile: `components/memory-service/Dockerfile`
- Intended local image tag: `memory-service:1.6.0`
- The container runs the production stdio entrypoint as a non-root user and declares `/data` as the durable volume.

Portability (Phase 1):

- Export current governed memory: `python3 export_memory.py --data-dir <dir> --jsonl out.jsonl --markdown profile.md`
- Import markdown truth files: `python3 import_markdown.py --data-dir <dir> path/to/USER.md path/to/MEMORY.md`
- JSONL round-trip re-import is supported via `import_markdown.py`
- Claude Code wiring examples: `components/memory-service/examples/claude-code/`
- Hook CLIs (lifecycle hooks, no MCP): `hook_remember.py`, `hook_search.py`
- Sample markdown inputs: `components/memory-service/examples/markdown/`

Operator CLI (Phase 2 polish):

- `npx ai-memory connect copilot [--dry-run]` — merge MCP server into `~/.copilot/mcp-config.json`
- `npx ai-memory review list|export|accept|reject` — human review queue (`review_memory.py`)
- `npx ai-memory consolidate [--export-review]` — run `safe_merge` consolidation (`run_consolidation.py`)
- Copilot wiring docs: `components/memory-service/examples/copilot/`
- Claude Code plugin skeleton: `components/memory-service/examples/claude-code-plugin/`
- Codex wiring docs: `components/memory-service/examples/codex/`
