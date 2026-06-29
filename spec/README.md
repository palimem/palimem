# Memory Service Specification

**Component:** `memory-service`  
**Status:** Published  
**Version:** `1.7.0`  
**Last updated:** 2026-06-25

## 1. Purpose and scope

`memory-service` is the component-owned memory system for the repository's agentic workflow. It stores and recalls governed memory across `user`, `session`, and `repository` scopes.

This specification defines the component's external behavior:

- the append and recall contract exposed through the MCP tool surface
- the canonical memory record schema
- supersession, propagation, recall, and audit semantics
- the separation between durable state and disposable indexes

This specification does not define application packages, deployment topology, or storage technology beyond the behavioral constraints required to preserve the component contract.

## 2. Design rationale

The component optimizes for **governed correct state** over maximal evidence recall. Durable memory is an append-only write-ahead log with derived semantic units; search indexes are rebuildable and disposable.

The v1 default retriever is sparse lexical ranking (TF-IDF), not a neural embedding backend. Optional subject filters partition same-field collisions without changing WAL authority or `as_of` semantics.

Distribution: Apache-2.0 (see repository `LICENSE`). Implementation language: Python 3.13+ with a thin npm MCP launcher.

## 3. Design thesis

Agent memory is a state-trajectory problem, not a vector-search problem.

The component shall preserve three distinct layers:

| Layer | Role | Contract status |
|------|------|-----------------|
| Write-ahead log (WAL) | Immutable append-only event history | Source of truth |
| Semantic units | Derived current and historical memory state with validity intervals and dependencies | Required derived view |
| Indexes | Search acceleration structures such as TF-IDF, full text, vector, or graph indexes | Rebuildable and disposable |

For this version, the default ranking implementation shall be TF-IDF over the current-state search corpus. The component shall define ranking behind a stable retriever contract that can be replaced later without changing the WAL schema, semantic-unit schema, governance rules, or, absent an explicitly versioned additive extension, the existing MCP surface.

The minimum retriever contract for this version is behaviorally equivalent to:

```text
rank(query, candidates) -> ordered candidates
```

`candidates` shall be drawn from WAL-derived semantic units and any explicitly requested append-only episodes. A future embedding-based retriever may implement the same contract as a non-breaking extension, but embeddings are not part of the v1 requirement set.

The retriever may consult each candidate's structured subject key `(scope, namespace, topic, field, memory_type)` in addition to the candidate's serialized `value` text.

`memory_search` may also provide an optional resolved subject hint composed from the public `subject` filter defined in Sections 9 and 10. When no subject hint is supplied, `memory_search` behavior shall remain the same as the `1.1.1` contract for the same request. A future version may standardize service-side subject-hint inference as a non-breaking extension only if it continues to operate strictly over the governed candidate corpus defined in Section 9.3 and does not change the meaning of requests that omit the public `subject` filter.

The component shall support eleven first-class operations over that model:

1. append a memory event
2. integrate it into governed state
3. propagate dependent updates when relationships require it
4. recall current state by default and historical state on request
5. rebuild indexes from durable state without losing governed memory facts
6. run explicit, opt-in consolidation maintenance without mutating WAL authority
7. review and accept or reject consolidation-proposed promotions before they become governed current state
8. assemble a bounded user-profile manifest for injection (`memory_profile`)
9. synthesize across governed evidence with citations (`memory_reflect`)
10. query namespace- and topic-scoped belief trajectories across audit points (`memory_query_temporal`)
11. export operator audit records for compliance review (`memory_audit_export`)

## 4. Published surfaces

The component publishes two coordinated product surfaces:

| Surface | Responsibility |
|--------|----------------|
| Python package | Canonical Python implementation of the memory substrate, retriever contract, and MCP server entry point |
| Thin npm MCP wrapper | Launch-only wrapper for MCP runtimes; it contains no independent memory logic |

Both surfaces shall expose the same memory semantics and shall share the same component version.

This version standardizes on Python for the service implementation. This decision is consistent with the validated research prototypes and with `docs/research/R17-adr-license-distribution.md`. The component does not require a low-level-language rewrite in v1 because the measured latency bottleneck is the LLM call rather than governed memory operations. If later profiling identifies a hot path, a native extension may be introduced behind the retriever or index boundary without changing the component's public contract.

The project license for this component is Apache-2.0, per `docs/research/R17-adr-license-distribution.md`.

## 5. Scopes and identity

### 5.1 Supported scopes

The component shall support exactly three behavioral scopes:

| Scope | Meaning |
|------|---------|
| `user` | Global memory for a single user across workspaces |
| `session` | Conversation-scoped memory |
| `repository` | Memory scoped to the current repository or working tree |

Each stored record shall belong to exactly one scope.

### 5.2 Namespace model

Each scope shall also carry a namespace string so multiple logical memory spaces can coexist without colliding. The namespace is an explicit part of the record identity and recall filters.

### 5.3 Subject key

For governed memory types, the component shall identify a memory subject by:

`(scope, namespace, topic, field, memory_type)`

Supersession, recall, and audit behavior defined below operate on that subject key unless a section states otherwise.

### 5.4 Persona identity

The component shall support optional **persona** scoping for user-modeling and multi-agent isolation.

| Concept | Contract |
|---------|----------|
| `persona_id` | Optional string on read and write filters. When omitted on a write, the record is stored without a persona tag. When omitted on a read, the caller operates in the **default persona** context. |
| Default persona | The implicit persona context when `persona_id` is omitted on a read. Records stored without a `persona_id` tag are visible **only** in the default persona context. |
| Named persona | When a read supplies `persona_id = P`, the result set shall include records tagged `persona_id = P` plus records explicitly shared to `P` per Section 17.3. |
| Namespace interaction | Adapters that encode session or user namespaces per Section 16.1.1 shall not embed `persona_id` in the namespace string. Persona is a separate filter dimension on the same `(scope, namespace)` pair. |

`persona_id` is not part of the governed subject key `(scope, namespace, topic, field, memory_type)`. Two records with the same subject key but different `persona_id` values are distinct governed subjects.

## 6. Canonical record model

### 6.1 WAL event

Every externally visible mutation shall append one canonical WAL event before it becomes visible through current-state recall.

The canonical event schema is:

```json
{
  "event_id": "evt_000042",
  "seq": 42,
  "recorded_at": "2026-06-03T12:34:56Z",
  "scope": "repository",
  "namespace": "palimem",
  "kind": "write | retract | belief_write | episode_append",
  "memory_type": "preference | fact | procedure | constraint | episode | belief",
  "topic": "user_prefs",
  "field": "city",
  "value": "Berlin",
  "episode_id": "ep_session_12",
  "extends": [
    { "topic": "travel_profile", "field": "home_city" }
  ],
  "expires_at": "2026-12-31T23:59:59Z",
  "blocks_actions": ["deploy_prod", "delete_database"],
  "observation": {
    "kind": "tool_failure",
    "tool_name": "Bash",
    "exit_code": 1,
    "paths": ["/workspace/components/memory-service/app/run_validation.py"],
    "stderr_excerpt": "ModuleNotFoundError: No module named 'memory_service'"
  },
  "provenance": {
    "source": "mcp",
    "tool": "memory_remember",
    "actor": "agent",
    "request_id": "req_123"
  },
  "persona_id": "default",
  "derived_from": ["ep_session_12", "ep_session_15"],
  "legal_hold": false
}
```

The component shall enforce the following rules:

- `event_id` and `seq` are immutable once assigned.
- `seq` is strictly increasing within one component instance's durable history.
- `recorded_at` is the service commit timestamp used for audit ordering.
- `scope`, `namespace`, `topic`, `field`, and `memory_type` are required for every event except `episode_append`, which may omit `field` when the episode is intentionally unstructured.
- `extends` is optional and declares dependent facts that must be reconsidered when the written subject changes.
- `expires_at` is optional. When present, it shall be an RFC3339 UTC timestamp after which the written subject is excluded from default current-state recall. Expiry does not delete WAL history and does not alter `as_of` audit behavior before the expiry point.
- `blocks_actions` is optional. When present, it shall be a JSON array of one or more non-empty action-name strings. It is valid only for `constraint` records and for `fact` records whose `topic` is `action_boundary`. The component shall persist and return `blocks_actions` metadata but shall not enforce caller-side action execution by itself.
- `observation` is optional. When present on an `episode_append` event, it shall be an object with required `kind` and optional `tool_name`, `exit_code`, `paths`, and `stderr_excerpt`. If `observation` is supplied, `value` shall be a JSON object that includes the observation fields and any additional caller-provided payload keys.
- `provenance` is required for every event created through the public MCP surface.
- `persona_id` is optional. When present, it tags the record for persona-scoped recall per Section 5.4. When omitted, the record is visible only in the default persona context.
- `derived_from` is optional. When present, it shall be a JSON array of one or more non-empty episode identifier strings. It is required when `memory_type` is `belief` and `provenance.source` is `profile_engine`. It is recommended for any `belief` write that synthesizes across episodes.
- `legal_hold` is optional (v1.6.0). When `true`, the written subject shall be protected from `memory_forget` and retention eviction per Section 19.5.2. Defaults to `false` when omitted.

### 6.2 Semantic unit

The component shall materialize each governed subject as a derived semantic unit:

```json
{
  "scope": "repository",
  "namespace": "palimem",
  "topic": "user_prefs",
  "field": "city",
  "memory_type": "preference",
  "versions": [
    {
      "value": "London",
      "seq": 1,
      "valid_from_seq": 1,
      "valid_to_seq": 2,
      "recorded_at": "2026-06-03T11:00:00Z",
      "episode_id": "ep_0",
      "event_id": "evt_000001"
    },
    {
      "value": "Berlin",
      "seq": 2,
      "valid_from_seq": 2,
      "valid_to_seq": null,
      "recorded_at": "2026-06-03T12:00:00Z",
      "episode_id": "ep_1",
      "event_id": "evt_000002"
    }
  ],
  "salience": 1.0,
  "extends": [
    { "topic": "travel_profile", "field": "home_city" }
  ]
}
```

The semantic unit is a derived view, not an independent source of truth. The component may rebuild semantic units from the WAL without changing observable behavior.

Within a semantic unit version entry, `seq` identifies the originating WAL event and is therefore equal to that version's `valid_from_seq`.

### 6.3 Memory types

The public contract recognizes the following memory types:

| Memory type | Behavior |
|------------|----------|
| `preference` | Current-state value with supersession |
| `fact` | Current-state value with supersession |
| `procedure` | Versioned operational knowledge; current version is recalled by default |
| `constraint` | Current-state rule or limitation with validity tracking |
| `episode` | Append-only episode history |
| `belief` | Derived or low-trust memory tracked separately from facts |

`belief` entries shall not supersede `fact` entries for the same `topic` and `field`. They occupy a separate memory type and must be recalled as such.

`belief` records with `derived_from` shall preserve that array on recall. Callers and adapters shall treat `derived_from` as provenance linking the belief to originating episodes, not as supersession edges.

### 6.4 Action boundaries and expiry

Governed records may carry optional action-boundary metadata:

| Field | Applies to | Behavior |
|-------|------------|----------|
| `expires_at` | Any governed memory type except `episode` | After the commit timestamp passes `expires_at`, the subject shall be excluded from default current-state recall and default current-state search. Historical `as_of` recall before expiry shall remain available. |
| `blocks_actions` | `constraint` and `fact` with `topic = action_boundary` | Returned on recall for caller-side enforcement. The service shall not block MCP tool execution based on this metadata in v1.3.0. |

Recommended harness profile for tool-failure observations written as `episode` records:

| Field | Value |
|-------|-------|
| `memory_type` | `episode` |
| `topic` | `tool_observation` |
| `field` | `failure` |
| `observation.kind` | `tool_failure` |

Callers that omit `observation` may still write unstructured episode text; the structured profile exists so hooks can parse failures deterministically.

### 6.5 Consolidation review promotions

Consolidation may propose promotions that require explicit human or operator review before becoming governed current state. A promotion proposal is not current-state memory until accepted.

Each promotion proposal shall carry:

| Field | Required | Notes |
|-------|----------|-------|
| `review_id` | Yes | Stable identifier within one namespace |
| `proposed_memory_type` | Yes | `belief` or `fact` |
| `topic` | Yes | Target subject family |
| `field` | Yes unless `episode` | Target subject field |
| `value` | Yes | Proposed payload |
| `rationale` | Yes | Human-readable explanation of why consolidation proposed the item |
| `source_seqs` | Yes | WAL sequence numbers that informed the proposal |

Accepting a promotion shall integrate it through the normal write path. Rejecting a promotion shall mark it rejected without creating governed current state for the proposed subject.

## 7. Behavioral invariants

The component shall satisfy all of the following invariants:

1. Every successful mutation is durably represented as a WAL event.
2. Current-state recall returns only versions whose validity interval is open at the requested recall point.
3. Superseded values are excluded from default search and default lookup results.
4. Historical recall is possible by `as_of` audit queries against the WAL-derived validity intervals.
5. Indexes may be discarded and rebuilt without changing the durable current or historical state returned by the service.
6. Correctness-bearing supersession and dependent-field propagation occur on ingest, not as a deferred consolidation prerequisite.
7. A failed or missing index rebuild must degrade search availability, not corrupt WAL history or semantic units.
8. Replacing the ranking implementation behind the retriever contract must not require a WAL migration, semantic-unit schema change, or MCP surface change.
9. Consolidation, if implemented, is an explicit opt-in operation rather than an automatic background mutation, and current-state correctness must remain identical whether consolidation has never run, has run once, or is temporarily unavailable.
10. Consolidation shall never mutate the append-only WAL. It may only compact or summarize the derived semantic-unit or index view in a way that preserves recovery of affected units from WAL history.
11. Expired governed records shall be excluded from default current-state recall and search but shall remain recoverable through `as_of` evaluation before expiry and through direct audit of WAL history.
12. Promotion proposals produced by consolidation shall not become governed current state until explicitly accepted through `memory_review`.

