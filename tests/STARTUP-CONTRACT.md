# Memory Service Validation Startup Contract

This document defines the application-owned validation startup contracts consumed by the black-box validation harness for spec v1.7.0. The harness does not create or own the application entrypoints and does not inspect implementation internals.

## Entrypoints

The orchestrator-facing validation entrypoint is exactly one command:

`python3 tests/run_validation.py`

The application-facing startup entrypoint consumed by that harness is exactly one command string supplied in `MEMORY_SERVICE_VALIDATION_COMMAND`.

The harness starts that command, waits for the documented readiness file, exercises the eleven published MCP tools over stdio, optionally uses the validation-only file control plane, and emits machine-readable results.

For spec v1.4.0 adapter coverage, the harness also consumes two additional app-owned validation bridge commands:

- `MEMORY_SERVICE_VALIDATION_HERMES_COMMAND`
- `MEMORY_SERVICE_VALIDATION_OPENCLAW_COMMAND`

## App-Owned Startup Requirements

The application must provide one validation-mode startup command that:

1. starts the MCP server over stdio
2. serves exactly these eleven tools: `memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, `memory_audit_export`
3. writes the readiness file only after the MCP surface and validation control plane are ready
4. honors the environment variables below without requiring any extra launcher manifest or sidecar contract

## Required Environment Variables

| Variable | Required | Meaning |
|----------|----------|---------|
| `MEMORY_SERVICE_VALIDATION_READY_FILE` | Yes | Absolute path where the app writes a JSON readiness document |
| `MEMORY_SERVICE_VALIDATION_CONTROL_DIR` | Yes | Absolute directory used for validation-only control requests and responses |
| `MEMORY_SERVICE_VALIDATION_DATA_DIR` | Yes | Durable storage root for the current validation run |
| `MEMORY_SERVICE_VALIDATION_SCHEMA_MODE` | Yes | `fresh` or `upgrade_from_v1_0_1` |
| `MEMORY_SERVICE_VALIDATION_NAMESPACE_SEED` | Yes | Namespace prefix reserved for validation isolation |
| `MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE` | Required when `MEMORY_SERVICE_VALIDATION_SCHEMA_MODE=upgrade_from_v1_0_1` | Fixture identifier; at minimum support `v1_0_1_minimal` |
| `MEMORY_SERVICE_VALIDATION_REFLECT_MODE` | No | When set to `deterministic`, `memory_reflect` may synthesize using a validation-only template grounded in cited evidence instead of a live LLM adapter |
| `MEMORY_SERVICE_VALIDATION_PROFILE_ENGINE_COMMAND` | No | Optional command string for a validation-only stub LLM adapter invoked by profile-engine extraction; when unset, the application may use a built-in deterministic stub |

## Readiness File

The application must write `MEMORY_SERVICE_VALIDATION_READY_FILE` as JSON with this minimum shape:

```json
{
  "ready": true,
  "protocol": "mcp-stdio",
  "tool_names": [
    "memory_remember",
    "memory_search",
    "memory_get",
    "memory_forget",
    "memory_status",
    "memory_consolidate",
    "memory_review",
    "memory_profile",
    "memory_reflect",
    "memory_query_temporal",
    "memory_audit_export"
  ],
  "supported_scopes": ["user", "session", "repository"],
  "schema_mode": "fresh",
  "consolidation_available": false
}
```

The harness will not begin tool calls until this file exists and reports `ready: true`.

## MCP Tool Result Encoding

The MCP server should return each tool result as `structuredContent` containing the published tool envelope.

Successful results must include at least:

```json
{
  "ok": true,
  "tool": "memory_get"
}
```

Error results should set `isError: true` and return the standard error envelope:

```json
{
  "ok": false,
  "tool": "memory_get",
  "error": {
    "code": "not_found",
    "message": "No current value exists for the requested subject at the requested recall point."
  }
}
```

The harness enforces the normative minimum response keys from spec v1.6.0 and permits extra implementation-specific fields.

## Validation-Only File Control Plane

Some required behaviors are operator-visible rather than public MCP-tool-visible: index rebuild, graph rebuild and enablement, index outage simulation, PII-scan enablement, audit-log enablement, retention policy changes, explicit retention runs, fleet-mode configuration, legacy explicit consolidation invocation, profile-engine triggers, session-summary triggers, context fencing, and validation-only fault injection. The application must also support a validation-only file-based control plane.

Under `MEMORY_SERVICE_VALIDATION_CONTROL_DIR`, the application must watch:

- `requests/` for inbound JSON request files
- `responses/` for outbound JSON response files with matching `request_id`

Request shape:

```json
{
  "request_id": "7b9f...",
  "action": "rebuild_indexes",
  "payload": {
    "namespace": "memory-service-validation-example"
  }
}
```

Success response shape:

```json
{
  "request_id": "7b9f...",
  "status": "ok",
  "details": {
    "namespace": "memory-service-validation-example"
  }
}
```

Failure response shape:

```json
{
  "request_id": "7b9f...",
  "status": "error",
  "code": "extraction_disabled",
  "message": "Profile extraction is disabled for this namespace."
}
```

Required actions:

| Action | Purpose |
|--------|---------|
| `rebuild_indexes` | Force a full rebuild of disposable indexes for the named namespace from durable state |
| `rebuild_graph` | Force a full rebuild of the derived graph projection for the named namespace from WAL-derived state |
| `set_index_availability` | Toggle indexed search availability for the named namespace without corrupting WAL-derived state |
| `set_graph_enabled` | Enable or disable graph materialization for the named namespace (`payload.enabled` boolean) |
| `set_pii_scan` | Configure Phase 5 PII scanning for the named namespace (`payload.enabled`, `payload.policy`, optional `payload.categories[]`, optional `payload.placeholder`) |
| `set_audit_logging` | Enable or disable audit logging for the named namespace; optional `payload.fail_closed` overrides the namespace policy during validation |
| `set_retention_policy` | Configure per-memory-type retention for the named namespace (`payload.enabled`, `payload.policies` map of memory type to TTL string such as `0s`, `24h`, or `30d`) |
| `run_retention` | Execute the operator retention pass for `payload.scope` and `payload.namespace`; optional `payload.effective_time` (RFC3339 UTC) lets validation evaluate deterministic future cutoffs |
| `set_fleet_config` | Configure Phase 5 fleet mode for the named namespace (`payload.mode`, optional `payload.backend_reachable`, `payload.serve_reads_from_replica`, `payload.max_staleness_seq`, `payload.replica_lag_seq`, `payload.lag_policy`) |
| `run_consolidation` | Legacy validation control action that invokes explicit consolidation for the named namespace when supported; v1.3.0 also exposes consolidation through the public `memory_consolidate` MCP tool |
| `set_fault` | Arm or disarm validation-only failure injection used to verify published error categories without inspecting internals |
| `set_profile_engine_enabled` | Enable or disable profile extraction for a namespace during validation (`payload.enabled` boolean) |
| `run_profile_engine` | Explicitly invoke profile-engine extraction for `payload.scope` and `payload.namespace`; when extraction is disabled, respond with `status=error` and `code=extraction_disabled` |
| `trigger_session_summary` | Asynchronously create or update the rolling `session_summary` subject for `payload.scope` and `payload.namespace`; optional `payload.messages` array supplies transcript input |
| `apply_context_fencing` | Apply Section 17.4.2 fencing to `payload.text` using `payload.known_injection_ids`; success `details` must include `fenced_text` |

The required `set_fault` payloads for v1.6.0 validation include:

```json
{
  "name": "integration_fail_next",
  "enabled": true
}
```

```json
{
  "name": "profile_unavailable",
  "enabled": true
}
```

```json
{
  "name": "reflection_unavailable",
  "enabled": true
}
```

```json
{
  "name": "temporal_query_unavailable",
  "enabled": true
}
```

```json
{
  "name": "audit_export_unavailable",
  "enabled": true
}
```

When `integration_fail_next` is armed, the next governed write that reaches the ingest path must fail with `integration_failed` and must not leave any partial current-state value visible through `memory_get` or `memory_search`.

When `profile_unavailable` is armed, the next `memory_profile` call must fail with `profile_unavailable`.

When `reflection_unavailable` is armed, the next `memory_reflect` call must fail with `reflection_unavailable`.

When `temporal_query_unavailable` is armed, the next `memory_query_temporal` call must fail with `temporal_query_unavailable`.

When `audit_export_unavailable` is armed, the next `memory_audit_export` call must fail with `audit_export_unavailable`.

## Validation-Only Reflection Mode

When `MEMORY_SERVICE_VALIDATION_REFLECT_MODE=deterministic`, the application may satisfy `memory_reflect` without a live external LLM by synthesizing text from cited governed records using a deterministic template. This mode is validation-only and must still return machine-readable `citations[]` grounded in the evidence corpus.

## Adapter Validation Bridge Commands

The adapter cases in spec Section 14.4 use validation-only stdio bridge commands so the harness can exercise the published adapter surfaces without importing app internals for assertions.

### Required environment variables for full v1.6.0 validation

| Variable | Required | Meaning |
|----------|----------|---------|
| `MEMORY_SERVICE_VALIDATION_HERMES_COMMAND` | Yes for Section 14.4 coverage | Command string that starts a Hermes adapter validation bridge process |
| `MEMORY_SERVICE_VALIDATION_OPENCLAW_COMMAND` | Yes for Section 14.4 coverage | Command string that starts an OpenClaw adapter validation bridge process |

### Generic bridge protocol

Each adapter bridge command must:

1. start a long-lived stdio process
2. accept one JSON object per line on stdin
3. write exactly one JSON object per line on stdout for each request
4. propagate public adapter errors with a machine-readable `error.code` and `error.message`

Request envelope:

```json
{
  "id": "1",
  "action": "initialize | call | shutdown",
  "method": "prefetch",
  "arguments": {
    "workspace_root": "/tmp/workspace",
    "data_dir": "/tmp/workspace/.ai-memory/data"
  }
}
```

Success response envelope:

```json
{
  "id": "1",
  "ok": true,
  "result": {}
}
```

Error response envelope:

```json
{
  "id": "1",
  "ok": false,
  "error": {
    "code": "invalid_request",
    "message": "Path is outside the configured workspace root."
  }
}
```

`elapsed_ms` is required on successful Hermes `sync_turn` responses so the harness can enforce the published non-blocking calling-thread bound:

```json
{
  "id": "7",
  "ok": true,
  "result": null,
  "elapsed_ms": 12.4
}
```

### Hermes bridge behavior

`MEMORY_SERVICE_VALIDATION_HERMES_COMMAND` must bridge the public Hermes `MemoryProvider` surface documented by the spec:

- `initialize` arguments: `workspace_root`, `data_dir`, `namespace`, `session_id`, `recall_mode`, `prefetch_limit`, `sync_turn_enabled`, optional `mirror_builtin_memory`, optional `profile_engine_enabled`
- `call` methods: `prefetch`, `sync_turn`, `get_tool_schemas`, `handle_tool_call`, `system_prompt_block`
- `handle_tool_call` result: JSON string containing the MCP-equivalent response envelope returned by the adapter

### OpenClaw bridge behavior

`MEMORY_SERVICE_VALIDATION_OPENCLAW_COMMAND` must bridge the public OpenClaw memory-slot adapter surface documented by the spec:

- `initialize` arguments: `workspace_root`, `data_dir`, `namespace`, `import_workspace_markdown`
- `call` methods: `memory_search`, `memory_get`
- `memory_search` result: JSON object with `results`
- `memory_get` result: either an empty success result or a single record-shaped object that reflects the adapter's workspace memory view

## Fresh-Create And Upgrade Modes

The startup command must support both modes through `MEMORY_SERVICE_VALIDATION_SCHEMA_MODE`:

- `fresh`: start against an empty `MEMORY_SERVICE_VALIDATION_DATA_DIR`; schema initialization must succeed automatically
- `upgrade_from_v1_0_1`: prepare or open the data directory using fixture `v1_0_1_minimal`, apply any required migration, and expose the migrated governed state through the same MCP contract

The minimum `v1_0_1_minimal` fixture must expose this logical state after startup:

```json
{
  "scope": "repository",
  "namespace": "upgrade-fixture",
  "topic": "migrated_profile",
  "field": "city",
  "memory_type": "fact",
  "value": "Berlin"
}
```

## Shutdown And Diagnostics

- The application should exit cleanly when stdin closes or when the harness terminates the process.
- Startup failures should be written to stderr in plain text.
- Control-plane failures must use the structured error schema above.

## Boundary

This contract is validation-consumed only. It does not authorize the suite to inspect internals or bypass the published MCP behavior under test.
