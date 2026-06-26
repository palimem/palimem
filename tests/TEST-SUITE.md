# Test Suite

**Spec version targeted:** 1.7.0  
**Generated:** 2026-06-25  
**Run command:** `python3 tests/run_validation.py`

## Summary

This suite is the black-box behavioral harness for the published `memory-service` component contract in `spec/README.md`.

It derives assertions directly from the spec, does not read application source, talks only through the eleven published MCP tools plus the documented validation-only file control plane and the `ai-memory connect` CLI subprocess surface, and emits machine-readable results conforming to the repository Test Results Schema.

The v1.7.0 revision preserves all 111 existing v1.6.0 behavior scenarios and adds 27 Phase 6 behaviors for the `ai-memory connect` CLI (copilot, cursor, windsurf, codex), integration catalog consistency, P0/P1 smoke script execution, and a backward-compatibility MCP surface marker.

## Coverage

| Spec Section | Behaviors Tested | Test Count |
|--------------|------------------|------------|
| 5 | Scope isolation, namespace isolation, supported scopes, persona_id default and named isolation (get + search) | 4 |
| 6, 10 | Normative response envelopes, governed-record keys, version-entry keys, belief/fact separation, append-only episode retrieval, subject request-shape validation, match_reason subject reflection, expires_at/blocks_actions/observation metadata | 14 |
| 8 | Atomic remember integration, supersession, retraction, direct `extends` propagation post-condition, episode append-only behavior | 7 |
| 9 | Current-state search boundaries, subject-filter partitioning, `subject` + `as_of` composition, `as_of` by `seq` and `recorded_at`, all four malformed `as_of` rejection shapes, provenance and validity metadata, expiry exclusion, blocks_actions recall | 16 |
| 10 | Eleven-tool MCP surface, per-tool minimum keys, standard error envelopes, `memory_status` required request fields, `memory_get` depth, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, and `memory_audit_export` | 33 |
| 11 | Index rebuild preservation, index-unavailable degradation, fresh-create durability, upgrade persistence | 4 |
| 12 | `invalid_scope`, `invalid_request`, `not_found`, `index_unavailable`, `integration_failed`, `consolidation_unavailable`, `review_unavailable`, `profile_unavailable`, `reflection_unavailable`, `extraction_disabled`, `pii_blocked`, `legal_hold`, `temporal_query_unavailable`, `audit_export_unavailable`, `fleet_sync_unavailable` | Covered across tool and maintenance scenarios |
| 13 | Consolidation remains explicit and non-destructive; no belief auto-promotion; review-gated promotion flow | 8 |
| 14.4 | Hermes prefetch/sync_turn/tool dispatch, Hermes recall_mode schema exposure, OpenClaw sandbox search/get behavior, outside-workspace rejection, adapter restart persistence | 5 |
| 17 | Profile engine opt-in, session summary bounds (including ellipsis prefix), `share_to` (get + search), context fencing post-conditions (known + unknown injection ids), memory_profile persona exclusion, memory_status operator metadata | 8 |
| 18 | Phase 4 validation hints: profile assembly, reflection citations, persona isolation, backward compatibility spot-check | Covered in Phase 4 groups above |
| 19, 20 | Graph derivation and rebuildability, trajectory parity and retraction visibility, PII block/redact, audit logging/export, legal hold, retention eviction, fleet mode visibility/lag policy, Phase 5-disabled backward compatibility | 26 |
| 21, 21.8 | Phase 6 connect CLI: `connect copilot` (empty merge, clobber refusal, `--replace`, `--dry-run`, merge-with-others, relative data-dir); `connect cursor` (project + global targets, `--dry-run`, absolute `MEMORY_SERVICE_DATA_DIR`); `connect windsurf` (global `mcp_config.json`, clobber refusal, `--replace`, `--dry-run`); `connect codex` (TOML merge, clobber refusal, `--replace`, `--dry-run`); unsupported harness exit 2; catalog P0 completeness and tier-B `connect_command`; catalog P1 tier-D `tier_target B`; smoke scripts for claude-code, copilot-cli, codex, cursor, windsurf; MCP surface unchanged marker | 27 |

**Total behavior cases:** 138

## Technology

The suite is pure Python and uses only the standard library. The harness includes:

- an MCP stdio client for `initialize`, `tools/list`, and `tools/call`
- a validation-only file-based control client for index rebuilds, graph rebuilds, graph enablement, PII-scan configuration, audit enablement, retention policy changes, explicit retention runs, fleet configuration, legacy explicit consolidation, profile-engine triggers, session-summary triggers, context fencing, and failure injection
- a deterministic JSON artifact writer that records one behavior result per spec-derived scenario
- Phase 6 subprocess shims that invoke `ai-memory connect <harness>` Python scripts against temporary directories for black-box CLI validation

## Validation Startup Contract

The harness consumes the application-owned startup contracts in `STARTUP-CONTRACT.md`.