These invariants express the design wedge validated by `docs/research/R14-stage2-scorecard.md` and `docs/research/R16-stage3-scorecard.md`.

## 8. Write, supersession, and propagation semantics

### 8.1 Write path

`memory_remember` shall execute the following steps atomically from the caller's perspective:

1. append a WAL event
2. integrate it into the subject's semantic unit
3. close any previously open version on the same subject key when the memory type is superseding
4. propagate dependent updates for affected units
5. schedule or perform index synchronization

The write is successful only when steps 1 through 4 complete. Index synchronization may complete after the mutation becomes visible, provided the component reports index freshness through `memory_status` and continues to honor direct lookup semantics from WAL-derived state.

### 8.2 Superseding types

`preference`, `fact`, and `constraint` are superseding types.

For these types, a new write on the same subject key shall:

- create a new version with an open validity interval
- close the previously open version by setting its `valid_to_seq` to the new event's sequence number
- make only the new version visible to default recall

### 8.3 Procedure versioning

`procedure` writes are versioned governed memory. A new procedure write shall become the current version for default recall while preserving prior versions for `as_of` audit.

### 8.4 Episode append behavior

`episode` writes are append-only and do not supersede one another unless a future version of this specification defines explicit episode compaction semantics.

### 8.5 Retraction behavior

`memory_forget` shall append a `retract` WAL event rather than mutating prior history in place.

When the target subject's current version carries `legal_hold = true` per Section 19.5.2, `memory_forget` shall return `error.code = legal_hold` without appending a `retract` event.

For superseding memory types, a retract operation shall:

- close the currently open version for the subject key
- remove the subject from default current-state recall
- preserve prior history for audit unless a later specification adds a stricter physical-deletion contract

This version of the specification does not define irreversible physical erasure semantics.

#### 8.5.1 Negation without replacement (S4-1e)

Stage 4 direction **S4-1e** (`docs/research/STAGE4-issues.md`) requires representing "X is no longer true" without supplying a replacement value. Supersession-by-overwrite cannot satisfy that probe because it requires a new governed value to close the prior version.

The research substrate evaluation confirms the wedge: `graphiti_lite` matches `bitemporal` on stale invalidation but scores **0%** on `dep_chain` because validity-window closure alone does not propagate consequences (`docs/research/R14-stage2-scorecard.md` substrate table). The shipped GEM model closes invalidation through governed supersession **and** records explicit removal through append-only `retract` WAL events — the same pattern R3 §7 (Vulcan / bi-temporal Datalog) describes as "retraction preserves history."

**v1.6.0 decision:** `memory_forget` / `retract` WAL events are the normative negation surface. The specification shall **not** introduce a separate negation `memory_type`. Phase 5 adds trajectory visibility for retracted subjects (`include_retracted` on `memory_query_temporal`, Section 19.2.1) to support the S4-2c retraction measurement suite without changing the write surface.

### 8.6 Dependent-field propagation

Dependent-field propagation is part of the normative contract.

When a governed subject declares `extends` relationships, the component shall update dependent semantic units on ingest so that recall of those dependent units reflects the new current parent state without requiring a later full consolidation pass.

The propagation mechanism shall be schema-driven rather than hard-coded to a fixed field list. This requirement is grounded in `docs/research/R16-stage3-scorecard.md`, which records `gem_full` at 100% propagation on generalized dependent fields while rule-based `gem_lite` achieves only 25% on the same probe.

For v1 wire-observable behavior, propagation is required only for direct `extends` edges declared on the dependent unit. Recursive or multi-hop closure across chains of dependencies is not required by this version.

For each direct `extends` edge, the component shall treat the parent subject's then-current serialized `value` at the time the dependent version becomes current as the bound parent value for that dependent version.

When that parent subject is later superseded, the component shall, during ingest of the parent change:

- close the previously open dependent version by setting its `valid_to_seq` to the parent change sequence
- materialize a new open dependent version for the same dependent subject key
- ensure the new dependent `value` no longer contains the stale bound parent value and does reflect the parent's new current value

A compliant implementation may satisfy that contract by value-substitution binding, but the mechanism is otherwise unconstrained so long as the required post-condition holds and direct recall observes the new dependent version immediately after the parent write succeeds.

## 9. Recall, search, and audit semantics

### 9.1 Default recall mode

Unless the caller supplies `as_of`, all recall operations shall use current-state mode.

In current-state mode the component shall:

- return only open validity intervals, meaning versions whose `valid_to_seq` is `null`
- exclude superseded values from ranked search results
- exclude governed records whose `expires_at` is present and strictly before the evaluation commit time
- include provenance sufficient for callers to explain why a value is current

For `memory_search`, the caller may also supply an optional `subject` filter object containing `topic`, `field`, or both. Each supplied key shall act as an equality constraint against the candidate record's subject key before ranking. When `subject` is omitted, search behavior shall remain the same as the `1.1.1` contract for the same request.

### 9.2 Historical recall mode

When the caller supplies `as_of`, the component shall evaluate validity intervals at that point in history and return the value that was current then, or no current value if the subject had none.

Historical recall shall be available through direct lookup and search-filtered retrieval.

`as_of` is part of the public request contract only for `memory_search` and `memory_get`.

`as_of` shall be encoded as an object with exactly one of the following keys:

```json
{ "seq": 42 }
```

or

```json
{ "recorded_at": "2026-06-03T12:34:56Z" }
```

The component shall reject any request that supplies both keys, neither key, a non-integer `seq`, or a non-RFC3339-UTC `recorded_at` string with `invalid_request`.

The evaluation point is inclusive:

- `as_of.seq = N` means the state immediately after successful integration of WAL event sequence `N`
- `as_of.recorded_at = T` means the state immediately after the highest WAL `seq` whose `recorded_at` is less than or equal to `T`

When historical evaluation resolves to sequence `N`, a version is current at that audit point if and only if `valid_from_seq <= N` and (`valid_to_seq` is `null` or `N < valid_to_seq`). If no WAL event exists at or before the requested audit point, the subject has no current value at that point.

When `memory_search` combines `as_of` with a `subject` filter, the component shall first evaluate the governed corpus at the requested audit point and shall then apply the same `subject` equality constraints before ranking.

### 9.3 Search corpus boundaries

Search indexes may accelerate retrieval, but the searchable current-state corpus shall be limited to current semantic units plus any explicitly requested append-only episodes.

Superseded governed values shall not be indexed into the default current-state search corpus.

The default search implementation in this version shall rank that corpus with TF-IDF through the retriever contract defined in Section 3. `subject` filtering is a deterministic partition on already-stored subject-key fields and shall not expand the searchable corpus, admit superseded governed values, or change rebuildability guarantees.

Alternative rankers may be added in future versions only if they preserve the current-state corpus boundary, rebuildability guarantees, current versus `as_of` semantics, and the rule that a supplied `subject` filter narrows the governed candidate set before final ordering.

### 9.4 Premise-safe retrieval support

This component version does not require LLM-side premise checking inside the service. It does require recall responses to include enough validity and provenance metadata for a caller or wrapper to detect when a user-supplied premise conflicts with current state.

The service shall therefore return, for each governed result:

- the subject key
- the current or historical validity interval used
- the originating `event_id` and `seq`
- whether the result was returned from current-state or `as_of` evaluation
- `expires_at` and `blocks_actions` when present on the returned version

### 9.5 Action-boundary recall

When a caller recalls `constraint` records or `fact` records with `topic = action_boundary`, the component shall return any persisted `blocks_actions` metadata unchanged.

The component shall not reject unrelated MCP requests solely because a matching `blocks_actions` entry exists. Enforcement remains caller-side in v1.3.0.

## 10. MCP tool contract

The published tool surface consists of exactly **eleven** MCP tools (Sections 10.1–10.11). The nine tools through v1.5.0 (Sections 10.1–10.9) retain v1.5.0 semantics when Phase 5 request fields are omitted. Phase 5 tools and fields (Sections 10.10–10.11, Section 19) are additive; unsupported Phase 5 capabilities shall return the standard unavailable error codes defined in Section 12.

Unless a tool section states otherwise, every tool response shall be exactly one of the following envelopes. Implementations may add fields, but they shall include at least the normative minimum keys defined here.

Successful response envelope:

```json
{
  "ok": true,
  "tool": "memory_get"
}
```

Error response envelope:

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

For every error response, `error.code` shall be one of the public failure categories defined in Section 12. `error.message` shall be a human-readable explanation of the failure. Implementations may add machine-readable detail fields inside `error`, but they shall not omit `code` or `message`.

When a successful response returns governed memory records, each returned record shall include at least the following keys unless the tool section narrows the set further:

| Key | Required | Notes |
|-----|----------|-------|
| `scope` | Yes | `user`, `session`, or `repository` |
| `namespace` | Yes | Logical memory namespace |
| `topic` | Yes | Subject family |
| `field` | Yes; may be `null` for unstructured `episode` records | Subject field |
| `memory_type` | Yes | Public memory type |
| `value` | Yes | Serialized payload returned at the evaluated recall point |
| `event_id` | Yes | Originating WAL event identifier for the returned version |
| `seq` | Yes | Originating WAL sequence number for the returned version |
| `valid_from_seq` | Yes | Inclusive lower validity bound for the returned version |
| `valid_to_seq` | Yes | Exclusive upper validity bound; `null` when open |
| `recorded_at` | Yes | Commit timestamp for the returned version |
| `provenance` | Yes | WAL provenance object for the returned version |
| `salience` | Yes | Numeric salience for `semantic_unit` records; `null` is permitted for `episode` records |
| `layer` | Yes | `semantic_unit` or `episode` |
| `status` | Yes | `current` or `historical` |
| `expires_at` | No | Present when the stored version carries an expiry timestamp |
| `blocks_actions` | No | Present when the stored version carries action-boundary metadata |
| `persona_id` | No | Present when the stored version is persona-tagged |
| `derived_from` | No | Present when the stored version links to originating episodes |
| `legal_hold` | No | v1.6.0 — present when the stored version is under legal hold |

When a tool returns `versions`, each version entry shall include at least `value`, `event_id`, `seq`, `valid_from_seq`, `valid_to_seq`, and `recorded_at`.

### 10.1 `memory_remember`

Purpose: append and integrate a memory mutation.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | `user`, `session`, or `repository` |
| `namespace` | Yes | Logical memory namespace |
| `memory_type` | Yes | One of the six public memory types |
| `topic` | Yes | Subject family |
| `field` | No for `episode`, yes otherwise | Subject field |
| `value` | Yes | Serialized payload |
| `episode_id` | No | Optional origin episode |
| `extends` | No | Dependent-field declarations |
| `expires_at` | No | RFC3339 UTC timestamp after which the subject is excluded from default current-state recall |
| `blocks_actions` | No | Array of action-name strings; valid only for `constraint` or `fact` with `topic = action_boundary` |
| `observation` | No | Structured observation envelope; valid only for `episode` writes |
| `persona_id` | No | Persona tag for multi-persona scoping per Section 5.4 |
| `derived_from` | No | Array of episode identifiers; required for `belief` writes with `provenance.source = profile_engine` |
| `share_to` | No | Array of persona id strings; grants cross-persona read visibility per Section 17.3 |
| `legal_hold` | No | v1.6.0 — when `true`, blocks `memory_forget` and retention eviction for the written subject per Section 19.5 |
| `provenance` | Yes | Tool and actor metadata |

When PII pre-store scanning is enabled for the namespace per Section 19.3, `memory_remember` shall run the scan on the serialized `value` (and any structured `observation` payload) before WAL append. Scanning is a write-path pipeline hook, not a separate MCP tool.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_remember` |
| `subject` | Yes | Object containing `scope`, `namespace`, `topic`, `field`, and `memory_type` |
| `event_id` | Yes | WAL event identifier for the accepted write |
| `seq` | Yes | WAL sequence number for the accepted write |
| `integration_status` | Yes | Shall be `integrated` |
| `current_version` | Yes | Object containing at least `value`, `event_id`, `seq`, `valid_from_seq`, `valid_to_seq`, and `recorded_at` for the newly current version |

### 10.2 `memory_search`

Purpose: search governed memory state.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Search namespace |
| `query` | Yes | Free text or structured query |
| `memory_types` | No | Filter subset |
| `subject` | No | Object containing `topic`, `field`, or both; each supplied key is an equality filter on the candidate subject key |
| `as_of` | No | Historical evaluation point object as defined in Section 9.2 |
| `include_episodes` | No | Defaults to `false` |
| `persona_id` | No | Persona filter per Section 5.4; defaults to default persona context |
| `limit` | No | Defaults implementation-defined, but deterministic |

The component shall reject any `subject` object that supplies neither `topic` nor `field`, or that contains keys other than `topic` and `field`, with `invalid_request`.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_search` |
| `scope` | Yes | Scope evaluated by the search |
| `namespace` | Yes | Namespace evaluated by the search |
| `evaluation_mode` | Yes | `current` when `as_of` is omitted, otherwise `as_of` |
| `results` | Yes | Ordered array of search results |

