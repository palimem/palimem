# Memory Service Startup Contract

This document is the application-owned validation and production startup contract for `memory-service` spec v1.6.0.

## Validation entrypoint

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

Validation readiness contract:

- Startup succeeds only after the SQLite store opens, schema bootstrap completes, and rebuildable indexes are ready for use.
- The ready file is a JSON object with `ready = true`, `protocol = "mcp-stdio"`, and `tool_names` containing exactly:
  - `memory_remember`
  - `memory_search`
  - `memory_get`
  - `memory_forget`
  - `memory_status`
  - `memory_consolidate`
  - `memory_review`
  - `memory_profile`
  - `memory_reflect`
  - `memory_query_temporal`
  - `memory_audit_export`
- The control plane watches `<control_dir>/requests/*.json` and writes one response file per request under `<control_dir>/responses/`.

Supported validation control actions:

- `rebuild_indexes`
- `set_index_availability`
- `rebuild_graph`
- `set_graph_enabled`
- `run_consolidation`
- `set_fault`
- `run_profile_engine`
- `set_profile_engine_enabled`
- `set_pii_scan`
- `set_audit_logging`
- `set_retention_policy`
- `run_retention`
- `set_fleet_config`
- `fleet_push`
- `fleet_pull`
- `apply_context_fencing`
- `trigger_session_summary`

Phase 5 control semantics:

- `set_pii_scan` configures opt-in deterministic PII scanning for a scope/namespace with `enabled`, `policy`, optional category filters, optional per-type enablement, and optional operator regex/name lists.
- `set_audit_logging` configures append-only audit logging with `enabled` and `fail_closed`.
- `set_retention_policy` configures per-memory-type TTLs for a scope/namespace with `enabled` and a `policies` map whose values may be integer seconds or TTL strings such as `0s`, `24h`, or `30d`.
- `run_retention` executes the operator-triggered retention pass; it accepts optional `effective_time` for deterministic validation.
- `set_fleet_config` configures the Phase 5 fleet-replica stub. Supported modes are `local` and `fleet_replica`; `fleet_primary` is not supported. Validation mode accepts either `backend_path` or `backend_id` plus optional `sync_on_write`.
- `fleet_push` uploads locally committed WAL `seq` ranges plus the matching derived semantic snapshot into the configured validation backend.
- `fleet_pull` fetches missing WAL `seq` ranges from the configured validation backend for catch-up sync and refreshes local derived state.
- `set_graph_enabled` enables or disables graph materialization for a scope/namespace; when disabled, `memory_query_temporal` ignores `include_graph_edges`.
- `rebuild_graph` rebuilds the derived graph snapshot from WAL-derived semantic state.

## Production entrypoint

```powershell
py -3 components/memory-service/app/run_production_stdio_server.py
```

Optional production environment:

- `MEMORY_SERVICE_DATA_DIR` defaults to `/data`

Production behavior:

- The production entrypoint runs the same MCP stdio contract and the same eleven tools as validation mode.
- Local SQLite WAL remains the only write authority before an MCP success response is returned.
- `fleet_primary` is not supported in v1.6.0. The published stub remains local-authoritative and only exposes validation-mode replica push/pull helpers through the control plane.