- Orchestrator-facing validation entrypoint: `python3 tests/run_validation.py`
- App-owned startup entrypoint consumed by the harness: `MEMORY_SERVICE_VALIDATION_COMMAND`
- Adapter validation bridge entrypoints consumed by the harness: `MEMORY_SERVICE_VALIDATION_HERMES_COMMAND`, `MEMORY_SERVICE_VALIDATION_OPENCLAW_COMMAND`
- Transport under test: MCP over stdio
- Readiness signal: JSON file path supplied in `MEMORY_SERVICE_VALIDATION_READY_FILE`
- Validation-only maintenance surface: file control plane under `MEMORY_SERVICE_VALIDATION_CONTROL_DIR`
- Adapter transport under test: validation-only JSON-lines stdio bridge commands that exercise public adapter behavior without app-internal assertions

## Machine-Readable Results

Default artifact path: `tests/artifacts/latest-results.json`

Artifact format:

```json
{
  "spec_version": "1.6.0",
  "generated_at": "2026-06-24T12:34:56+00:00",
  "run_command": "python3 tests/run_validation.py",
  "results": [
    {
      "behavior": "memory_query_temporal matches independent memory_get(as_of) across a multi-subject topic partition.",
      "status": "pass",
      "group": "Temporal Query",
      "spec_section": "10.10, 19.2, 20",
      "notes": "Each returned trajectory point matched the independent memory_get(as_of) result for the same subject and audit point."
    },
    {
      "behavior": "memory_audit_export returns audit_export_unavailable through the standard error envelope.",
      "status": "fail",
      "group": "Error Codes",
      "spec_section": "10.11, 12, 19.4, 20",
      "reason": "memory_audit_export should return audit_export_unavailable when the fault is armed."
    }
  ]
}
```

## Coverage Notes

- v1.7.0 adds 14 Phase 6 behaviors covering the `ai-memory connect` CLI (copilot, cursor, windsurf, codex), integration catalog consistency, and the MCP backward-compatibility marker, while preserving all 111 v1.6.0 behavior cases unchanged.
- Phase 6 connect CLI tests invoke the Python connect scripts as subprocesses against isolated temporary directories.  No IDE installation (Cursor, Windsurf) is required.  The tests assert exit codes, stdout JSON/TOML, and written config file content.
- `connect copilot` coverage: empty-config merge; clobber refusal (exit 1); `--replace` overwrites only `memory-service` while preserving other servers; `--dry-run` prints merged JSON without writing to disk; merge-with-others (success when other servers exist but no `memory-service` entry); relative `--data-dir` resolved to absolute against `--project-root`.
- `connect cursor` coverage: project `.cursor/mcp.json` target (`--project-config`); global `~/.cursor/mcp.json` target (`--global-config`); `--dry-run`; all assert absolute `MEMORY_SERVICE_DATA_DIR`.
- `connect windsurf` coverage: global `mcp_config.json` target; clobber refusal; `--replace` preserves other servers; `--dry-run`.  Windsurf does not support project-level MCP config.
- `connect codex` coverage: TOML merge producing `[mcp_servers.memory-service]` + `[mcp_servers.memory-service.env]` with absolute `MEMORY_SERVICE_DATA_DIR`; clobber refusal; `--replace` preserves other TOML sections; `--dry-run`.  When `tomllib` is available (Python 3.11+), the TOML is structurally parsed; otherwise string-based assertions are used.
- Unsupported harness: `ai-memory connect <unknown>` exits with code 2 via the Node.js wrapper.  When Node.js is unavailable, the test skips with an explicit reason message.
- Catalog consistency: `integrations.yaml` must contain all eight P0 harness IDs at tier A or B; tier-B CLI-managed harnesses must carry a non-null `connect_command`; all three P1 harnesses (`vscode-copilot-agent`, `copilot-ide`, `gemini-cli`) must carry `tier D` and `tier_target B`.
- Smoke scripts: all five P0 tier B+ harness smoke scripts (`claude-code/phase3-smoke.sh`, `copilot/copilot-smoke.sh`, `codex/codex-smoke.sh`, `cursor/cursor-smoke.sh`, `windsurf/windsurf-smoke.sh`) are run as non-interactive subprocesses.  Node-dependent scripts skip with an explicit message when Node is unavailable; the claude-code script is pure Python and always runs.
- MCP backward-compatibility marker: explicitly asserts the eleven-tool MCP surface is unchanged after Phase 6 artifacts are loaded.
- v1.6.0 extends the MCP surface to eleven tools with `memory_query_temporal` and `memory_audit_export`, while preserving the existing nine-tool v1.5.0 behavior when Phase 5 features are disabled or omitted.
- All 85 v1.5.0 behavior cases remain in place; 26 Phase 5 behaviors were added without reading or adapting to application source.
- Temporal-query coverage asserts multi-subject trajectory parity with independent `memory_get(as_of)`, empty partition success, retraction visibility with `include_retracted` true/false, and the full Section 10.10 invalid-request set for empty `audit_points`, malformed audit-point selectors, and missing `topic`.
- Graph coverage uses ordinary `memory_remember` writes plus `include_graph_edges=true` to validate topic-entity projection, direct `depends_on` edges from `extends`, and graph rebuild repeatability without WAL-authority drift.
- PII coverage uses validation control actions to toggle scan enablement and policy, asserting `pii_blocked` in block mode and placeholder persistence in redact mode.
- Audit coverage uses validation control actions to enable logging, then asserts `memory_remember` write and `memory_get` read events plus JSONL export through `memory_audit_export`, malformed `since`/`until` and `format` rejections, and JSON export with `event_kinds`, `limit`, and `truncated` behavior.
- Retention and legal-hold coverage uses validation control actions rather than any invented MCP tool, asserting held-subject skip behavior and per-type retention eviction from default recall while preserving historical `as_of` access.
- Fleet coverage asserts `memory_status.fleet.mode = local` by default, exercises a lag policy that returns `fleet_sync_unavailable` without losing the locally committed WAL write, and covers the `fallback_local` read path in `fleet_replica` mode.
- Multi-hop dependency-chain graph expansion (research S4-2a) remains deferred by the published spec; the suite stays on the normative v1.6.0 direct-edge and trajectory surface.
- Phase 4 profile, reflection, persona, fencing, profile-engine, session-summary, and adapter coverage is unchanged and still consumes the same single orchestrator entrypoint.