Each entry in `results` shall include the common governed-record keys defined above plus `match_reason`, which shall briefly explain why the result matched or ranked.

When a `subject` filter is supplied, `match_reason` shall reflect that the result satisfied the applied subject constraint.

`memory_search` shall never return a superseded governed value as current.

### 10.3 `memory_get`

Purpose: direct lookup by subject key.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Lookup namespace |
| `topic` | Yes | Subject family |
| `field` | No for `episode`, yes otherwise | Subject field |
| `memory_type` | Yes | Requested memory type |
| `as_of` | No | Historical evaluation point object as defined in Section 9.2 |
| `include_versions` | No | If `true`, return the full version chain |
| `depth` | No | `full` or `summary`; defaults to `full` |
| `persona_id` | No | Persona filter per Section 5.4; defaults to default persona context |

When `depth = summary`, the component shall return the same governed record shape as `depth = full`, but `value` shall be truncated to at most 256 Unicode scalar values. If truncation occurs, the implementation shall append an ellipsis (`...`) after the truncated prefix. `depth = summary` shall not change subject identity, validity metadata, or provenance.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_get` |
| `scope` | Yes | Scope evaluated by the lookup |
| `namespace` | Yes | Namespace evaluated by the lookup |
| `evaluation_mode` | Yes | `current` when `as_of` is omitted, otherwise `as_of` |
| `record` | Yes | One governed memory record using the common keys defined above |
| `versions` | No | Present only when `include_versions` is `true`; ordered newest-first or oldest-first is implementation-defined but shall be consistent within one implementation |

If the requested subject has no value at the requested recall point, `memory_get` shall return the standard error envelope with `error.code = not_found`.

When `depth` is omitted, `memory_get` behavior shall remain the same as the `1.2.0` contract for the same request.

### 10.4 `memory_forget`

Purpose: retract governed memory from current recall.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Retraction namespace |
| `topic` | Yes | Subject family |
| `field` | No for `episode`, yes otherwise | Subject field |
| `memory_type` | Yes | Target memory type |
| `persona_id` | No | Persona filter per Section 5.4; when supplied, retraction applies only to the persona-tagged subject |
| `provenance` | Yes | Caller metadata |

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_forget` |
| `subject` | Yes | Object containing `scope`, `namespace`, `topic`, `field`, and `memory_type` |
| `event_id` | Yes | WAL event identifier for the retraction |
| `seq` | Yes | WAL sequence number for the retraction |
| `retraction_status` | Yes | Shall be `retracted` |
| `current_visibility` | Yes | Shall be `hidden` to confirm the subject is no longer returned by default current-state recall |

### 10.5 `memory_status`

Purpose: expose service and namespace state.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request; `user`, `session`, or `repository` |
| `namespace` | Yes | Namespace whose status is being requested |

The component shall reject any `memory_status` request that omits either field with `invalid_request`. `memory_status` does not define a default scope or default namespace.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_status` |
| `scope` | Yes | Scope evaluated by the status call |
| `namespace` | Yes | Namespace evaluated by the status call |
| `supported_scopes` | Yes | Array containing exactly `user`, `session`, and `repository` |
| `wal_high_water_seq` | Yes | Highest durable WAL `seq` visible in the namespace |
| `semantic_units_in_sync` | Yes | Boolean indicating whether semantic units have caught up with the WAL |
| `index_status` | Yes | Object containing at least `state`, where `state` is `current`, `stale`, or `unavailable` |
| `consolidation` | Yes | Object containing `available` and `last_run_at`; `last_run_at` shall be `null` when consolidation has never run or is unsupported |
| `review_queue` | Yes | Object containing `pending_count`; shall be `0` when review is unsupported or no promotions are pending |
| `profile_engine` | Yes | Object containing `enabled` (boolean) and `last_run_at` (`null` when never run or unsupported) |
| `session_summary` | Yes | Object containing `topic` (shall be `session_summary` when present) and `last_updated_seq` (`null` when no summary exists) |
| `pii_scan` | No | v1.6.0 — object containing `enabled` and `policy` when PII scanning is configured; omit or return `enabled: false` when unsupported |
| `retention` | No | v1.6.0 — object containing `enabled`, `policies`, and `legal_hold_count` when retention is configured |
| `fleet` | No | v1.6.0 — object containing `mode`, `backend_reachable`, `last_synced_seq`, and `replica_lag_seq` when fleet backend is configured; omit or return `mode: local` when unsupported |

`memory_status` may report consolidation metadata when available, but the correctness of current-state recall, supersession, retraction visibility, and dependent-field propagation shall not depend on consolidation having run.

If an implementation supports consolidation, it shall expose it only as an explicit caller- or operator-invoked maintenance operation. This specification does not require consolidation support, but if supported the operation shall satisfy all of the following constraints:

- it shall not run automatically in the background as part of the correctness path
- it may deduplicate redundant units and collapse clusters of low-salience, noise-class entries into a bounded summary unit
- it shall not remove, evict, or hide any unit that is the current value of a held `fact`
- any affected unit shall remain recoverable from the immutable WAL, which remains the sole source of truth
- failure or omission of consolidation shall not change the correct result of current-state recall, direct lookup, retraction visibility, or dependent-field propagation

### 10.6 `memory_consolidate`

Purpose: run explicit, opt-in consolidation maintenance for one namespace.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Namespace to consolidate |
| `dry_run` | No | Defaults to `false`; when `true`, report the planned result without mutating derived state |

The component shall reject any request that omits `scope` or `namespace` with `invalid_request`.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_consolidate` |
| `scope` | Yes | Scope consolidated |
| `namespace` | Yes | Namespace consolidated |
| `available` | Yes | `true` when consolidation is supported in this implementation |
| `dry_run` | Yes | Echo of the request flag |
| `last_run_at` | Yes | Timestamp of the most recent successful non-dry consolidation run for this namespace; `null` when consolidation has never completed successfully. A `dry_run` request shall not advance `last_run_at`. |
| `stats` | Yes | Object containing at least `units_before`, `units_after`, `bytes_before`, and `bytes_after` for the active derived view |
| `promotions` | Yes | Array of promotion proposals as defined in Section 6.5; may be empty |

When consolidation is unsupported, `memory_consolidate` shall return the standard error envelope with `error.code = consolidation_unavailable`.

When `dry_run = true`, the component shall not mutate derived semantic units, indexes, or review-queue state, but shall still return the `stats` and `promotions` that would result from a non-dry run.

A non-dry consolidation run shall satisfy all consolidation constraints defined in Section 10.5 and Section 11. It may deduplicate redundant units and summarize low-salience noise clusters using the validated `safe_merge` policy from `research/results/stage4-consolidation.json`. It shall not remove, evict, or hide any unit that is the current value of a held `fact`.

### 10.7 `memory_review`

Purpose: list, accept, or reject consolidation promotion proposals.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Namespace whose review queue is being accessed |
| `action` | Yes | `list`, `accept`, or `reject` |
| `review_id` | No | Required for `accept` and `reject` |
| `limit` | No | For `list`; defaults implementation-defined but deterministic |

The component shall reject any request with an unknown `action`, a missing `review_id` on `accept` or `reject`, or a `review_id` that does not identify a pending promotion with `invalid_request`.

Successful response minimum keys for `action = list`:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_review` |
| `action` | Yes | Shall be `list` |
| `scope` | Yes | Scope evaluated |
| `namespace` | Yes | Namespace evaluated |
| `pending` | Yes | Array of promotion proposals as defined in Section 6.5 |

Successful response minimum keys for `action = accept`:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_review` |
| `action` | Yes | Shall be `accept` |
| `review_id` | Yes | Accepted promotion identifier |
| `integration_status` | Yes | Shall be `integrated` |
| `subject` | Yes | Subject key of the accepted promotion |
| `event_id` | Yes | WAL event identifier for the accepted write |
| `seq` | Yes | WAL sequence number for the accepted write |

Successful response minimum keys for `action = reject`:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_review` |
| `action` | Yes | Shall be `reject` |
| `review_id` | Yes | Rejected promotion identifier |
| `review_status` | Yes | Shall be `rejected` |

Accepting a promotion shall integrate the proposed subject through the normal write path. Rejecting a promotion shall remove it from the pending review queue without creating governed current state for the proposed subject. A rejected promotion shall not reappear unless consolidation proposes it again in a later run.

When review is unsupported, `memory_review` shall return the standard error envelope with `error.code = review_unavailable`.

### 10.8 `memory_profile`

Purpose: return a bounded user-profile manifest suitable for prompt injection, assembled from governed `preference`, `procedure`, `constraint`, and eligible `belief` records in `user` scope.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | Shall be `user` for this tool |
| `namespace` | Yes | Profile namespace |
| `persona_id` | No | Persona filter per Section 5.4; defaults to default persona context |
| `depth` | No | `summary` or `full`; defaults to `summary` |
| `budget_tokens` | No | Positive integer upper bound on manifest size; when omitted, implementations shall use a deterministic default of `2048` Unicode scalars |
| `memory_types` | No | Subset filter; defaults to `preference`, `procedure`, `constraint`, and `belief` |

The component shall reject any request whose `scope` is not `user` with `invalid_request`.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_profile` |
| `scope` | Yes | Shall be `user` |
| `namespace` | Yes | Namespace evaluated |
| `persona_id` | Yes | Persona context used for assembly; `null` when default persona |
| `depth` | Yes | Echo of effective depth |
| `budget_tokens` | Yes | Effective scalar budget applied |
| `manifest` | Yes | Bounded text block for injection |
| `sections` | Yes | Array of section objects, each containing at least `heading`, `text`, and `citations` |
| `citations` | Yes | Top-level deduplicated array of citation objects backing the manifest |

Each citation object shall include at least:

| Key | Required | Notes |
|-----|----------|-------|
| `scope` | Yes | Governed record scope |
| `namespace` | Yes | Governed record namespace |
| `topic` | Yes | Subject family |
| `field` | Yes; may be `null` for unstructured episodes | Subject field |
| `memory_type` | Yes | Public memory type |
| `seq` | Yes | WAL sequence number for the cited version |
| `event_id` | Yes | WAL event identifier for the cited version |

Assembly rules:

- `belief` records with default profile-engine salience (Section 17.1) shall rank below `preference` and `procedure` records when trimming to `budget_tokens`.
- Records excluded by persona filtering shall not appear in `manifest`, `sections`, or `citations`.
- When no eligible records exist, `manifest` shall be an empty string, `sections` shall be an empty array, and `citations` shall be an empty array. This is success, not `not_found`.

When profile assembly is unsupported or profile extraction is disabled and no governed profile records exist to assemble, implementations may still return an empty success envelope. When profile assembly cannot run due to implementation outage, `memory_profile` shall return the standard error envelope with `error.code = profile_unavailable`.

### 10.9 `memory_reflect`

Purpose: synthesize an answer to a natural-language `query` across governed evidence with explicit citations. This tool is read-only and shall not append WAL events.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Namespace to search |
| `query` | Yes | Non-empty natural-language question or synthesis prompt |
| `persona_id` | No | Persona filter per Section 5.4 |
| `memory_types` | No | Subset filter on evidence |
| `subject` | No | Optional subject partition per Section 9.1 |
| `limit` | No | Maximum evidence records to consider; defaults to `10` |
| `as_of` | No | Historical evaluation point per Section 9.2 |

The component shall reject requests with an empty `query` with `invalid_request`.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_reflect` |
| `scope` | Yes | Scope evaluated |
| `namespace` | Yes | Namespace evaluated |
| `evaluation_mode` | Yes | `current` when `as_of` is omitted, otherwise `as_of` |
| `query` | Yes | Echo of request query |
| `synthesis` | Yes | Synthesized text grounded in cited evidence; empty string when no evidence matches |
| `citations` | Yes | Array of citation objects, one per evidence record used |
| `evidence_count` | Yes | Number of governed records considered |

Each entry in `citations` shall include at least `scope`, `namespace`, `topic`, `field`, `memory_type`, `seq`, and `event_id`, matching the citation shape defined in Section 10.8.

Synthesis rules:

- The service shall ground synthesis only in governed records returned as evidence for the request. It shall not invent facts absent from cited records.
- When `evidence_count = 0`, the component shall return `synthesis = ""`, `citations = []`, and `ok = true`.
- Reflection may use an external LLM through the pluggable adapter contract defined in Section 17.1, but the MCP response shall always expose machine-readable `citations[]` even when synthesis text is empty.
- `memory_reflect` shall never perform silent WAL writes, promotion, or consolidation.

When reflection is unsupported or the configured LLM adapter is unavailable, `memory_reflect` shall return the standard error envelope with `error.code = reflection_unavailable`.

### 10.10 `memory_query_temporal`

**Status:** Published in v1.6.0 (Section 19.2).

Purpose: return a **belief trajectory** — the ordered sequence of governed values for subjects matching caller-supplied scope filters across one or more audit points — without requiring the caller to issue one `memory_get` or `memory_search` call per subject per timestamp. This tool extends Section 19.2 temporal query semantics beyond single-subject `as_of` lookup.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Namespace to evaluate |
| `topic` | Yes | Subject family partition; required for trajectory queries |
| `field` | No | When supplied, narrows to one subject field within `topic` |
| `memory_types` | No | Subset filter; defaults to all superseding governed types (`preference`, `fact`, `procedure`, `constraint`, `belief`) |
| `persona_id` | No | Persona filter per Section 5.4 |
| `audit_points` | Yes | Non-empty array of audit-point objects, each using the same encoding as Section 9.2 `as_of` (`seq` or `recorded_at`, exactly one key per element) |
| `include_retracted` | No | Defaults to `false`; when `true`, include intervals closed by `retract` events in the trajectory |
| `include_graph_edges` | No | Defaults to `false`; when `true` and graph support is enabled per Section 19.1, include derived entity/edge snapshots at each audit point |

The component shall reject requests with an empty `audit_points` array, any audit-point object with both or neither `seq` and `recorded_at`, or a missing `topic` with `invalid_request`.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_query_temporal` |
| `scope` | Yes | Scope evaluated |
| `namespace` | Yes | Namespace evaluated |
| `topic` | Yes | Topic partition evaluated |
| `trajectories` | Yes | Array of trajectory objects |