## Revision History

| Date | Reason |
|------|--------|
| 2026-06-25 | Peer-review rework for spec v1.7.0: preserved all 125 prior cases; added 13 new behaviors: smoke scripts for claude-code/copilot-cli/codex/cursor/windsurf (CF-1); fixed unknown-harness exit-2 to skip explicitly when Node unavailable (CF-2); `--replace` tests for windsurf and codex preserving other entries (SG-1); copilot merge-with-others success path (SG-2); catalog P1 tier-D `tier_target B` assertion (SG-3); `--dry-run` for cursor/windsurf/codex (S-1); relative `--data-dir` resolution (S-2); bumped STARTUP-CONTRACT.md to v1.7.0 (S-4) |
| 2026-06-25 | Revised for spec v1.7.0 Phase 6: preserved all 111 v1.6.0 behavior cases, added 14 Phase 6 behaviors covering `connect copilot/cursor/windsurf/codex` CLI, unsupported-harness exit-2, catalog P0 completeness and tier-B `connect_command`, and MCP surface backward-compatibility marker; updated spec_version to 1.7.0 in runner artifact |
| 2026-06-24 | Peer-review rework for spec v1.6.0: added the full Section 10.10 invalid-request set for `memory_query_temporal`, added malformed-bound/format coverage plus JSON filter+limit+truncation coverage for `memory_audit_export`, added the fleet `fallback_local` read case, and documented the deferred S4-2a multi-hop graph scope |
| 2026-06-24 | Revised for spec v1.6.0 Phase 5: preserved all 85 v1.5.0 behavior cases, added 16 Phase 5 behaviors, updated the harness/startup contract to the eleven-tool surface, and expanded the validation control plane for graph, PII, audit, retention, and fleet actions |
| 2026-06-25 | Peer-review follow-up: persona isolation on memory_search, memory_profile persona exclusion, unknown injection_id fencing, session_summary ellipsis prefix, memory_status operator metadata (+2 cases → 85 total) |
| 2026-06-24 | Revised for spec v1.5.0 Phase 4: preserved all 62 v1.4.1 cases, added 19 Phase 4 behaviors, updated tool catalog to nine tools, expanded startup contract for profile engine / reflection / fencing control actions |
| 2026-06-24 | Peer-review rework (v1.4.0 adapters): OpenClaw empty-store `memory_get` now asserts an explicitly empty record; Hermes `sync_turn` now asserts session-scope `session_turn` episode persistence via `memory_get` after the non-blocking latency check |
| 2026-06-24 | Revised for spec v1.4.0: preserved all 57 MCP cases from Sections 5-13, added five Section 14.4 black-box adapter cases, switched run command metadata to `python3`, and documented the Hermes/OpenClaw validation bridge entrypoints |
| 2026-06-24 | Peer-review rework: review-flow case now seeds deterministic safe_merge clusters, requires ≥2 promotions (fail not skip), asserts pre-accept not_found for all pending subjects, and always exercises accept+reject when consolidation is available |
| 2026-06-24 | Revised in place for spec v1.3.0: seven-tool MCP surface, `memory_get.depth`, lifecycle metadata on `memory_remember`, `memory_status.review_queue`, `memory_consolidate`/`memory_review` flows, expiry and blocks_actions recall, new error codes |
| 2026-06-04 | Revised in place for spec v1.2.0 to add `memory_search.subject` coverage, request-shape validation, `match_reason` assertions, and subject/history corpus-partition checks while preserving the existing validation entrypoint |
| 2026-06-03 | Revised in place for spec v1.1.1 to enforce the normative wire contract, fix the restart durability bug, add governed metadata and append-only episode coverage, and collapse the startup contract to a single entrypoint |
| 2026-06-03 | Strengthened Section 5 scope-isolation coverage to check all ordered cross-scope pairs through both `memory_get` and `memory_search`, with same-scope positive controls and retraction cleanup via the published MCP surface |