Each trajectory object shall include at least:

| Key | Required | Notes |
|-----|----------|-------|
| `subject` | Yes | Object containing `scope`, `namespace`, `topic`, `field`, and `memory_type` |
| `points` | Yes | Array aligned with `audit_points` order; each point contains `audit_point`, `value` (or `null` when no current value existed), `status` (`current`, `historical`, `retracted`, or `absent`), `seq`, `event_id`, `valid_from_seq`, `valid_to_seq`, and `recorded_at` when a version exists |

When `include_graph_edges = true`, each point may additionally include `graph_snapshot` with `entities[]` and `edges[]` as defined in Section 19.1.3.

When temporal trajectory queries are unsupported, `memory_query_temporal` shall return the standard error envelope with `error.code = temporal_query_unavailable`.

### 10.11 `memory_audit_export`

**Status:** Published in v1.6.0 (Section 19.2).

Purpose: export an append-only **operator audit log** (Section 19.4) for compliance review. This tool is read-only and shall not append WAL memory events.

Minimum request fields:

| Field | Required | Notes |
|------|----------|-------|
| `scope` | Yes | One scope per request |
| `namespace` | Yes | Namespace whose audit log is exported |
| `since` | No | Inclusive lower bound — RFC3339 UTC `recorded_at` or integer `seq` (same mutual-exclusion rules as Section 9.2) |
| `until` | No | Exclusive upper bound — RFC3339 UTC `recorded_at` or integer `seq` |
| `event_kinds` | No | Subset filter: `read`, `write`, `delete`; defaults to all kinds |
| `format` | No | `jsonl` (default) or `json` |
| `limit` | No | Maximum events returned; defaults to implementation-defined cap with deterministic ordering |

The component shall reject malformed bound objects, `since` after `until` when both resolve to comparable points, or unknown `format` values with `invalid_request`.

Successful response minimum keys:

| Key | Required | Notes |
|-----|----------|-------|
| `ok` | Yes | Shall be `true` |
| `tool` | Yes | Shall be `memory_audit_export` |
| `scope` | Yes | Scope exported |
| `namespace` | Yes | Namespace exported |
| `format` | Yes | Effective export format |
| `event_count` | Yes | Number of audit events in `records` |
| `records` | Yes | Array of audit events per Section 19.4.2 when `format = json`; newline-delimited JSON objects when `format = jsonl` (each object is one element of the logical array) |
| `truncated` | Yes | `true` when `limit` caused truncation |
| `export_id` | Yes | Stable identifier for this export invocation |

When audit logging or export is unsupported, `memory_audit_export` shall return the standard error envelope with `error.code = audit_export_unavailable`.

**PII scan exposure decision:** PII pre-store scanning (Section 19.3) is **write-path only**. It runs as an opt-in pipeline hook on `memory_remember` and is surfaced through `memory_status` operator metadata. It is intentionally **not** exposed as a standalone MCP tool so agents cannot bypass governance configuration or probe detection rules interactively.

## 11. Persistence and rebuild contract

This specification constrains behavior, not the storage engine choice.

An implementation is compliant if it preserves all normative WAL, semantic-unit, supersession, propagation, recall, and audit behaviors defined in this document.

The persistence mechanism remains a bounded implementation decision in this version.

Indexes are explicitly non-authoritative. The component shall be able to rebuild them from durable state without information loss for governed current-state recall and `as_of` audit.

Consolidation is part of the same authority boundary: it may compact the derived active semantic-unit view or its supporting indexes, but it shall not rewrite, delete, or reorder WAL events. Rebuilding from the WAL shall be sufficient to recover any unit touched by consolidation, including units deduplicated into a summary representation.

Under the v1 TF-IDF default, rebuilding indexes shall remain a bounded maintenance operation over durable state rather than a dependency on external model artifacts. A missing or stale TF-IDF index may reduce ranked-search availability, but it shall not alter governed lookup results, current-state truth, or historical audit behavior.

## 12. Error conditions

The public MCP surface shall distinguish at least the following failure categories:

| Code | Meaning |
|------|---------|
| `invalid_scope` | Requested scope is not one of the three supported scopes |
| `invalid_request` | Required fields are missing or malformed |
| `not_found` | The requested subject has no value at the requested recall point |
| `index_unavailable` | Indexed search cannot run, but direct lookup and audit state remain intact |
| `integration_failed` | WAL append or semantic integration did not complete |
| `consolidation_unavailable` | Consolidation is not supported or cannot run in the current namespace state |
| `review_unavailable` | Review-queue operations are not supported in the current implementation |
| `profile_unavailable` | Profile assembly cannot run due to implementation outage |
| `reflection_unavailable` | Reflection synthesis cannot run because reflection is unsupported or the LLM adapter is unavailable |
| `extraction_disabled` | A profile-engine or session-summary trigger was invoked while extraction is disabled for the namespace |
| `pii_blocked` | v1.6.0 — PII pre-store scan is enabled and the write cannot proceed under the configured redaction policy (Section 19.3) |
| `legal_hold` | v1.6.0 — the requested `memory_forget` or retention eviction targets a subject under active legal hold (Section 19.5) |
| `temporal_query_unavailable` | v1.6.0 — temporal trajectory queries are not supported in the current implementation |
| `audit_export_unavailable` | v1.6.0 — audit logging or export is not supported in the current implementation |
| `fleet_sync_unavailable` | v1.6.0 — optional fleet backend is configured but unreachable or not licensed for the namespace |

An `index_unavailable` error shall not imply WAL corruption.

When a tool returns an error, it shall use the standard error envelope defined in Section 10 and place one of the following codes in `error.code`.

## 13. Non-goals for this version

This version of the component specification does not define:

- connector-specific ingestion from third-party SaaS systems
- cross-component behavior or routing outside `memory-service`
- irreversible physical deletion guarantees
- an embedding-based retriever backend or any alternative recall source that expands retrieval beyond the governed current-state or `as_of` corpus
- mandatory or automatic service-side subject inference when the caller omits the public `subject` filter
- automatic consolidation as part of the correctness path
- salience-driven eviction of low-salience units
- automatic promotion of `belief` to `fact` without an explicit `memory_review` accept
- service-side enforcement of `blocks_actions` metadata
- mandatory scheduled consolidation or mandatory human review before any memory write
- mandatory profile-engine extraction or mandatory reflection on every session
- mandatory cloud sync or multi-device replication (local-first remains the default; optional `fleet_replica` tier is defined in Section 19.6)
- production fleet multi-tenancy as a v1 requirement (optional fleet backend is a single-operator sync tier, not shared SaaS tenancy)
- embedding-based retriever backends as a requirement for profile or reflection tools
- mandatory PII scanning on every write (Section 19.3 defines opt-in pre-store scan only)
- mandatory audit logging on every deployment (Section 19.4 defines opt-in operator audit log)
- `fleet_primary` remote-authoritative WAL mode (deferred post–v1.6.0 per Section 19.6.1)
- explicit `graph` payload ingest on `memory_remember` (deferred post–v1.6.0 per Section 19.1.4)

## 14. Harness adapters (Phase 3)

This section defines optional **delivery artifacts** that connect external harness runtimes to the same governed store and MCP semantics defined in Sections 5–13. Adapters shall not redefine MCP tool behavior, WAL authority, or supersession rules.

Adapters live under `components/memory-service/adapters/` and call the in-process `MemoryService` API or the documented production stdio MCP entrypoint. They are not required for MCP-only integrations (Claude Code, Copilot, Codex).

### 16.1 Shared adapter invariants

| Invariant | Requirement |
|-----------|-------------|
| Store authority | All governed writes and reads go through the same `MemoryService` contract as production MCP |
| Scope mapping | `user` scope maps to Hermes/OpenClaw profile or workspace identity; `session` scope maps to harness session id; `repository` scope maps to workspace root namespace |
| Namespace | Default namespace is the workspace directory basename unless overridden by adapter config. See **16.1.1** for session-scoped namespace encoding. |
| Data directory | Default `MEMORY_SERVICE_DATA_DIR` is `<workspace>/.ai-memory/data` |
| Non-blocking sync | Turn-sync and post-turn writes shall not block the harness agent loop for more than 50 ms on the calling thread; heavier work runs in a background thread or queue |
| No embedding requirement | Adapters shall not require neural embeddings; TF-IDF semantics remain the default |
| Path sandbox (OpenClaw) | OpenClaw adapter reads and writes only paths under the configured workspace root |

#### 16.1.1 Adapter namespace encoding

Adapters derive MCP `namespace` values from the configured repository namespace and harness identity. Implementations shall use the following encoding so session-scoped writes remain isolated without changing the published MCP tool schemas:

| Scope | Default adapter namespace | Notes |
|-------|---------------------------|-------|
| `repository` | `{namespace}` | `namespace` is the configured workspace basename unless overridden |
| `session` | `{namespace}__session__{slug(session_id)}` | `slug` is a lowercase ASCII token derived from the harness session id; default session token is `default` when empty |
| `user` | `{namespace}` or `{namespace}__user__{slug(user_identity)}` | When the host supplies a stable user identity, adapters may suffix the repository namespace; otherwise user scope uses the repository namespace |

Callers invoking MCP tools directly through adapters may still supply an explicit `namespace`. When omitted, adapters shall apply the table above.

Hermes `sync_turn` and session-scoped tool calls shall use the session encoding row. OpenClaw session-scoped reads shall use the same encoding when `scope=session`.

### 16.2 Hermes `MemoryProvider` adapter

**Delivery path:** `components/memory-service/adapters/hermes/`

**Registration name:** `ai-memory` (config key `memory.provider: ai-memory`)

The adapter implements the Hermes `MemoryProvider` ABC (`initialize`, `system_prompt_block`, `prefetch`, `sync_turn`, `get_tool_schemas`, `handle_tool_call`, `shutdown`) and optional hooks `on_session_end`, `on_pre_compress`, `on_memory_write` when the host exposes them.

#### 16.2.1 Configuration

`hermes memory setup` (or equivalent config schema) shall accept:

| Key | Required | Default | Purpose |
|-----|----------|---------|---------|
| `data_dir` | no | `<workspace>/.ai-memory/data` | SQLite store location |
| `namespace` | no | workspace basename | Repository namespace |
| `recall_mode` | no | `hybrid` | `hybrid` \| `context` \| `tools` |
| `mirror_builtin_memory` | no | `false` | When true, mirror selected built-in MEMORY.md / USER.md writes via `on_memory_write` |
| `prefetch_limit` | no | `5` | Max search results injected per prefetch |
| `sync_turn_enabled` | no | `true` | Persist completed turns as episodes |
| `profile_engine_enabled` | no | `false` | Opt-in background profile extraction per Section 17.1 |
| `context_fencing_enabled` | no | `true` | Strip prior injection markers before auto-capture per Section 17.4 |

Environment override: `MEMORY_SERVICE_DATA_DIR`.

#### 16.2.2 `recall_mode` behavior

| Mode | `prefetch` | Provider tools |
|------|------------|----------------|
| `context` | Runs `memory_search` + formats bounded text block | Empty tool list |
| `tools` | Returns empty string | Exposes subset: `memory_search`, `memory_get`, `memory_remember`, `memory_forget` |
| `hybrid` | Bounded prefetch block | Same tool subset as `tools` |

Tool schemas shall follow OpenAI function-calling JSON shape. Tool implementations delegate to the same request/response envelopes as MCP Section 10.

#### 16.2.3 Lifecycle mapping

| Hermes hook | Required behavior |
|-------------|-------------------|
| `initialize(session_id, **kwargs)` | Open store; cache `session_id`, `hermes_home`, namespace |
| `system_prompt_block()` | Return static usage hint + optional bounded profile manifest (≤ 2048 Unicode scalars) from `user` scope via `memory_profile` when available, otherwise legacy governed-record formatting |
| `prefetch(query, session_id=...)` | `memory_search` for `user` + `session` scopes; format results with `match_reason` and subject keys |
| `sync_turn(user, assistant, session_id=...)` | When enabled, append `episode` with `topic=session_turn` for the session scope; **non-blocking** |
| `get_tool_schemas()` | Per `recall_mode` |
| `handle_tool_call(name, args)` | Dispatch to MCP-equivalent operations; return JSON string |
| `on_session_end(messages)` | Optional: enqueue explicit `memory_consolidate` dry-run stats in logs only (no mandatory consolidation); when profile engine is enabled per Section 17.1, enqueue background profile extraction and session-summary update (non-blocking) |
| `on_pre_compress(messages)` | Optional: flush compaction checkpoint fact to `repository` scope (same semantics as Claude Code PreCompact hook) |
| `on_memory_write(action, target, content)` | When `mirror_builtin_memory` is true, map MEMORY.md / USER.md targets to governed writes; `action=remove` semantics in **16.2.5**. Before forwarding episode or profile-engine input, apply context fencing per Section 17.4 |
| `shutdown()` | Close service handle |

#### 16.2.4 Profile shelf (optional)

When `mirror_builtin_memory` is true, USER.md slices may be imported into `user` scope on initialize via the existing markdown import adapter. Automatic profile extraction is defined in Section 17.1; v1.4.x adapters required import-on-start and mirror-on-write only.

#### 16.2.5 `on_memory_write` remove semantics

When `mirror_builtin_memory` is true and Hermes invokes `on_memory_write` with `action=remove`:

| Input | Requirement |
|-------|-------------|
| `target` | `memory` or `user` (case-insensitive). `memory` maps to `repository` scope; `user` maps to `user` scope. |
| `metadata.topic` | Required. Missing or blank topic is a no-op. |
| `metadata.field` | Optional. Omitted means forget applies with `field` unset. |
| `metadata.memory_type` | Optional. When omitted, default is `fact` for `memory` target and `preference` for `user` target. |

The adapter shall enqueue `memory_forget` with the derived `(scope, namespace, topic, field, memory_type)` subject key and provenance `source=hermes-remove`. Retraction follows MCP `memory_forget` semantics; adapters shall not physically delete WAL history.

### 16.3 OpenClaw memory plugin adapter

**Delivery path:** `components/memory-service/adapters/openclaw/`

The adapter registers for OpenClaw `plugins.slots.memory` and exposes host-compatible `memory_search` and `memory_get` tools backed by this component.

#### 16.3.1 Tool compatibility

| OpenClaw tool | Delegation |
|---------------|------------|
| `memory_search` | `memory_search` with `query`, optional `scope`, `namespace`, `limit`; returns JSON `{ results: [...] }` with `path`, `snippet`, `score`, `startLine`, `endLine` fields synthesized from governed records |
| `memory_get` | `memory_get` by subject key or by `path` alias `memory/<topic>/<field>.md` under workspace |

`memory_search` and `memory_get` shall succeed on a fresh workspace with empty store (empty results, not error).

#### 16.3.2 Workspace sandbox

- Allowed read paths: `<workspace>/MEMORY.md`, `<workspace>/memory/**`, `<workspace>/.ai-memory/**`
- Adapter shall reject paths outside workspace root with `invalid_request`
- Markdown files in workspace may be imported on plugin activate (opt-in config `import_workspace_markdown: true`)

#### 16.3.3 Configuration

| Key | Default | Purpose |
|-----|---------|---------|
| `data_dir` | `.ai-memory/data` | Relative to workspace |
| `namespace` | workspace basename | Repository namespace |
| `import_workspace_markdown` | `false` | Seed store from MEMORY.md + memory/*.md on activate |

#### 16.3.4 Path alias resolution

OpenClaw path aliases use the form `memory/<topic_slug>/<field_slug>.md` relative to the workspace root.

| Rule | Behavior |
|------|----------|
| `topic_slug` | Lowercase slug of the governed `topic` string |
| `field_slug` | Lowercase slug of `field`, or the literal `index` when `field` is null or omitted |
| Lookup scope | Uses request `scope` when supplied; otherwise `repository` |
| Lookup namespace | Uses request `namespace` when supplied; otherwise the adapter namespace for the scope (see **16.1.1**) |
| `memory_type` omitted | Adapter shall try, in order: `fact`, `preference`, `procedure`, `constraint`, `belief`, `episode` until one current record matches the topic/field slug pair |
| Ambiguous match | Multiple records with the same slug pair but different memory types: return the first match in the order above |
| No match | `memory_get` succeeds with an empty result (empty text / no record), not `not_found` error |
| Direct subject lookup | When `topic` / `memory_type` are supplied explicitly, adapter shall call MCP-equivalent `memory_get` directly without slug search |

### 16.4 Adapter validation

Black-box adapter tests shall verify:

1. Hermes: `prefetch` returns governed results; `sync_turn` is non-blocking; `handle_tool_call` round-trips `memory_remember` → `memory_get`
2. Hermes: `recall_mode=tools` exposes schemas; `recall_mode=context` exposes none
3. OpenClaw: `memory_search` / `memory_get` succeed against empty and populated stores within workspace sandbox
4. OpenClaw: path outside workspace is rejected
5. Both adapters: restart with same `data_dir` preserves recalled state

Adapter tests do not replace MCP Section 10–13 coverage; they assert harness-boundary behavior only.

## 17. User modeling and reflection (Phase 4)

This section defines Phase 4 behavior: profile engine, session summary, multi-persona scoping, and context fencing. Existing callers that omit new request fields shall observe the same semantics as the `1.4.1` contract for the seven original tools.

### 17.1 Profile engine

The profile engine is an **opt-in**, **non-critical-path** background extractor that proposes user-model records from episodes.

#### 17.1.1 Enablement

Profile extraction shall be disabled by default. An implementation shall enable it only when one of the following is true:

| Trigger | Configuration |
|---------|---------------|
| Global | `profile_engine.enabled = true` in service config |
| Per namespace | `profile_engine.enabled_namespaces` contains the target namespace |
| Adapter | Hermes `profile_engine_enabled = true` or equivalent adapter config |

When disabled, profile-engine triggers shall no-op or return `extraction_disabled` when invoked explicitly. They shall not block MCP read/write tools.

#### 17.1.2 Execution model

The profile engine shall run:

- on session end (adapter `on_session_end` hook or equivalent lifecycle signal), and/or
- on an operator-scheduled trigger (cron, CLI, or explicit internal job),

but **not** on the critical path of `memory_remember`, `memory_search`, `memory_get`, `memory_reflect`, or harness turn completion.

Execution shall be asynchronous. Failures shall be logged and surfaced through `memory_status.profile_engine` without corrupting WAL authority.

#### 17.1.3 LLM adapter contract

The profile engine shall call an external LLM only through a pluggable adapter interface. This specification defines the contract, not a vendor:

```text
extract_profile(candidates: EpisodeBatch, config: ProfileEngineConfig) -> ProfileExtractionResult
```

| Type | Minimum fields |
|------|----------------|
| `EpisodeBatch` | `episodes[]` each with `episode_id`, `value`, `recorded_at`; input episodes shall already be context-fenced per Section 17.4 |
| `ProfileEngineConfig` | `namespace`, `scope`, optional `persona_id`, `max_candidates` |
| `ProfileExtractionResult` | `items[]` each with `memory_type` (`preference` or `belief` only), `topic`, `field`, `value`, `derived_from[]`, optional `confidence` |

The component shall reject profile-engine outputs that propose `fact` or `procedure` writes directly. Such promotions require the review path in Section 17.1.4.

#### 17.1.4 Promotion and write policy

Profile-engine extractions shall become governed state through the following **normative** path:

| Proposed type | Write behavior |
|---------------|--------------|
| `preference` | Written directly via the normal `memory_remember` path with `provenance.source = profile_engine` and `derived_from[]` |
| `belief` | Written directly as `belief` with `provenance.source = profile_engine`, required `derived_from[]`, and default `salience = 0.5` unless the operator overrides |
| `fact` | **Not** written directly. The engine shall emit a consolidation-style promotion proposal consumable by `memory_review`, or downgrade to `belief` with `salience = 0.5` |

Automatic `belief` → `fact` promotion without `memory_review` accept remains out of scope (Section 13).

Lower `salience` on profile-engine `belief` records governs injection ordering in `memory_profile` but does not exclude the record from search or direct lookup.

#### 17.1.5 Input hygiene

Profile-engine input transcripts shall be passed through context fencing (Section 17.4) before LLM extraction.

### 17.2 Session summary

Each active project session may maintain one rolling summary record.

| Property | Value |
|----------|-------|
| Scope | `session` |
| Topic | `session_summary` |
| Field | `body` |
| Memory type | `procedure` (default) or `fact` when the summary is purely descriptive state |
| Max size | 4096 Unicode scalars for `value`; older text is truncated from the front with an ellipsis prefix when updating |

Update triggers (at least one shall be supported by compliant implementations):

1. **Session end hook** — adapter `on_session_end` or equivalent lifecycle signal updates the summary asynchronously.
2. **Explicit tool** — `memory_remember` on the `session_summary` subject with `provenance.source = session_summary` replaces the rolling summary through normal supersession semantics.

Session summaries shall respect persona scoping when `persona_id` is set on the write. Session summaries are included in `memory_profile` assembly only when `depth = full` and the caller explicitly requests session scope in a future extension; in v1.5.0 they are recalled via `memory_get` and `memory_search`, not default `memory_profile`.

### 17.3 Multi-persona share rules

Cross-persona visibility is explicit, not implicit.

| Mechanism | Behavior |
|-----------|----------|
| Default isolation | Records tagged `persona_id = P` are visible only to reads with `persona_id = P` (plus shared targets below) |
| Untagged records | Records without `persona_id` are visible only in the default persona context |
| `share_to` on write | Optional array of persona id strings. Each listed persona may read the record in addition to the record's own `persona_id` |
| Recall without `persona_id` | Operates in default persona context and sees only untagged records plus records that list the default persona in `share_to` |

Adapters that serve multiple Hermes profiles shall map each host profile to a distinct `persona_id` and pass it on MCP calls. Namespace encoding per Section 16.1.1 is unchanged.

### 17.4 Context fencing

Auto-capture and profile-engine pipelines shall strip previously injected or prefetched memory text before extraction or episode promotion.

#### 17.4.1 Marker contract

Implementations shall recognize injection blocks bounded by markers in this canonical form:

```text
<!-- ai-memory:begin injection_id={id} -->
... recalled or prefetched content ...
<!-- ai-memory:end injection_id={id} -->
```

Fencing shall remove the entire block including markers from capture input. Blocks with unknown `injection_id` values shall still be removed when the marker prefix matches.

#### 17.4.2 Hook contract (validation-facing)

Harness adapters that auto-capture transcripts shall expose a pure function or equivalent hook:

```text
apply_context_fencing(text: str, known_injection_ids: list[str]) -> str
```

| Post-condition | Requirement |
|----------------|-------------|
| No echoed recall | Output shall not contain `ai-memory:begin` blocks whose `injection_id` is in `known_injection_ids` |
| Idempotent | Applying fencing twice yields the same output |
| Preserve user/agent turns | Fencing removes injection blocks only; it shall not delete untagged user or assistant content |

Hermes `on_memory_write`, `sync_turn`, and profile-engine inputs shall call fencing when `context_fencing_enabled` is true (default). Validation suites shall assert the post-conditions above on representative transcripts.

### 17.5 Local-first note

Cloud sync and fleet-scale replication are optional tiers defined in Section 19.6. **Local-first remains the default.** v1.6.0 publishes optional `fleet_replica` sync without making remote storage mandatory.

## 18. Validation hints (Phase 4)

The following black-box behaviors are in scope for the Validation Agent when extending the test suite for spec `1.5.0`. This section is informative for test design; it is not itself executable test code.

| Area | Behaviors to cover |
|------|-------------------|
| `memory_profile` | Empty store returns empty `manifest` success; populated `preference` records appear in `sections` with matching `citations[]`; `budget_tokens` truncates deterministically; `belief` with `salience = 0.5` ranks below `preference` when trimming |
| `memory_reflect` | Empty evidence returns empty `synthesis` success; non-empty evidence returns `citations[]` with `topic`, `field`, `memory_type`, `seq`; synthesis does not cite records outside the governed namespace |
| Persona isolation | Record written with `persona_id = A` is invisible to reads in default persona context and to `persona_id = B` unless `share_to` includes `B` |
| `share_to` | Record with `share_to: ["B"]` is visible to `persona_id = B` reads |
| Context fencing | Transcript containing a known injection block is stripped before episode append in adapter path; fenced output satisfies Section 17.4.2 post-conditions |
| Profile engine opt-in | With extraction disabled, session-end trigger does not create `provenance.source = profile_engine` records; with extraction enabled, created `belief` records include `derived_from[]` |
| Session summary | Session-end or explicit write creates/updates `topic = session_summary` subject bounded to 4096 scalars |
| Backward compatibility | Original seven tools with no new fields behave as `1.4.1` |
| Error codes | `reflection_unavailable`, `profile_unavailable`, and `extraction_disabled` use the standard error envelope |

## 19. Temporal graph and enterprise governance (Phase 5)

This section defines Phase 5 behavior: temporal graph reasoning, enterprise governance (PII pre-store scan, audit log, retention and legal hold), and an optional fleet backend tier. Existing callers that omit Phase 5 request fields and invoke only Sections 10.1–10.9 shall observe the same semantics as v1.5.0.

Phase 5 is **additive**. WAL authority, semantic-unit rebuildability, TF-IDF default retriever, current versus single-subject `as_of` semantics, and the nine tools in Sections 10.1–10.9 retain v1.5.0 behavior when Phase 5 features are disabled or omitted.

### 19.0 Research-backed Phase 5 decisions

The following resolutions are grounded in the cleared research program (`docs/research/R11-research-stages.md`, Stage 2–4 scorecards) and in `docs/research/STAGE4-issues.md`.

| # | Question | Decision for v1.6.0 | Research basis |
|---|----------|---------------------|----------------|
| 1 | Graph ingest path | **Deterministic derivation only** — project governed subjects and direct `extends` edges into entities/edges on ingest. No `graph` payload on `memory_remember` in v1.6.0. | R7: graphs are rebuildable indexes on derived views, not substrate. R14: `graphiti_lite` derives entities deterministically from write subjects and matches supersession on stale probes but scores **0%** on `dep_chain` vs **100%** for `gem_lite` propagation. STAGE4 S4-1d: recall-time graph expansion loses to write-time governed propagation. CortexDB/GEM pattern (R3 §2, R15): WAL + deterministic projections. |
| 2 | Negation / retraction (S4-1e) | **`memory_forget` / `retract` WAL events are sufficient** — no new negation `memory_type`. Phase 5 adds `include_retracted` trajectory visibility for S4-2c validation. | STAGE4 S4-1e / S4-2c. R3 §7: retraction preserves history. Section 8.5.1. |
| 3 | Fleet conflict policy | **Ship `local` + `fleet_replica` only.** Local WAL is authoritative for MCP ACK; remote sync is ordered `seq` push/pull (event-sourcing replay). **Defer `fleet_primary`** until H-C2 (R5) CRDT + semantic arbiter policy is measured. | R3 §8: CRDT is transport, not GEM replacement; belief conflicts need policy. R7/R15: immutable WAL is source of truth. `research/results/stage4-consolidation.json` and S4-1c: destructive eviction without access signal is unsafe — remote overwrite of local committed state is the same failure class. |
| 4 | PII detection engine | **Minimum deterministic detection contract** — normative category list with pattern-based detectors required; richer engines are optional plug-ins. No neural PII model required in v1.6.0. | Aligns with v1 TF-IDF default (no neural embeddings required). R3 §7 Vulcan: deterministic fact extraction for regulated audit. `docs/03-memory-architecture-patterns.md`: Trace Continuity-class pre-store scan is the governance pattern; testability requires a fixed minimum contract. |
| 5 | Retention eviction invocation | **Operator CLI / scheduler only** — no `memory_retention_run` MCP tool in v1.6.0. | S4-1c (`research/results/stage4-consolidation.json`): `salience_evict@cold` → **0%** correct; automatic visibility mutation on the correctness path is unsafe. Consolidation ADR (v1.3.0): destructive visibility changes are explicit opt-in maintenance, not silent background mutation. Per-type TTL eviction closes semantic-unit visibility only; WAL is never deleted. |

Explicit `graph` payload ingest and `fleet_primary` mode remain **deferred post–v1.6.0** pending S4-2a graph-ingest measurement and H-C2 fleet conflict falsification respectively.

### 19.1 Graph model

The component may materialize a **temporal graph** as a derived view over WAL history and semantic units. The graph layer accelerates multi-subject and relational temporal queries; it is not an independent source of truth.

#### 19.1.1 Entities

A graph **entity** represents a durable identity referenced across memory subjects or external identifiers supplied by callers. Each entity shall carry:

| Field | Required | Notes |
|-------|----------|-------|
| `entity_id` | Yes | Stable identifier within one namespace |
| `entity_type` | Yes | Caller-defined type string (for example `person`, `project`, `file`, `decision`) |
| `label` | Yes | Human-readable display name |
| `aliases` | No | Alternate string identifiers |
| `valid_from_seq` | Yes | WAL sequence when the entity became current |
| `valid_to_seq` | No | Exclusive upper bound; `null` when the entity is current |
| `source_subjects` | No | Subject keys that contributed to entity materialization |

Entities are derived. The component shall be able to rebuild the entity set from WAL events and semantic units without changing recall results for callers that do not use graph features.

#### 19.1.2 Edges

A graph **edge** represents a directed relationship between two entities with its own validity interval:

| Field | Required | Notes |
|-------|----------|-------|
| `edge_id` | Yes | Stable identifier within one namespace |
| `edge_type` | Yes | Caller-defined relationship type (for example `depends_on`, `supersedes`, `authored_by`, `located_in`) |
| `from_entity_id` | Yes | Source entity |
| `to_entity_id` | Yes | Target entity |
| `valid_from_seq` | Yes | WAL sequence when the edge became current |
| `valid_to_seq` | No | Exclusive upper bound; `null` when the edge is current |
| `properties` | No | JSON object of edge attributes |
| `source_event_id` | Yes | WAL event that established or closed the edge |

Edges follow the same supersession rules as governed memory: a new edge write on the same `(from_entity_id, to_entity_id, edge_type)` triple shall close the prior open edge version and open a new one.

#### 19.1.3 WAL authority and rebuildability

The graph layer shall satisfy all of the following invariants, consistent with Section 7:

1. Every graph mutation that affects observable graph recall shall be recoverable from WAL history or from deterministic derivation rules applied to WAL-derived semantic units.
2. Discarding the graph index shall degrade graph-accelerated query availability only; it shall not corrupt WAL history, semantic units, or single-subject `memory_get` / `memory_search` results.
3. Graph materialization shall not append substitute WAL events that replace governed memory writes. Entity and edge derivation may append auxiliary `episode_append` or dedicated graph-ingest events only when those events are themselves part of the durable audit trail and rebuildable.
4. Rebuilding the graph from the WAL shall yield the same entity and edge validity intervals as incremental maintenance for the same durable history.

Graph storage technology (embedded relational, embedded graph database, or in-memory projection) is an implementation decision. Compliance is determined by observable behavior and rebuildability, not by engine choice.

#### 19.1.4 Graph materialization (derivation only)

Graph entities and edges shall be materialized **only** through **deterministic derivation** from WAL-derived semantic units. Derivation runs on ingest or during explicit rebuild; it shall not introduce a parallel write authority.

**Normative derivation rules for v1.6.0:**

1. **Subject projection** — For each governed semantic unit, materialize one entity per distinct `topic` within a namespace (`entity_id` stable per namespace+topic; `entity_type` defaults to `subject_topic`; `label` from `topic`). Materialize one edge per unit from the topic entity to a field node (or equivalent projection) using `field` as `edge_type` and the governed `value` as edge property, with validity intervals mirroring the source unit's `valid_from_seq` / `valid_to_seq`.
2. **`extends` projection** — For each direct `extends` declaration on a dependent unit (Section 8.6), materialize a directed `depends_on` edge from the dependent topic entity to the parent topic entity with validity intervals aligned to the dependent unit's open interval.

Derivation rules are operator-configurable extensions of the two rules above. They shall not run on the critical path of `memory_remember` unless explicitly enabled for the namespace.

**Deferred:** explicit `graph` payload on `memory_remember` declaring `entities[]` and `edges[]` is **out of scope for v1.6.0**. R14 shows graph recall without governed ingest propagation does not beat the GEM wedge; explicit graph writes risk dual authority against WAL. Revisit only after S4-2a deep-chain measurement defines an ingest surface that derivation cannot express.

When graph support is disabled, requests with `include_graph_edges` shall be ignored or rejected per tool section rules without affecting v1.5.0 recall behavior.

### 19.2 Temporal query contract

Section 9.2 defines single-subject historical evaluation through `as_of` on `memory_get` and `memory_search`. Phase 5 extends temporal reasoning to **namespace- and topic-scoped belief trajectories** — the observable answer to questions such as "what did we believe about deployment policy across March?" without mandating how the store indexes time.

#### 19.2.1 Trajectory semantics

A **belief trajectory** is the time-ordered sequence of governed values for all subjects sharing a `(scope, namespace, topic)` partition (optionally narrowed by `field` and `memory_types`) evaluated at one or more audit points.

For each audit point `P` and each matching subject:

1. Resolve `P` to a WAL sequence `N` using Section 9.2 rules.
2. Select the version that was current at `N` using validity-interval rules (`valid_from_seq <= N` and (`valid_to_seq` is `null` or `N < valid_to_seq`)).
3. When `include_retracted = false`, exclude subjects whose only version at `P` was closed by a `retract` event with no superseding open version.
4. Return each point with full provenance (`event_id`, `seq`, `valid_from_seq`, `valid_to_seq`, `recorded_at`, `status`).

Trajectory evaluation shall be **deterministic** for a fixed durable history and request parameters. Rank ordering within a single audit point is undefined unless the caller supplies a `field` filter that resolves to one subject.

#### 19.2.2 Conflict and authority signals

When multiple versions of the same subject are candidates at one audit point, validity-interval rules from Section 9.2 take precedence. Phase 5 does not introduce "latest text wins" overrides.

When S4-1b-style **authority resolution** is enabled by operator configuration, the component may use provenance metadata (`provenance.source`, `provenance.actor`, record `salience`, and optional `confidence` on `belief` records) to break ties only when two versions would otherwise be open at the same audit point — a condition that shall not occur for correctly integrated superseding types. Authority resolution is an operator opt-in extension; default behavior remains validity-interval governed.

#### 19.2.3 Observable interfaces

Temporal trajectory queries are available through:

- `memory_query_temporal` (Section 10.10) for multi-point trajectories, and
- repeated `memory_get` / `memory_search` with `as_of` for single-point, single-subject access (unchanged v1.5.0 contract).

Implementations may accelerate trajectory evaluation with the graph layer (Section 19.1) or semantic-unit indexes, but acceleration shall not change trajectory results relative to WAL-derived evaluation.

### 19.3 PII pre-store scan

The component may run an **opt-in PII pre-store scan** on the `memory_remember` write path before WAL append. Scanning protects against persisting regulated identifiers when operators enable it; it is disabled by default.

#### 19.3.1 Enablement

PII scanning shall be disabled by default. An implementation shall enable it only when one of the following is true:

| Trigger | Configuration |
|---------|---------------|
| Global | `pii_scan.enabled = true` in service config |
| Per namespace | `pii_scan.enabled_namespaces` contains the target namespace |
| Per memory type | `pii_scan.enabled_memory_types` contains the request `memory_type` |

When disabled, `memory_remember` shall not invoke the scan and v1.5.0 write behavior is unchanged.

#### 19.3.2 Scan behavior

When enabled, the scan shall evaluate the request `value` string and any structured `observation` payload as a single inspectable document.

The operator shall configure a **redaction policy** with one of the following modes:

| Mode | Behavior |
|------|----------|
| `redact` | Replace detected PII spans with a stable placeholder token (default `[REDACTED_PII]`) and proceed with the mutated payload |
| `block` | Reject the write with `error.code = pii_blocked` when any configured PII category is detected |
| `annotate` | Proceed with the original payload but attach `pii_scan` metadata to the successful response listing detected categories and spans |

Detected categories shall be drawn from a configurable set at minimum including: `email`, `phone`, `government_id`, `financial_account`, `ip_address`, and `free_text_name` (operator-tunable).

The scan shall complete on the write path before WAL append. Failures in the scanner implementation shall return `integration_failed` and shall not append a partial WAL event.

#### 19.3.3 Operator visibility

`memory_status` shall expose a `pii_scan` object containing at least `enabled` (boolean) and `policy` (`redact`, `block`, or `annotate`) for the requested namespace.

PII scanning is **write-path only** (Section 10.11). Agents do not receive a dedicated scan tool.

#### 19.3.4 Minimum detection contract

Implementations shall provide a **deterministic, engine-agnostic minimum detector** for every enabled namespace. The minimum detector is required so governance behavior is testable without mandating a neural model (consistent with the v1 TF-IDF default and R3 §7 deterministic audit path).

For each configured category in Section 19.3.2, the minimum detector shall use pattern-based matching (regular expressions or equivalent deterministic rules) at minimum for:

| Category | Minimum detection requirement |
|----------|------------------------------|
| `email` | RFC5322-simplified local@domain pattern |
| `phone` | E.164 or common national digit-group patterns |
| `government_id` | Operator-supplied format templates per jurisdiction |
| `financial_account` | Operator-supplied PAN/IBAN/account mask templates |
| `ip_address` | IPv4 and IPv6 literal patterns |
| `free_text_name` | Operator-configured allow/deny name lists only (no required ML) |

Operators may register **extended detectors** (local NER, external API) that supplement but shall not weaken the minimum contract: when extended detection is unavailable, the minimum detector remains authoritative for validation and default deployments.

### 19.4 Audit log

The component may maintain an **append-only operator audit log** recording read, write, and delete access to governed memory. The audit log is distinct from the WAL: the WAL remains the authoritative memory mutation history; the audit log records **who accessed what, when, and through which tool**.

#### 19.4.1 Enablement

Audit logging shall be disabled by default. When enabled globally or per namespace, the component shall append one audit record for each successful MCP tool invocation that reads or mutates governed state in that namespace.

Audit logging shall not block or alter the success semantics of the underlying tool except when the audit subsystem itself is unavailable and the operator has configured `audit.fail_closed = true` (default `false`).

#### 19.4.2 Audit event shape

Each audit event shall include at minimum:

```json
{
  "audit_id": "aud_000042",
  "recorded_at": "2026-06-24T12:34:56Z",
  "event_kind": "read | write | delete",
  "scope": "repository",
  "namespace": "palimem",
  "tool": "memory_get",
  "actor": {
    "source": "mcp",
    "actor_id": "agent",
    "request_id": "req_456"
  },
  "subject": {
    "topic": "user_prefs",
    "field": "city",
    "memory_type": "preference"
  },
  "wal_seq": 42,
  "wal_event_id": "evt_000042",
  "outcome": "success | denied | error",
  "error_code": null
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `audit_id` | Yes | Unique within the namespace audit log |
| `recorded_at` | Yes | RFC3339 UTC timestamp |
| `event_kind` | Yes | `read` for recall tools; `write` for `memory_remember` and accepted `memory_review` accepts; `delete` for `memory_forget` |
| `tool` | Yes | MCP tool name |
| `actor` | Yes | Provenance-style object identifying the caller |
| `subject` | No | Present when the tool addressed a specific subject key |
| `wal_seq` | No | Present when the tool produced or targeted a WAL event |
| `outcome` | Yes | Result classification |
| `error_code` | No | Public error code when `outcome` is `error` or `denied` |

Read audit events shall be recorded for `memory_get`, `memory_search`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, and `memory_audit_export` (export itself does not recurse-audit).

#### 19.4.3 Immutability and export

Audit records shall be append-only. Operators may truncate audit logs only through an explicit operator maintenance action that itself generates a final audit record describing the truncation window — physical truncation semantics are implementation-defined.

`memory_audit_export` (Section 10.11) is the normative export surface. JSONL is the minimum required format for SOC2-ready offline review.

### 19.5 Retention and legal hold

Phase 5 extends Section 6.4 expiry semantics with **per-memory-type retention TTL** and a **legal hold** flag that blocks destructive visibility changes.

#### 19.5.1 Per-type retention TTL

Operators may configure a default retention TTL per `memory_type`:

| Memory type | Default retention when configured | WAL history |
|-------------|-----------------------------------|-------------|
| `episode` | Operator-defined TTL or unlimited | WAL events are never deleted by retention |
| `preference`, `fact`, `procedure`, `constraint`, `belief` | Operator-defined TTL measured from last write `recorded_at` | Retention evicts from default current-state recall and default search only |

Retention eviction shall:

1. Close the open semantic-unit version for the subject at the eviction sequence
2. Exclude the subject from default current-state recall and search
3. **Not** delete WAL events
4. **Not** remove subjects under active legal hold

Retention runs as an explicit **operator CLI or scheduler** maintenance action, not as an automatic correctness-path mutation and **not** through a dedicated MCP tool in v1.6.0. Failure or omission of retention shall not change v1.5.0 recall behavior for subjects not yet evicted.

This matches the consolidation ADR (`memory_consolidate` explicit opt-in) and Stage 4 S4-1c finding that destructive visibility mutation without a strong access signal is unsafe (`research/results/stage4-consolidation.json`: `salience_evict@cold` → 0% correct). A future version may add `memory_retention_run` only after S4-2c retraction and retention suites pass with operator ergonomics evidence.

When a per-type TTL is configured, it applies in addition to any record-level `expires_at` already defined in v1.5.0. The earlier effective expiry wins for default current-state recall.

#### 19.5.2 Legal hold

A governed subject may carry `legal_hold = true`, set on write via `memory_remember` or through an operator maintenance action.

When `legal_hold` is active on the current version of a subject:

- `memory_forget` shall return `error.code = legal_hold` without appending a `retract` event
- Retention eviction shall skip the subject
- `expires_at` and per-type TTL shall not remove the subject from default current-state recall while hold is active

Legal hold does not disable historical `as_of` recall or audit export of WAL history.

Clearing legal hold requires an explicit operator action or a `memory_remember` write on the same subject with `legal_hold = false` and provenance identifying the clearing actor.

#### 19.5.3 Operator visibility

`memory_status` shall expose a `retention` object containing at least `enabled`, `policies` (per-type TTL summary), and `legal_hold_count` for the namespace.

### 19.6 Fleet backend (optional tier)

Phase 5 defines an **optional fleet backend** for operators who want remote sync or a read replica without making cloud storage mandatory. **Local-first remains the default** for all deployments that do not explicitly enable fleet mode.

#### 19.6.1 Deployment modes

| Mode | Behavior |
|------|----------|
| `local` (default) | All durable state resides in the operator-controlled local data directory. No remote sync. v1.5.0 semantics apply in full. |
| `fleet_replica` | Local WAL remains authoritative for writes and MCP ACK. The component may asynchronously push WAL segments or derived snapshots to a configured remote backend and may serve read acceleration from the replica when local indexes are stale. |

**Deferred post–v1.6.0:** `fleet_primary` (remote authoritative WAL) is **not** in v1.6.0. R3 §8 and R5 H-C2 require a measured CRDT + semantic-conflict arbiter policy before any mode may treat a remote store as write authority. Until that falsification completes, local WAL remains the only write authority.

Fleet mode is opt-in per namespace. A namespace in `local` mode shall not contact a remote backend.

#### 19.6.2 Remote backend contract

The remote backend shall expose a documented sync API supporting at minimum:

1. **Push** — upload ordered WAL segments or idempotent mutation batches keyed by `namespace` and `seq` range
2. **Pull** — fetch missing `seq` ranges for catch-up sync
3. **Read replica** — serve `memory_get`, `memory_search`, and `memory_query_temporal` against eventually consistent derived state when local mode is `fleet_replica` and the operator enables `fleet.serve_reads_from_replica`

The specification does not mandate a particular remote database engine. Postgres with a vector extension is one compliant implementation option; embedded local SQLite remains valid for `local` mode.

#### 19.6.3 Consistency and failure behavior

- Writes acknowledged to the caller shall be durably present in the **local WAL** before the MCP success response returns, regardless of fleet mode.
- Remote sync failures shall not roll back locally committed WAL events.
- Catch-up sync shall merge missing WAL `seq` ranges by **monotonic replay** — remote segments with `seq` less than or equal to the local durable high-water mark are idempotent; segments with higher `seq` extend local history. Semantic-unit and graph projections shall be rebuilt from the merged WAL. **Remote state shall never overwrite or truncate locally committed WAL events** in `fleet_replica` mode.
- When `fleet.serve_reads_from_replica = true` and the replica lags beyond `fleet.max_staleness_seq`, read tools shall fall back to local WAL-derived state or return `fleet_sync_unavailable` per operator policy.
- Fleet mode does not introduce multi-tenant isolation guarantees. Each fleet deployment is single-operator scope; production shared tenancy remains out of v1 scope per project non-goals.

#### 19.6.4 Operator visibility

`memory_status` shall expose a `fleet` object containing at least `mode` (`local` or `fleet_replica` in v1.6.0), `backend_reachable` (boolean), `last_synced_seq` (integer or `null`), and `replica_lag_seq` (integer or `null`).

## 20. Validation hints (Phase 5)

The following black-box behaviors are in scope for the Validation Agent when extending the test suite for spec `1.6.0`. This section is informative for test design; it is not itself executable test code.

| Area | Behaviors to cover |
|------|-------------------|
| Graph derivation | Subject projection from governed units yields stable `entity_id` per topic; `extends` yields `depends_on` edges; no `graph` payload required on `memory_remember` |
| WAL authority | Graph materialization does not remove or rewrite existing WAL events; rebuilding semantic units from WAL alone preserves v1.5.0 single-subject recall |
| `memory_query_temporal` | Multi-point trajectory matches independent `memory_get` with `as_of` at each audit point for the same subjects; empty topic partition returns empty `trajectories` success |
| Retraction trajectories | With `include_retracted = true`, retracted subjects appear with `status = retracted`; with `false`, they are omitted (S4-2c alignment) |
| Dependency chains | Parent supersession closes dependent versions per Section 8.6; multi-hop chain probes per S4-2a when transitive propagation is enabled |
| PII scan `block` mode | Enabled scan rejects regulated fixture with `pii_blocked`; disabled scan allows the same write |
| PII scan `redact` mode | Write succeeds with placeholder-substituted `value`; recalled value reflects redacted payload |
| Audit log | Enabled logging records `read` on `memory_get` and `write` on `memory_remember`; `memory_audit_export` returns JSONL with required keys |
| Legal hold | `memory_forget` on held subject returns `legal_hold`; retention eviction skips held subject; hold clear allows forget |
| Per-type retention | Eviction removes subject from default recall but `as_of` before eviction and WAL audit remain available |
| Fleet local default | Default config performs no remote sync; `memory_status.fleet.mode = local` |
| Fleet replica lag | When replica exceeds `max_staleness_seq`, reads fall back or error per policy without losing local writes |
| Backward compatibility | Nine v1.5.0 tools with no Phase 5 fields behave as v1.5.0 when Phase 5 features are disabled |
| Error codes | `pii_blocked`, `legal_hold`, `temporal_query_unavailable`, `audit_export_unavailable`, and `fleet_sync_unavailable` use the standard error envelope |

## 21. Integration surfaces (Phase 6)

This section defines normative **integration quality**, the **published harness catalog**, the **connect** operator CLI extension, example-package requirements, and integration readiness validation. Sections 5–13 and the eleven-tool MCP surface in Section 10 are unchanged unless a future patch documents a required contract fix.

Related specifications:

- Machine-readable catalog: [integrations.yaml](./integrations.yaml)
- Documentation site: [docs-site.md](./docs-site.md)
- Marketing landing (tier-gated logos): [landing-page.md](./landing-page.md)

### 21.1 Integration quality tiers

Every harness integration in the published catalog shall carry exactly one tier. Tiers describe delivery completeness, not memory semantics.

| Tier | Label | Requirements | Public claim allowed |
|------|-------|--------------|----------------------|
| **A** | Native | Adapter or full hooks + MCP (when applicable) + example README + smoke test + docs integration page | "Works with {harness}" (may be featured) |
| **B** | Connected | MCP (when applicable) + `ai-memory connect <harness>` (or documented harness-native one-liner) + example README + smoke test + docs integration page | "Works with {harness}" |
| **C** | MCP documented | Copy-paste MCP config + docs page + manual verify steps | "MCP compatible" only — not "Works with" |
| **D** | Planned | Roadmap entry only; `tier_target` documents intended tier | "Coming soon" only |

**Regression rule:** A catalog entry at tier **B** or **A** shall not be downgraded to **C** or **D** without a documented breaking harness change recorded in the component spec version notes.

**MCP relationship:** Integration tiers govern delivery artifacts and public integration claims. They do not add, remove, or alter MCP tool behavior in Section 10.

### 21.2 Public claim rules

Public Palimem properties (documentation site, marketing landing, marketplace listings governed by this component) shall obey:

| Surface | Rule |
|---------|------|
| "Works with {harness}" | Allowed only when catalog `tier` is **A** or **B** for `harness_id` on the release branch |
| "MCP compatible" | Allowed for tier **C** with honest labeling — not in the primary "Works with" group |
| "Coming soon" | Tier **D** only — roadmap or changelog |
| Hero / page title | Must not lead with "MCP" or "MCP server" (see [docs-site.md](./docs-site.md) and [landing-page.md](./landing-page.md)) |

### 21.3 Published integration catalog

The authoritative harness list is [integrations.yaml](./integrations.yaml). Each entry shall include:

| Field | Required | Description |
|-------|----------|-------------|
| `harness_id` | yes | Stable slug (`claude-code`, `cursor`, …) |
| `display_name` | yes | Human-readable harness name |
| `tier` | yes | Current tier: `A`, `B`, `C`, or `D` |
| `tier_target` | yes when `tier` is `D` | Intended tier after Phase 6 delivery |
| `mechanisms` | yes | One or more of: `mcp`, `hooks`, `adapter`, `plugin` |
| `config_paths` | yes | Files written or edited by install (may be empty for tier D) |
| `connect_command` | no | Operator CLI invocation when tier B+ |
| `example_path` | no | Repository path to example or adapter README |
| `smoke_entrypoint` | no | Non-interactive verification script |
| `docs_path` | no | Docs site path slug when tier ≥ C |

Phase 6 P0 entries (minimum catalog): `claude-code`, `claude-code-plugin`, `copilot-cli`, `codex`, `hermes`, `openclaw`, `cursor`, `windsurf`.

Phase 6 P1 entries (delivered at tier B in v1.7.0): `vscode-copilot-agent`, `copilot-ide`, `gemini-cli`.

### 21.4 Connect operator CLI

The operator CLI entrypoint `ai-memory` (Node wrapper delegating to Python scripts under `components/memory-service/app/`) shall support:

```text
ai-memory connect <harness> [--project-root PATH] [--data-dir PATH] [--replace] [--dry-run]
```

#### 21.4.1 Supported harnesses (v1.7.0)

| CLI `<harness>` | Catalog `harness_id` | Config target |
|-----------------|----------------------|---------------|
| `copilot` | `copilot-cli` | `~/.copilot/mcp-config.json`; optional project `.copilot/mcp-config.json` via `--project-config` |
| `cursor` | `cursor` | `.cursor/mcp.json` (project) and/or `~/.cursor/mcp.json` (global) |
| `windsurf` | `windsurf` | `~/.codeium/windsurf/mcp_config.json` (global only — Windsurf does not support project-level MCP) |
| `codex` | `codex` | `~/.codex/config.toml` or project `.codex/config.toml` |
| `vscode` | `vscode-copilot-agent`, `copilot-ide` | `.vscode/mcp.json` (`servers` object per VS Code MCP schema) |
| `gemini` | `gemini-cli` | `~/.gemini/settings.json`; optional project `.gemini/settings.json` via `--project-config` |

Invoking `ai-memory connect` with an unsupported `<harness>` shall exit with code `2` and print usage listing supported harnesses.

#### 21.4.2 Merge semantics

- When the target config file already contains a `memory-service` server entry and `--replace` is **not** set, the command shall exit with code `1` and a human-readable message — same behavior as existing `connect copilot`.
- When `--replace` is set, the command shall overwrite only the `memory-service` entry; other server entries shall be preserved.
- JSON targets (`copilot`, `cursor`) shall normalize to a top-level `mcpServers` object. TOML target (`codex`) shall merge under `[mcp_servers.memory-service]`.

#### 21.4.3 Path resolution

- `--project-root` defaults to the current working directory and resolves relative paths for the MCP launch script.
- `--data-dir` defaults to `.ai-memory/data`. When relative, it shall be resolved to an absolute path against `--project-root` before writing into config `env.MEMORY_SERVICE_DATA_DIR` or TOML equivalent.
- Written configs shall use absolute paths for `MEMORY_SERVICE_DATA_DIR` and for the `memory-service-mcp.js` launch script unless the harness documents a supported variable (for example `${workspaceFolder}` in Cursor project config).

#### 21.4.4 Server registration

All connect targets shall register the server name `memory-service` with stdio transport:

- `command`: `node`
- `args`: path to `components/memory-service/app/scripts/memory-service-mcp.js` resolved from `--project-root`
- `env.MEMORY_SERVICE_DATA_DIR`: resolved data directory

#### 21.4.5 Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success — config written or `--dry-run` printed merged config |
| `1` | Operational failure — existing entry without `--replace`, invalid JSON/TOML, missing launch script, I/O error |
| `2` | Usage error — missing/unknown subcommand or harness |

On success (non-dry-run), the command shall print a single-line JSON object to stdout: `{"ok": true, "config": "<path>", "server": "memory-service"}`.

### 21.5 Example package requirements

For each catalog entry at tier **B**, the repository shall contain under `components/memory-service/examples/<harness>/` (or documented adapter path for tier **A** adapters):

| Artifact | Requirement |
|----------|-------------|
| `README.md` | Prerequisites, install steps, verify step, troubleshooting |
| Sample config | Committed file matching a `config_paths` entry (for example `.cursor/mcp.json`) |
| `demo/*-smoke.sh` | Non-interactive script that verifies `memory_status` or equivalent integrated-store signal |

Tier **A** adapter integrations shall provide equivalent README and smoke coverage under `adapters/<name>/` or `examples/<harness>/`.

### 21.6 Integration readiness validation

Component-owned integration readiness entrypoint:

```text
components/memory-service/integrations/run_readiness.sh
```

#### 21.6.1 Behavior

- The script shall execute every catalog entry where `tier` is **A** or **B** and `smoke_entrypoint` is non-null.
- Each smoke shall exit `0` on success; any failure shall cause `run_readiness.sh` to exit non-zero and identify the failing `harness_id`.
- The Orchestrator runs this script after Application and Validation deliver Phase 6 artifacts; it is not a substitute for the component MCP test suite.

#### 21.6.2 Output

On failure, the script shall print the `harness_id` and smoke script path to stderr. On full success, exit `0`.

### 21.7 Documentation site binding

For each catalog entry at tier **A** or **B**, the documentation site ([docs-site.md](./docs-site.md)) shall publish `/docs/integrations/<harness_id>` meeting the integration page template. Page content shall be traceable to the entry's `example_path` README.

`llms.txt` shall list all tier **B+** entries with install URLs.

### 21.8 Validation hints (Phase 6)

Informative behaviors for the Validation Agent when extending the test suite for spec `1.7.0`:

| Area | Behaviors to cover |
|------|-------------------|
| `connect copilot` | Merge into empty and existing configs; `--replace`; `--dry-run`; refuse clobber without `--replace` |
| `connect cursor` | Project and global targets; absolute `MEMORY_SERVICE_DATA_DIR` |
| `connect windsurf` | Global `mcp_config.json` only; valid JSON with `mcpServers` |
| `connect codex` | TOML merge under `[mcp_servers.memory-service]` |
| Smoke scripts | Each P0 tier B+ `smoke_entrypoint` exits 0 against a test data directory |
| Catalog consistency | [integrations.yaml](./integrations.yaml) tiers match claim rules in Section 21.2 |
| MCP unchanged | Eleven tools in Section 10 behave as v1.6.0 when Phase 6 fields and tools are not invoked |

## 15. ADR status and deferred scope

No ADRs remain open in this published version.

| Decision | Status | Boundaries in this version |
|---------|--------|-----------------------------|
| Default retriever backend | Resolved in `1.0.1` | TF-IDF is the required v1 default behind the stable retriever contract; embeddings remain an optional future extension. |
| Implementation language and distribution surface | Resolved in `1.0.1` | Python is the canonical service implementation with a thin npm MCP wrapper and shared component semantics. |
| Consolidation policy | Resolved in `1.1.0` | Consolidation is an optional, explicit, non-destructive maintenance capability. It may deduplicate redundant units and summarize low-salience noise clusters in the derived active view, but it shall never mutate the WAL or remove, evict, or hide the current value of a held `fact`. |
| Consolidation invocation surface | Resolved in `1.3.0` | Supported implementations expose consolidation through `memory_consolidate`; unsupported implementations return `consolidation_unavailable`. |
| Review-gated promotion | Resolved in `1.3.0` | Consolidation may propose promotions, but proposals become governed current state only through `memory_review` accept. Reject removes the pending proposal without a governed write. |
| Action boundaries and expiry | Resolved in `1.3.0` | `expires_at` excludes expired governed records from default current-state recall; `blocks_actions` is persisted metadata for caller-side enforcement on `constraint` and `action_boundary` facts. |
| Destructive eviction and auto-promotion | Deferred future scope, not an open ADR | Salience-driven eviction and automatic `belief` to `fact` promotion without review are out of scope for v1.3.0. A future specification may define them only after Stage 4 S4-2c establishes a retraction and eviction measurement suite together with an access-signal-aware safety policy. |
| Hermes / OpenClaw adapters | Resolved in `1.4.0` | Optional harness delivery artifacts under `adapters/`; MCP Sections 5–13 unchanged for callers omitting Phase 4 fields. |
| Automatic USER.md profile extraction | Resolved in `1.5.0` | Profile engine (Section 17.1) with opt-in extraction, context fencing, and review-gated `fact` promotion. Markdown import/mirror remains adapter-optional. |
| User modeling and reflection | Resolved in `1.5.0` | `memory_profile`, `memory_reflect`, session summary, `persona_id`, and context fencing per Sections 10.8–10.9 and 17. |
| Temporal graph layer | Resolved in `1.6.0` | Derivation-only entity/edge view (Section 19.1.4); rebuildability invariants in Section 19.1.3. |
| Temporal trajectory queries | Resolved in `1.6.0` | Namespace/topic-scoped belief trajectories via `memory_query_temporal`; single-subject `as_of` unchanged. Section 19.2. |
| PII pre-store scan | Resolved in `1.6.0` | Opt-in write-path hook; minimum deterministic detector (Section 19.3.4). Not a standalone MCP tool. |
| Audit log and export | Resolved in `1.6.0` | Append-only operator audit log and `memory_audit_export` (JSONL minimum). Section 19.4. |
| Retention and legal hold | Resolved in `1.6.0` | Per-type TTL plus `legal_hold`; eviction via operator CLI/scheduler only. Section 19.5. |
| Fleet backend (optional tier) | Partially resolved in `1.6.0` | `local` + `fleet_replica` only; local WAL authoritative. `fleet_primary` deferred (R5 H-C2). Section 19.6. |
| Explicit graph payload ingest | Deferred post–`1.6.0` | Derivation-only graph per R14/R7; revisit after S4-2a measurement. |
| Fleet primary (remote authoritative WAL) | Deferred post–`1.6.0` | Requires H-C2 CRDT + semantic arbiter falsification. |
| Authority-based conflict resolution (S4-1b) | Deferred post–`1.6.0` | Validity-interval supersession remains default; provenance tie-breaking is operator opt-in only (Section 19.2.2). |
| Write-time transitive propagation (S4-1d) | Deferred post–`1.6.0` | v1.6.0 requires direct `extends` edges only; full dependency closure is not in scope. |

## 16. Publish-time traceability

This version promotes the pre-gate draft in `docs/research/R15-architecture-spec-draft.md` into governed component behavior with the following explicit carry-forwards:

- WAL as append-only source of truth
- semantic units as the required derived view
- indexes as rebuildable disposable structures
- current-state recall by default with `as_of` audit support
- schema-driven dependent-field propagation
- MCP tool surface of `memory_remember`, `memory_search`, `memory_get`, `memory_forget`, `memory_status`, `memory_consolidate`, `memory_review`, `memory_profile`, `memory_reflect`, `memory_query_temporal`, and `memory_audit_export`

This version also records the Stage 4 decision thesis from `docs/research/R14-stage2-scorecard.md`: governed correct state beats maximal recall for the target memory workload.

Version `1.1.0` was a minor release because it resolved the consolidation ADR by adding a new, optional consolidation capability contract without changing the five-tool MCP surface or the rule that correctness does not depend on consolidation.

Version `1.1.1` is a patch release because it does not add a new capability or change a previously committed behavior. It tightens the existing five-tool contract by making already-committed request shapes, response keys, `as_of` encoding, and direct `extends` propagation expectations explicit and testable.

Version `1.2.0` is a minor release because it adds one new optional search capability without breaking existing callers: `memory_search` may now accept a caller-supplied `subject` filter that deterministically narrows the governed candidate set by `topic` and/or `field` before ranking. The default TF-IDF corpus boundary, WAL authority, semantic-unit rebuildability, and current versus `as_of` semantics are unchanged.

Version `1.3.0` is a minor release because it extends the component lifecycle contract without breaking existing callers:

- two new explicit maintenance tools: `memory_consolidate` and `memory_review`
- optional `depth` on `memory_get` (`full` default preserves `1.2.0` behavior)
- optional `expires_at`, `blocks_actions`, and episode `observation` metadata on `memory_remember`
- optional review-gated promotion flow for consolidation proposals
- `memory_status.review_queue.pending_count` for operator visibility

WAL authority, semantic-unit rebuildability, current versus `as_of` semantics, and the rule that correctness does not depend on consolidation having run are unchanged.

Version `1.4.0` is a minor release because it adds harness adapter contracts without changing MCP tool semantics for existing callers:

- Hermes `MemoryProvider` adapter (`adapters/hermes/`) with `recall_mode`, lifecycle hooks, and non-blocking `sync_turn`
- OpenClaw memory-slot plugin (`adapters/openclaw/`) with compatible `memory_search` / `memory_get` and workspace path sandbox
- Black-box adapter validation requirements (Section 14.4)

Sections 5–13 and the seven-tool MCP surface are unchanged.

Version `1.4.1` is a patch release because it clarifies adapter namespace encoding, OpenClaw path-alias resolution, and Hermes `on_memory_write` remove semantics without changing MCP tool behavior or adapter validation requirements.

Version `1.5.0` is a minor release because it adds user modeling and reflection capabilities without breaking existing callers that omit new fields:

- two new read tools: `memory_profile` and `memory_reflect`
- optional `persona_id` and `share_to` on writes; optional `persona_id` on reads
- optional `derived_from[]` on `belief` records, required for profile-engine beliefs
- profile engine with opt-in extraction, pluggable LLM adapter contract, and review-gated `fact` promotion (Section 17.1.4)
- rolling session summary (Section 17.2)
- context fencing for auto-capture pipelines (Section 17.4)
- `memory_status.profile_engine` and `memory_status.session_summary` operator visibility
- new error codes: `profile_unavailable`, `reflection_unavailable`, `extraction_disabled`

WAL authority, TF-IDF default retriever, semantic-unit rebuildability, current versus `as_of` semantics, and the rule that correctness does not depend on consolidation or profile extraction having run are unchanged. The original seven MCP tools retain `1.4.1` behavior when Phase 4 request fields are omitted. The nine tools through v1.5.0 retain v1.5.0 behavior when Phase 5 request fields are omitted and Phase 5 tools are not invoked.

Version `1.6.0` is a minor release because it adds temporal graph, enterprise governance, and optional fleet capabilities without breaking existing callers that omit Phase 5 fields:

- two new MCP tools: `memory_query_temporal` and `memory_audit_export`
- temporal graph as a derivation-only, rebuildable view over WAL (Sections 19.0, 19.1)
- namespace/topic-scoped belief trajectories with retract visibility (`include_retracted`) per Section 19.2 and Section 8.5.1
- opt-in PII pre-store scan with minimum deterministic detector contract (Sections 19.3, 19.3.4)
- append-only operator audit log and JSONL export via `memory_audit_export` (Sections 19.4, 10.11)
- per-memory-type retention TTL, `legal_hold`, and operator-scheduled eviction (Section 19.5)
- optional fleet tier: `local` (default) and `fleet_replica` with local WAL authoritative (Section 19.6)
- `memory_status` extensions: `pii_scan`, `retention`, `fleet`
- optional `legal_hold` on `memory_remember`
- new error codes: `pii_blocked`, `legal_hold`, `temporal_query_unavailable`, `audit_export_unavailable`, `fleet_sync_unavailable`

Research basis for Phase 5 design resolutions is recorded in Section 19.0 (`docs/research/R14-stage2-scorecard.md`, `docs/research/STAGE4-issues.md`, `research/results/stage4-consolidation.json`, R3 §7–§8, R5 H-C2). Explicit `graph` payload ingest and `fleet_primary` remain deferred post–`1.6.0`.

Version `1.7.0` is a minor release because it adds Phase 6 integration surfaces without changing MCP tool semantics for existing callers:

- integration quality tiers A/B/C/D and public claim rules (Section 21.1–21.2)
- published harness catalog [integrations.yaml](./integrations.yaml) (Section 21.3)
- `ai-memory connect` extension for `copilot`, `cursor`, `windsurf`, and `codex` (Section 21.4)
- example package and smoke requirements for tier B integrations (Section 21.5)
- component-owned `integrations/run_readiness.sh` gate (Section 21.6)
- binding to documentation site and landing page specs (Section 21.7, [docs-site.md](./docs-site.md), [landing-page.md](./landing-page.md))

WAL authority, TF-IDF default retriever, semantic-unit rebuildability, current versus `as_of` semantics, and the eleven-tool MCP surface in Section 10 are unchanged. Sections 5–13 and Section 10 behave as `1.6.0` when Phase 6 connect and integration artifacts are not used.