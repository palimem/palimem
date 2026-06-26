from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .adapter_bridge import AdapterBridgeClient
from .connect_scenarios import CONNECT_CASES
from .contracts import BehaviorCase, ToolCallError, ValidationFailure, ensure, expect_error_code, require_keys


def _subject(scope: str, namespace: str, topic: str, field: str, memory_type: str) -> dict[str, str]:
    return {
        "scope": scope,
        "namespace": namespace,
        "topic": topic,
        "field": field,
        "memory_type": memory_type,
    }


def _current_result(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results", [])
    ensure(bool(results), "Expected at least one search result.")
    return results[0]


GOVERNED_RECORD_KEYS = [
    "scope",
    "namespace",
    "topic",
    "field",
    "memory_type",
    "value",
    "event_id",
    "seq",
    "valid_from_seq",
    "valid_to_seq",
    "recorded_at",
    "provenance",
    "salience",
    "layer",
    "status",
]

VERSION_KEYS = ["value", "event_id", "seq", "valid_from_seq", "valid_to_seq", "recorded_at"]


def _assert_success_envelope(payload: dict[str, Any], tool: str, keys: list[str]) -> None:
    require_keys(payload, ["ok", "tool", *keys], f"{tool} response")
    ensure(payload["ok"] is True, f"{tool} success responses must set ok=true.")
    ensure(payload["tool"] == tool, f"{tool} success responses must identify the tool name.")


def _assert_error_envelope(exc: ToolCallError, tool: str, code: str) -> None:
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    require_keys(payload, ["ok", "tool", "error"], f"{tool} error response")
    ensure(payload["ok"] is False, f"{tool} error responses must set ok=false.")
    ensure(payload["tool"] == tool, f"{tool} error responses must identify the tool name.")
    ensure(isinstance(payload["error"], dict), f"{tool} error response must include an error object.")
    require_keys(payload["error"], ["code", "message"], f"{tool} error response error")
    expect_error_code(exc, code, tool)


def _assert_governed_record(record: dict[str, Any], context: str) -> None:
    require_keys(record, GOVERNED_RECORD_KEYS, context)


def _assert_version_entries(versions: list[dict[str, Any]], context: str) -> None:
    ensure(bool(versions), f"{context} should include at least one version entry.")
    for version in versions:
        require_keys(version, VERSION_KEYS, context)


def _matching_search_results(
    payload: dict[str, Any],
    *,
    scope: str,
    namespace: str,
    topic: str,
    field: str,
    memory_type: str,
    value: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        if (
            result.get("scope") == scope
            and result.get("namespace") == namespace
            and result.get("topic") == topic
            and result.get("field") == field
            and result.get("memory_type") == memory_type
            and result.get("value") == value
        ):
            matches.append(result)
    return matches


def _assert_memory_search_rejects_invalid_subject(
    ctx: "ValidationHarness",
    namespace_suffix: str,
    subject: dict[str, Any],
    failure_message: str,
) -> None:
    namespace = ctx.namespace(namespace_suffix)
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="Berlin",
        provenance=ctx.provenance(namespace_suffix),
    )
    try:
        ctx.search(
            scope="repository",
            namespace=namespace,
            query="Berlin",
            subject=subject,
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_search", "invalid_request")
        return
    raise ValidationFailure(failure_message)


PROMOTION_KEYS = [
    "review_id",
    "proposed_memory_type",
    "topic",
    "field",
    "value",
    "rationale",
    "source_seqs",
]


def _assert_promotion_proposal(proposal: dict[str, Any], context: str) -> None:
    require_keys(proposal, PROMOTION_KEYS, context)
    ensure(
        proposal["proposed_memory_type"] in {"belief", "fact"},
        f"{context} proposed_memory_type must be belief or fact.",
    )
    ensure(isinstance(proposal["source_seqs"], list), f"{context} source_seqs must be an array.")


REVIEW_FLOW_MIN_PROMOTIONS = 2
REVIEW_FLOW_NOISE_CLUSTERS = (
    ("validation_safe_merge_cluster_a", 6),
    ("validation_safe_merge_cluster_b", 6),
)


def _proposal_lookup_kwargs(
    proposal: dict[str, Any],
    *,
    scope: str,
    namespace: str,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "scope": scope,
        "namespace": namespace,
        "topic": proposal["topic"],
        "memory_type": proposal["proposed_memory_type"],
    }
    if proposal.get("field") is not None:
        kwargs["field"] = proposal["field"]
    return kwargs


def _assert_proposed_subject_not_in_current_state(
    ctx: "ValidationHarness",
    proposal: dict[str, Any],
    *,
    scope: str,
    namespace: str,
) -> None:
    try:
        ctx.get(**_proposal_lookup_kwargs(proposal, scope=scope, namespace=namespace))
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure(
            "Pending promotion subject "
            f"({proposal['proposed_memory_type']}:{proposal['topic']}/{proposal.get('field')}) "
            "must not be governed current state before memory_review accept."
        )


def _seed_review_flow_consolidation_corpus(ctx: "ValidationHarness", namespace: str) -> None:
    """Seed held fact plus multiple same-topic low-salience belief clusters for safe_merge."""
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="validation_held_fact",
        field="status",
        value="stable held fact for safe_merge",
        provenance=ctx.provenance("review-flow-held-fact"),
    )
    for cluster_index, (topic, belief_count) in enumerate(REVIEW_FLOW_NOISE_CLUSTERS):
        for note_index in range(belief_count):
            ctx.remember(
                scope="repository",
                namespace=namespace,
                memory_type="belief",
                topic=topic,
                field=f"noise_note_{note_index}",
                value=(
                    f"validation low-salience belief cluster={cluster_index} "
                    f"note={note_index} safe_merge distractor"
                ),
                provenance=ctx.provenance(f"review-flow-noise-{cluster_index}-{note_index}"),
            )


def _require_consolidation_promotions(
    ctx: "ValidationHarness",
    namespace: str,
    *,
    min_count: int = REVIEW_FLOW_MIN_PROMOTIONS,
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    payload = ctx.consolidate(scope="repository", namespace=namespace, dry_run=dry_run)
    ensure(isinstance(payload.get("promotions"), list), "Consolidation must return a promotions array.")
    promotions = payload["promotions"]
    for promotion in promotions:
        _assert_promotion_proposal(promotion, "memory_consolidate promotion")
    ensure(
        len(promotions) >= min_count,
        "Consolidation must produce at least "
        f"{min_count} promotion proposals for review-flow coverage; observed {len(promotions)}.",
    )
    return promotions


CITATION_KEYS = ["scope", "namespace", "topic", "field", "memory_type", "seq", "event_id"]

PROFILE_SUCCESS_KEYS = [
    "scope",
    "namespace",
    "persona_id",
    "depth",
    "budget_tokens",
    "manifest",
    "sections",
    "citations",
]

REFLECT_SUCCESS_KEYS = [
    "scope",
    "namespace",
    "evaluation_mode",
    "query",
    "synthesis",
    "citations",
    "evidence_count",
]

TEMPORAL_QUERY_SUCCESS_KEYS = ["scope", "namespace", "topic", "trajectories"]
TEMPORAL_SUBJECT_KEYS = ["scope", "namespace", "topic", "field", "memory_type"]
TEMPORAL_POINT_VERSION_KEYS = ["seq", "event_id", "valid_from_seq", "valid_to_seq", "recorded_at"]
GRAPH_ENTITY_KEYS = ["entity_id", "entity_type", "label", "valid_from_seq"]
GRAPH_EDGE_KEYS = ["edge_id", "edge_type", "from_entity_id", "to_entity_id", "valid_from_seq", "source_event_id"]
AUDIT_EXPORT_SUCCESS_KEYS = ["scope", "namespace", "format", "event_count", "records", "truncated", "export_id"]
AUDIT_EVENT_KEYS = ["audit_id", "recorded_at", "event_kind", "scope", "namespace", "tool", "actor", "outcome"]

SESSION_SUMMARY_MAX_SCALARS = 4096
PROFILE_ENGINE_POLL_TIMEOUT_S = 5.0
PROFILE_ENGINE_POLL_INTERVAL_S = 0.1


def _assert_citation_shape(citation: dict[str, Any], context: str) -> None:
    require_keys(citation, CITATION_KEYS, context)


def _assert_temporal_point_shape(point: dict[str, Any], context: str) -> None:
    require_keys(point, ["audit_point", "value", "status"], context)
    ensure(isinstance(point["audit_point"], dict), f"{context} audit_point must be an object.")
    ensure(
        point["status"] in {"current", "historical", "retracted", "absent"},
        f"{context} status must be current, historical, retracted, or absent.",
    )
    if point["status"] == "absent":
        ensure(point["value"] is None, f"{context} absent points should return value=null.")
        return
    require_keys(point, TEMPORAL_POINT_VERSION_KEYS, context)


def _assert_graph_entity_shape(entity: dict[str, Any], context: str) -> None:
    require_keys(entity, GRAPH_ENTITY_KEYS, context)


def _assert_graph_edge_shape(edge: dict[str, Any], context: str) -> None:
    require_keys(edge, GRAPH_EDGE_KEYS, context)


def _parse_jsonl_records(payload: Any, context: str) -> list[dict[str, Any]]:
    ensure(isinstance(payload, str), f"{context} should be newline-delimited JSON text.")
    text = payload.strip()
    if not text:
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationFailure(f"{context} line {line_number} should be valid JSON: {exc}") from exc
        ensure(isinstance(decoded, dict), f"{context} line {line_number} should decode to a JSON object.")
        records.append(decoded)
    return records


def _find_trajectory(
    payload: dict[str, Any],
    *,
    scope: str,
    namespace: str,
    topic: str,
    field: str,
    memory_type: str,
) -> dict[str, Any]:
    for trajectory in payload.get("trajectories", []):
        subject = trajectory.get("subject") if isinstance(trajectory.get("subject"), dict) else {}
        if (
            subject.get("scope") == scope
            and subject.get("namespace") == namespace
            and subject.get("topic") == topic
            and subject.get("field") == field
            and subject.get("memory_type") == memory_type
        ):
            return trajectory
    raise ValidationFailure(
        "Temporal query did not return the expected subject trajectory for "
        f"{scope}/{namespace}/{topic}/{field}/{memory_type}."
    )


def _find_graph_entity_by_label(graph_snapshot: dict[str, Any], label: str) -> dict[str, Any]:
    entities = graph_snapshot.get("entities")
    ensure(isinstance(entities, list), "graph_snapshot.entities must be an array.")
    for entity in entities:
        ensure(isinstance(entity, dict), "graph_snapshot entities must be objects.")
        _assert_graph_entity_shape(entity, "graph_snapshot entity")
        if entity.get("label") == label:
            return entity
    raise ValidationFailure(f"graph_snapshot should include an entity labeled '{label}'.")


def _graph_snapshot_signature(graph_snapshot: dict[str, Any]) -> tuple[tuple[str, str, int, Any], ...]:
    entities = graph_snapshot.get("entities")
    edges = graph_snapshot.get("edges")
    ensure(isinstance(entities, list), "graph_snapshot.entities must be an array.")
    ensure(isinstance(edges, list), "graph_snapshot.edges must be an array.")
    entity_signature = []
    for entity in entities:
        ensure(isinstance(entity, dict), "graph_snapshot entities must be objects.")
        _assert_graph_entity_shape(entity, "graph_snapshot entity")
        entity_signature.append(
            (
                "entity",
                str(entity["entity_id"]),
                int(entity["valid_from_seq"]),
                entity.get("valid_to_seq"),
                str(entity["label"]),
            )
        )
    edge_signature = []
    for edge in edges:
        ensure(isinstance(edge, dict), "graph_snapshot edges must be objects.")
        _assert_graph_edge_shape(edge, "graph_snapshot edge")
        edge_signature.append(
            (
                "edge",
                str(edge["edge_id"]),
                int(edge["valid_from_seq"]),
                edge.get("valid_to_seq"),
                str(edge["edge_type"]),
                str(edge["from_entity_id"]),
                str(edge["to_entity_id"]),
            )
        )
    return tuple(sorted(entity_signature + edge_signature))


def _rfc3339_utc_plus_seconds(recorded_at: str, seconds: int) -> str:
    normalized = recorded_at[:-1] + "+00:00" if recorded_at.endswith("Z") else recorded_at
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    adjusted = parsed.astimezone(timezone.utc) + timedelta(seconds=seconds)
    return adjusted.isoformat().replace("+00:00", "Z")


def _assert_phase5_status_metadata(payload: dict[str, Any], context: str) -> None:
    require_keys(payload, ["pii_scan", "retention", "fleet"], context)
    require_keys(payload["pii_scan"], ["enabled", "policy"], f"{context} pii_scan")
    require_keys(payload["retention"], ["enabled", "policies", "legal_hold_count"], f"{context} retention")
    require_keys(
        payload["fleet"],
        ["mode", "backend_reachable", "last_synced_seq", "replica_lag_seq"],
        f"{context} fleet",
    )


def _injection_marker_pattern(injection_id: str) -> str:
    return f"ai-memory:begin injection_id={injection_id}"


def _assert_context_fencing_post_conditions(
    fenced: str,
    *,
    known_injection_ids: list[str],
    preserved_fragments: list[str],
    context: str,
) -> None:
    for injection_id in known_injection_ids:
        ensure(
            _injection_marker_pattern(injection_id) not in fenced,
            f"{context} must not echo known injection block for injection_id={injection_id}.",
        )
    for fragment in preserved_fragments:
        ensure(fragment in fenced, f"{context} must preserve untagged content containing '{fragment}'.")


def _unicode_scalars(value: str) -> int:
    return len(list(value))


HERMES_ADAPTER_COMMAND_ENV = "MEMORY_SERVICE_VALIDATION_HERMES_COMMAND"
OPENCLAW_ADAPTER_COMMAND_ENV = "MEMORY_SERVICE_VALIDATION_OPENCLAW_COMMAND"
HERMES_SYNC_TURN_MAX_MS = 50.0
HERMES_SYNC_TURN_PERSIST_TIMEOUT_S = 2.0
HERMES_SYNC_TURN_PERSIST_INTERVAL_S = 0.05


def _require_adapter_command(env_name: str, label: str) -> str:
    command = os.environ.get(env_name)
    ensure(
        bool(command),
        f"Set {env_name} to the application-owned {label} adapter validation bridge command before running the v1.4.0 suite.",
    )
    return str(command)


@contextmanager
def _temporary_workspace(prefix: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=prefix) as workspace_dir:
        yield Path(workspace_dir)


@contextmanager
def _adapter_bridge(
    ctx: "ValidationHarness",
    *,
    env_name: str,
    label: str,
    initialize_arguments: dict[str, Any],
) -> Iterator[AdapterBridgeClient]:
    client = AdapterBridgeClient(_require_adapter_command(env_name, label), cwd=ctx.workspace_root)
    client.start()
    try:
        client.initialize(initialize_arguments)
        yield client
    finally:
        client.close()


def _decode_json_string_result(result: Any, context: str) -> dict[str, Any]:
    ensure(isinstance(result, str), f"{context} should return a JSON string.")
    try:
        decoded = json.loads(result)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(f"{context} should return valid JSON text: {exc}") from exc
    ensure(isinstance(decoded, dict), f"{context} should return a JSON object.")
    return decoded


def _assert_openclaw_search_result_shape(record: dict[str, Any], context: str) -> None:
    require_keys(record, ["path", "snippet", "score", "startLine", "endLine"], context)
    ensure(isinstance(record["path"], str) and bool(record["path"]), f"{context} path should be a non-empty string.")
    ensure(isinstance(record["snippet"], str), f"{context} snippet should be a string.")


def _assert_openclaw_path_in_workspace_or_alias(path_value: str, workspace_root: Path, context: str) -> None:
    if path_value.startswith("memory/"):
        return
    resolved_path = Path(path_value).resolve()
    ensure(
        resolved_path.is_relative_to(workspace_root.resolve()),
        f"{context} path should stay inside the configured workspace root or use a memory/ alias.",
    )


def _extract_openclaw_record(result: Any, context: str) -> dict[str, Any] | None:
    if result is None:
        return None
    ensure(isinstance(result, dict), f"{context} should return a JSON object.")
    if "record" in result:
        record = result["record"]
        ensure(record is None or isinstance(record, dict), f"{context} record should be an object or null.")
        return record
    if {"path", "snippet"}.issubset(result.keys()):
        return result
    if "results" in result:
        ensure(result["results"] == [], f"{context} should return an empty result on miss, not a populated results array.")
        return None
    if result.get("found") is False:
        return None
    ensure(not result, f"{context} should return an empty result on miss or a record-shaped object on hit.")
    return None


def case_tool_catalog(ctx: "ValidationHarness") -> str:
    tools = ctx.list_tools()
    observed = {tool["name"] for tool in tools}
    expected = {
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
        "memory_audit_export",
    }
    ensure(observed == expected, f"Expected tool surface {sorted(expected)}, observed {sorted(observed)}")
    return "MCP surface matches the eleven published tools."


def case_memory_remember_success(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("remember-success")
    payload = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="Berlin",
        provenance=ctx.provenance("remember-success"),
    )
    _assert_success_envelope(payload, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    require_keys(payload["subject"], ["scope", "namespace", "topic", "field", "memory_type"], "memory_remember subject")
    require_keys(payload["current_version"], VERSION_KEYS, "memory_remember current_version")
    ensure(payload["integration_status"] == "integrated", "memory_remember must report integration_status='integrated'.")
    ensure(payload["seq"] >= 1, "memory_remember should return a positive WAL sequence number.")
    return "Successful remember returned WAL identity and integrated current version metadata."


def case_memory_remember_invalid_scope(ctx: "ValidationHarness") -> str:
    try:
        ctx.remember(
            scope="project",
            namespace=ctx.namespace("invalid-scope"),
            memory_type="fact",
            topic="user_profile",
            field="city",
            value="Berlin",
            provenance=ctx.provenance("invalid-scope"),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_remember", "invalid_scope")
        return "Invalid scope is rejected with the published error category."
    raise ValidationFailure("memory_remember accepted an unsupported scope.")


def case_memory_remember_missing_required_field(ctx: "ValidationHarness") -> str:
    try:
        ctx.remember(
            scope="repository",
            namespace=ctx.namespace("missing-required-field"),
            memory_type="fact",
            topic="user_profile",
            value="Berlin",
            provenance=ctx.provenance("missing-required-field"),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_remember", "invalid_request")
        return "Missing required fields are rejected as invalid_request."
    raise ValidationFailure("memory_remember accepted a governed write without a required field.")


def case_memory_search_current_metadata(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-current")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="Lisbon travel profile",
        provenance=ctx.provenance("search-current"),
    )
    payload = ctx.search(scope="repository", namespace=namespace, query="Lisbon")
    _assert_success_envelope(payload, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(payload["evaluation_mode"] == "current", "Default search mode must be current.")
    result = _current_result(payload)
    _assert_governed_record(result, "memory_search result")
    require_keys(result, ["match_reason"], "memory_search result")
    return "Current-state search returns the normative minimum result metadata and match_reason."


def case_memory_search_excludes_superseded_current(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-superseded")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="London",
        provenance=ctx.provenance("search-superseded-old"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="Berlin",
        provenance=ctx.provenance("search-superseded-new"),
    )
    payload = ctx.search(scope="repository", namespace=namespace, query="city")
    values = [item.get("value") for item in payload.get("results", [])]
    ensure("Berlin" in values, "Current search should contain the latest fact value.")
    ensure("London" not in values, "Default current-state search must exclude superseded governed values.")
    return "Default search excludes superseded governed values from the current corpus."


def case_memory_search_subject_filters_ambiguous_same_field(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-subject-pollution")
    target = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="Porto itinerary city",
        provenance=ctx.provenance("search-subject-pollution-travel"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="billing_profile",
        field="city",
        value="Porto billing city",
        provenance=ctx.provenance("search-subject-pollution-billing"),
    )

    unfiltered = ctx.search(scope="repository", namespace=namespace, query="Porto city")
    _assert_success_envelope(unfiltered, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    unfiltered_topics = {(item.get("topic"), item.get("field"), item.get("value")) for item in unfiltered.get("results", [])}
    ensure(
        ("travel_profile", "city", "Porto itinerary city") in unfiltered_topics,
        "Unfiltered search should include the travel_profile city candidate before any subject partition is applied.",
    )
    ensure(
        ("billing_profile", "city", "Porto billing city") in unfiltered_topics,
        "Unfiltered search should remain ambiguous when same-field candidates exist under different topics.",
    )

    filtered = ctx.search(
        scope="repository",
        namespace=namespace,
        query="Porto city",
        subject={"topic": "travel_profile", "field": "city"},
    )
    _assert_success_envelope(filtered, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    filtered_matches = _matching_search_results(
        filtered,
        scope="repository",
        namespace=namespace,
        topic="travel_profile",
        field="city",
        memory_type="fact",
        value="Porto itinerary city",
    )
    ensure(filtered_matches, "Subject-filtered search should retain the requested travel_profile city candidate.")
    ensure(
        not _matching_search_results(
            filtered,
            scope="repository",
            namespace=namespace,
            topic="billing_profile",
            field="city",
            memory_type="fact",
            value="Porto billing city",
        ),
        "Subject-filtered search must exclude same-field candidates from other topics.",
    )
    ensure(
        filtered_matches[0].get("event_id") == target["event_id"],
        "Subject-filtered search should return the governed candidate for the requested subject.",
    )
    return "A supplied subject filter resolves same-field different-topic search pollution by partitioning candidates before ranking."


def case_memory_search_without_subject_preserves_prior_contract(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-subject-backward-compatible")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="Porto itinerary city",
        provenance=ctx.provenance("search-subject-backward-compatible-travel"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="billing_profile",
        field="city",
        value="Porto billing city",
        provenance=ctx.provenance("search-subject-backward-compatible-billing"),
    )

    payload = ctx.search(scope="repository", namespace=namespace, query="Porto city")
    _assert_success_envelope(payload, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(payload["evaluation_mode"] == "current", "Omitting subject must preserve default current-state evaluation.")
    ensure(
        len(_matching_search_results(
            payload,
            scope="repository",
            namespace=namespace,
            topic="travel_profile",
            field="city",
            memory_type="fact",
            value="Porto itinerary city",
        ))
        == 1,
        "Omitting subject must keep the prior contract's ability to return the travel_profile candidate from the governed corpus.",
    )
    ensure(
        len(_matching_search_results(
            payload,
            scope="repository",
            namespace=namespace,
            topic="billing_profile",
            field="city",
            memory_type="fact",
            value="Porto billing city",
        ))
        == 1,
        "Omitting subject must keep the prior contract's unpartitioned governed search behavior.",
    )
    return "When subject is omitted, memory_search stays on the prior v1.1.1 current-state contract."


def case_memory_search_subject_match_reason(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-subject-match-reason")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="Porto itinerary city",
        provenance=ctx.provenance("search-subject-match-reason-travel"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="billing_profile",
        field="city",
        value="Porto billing city",
        provenance=ctx.provenance("search-subject-match-reason-billing"),
    )

    payload = ctx.search(
        scope="repository",
        namespace=namespace,
        query="Porto city",
        subject={"topic": "travel_profile", "field": "city"},
    )
    _assert_success_envelope(payload, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    result = _current_result(payload)
    match_reason = str(result.get("match_reason", "")).lower()
    subject_constraint_indicators = ("subject", "topic", "field", "travel_profile")
    ensure(match_reason, "Subject-filtered search results must provide a non-empty match_reason.")
    ensure(
        any(token in match_reason for token in subject_constraint_indicators),
        "match_reason should reflect that the supplied subject constraint was applied, not only that query terms matched.",
    )
    return "Subject-filtered search reports a match_reason that reflects the applied subject constraint."


def case_memory_search_invalid_subject_missing_keys(ctx: "ValidationHarness") -> str:
    _assert_memory_search_rejects_invalid_subject(
        ctx,
        "search-invalid-subject-missing-keys",
        {},
        "memory_search accepted a subject object that supplied neither topic nor field.",
    )
    return "memory_search rejects subject filters that supply neither topic nor field."


def case_memory_search_invalid_subject_extra_keys(ctx: "ValidationHarness") -> str:
    _assert_memory_search_rejects_invalid_subject(
        ctx,
        "search-invalid-subject-extra-keys",
        {"topic": "travel_profile", "field": "city", "memory_type": "fact"},
        "memory_search accepted a subject object with keys other than topic and field.",
    )
    return "memory_search rejects subject filters that include keys beyond topic and field."


def case_memory_search_subject_with_as_of(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-subject-as-of")
    first = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="London itinerary city",
        provenance=ctx.provenance("search-subject-as-of-travel-old"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="billing_profile",
        field="city",
        value="London billing city",
        provenance=ctx.provenance("search-subject-as-of-billing"),
    )
    second = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="Berlin itinerary city",
        provenance=ctx.provenance("search-subject-as-of-travel-new"),
    )

    historical = ctx.search(
        scope="repository",
        namespace=namespace,
        query="city",
        subject={"topic": "travel_profile", "field": "city"},
        as_of=ctx.as_of_from_seq(first["seq"]),
    )
    _assert_success_envelope(historical, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(historical["evaluation_mode"] == "as_of", "Search with as_of must report evaluation_mode='as_of'.")
    historical_result = _current_result(historical)
    ensure(
        historical_result.get("value") == "London itinerary city",
        "subject + as_of should return the value current for the requested subject at the audit point.",
    )
    ensure(
        historical_result.get("topic") == "travel_profile",
        "subject + as_of must still enforce the requested subject topic after historical evaluation.",
    )

    current = ctx.search(
        scope="repository",
        namespace=namespace,
        query="city",
        subject={"topic": "travel_profile", "field": "city"},
        as_of=ctx.as_of_from_seq(second["seq"]),
    )
    _assert_success_envelope(current, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    current_result = _current_result(current)
    ensure(
        current_result.get("value") == "Berlin itinerary city",
        "subject + as_of should advance with the requested subject's historical state, not a different topic's state.",
    )
    return "Historical search composes as_of with subject by evaluating the audit point first and then narrowing to the requested subject."


def case_memory_search_subject_partition_safety(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-subject-partition-safety")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="London itinerary city",
        provenance=ctx.provenance("search-subject-partition-safety-old"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="city",
        value="Berlin itinerary city",
        provenance=ctx.provenance("search-subject-partition-safety-new"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="episode",
        topic="travel_profile",
        value="Episode: discussed Berlin itinerary city",
        episode_id="ep_subject_partition_safety",
        provenance=ctx.provenance("search-subject-partition-safety-episode"),
    )

    filtered_current = ctx.search(
        scope="repository",
        namespace=namespace,
        query="city itinerary",
        subject={"topic": "travel_profile", "field": "city"},
    )
    _assert_success_envelope(filtered_current, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    current_values = [item.get("value") for item in filtered_current.get("results", [])]
    ensure(
        "Berlin itinerary city" in current_values,
        "Subject filtering must preserve the current governed value inside the existing current-state corpus.",
    )
    ensure(
        "London itinerary city" not in current_values,
        "Subject filtering must not admit superseded governed values into current-state search.",
    )
    ensure(
        "Episode: discussed Berlin itinerary city" not in current_values,
        "Subject filtering alone must not expand the searchable corpus to include episodes unless they are explicitly requested.",
    )

    filtered_with_episodes = ctx.search(
        scope="repository",
        namespace=namespace,
        query="city itinerary",
        subject={"topic": "travel_profile"},
        include_episodes=True,
    )
    _assert_success_envelope(filtered_with_episodes, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    with_episode_values = [item.get("value") for item in filtered_with_episodes.get("results", [])]
    ensure(
        "Episode: discussed Berlin itinerary city" in with_episode_values,
        "Episodes may join the filtered corpus only when they are explicitly requested.",
    )
    ensure(
        "London itinerary city" not in with_episode_values,
        "Explicitly requested episodes must not let subject filtering reintroduce superseded governed values.",
    )
    return "Subject filtering is a deterministic partition over the governed corpus: it excludes superseded values and adds episodes only when explicitly requested."


def case_memory_search_as_of(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-as-of")
    first = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="London",
        provenance=ctx.provenance("search-as-of-old"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="Berlin",
        provenance=ctx.provenance("search-as-of-new"),
    )
    payload = ctx.search(
        scope="repository",
        namespace=namespace,
        query="London",
        as_of=ctx.as_of_from_seq(first["seq"]),
    )
    _assert_success_envelope(payload, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(payload["evaluation_mode"] == "as_of", "Historical search must report evaluation_mode='as_of'.")
    result = _current_result(payload)
    ensure(result["value"] == "London", "Historical search should return the value current at the requested audit point.")
    return "Search honors as_of validity evaluation for historical recall."


def _assert_memory_search_rejects_invalid_as_of(
    ctx: "ValidationHarness",
    namespace_suffix: str,
    as_of: dict[str, Any],
    failure_message: str,
) -> None:
    namespace = ctx.namespace(namespace_suffix)
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="Berlin",
        provenance=ctx.provenance(namespace_suffix),
    )
    try:
        ctx.search(
            scope="repository",
            namespace=namespace,
            query="Berlin",
            as_of=as_of,
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_search", "invalid_request")
        return
    raise ValidationFailure(failure_message)


def case_memory_search_invalid_as_of_shape(ctx: "ValidationHarness") -> str:
    _assert_memory_search_rejects_invalid_as_of(
        ctx,
        "search-invalid-as-of-both-keys",
        {"seq": 1, "recorded_at": "2026-06-03T12:34:56Z"},
        "memory_search accepted an as_of object with both seq and recorded_at.",
    )
    return "Historical search rejects malformed as_of objects that supply both seq and recorded_at."


def case_memory_search_invalid_as_of_missing_selector(ctx: "ValidationHarness") -> str:
    _assert_memory_search_rejects_invalid_as_of(
        ctx,
        "search-invalid-as-of-missing-selector",
        {},
        "memory_search accepted an as_of object with neither seq nor recorded_at.",
    )
    return "Historical search rejects malformed as_of objects that omit both seq and recorded_at."


def case_memory_search_invalid_as_of_non_integer_seq(ctx: "ValidationHarness") -> str:
    _assert_memory_search_rejects_invalid_as_of(
        ctx,
        "search-invalid-as-of-non-integer-seq",
        {"seq": "1"},
        "memory_search accepted an as_of object whose seq was not an integer.",
    )
    return "Historical search rejects malformed as_of objects whose seq is not an integer."


def case_memory_search_invalid_as_of_non_rfc3339_recorded_at(ctx: "ValidationHarness") -> str:
    _assert_memory_search_rejects_invalid_as_of(
        ctx,
        "search-invalid-as-of-non-rfc3339-recorded-at",
        {"recorded_at": "2026-06-03 12:34:56"},
        "memory_search accepted an as_of object whose recorded_at was not RFC3339 UTC.",
    )
    return "Historical search rejects malformed as_of objects whose recorded_at is not RFC3339 UTC."


def case_memory_search_episodes_opt_in(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("search-episodes")
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="conversation",
        value="Episode note about a delayed flight to Porto",
        episode_id="ep_episodes_1",
        provenance=ctx.provenance("search-episodes"),
    )
    without_episodes = ctx.search(scope="session", namespace=namespace, query="Porto")
    values_without = [item.get("value") for item in without_episodes.get("results", [])]
    ensure(
        "Episode note about a delayed flight to Porto" not in values_without,
        "Episode content must stay out of the default current-state search corpus.",
    )
    with_episodes = ctx.search(
        scope="session",
        namespace=namespace,
        query="Porto",
        include_episodes=True,
    )
    values_with = [item.get("value") for item in with_episodes.get("results", [])]
    ensure(
        "Episode note about a delayed flight to Porto" in values_with,
        "Episode content should become searchable when include_episodes=true.",
    )
    return "Append-only episodes are excluded by default and included only when explicitly requested."


def case_memory_get_current_and_versions(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("get-current")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="procedure",
        topic="deploy_runbook",
        field="rollback",
        value="Procedure v1",
        provenance=ctx.provenance("get-current-v1"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="procedure",
        topic="deploy_runbook",
        field="rollback",
        value="Procedure v2",
        provenance=ctx.provenance("get-current-v2"),
    )
    payload = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="deploy_runbook",
        field="rollback",
        memory_type="procedure",
        include_versions=True,
    )
    _assert_success_envelope(payload, "memory_get", ["scope", "namespace", "evaluation_mode", "record", "versions"])
    _assert_governed_record(payload["record"], "memory_get record")
    ensure(payload["record"].get("value") == "Procedure v2", "Default memory_get must return the current procedure version.")
    versions = payload.get("versions", [])
    ensure(len(versions) >= 2, "include_versions=true should return the full version chain.")
    _assert_version_entries(versions, "memory_get versions")
    return "memory_get returns the current value and can expose the full version chain."


def case_memory_get_not_found(ctx: "ValidationHarness") -> str:
    try:
        ctx.get(
            scope="repository",
            namespace=ctx.namespace("get-missing"),
            topic="absent",
            field="value",
            memory_type="fact",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
        return "Direct lookup uses the standard not_found error envelope when no value exists."
    raise ValidationFailure("memory_get should return the published not_found error envelope for an absent subject.")


def case_memory_get_returns_normative_metadata(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("get-metadata")
    payload = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="user_profile",
        field="city",
        value="Berlin",
        provenance=ctx.provenance("get-metadata"),
    )
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="user_profile",
        field="city",
        memory_type="fact",
    )
    _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    record = fetched["record"]
    _assert_governed_record(record, "memory_get record")
    ensure(record["provenance"]["request_id"] == "get-metadata", "memory_get must return provenance for the current record.")
    ensure(record["valid_from_seq"] == payload["seq"], "memory_get must surface the record validity lower bound.")
    ensure(record["valid_to_seq"] is None, "Open current records must expose valid_to_seq=null.")
    ensure(record["layer"] == "semantic_unit", "Governed fact lookup should report semantic_unit as the record layer.")
    ensure(record["status"] == "current", "Default lookup must report the record as current.")
    return "memory_get returns provenance and validity metadata required for premise-safe retrieval."


def case_memory_forget_retracts_current(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("forget-current")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="constraint",
        topic="policy",
        field="preferred_region",
        value="eu-west",
        provenance=ctx.provenance("forget-current-write"),
    )
    payload = ctx.forget(
        scope="repository",
        namespace=namespace,
        memory_type="constraint",
        topic="policy",
        field="preferred_region",
        provenance=ctx.provenance("forget-current-forget"),
    )
    _assert_success_envelope(payload, "memory_forget", ["subject", "event_id", "seq", "retraction_status", "current_visibility"])
    ensure(payload["retraction_status"] == "retracted", "memory_forget must report retraction_status='retracted'.")
    ensure(
        payload["current_visibility"] == "hidden",
        "Retraction should confirm the subject is no longer returned by default current-state recall.",
    )
    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="policy",
            field="preferred_region",
            memory_type="constraint",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("Retracted subjects must be absent from current-state lookup.")
    return "memory_forget appends a retraction and removes the subject from current-state recall."


def case_memory_status_shape(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("status")
    payload = ctx.status(scope="repository", namespace=namespace)
    _assert_success_envelope(
        payload,
        "memory_status",
        [
            "scope",
            "namespace",
            "supported_scopes",
            "wal_high_water_seq",
            "semantic_units_in_sync",
            "index_status",
            "consolidation",
            "review_queue",
            "profile_engine",
            "session_summary",
        ],
    )
    require_keys(payload["index_status"], ["state"], "memory_status index_status")
    require_keys(payload["consolidation"], ["available", "last_run_at"], "memory_status consolidation")
    require_keys(payload["review_queue"], ["pending_count"], "memory_status review_queue")
    require_keys(payload["profile_engine"], ["enabled", "last_run_at"], "memory_status profile_engine")
    require_keys(payload["session_summary"], ["topic", "last_updated_seq"], "memory_status session_summary")
    ensure(
        isinstance(payload["review_queue"]["pending_count"], int),
        "memory_status review_queue.pending_count must be an integer.",
    )
    ensure(
        sorted(payload["supported_scopes"]) == ["repository", "session", "user"],
        "memory_status must advertise exactly user, session, and repository with no duplicates.",
    )
    return "memory_status reports required scope, WAL, semantic-unit, index, consolidation, review-queue, profile-engine, and session-summary metadata."


def case_memory_status_requires_scope_and_namespace(ctx: "ValidationHarness") -> str:
    try:
        ctx.status(namespace=ctx.namespace("status-missing-scope"))
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_status", "invalid_request")
    else:
        raise ValidationFailure("memory_status accepted a request without scope.")

    try:
        ctx.status(scope="repository")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_status", "invalid_request")
        return "memory_status rejects requests that omit either scope or namespace."
    raise ValidationFailure("memory_status accepted a request without namespace.")


def case_supersession_current_lookup(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("supersession")
    first = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="preference",
        topic="ui",
        field="theme",
        value="light",
        provenance=ctx.provenance("supersession-first"),
    )
    second = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="preference",
        topic="ui",
        field="theme",
        value="dark",
        provenance=ctx.provenance("supersession-second"),
    )
    payload = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="ui",
        field="theme",
        memory_type="preference",
        include_versions=True,
    )
    _assert_success_envelope(payload, "memory_get", ["scope", "namespace", "evaluation_mode", "record", "versions"])
    ensure(payload["record"].get("value") == "dark", "Current lookup must return the latest superseding value.")
    versions = payload.get("versions", [])
    ensure(len(versions) >= 2, "Superseding writes should preserve historical versions.")
    _assert_version_entries(versions, "memory_get versions")
    first_version = next((version for version in versions if version.get("event_id") == first.get("event_id")), None)
    second_version = next((version for version in versions if version.get("event_id") == second.get("event_id")), None)
    ensure(first_version is not None and second_version is not None, "Version chain should include both writes.")
    ensure(first_version.get("valid_to_seq") == second["seq"], "The prior open interval must close at the superseding sequence number.")
    ensure(second_version.get("valid_to_seq") is None, "The latest superseding write must remain open.")
    return "Superseding writes close the prior interval and expose only the latest open version by default."


def case_belief_does_not_supersede_fact(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("belief-vs-fact")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="Berlin",
        provenance=ctx.provenance("belief-vs-fact-fact"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="belief",
        topic="travel_profile",
        field="home_city",
        value="Maybe Munich",
        provenance=ctx.provenance("belief-vs-fact-belief"),
    )
    fact = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="travel_profile",
        field="home_city",
        memory_type="fact",
    )
    belief = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="travel_profile",
        field="home_city",
        memory_type="belief",
    )
    ensure(fact["record"].get("value") == "Berlin", "Belief writes must not supersede facts on the same topic/field.")
    ensure(belief["record"].get("value") == "Maybe Munich", "Belief values should remain independently retrievable.")
    return "Belief and fact memory types remain separate, with belief never superseding fact."


def case_as_of_after_supersession(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("audit-supersession")
    first = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="customer",
        field="tier",
        value="silver",
        provenance=ctx.provenance("audit-supersession-first"),
    )
    second = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="customer",
        field="tier",
        value="gold",
        provenance=ctx.provenance("audit-supersession-second"),
    )
    historical = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="customer",
        field="tier",
        memory_type="fact",
        as_of=ctx.as_of_from_recorded_at(first["current_version"]["recorded_at"]),
    )
    current = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="customer",
        field="tier",
        memory_type="fact",
        as_of=ctx.as_of_from_seq(second["seq"]),
    )
    _assert_success_envelope(historical, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    _assert_success_envelope(current, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    ensure(historical["evaluation_mode"] == "as_of", "Historical memory_get should report evaluation_mode='as_of'.")
    ensure(historical["record"].get("value") == "silver", "Historical as_of lookup should return the value current at the first audit point.")
    ensure(current["record"].get("value") == "gold", "Historical as_of lookup should advance after supersession.")
    return "Historical get respects both recorded_at and seq as_of evaluation points."


def case_as_of_after_retract(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("audit-retract")
    write = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="constraint",
        topic="policy",
        field="retention_window",
        value="90d",
        provenance=ctx.provenance("audit-retract-write"),
    )
    retract = ctx.forget(
        scope="repository",
        namespace=namespace,
        memory_type="constraint",
        topic="policy",
        field="retention_window",
        provenance=ctx.provenance("audit-retract-forget"),
    )
    before = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="policy",
        field="retention_window",
        memory_type="constraint",
        as_of=ctx.as_of_from_seq(write["seq"]),
    )
    ensure(before["record"].get("value") == "90d", "Audit lookup before retract should expose the formerly current value.")
    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="policy",
            field="retention_window",
            memory_type="constraint",
            as_of=ctx.as_of_from_seq(retract["seq"]),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
        return "Retractions preserve prior history for audit while closing current-state visibility."
    raise ValidationFailure("Audit lookup at or after retract should show no current value.")


def case_schema_driven_propagation(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("propagation")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="London",
        provenance=ctx.provenance("propagation-parent-first"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_summary",
        field="summary",
        value="Traveler summary bound to London home city.",
        extends=[{"topic": "travel_profile", "field": "home_city"}],
        provenance=ctx.provenance("propagation-dependent"),
    )
    parent_update = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="Berlin",
        provenance=ctx.provenance("propagation-parent-second"),
    )
    dependent = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        memory_type="fact",
        include_versions=True,
    )
    record = dependent["record"]
    ensure("London" not in record["value"], "Dependent current value must not contain the stale bound parent value after propagation.")
    ensure("Berlin" in record["value"], "Dependent current value must reflect the parent's new current value after propagation.")
    versions = dependent.get("versions", [])
    _assert_version_entries(versions, "propagated dependent versions")
    ensure(any(version.get("valid_to_seq") == parent_update["seq"] for version in versions), "Propagation must close the prior dependent version at the parent-change sequence.")
    ensure(any(version.get("valid_to_seq") is None and "Berlin" in str(version.get("value")) for version in versions), "Propagation must open a new dependent version that reflects the new parent state.")
    return "Schema-driven propagation enforces the direct-extends post-condition and rotates dependent versions at the parent-change seq."


def case_integration_failed_error(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("integration-failed")
    ctx.set_fault("integration_fail_next", True)
    try:
        ctx.remember(
            scope="repository",
            namespace=namespace,
            memory_type="fact",
            topic="ops",
            field="write_probe",
            value="should not commit",
            provenance=ctx.provenance("integration-failed"),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_remember", "integration_failed")
    else:
        raise ValidationFailure("memory_remember should surface integration_failed when ingest cannot complete.")
    finally:
        ctx.set_fault("integration_fail_next", False)

    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="ops",
            field="write_probe",
            memory_type="fact",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("A failed integration must not leak a partial current-state value.")
    return "integration_failed is surfaced cleanly and leaves no partially integrated state behind."


def case_index_rebuild_preserves_recall(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("index-rebuild")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="ops",
        field="pager_rotation",
        value="Rotation owner is Alice",
        provenance=ctx.provenance("index-rebuild"),
    )
    before = ctx.search(scope="repository", namespace=namespace, query="Alice")
    direct = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="ops",
        field="pager_rotation",
        memory_type="fact",
    )
    ctx.rebuild_indexes(namespace=namespace)
    after = ctx.search(scope="repository", namespace=namespace, query="Alice")
    before_subjects = [(_subject(item["scope"], item["namespace"], item["topic"], item["field"], item["memory_type"]), item["value"]) for item in before.get("results", [])]
    after_subjects = [(_subject(item["scope"], item["namespace"], item["topic"], item["field"], item["memory_type"]), item["value"]) for item in after.get("results", [])]
    ensure(before_subjects == after_subjects, "Index rebuild must not change governed current-state recall results.")
    ensure(direct["record"].get("value") == "Rotation owner is Alice", "Direct lookup must remain intact across index rebuilds.")
    return "Search correctness is preserved after a full index rebuild from durable state."


def case_index_unavailable_only_degrades_search(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("index-unavailable")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="ops",
        field="pager_rotation",
        value="Rotation owner is Bob",
        provenance=ctx.provenance("index-unavailable"),
    )
    ctx.set_index_availability(namespace=namespace, available=False)
    try:
        ctx.search(scope="repository", namespace=namespace, query="Bob")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_search", "index_unavailable")
    else:
        raise ValidationFailure("memory_search should report index_unavailable when the index is deliberately unavailable.")
    direct = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="ops",
        field="pager_rotation",
        memory_type="fact",
    )
    ensure(direct["record"].get("value") == "Rotation owner is Bob", "Direct lookup must continue to work while the search index is unavailable.")
    ctx.set_index_availability(namespace=namespace, available=True)
    return "Index outages degrade search only and do not corrupt direct lookup or audit state."


def case_namespace_isolation(ctx: "ValidationHarness") -> str:
    namespace_a = ctx.namespace("namespace-a")
    namespace_b = ctx.namespace("namespace-b")
    subject = {"topic": "prefs", "field": "language", "memory_type": "preference"}
    shared_query = "namespace isolation shared token"
    value_a = f"{shared_query} English"
    value_b = f"{shared_query} German"
    ctx.remember(
        scope="user",
        namespace=namespace_a,
        value=value_a,
        provenance=ctx.provenance("namespace-a"),
        **subject,
    )
    ctx.remember(
        scope="user",
        namespace=namespace_b,
        value=value_b,
        provenance=ctx.provenance("namespace-b"),
        **subject,
    )
    a_value = ctx.get(scope="user", namespace=namespace_a, **subject)
    b_value = ctx.get(scope="user", namespace=namespace_b, **subject)
    ensure(a_value["record"].get("value") == value_a, "Namespace A should retain its own value.")
    ensure(b_value["record"].get("value") == value_b, "Namespace B should retain its own value.")

    search_a = ctx.search(scope="user", namespace=namespace_a, query=shared_query)
    _assert_success_envelope(search_a, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(
        bool(
            _matching_search_results(
                search_a,
                scope="user",
                namespace=namespace_a,
                value=value_a,
                **subject,
            )
        ),
        "Namespace-scoped search should return the namespace A record as a positive control.",
    )
    ensure(
        not _matching_search_results(
            search_a,
            scope="user",
            namespace=namespace_a,
            value=value_b,
            **subject,
        ),
        "Namespace-scoped search for namespace A must not return the colliding record from namespace B.",
    )

    search_b = ctx.search(scope="user", namespace=namespace_b, query=shared_query)
    _assert_success_envelope(search_b, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(
        bool(
            _matching_search_results(
                search_b,
                scope="user",
                namespace=namespace_b,
                value=value_b,
                **subject,
            )
        ),
        "Namespace-scoped search should return the namespace B record as a positive control.",
    )
    ensure(
        not _matching_search_results(
            search_b,
            scope="user",
            namespace=namespace_b,
            value=value_a,
            **subject,
        ),
        "Namespace-scoped search for namespace B must not return the colliding record from namespace A.",
    )
    return "Namespaces partition memory identity and prevent collisions within the same scope."


def case_scope_isolation(ctx: "ValidationHarness") -> str:
    advertised = ctx.status(scope="repository", namespace=ctx.namespace("scope-isolation-status"))
    _assert_success_envelope(
        advertised,
        "memory_status",
        [
            "scope",
            "namespace",
            "supported_scopes",
            "wal_high_water_seq",
            "semantic_units_in_sync",
            "index_status",
            "consolidation",
            "review_queue",
        ],
    )
    supported_scopes = advertised["supported_scopes"]
    expected_scopes = {"user", "session", "repository"}
    ensure(
        set(supported_scopes) == expected_scopes and len(supported_scopes) == len(expected_scopes),
        "memory_status must advertise exactly user, session, and repository for scope-isolation coverage.",
    )

    subject = {"topic": "prefs", "field": "editor", "memory_type": "preference"}

    for source_scope in supported_scopes:
        for target_scope in supported_scopes:
            if source_scope == target_scope:
                continue

            namespace = ctx.namespace(f"scope-isolation-{source_scope}-to-{target_scope}")
            shared_query = "shared editor"
            source_value = f"{shared_query} {source_scope} against {target_scope}"
            target_value = f"{shared_query} {target_scope} against {source_scope}"

            remembered_source = ctx.remember(
                scope=source_scope,
                namespace=namespace,
                value=source_value,
                provenance=ctx.provenance(f"scope-isolation-{source_scope}-to-{target_scope}"),
                **subject,
            )
            _assert_success_envelope(
                remembered_source,
                "memory_remember",
                ["subject", "event_id", "seq", "integration_status", "current_version"],
            )

            remembered_target = ctx.remember(
                scope=target_scope,
                namespace=namespace,
                value=target_value,
                provenance=ctx.provenance(f"scope-isolation-{target_scope}-to-{source_scope}"),
                **subject,
            )
            _assert_success_envelope(
                remembered_target,
                "memory_remember",
                ["subject", "event_id", "seq", "integration_status", "current_version"],
            )

            source_get = ctx.get(scope=source_scope, namespace=namespace, **subject)
            _assert_success_envelope(source_get, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
            ensure(
                source_get["record"].get("value") == source_value,
                f"{source_scope} should return its own current value for {source_scope}->{target_scope}.",
            )

            target_get = ctx.get(scope=target_scope, namespace=namespace, **subject)
            _assert_success_envelope(target_get, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
            ensure(
                target_get["record"].get("value") == target_value,
                f"{target_scope} should return its own current value for {source_scope}->{target_scope}.",
            )

            source_search = ctx.search(scope=source_scope, namespace=namespace, query=shared_query)
            _assert_success_envelope(source_search, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
            ensure(
                bool(
                    _matching_search_results(
                        source_search,
                        scope=source_scope,
                        namespace=namespace,
                        value=source_value,
                        **subject,
                    )
                ),
                f"memory_search should return the written value inside its own {source_scope} scope for {source_scope}->{target_scope}.",
            )
            ensure(
                not _matching_search_results(
                    source_search,
                    scope=source_scope,
                    namespace=namespace,
                    value=target_value,
                    **subject,
                ),
                f"Value written in {target_scope} leaked into {source_scope} via memory_search with identical namespace and subject.",
            )

            target_search = ctx.search(scope=target_scope, namespace=namespace, query=shared_query)
            _assert_success_envelope(target_search, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
            ensure(
                bool(
                    _matching_search_results(
                        target_search,
                        scope=target_scope,
                        namespace=namespace,
                        value=target_value,
                        **subject,
                    )
                ),
                f"memory_search should return the written value inside its own {target_scope} scope for {source_scope}->{target_scope}.",
            )
            ensure(
                not _matching_search_results(
                    target_search,
                    scope=target_scope,
                    namespace=namespace,
                    value=source_value,
                    **subject,
                ),
                f"Value written in {source_scope} leaked into {target_scope} via memory_search with identical namespace and subject.",
            )

            forgotten = ctx.forget(
                scope=source_scope,
                namespace=namespace,
                provenance=ctx.provenance(f"scope-isolation-forget-{source_scope}-to-{target_scope}"),
                **subject,
            )
            _assert_success_envelope(
                forgotten,
                "memory_forget",
                ["subject", "event_id", "seq", "retraction_status", "current_visibility"],
            )
            ensure(
                forgotten["retraction_status"] == "retracted",
                f"memory_forget should retract the source-scope value after the {source_scope}->{target_scope} isolation check.",
            )

            try:
                ctx.get(scope=source_scope, namespace=namespace, **subject)
            except ToolCallError as exc:
                _assert_error_envelope(exc, "memory_get", "not_found")
            else:
                raise ValidationFailure(
                    f"memory_forget should remove the {source_scope} value from current-state recall after the {source_scope}->{target_scope} check."
                )

            source_search_after_forget = ctx.search(scope=source_scope, namespace=namespace, query=shared_query)
            _assert_success_envelope(source_search_after_forget, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
            ensure(
                not _matching_search_results(
                    source_search_after_forget,
                    scope=source_scope,
                    namespace=namespace,
                    value=source_value,
                    **subject,
                ),
                f"memory_search should not return the retracted {source_scope} value after the {source_scope}->{target_scope} isolation check.",
            )
            ensure(
                not _matching_search_results(
                    source_search_after_forget,
                    scope=source_scope,
                    namespace=namespace,
                    value=target_value,
                    **subject,
                ),
                f"memory_search in {source_scope} must not surface the colliding {target_scope} record after retracting only the {source_scope} subject.",
            )

            target_get_after_forget = ctx.get(scope=target_scope, namespace=namespace, **subject)
            _assert_success_envelope(target_get_after_forget, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
            ensure(
                target_get_after_forget["record"].get("value") == target_value,
                f"memory_forget for {source_scope} must not retract the colliding {target_scope} subject.",
            )

            target_search_after_forget = ctx.search(scope=target_scope, namespace=namespace, query=shared_query)
            _assert_success_envelope(target_search_after_forget, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
            ensure(
                bool(
                    _matching_search_results(
                        target_search_after_forget,
                        scope=target_scope,
                        namespace=namespace,
                        value=target_value,
                        **subject,
                    )
                ),
                f"memory_search for {target_scope} must still return the colliding subject after retracting only {source_scope}.",
            )
            ensure(
                not _matching_search_results(
                    target_search_after_forget,
                    scope=target_scope,
                    namespace=namespace,
                    value=source_value,
                    **subject,
                ),
                f"memory_search in {target_scope} must continue to isolate the retracted {source_scope} subject.",
            )

    return "All six ordered scope pairs preserve isolation for identical namespace+subject across memory_get and memory_search, with same-scope positive controls."


def case_consolidation_optional_absence(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("consolidation-optional")
    status = ctx.status(scope="repository", namespace=namespace)
    ensure("consolidation" in status, "memory_status must report whether consolidation is available.")
    ensure(isinstance(status["consolidation"], dict), "memory_status consolidation must be an object.")
    if status["consolidation"].get("available"):
        return "Implementation advertises explicit consolidation support; supported-branch invariants run in the next cases."
    return "Implementation does not advertise consolidation support; correctness still remains available without consolidation."


def case_consolidation_non_destructive(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("consolidation-non-destructive")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="account",
        field="status",
        value="active",
        provenance=ctx.provenance("consolidation-fact"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="belief",
        topic="account",
        field="status",
        value="might churn",
        provenance=ctx.provenance("consolidation-belief"),
    )
    before = ctx.status(scope="repository", namespace=namespace)
    if not before["consolidation"].get("available"):
        return "Consolidation not advertised; destructive-branch assertions are not applicable for this run."
    before_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="account",
        field="status",
        memory_type="fact",
    )
    before_wal = before.get("wal_high_water_seq")
    ctx.run_consolidation(namespace=namespace)
    after = ctx.status(scope="repository", namespace=namespace)
    after_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="account",
        field="status",
        memory_type="fact",
    )
    belief = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="account",
        field="status",
        memory_type="belief",
    )
    ensure(before_get["record"].get("value") == after_get["record"].get("value") == "active", "Consolidation must not remove, evict, or hide the current value of a held fact.")
    ensure(after.get("wal_high_water_seq") == before_wal, "Consolidation must not mutate the append-only WAL.")
    ensure(after["consolidation"].get("last_run_at"), "Successful consolidation should update consolidation.last_run_at.")
    ensure(belief["record"].get("value") == "might churn", "Consolidation must not auto-promote belief to fact in v1.")
    return "Explicit consolidation preserves current facts, leaves the WAL untouched, and does not auto-promote belief entries."


def case_fresh_create_persistence(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("fresh-create")
    with ctx.restarted(schema_mode="fresh") as fresh_ctx:
        durable_data_dir = fresh_ctx.startup.data_dir
        status = fresh_ctx.status(scope="repository", namespace=namespace)
        ensure(status.get("wal_high_water_seq") in (0, None), "Fresh-create startup should begin with an empty or zeroed WAL high-water mark.")
        fresh_ctx.remember(
            scope="repository",
            namespace=namespace,
            memory_type="fact",
            topic="bootstrap",
            field="state",
            value="initialized",
            provenance=fresh_ctx.provenance("fresh-create"),
        )
    with ctx.restarted(schema_mode="fresh", data_dir=durable_data_dir) as restarted:
        persisted = restarted.get(
            scope="repository",
            namespace=namespace,
            topic="bootstrap",
            field="state",
            memory_type="fact",
        )
        ensure(persisted["record"].get("value") == "initialized", "State written after fresh-create should persist across restart.")
    return "Fresh database creation supports normal writes and preserves data across restart."


def case_upgrade_path_persistence(ctx: "ValidationHarness") -> str:
    with ctx.restarted(schema_mode="upgrade_from_v1_0_1", upgrade_fixture="v1_0_1_minimal") as upgraded:
        migrated = upgraded.get(
            scope="repository",
            namespace="upgrade-fixture",
            topic="migrated_profile",
            field="city",
            memory_type="fact",
        )
        ensure(migrated["record"].get("value") == "Berlin", "In-place upgrade should preserve previously stored governed facts.")
        upgraded.remember(
            scope="repository",
            namespace="upgrade-fixture",
            memory_type="fact",
            topic="migrated_profile",
            field="timezone",
            value="Europe/Berlin",
            provenance=upgraded.provenance("upgrade-write"),
        )
        post_write = upgraded.get(
            scope="repository",
            namespace="upgrade-fixture",
            topic="migrated_profile",
            field="timezone",
            memory_type="fact",
        )
        ensure(post_write["record"].get("value") == "Europe/Berlin", "Writes after upgrade should succeed against the migrated store.")
    return "In-place upgrade preserves prior state and supports new writes after startup migration."


def case_episode_append_only_history(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("episode-append")
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="conversation",
        value="Episode one: booked the Porto flight.",
        episode_id="ep_append_1",
        provenance=ctx.provenance("episode-append-1"),
    )
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="conversation",
        value="Episode two: rebooked after delay.",
        episode_id="ep_append_2",
        provenance=ctx.provenance("episode-append-2"),
    )
    payload = ctx.search(
        scope="session",
        namespace=namespace,
        query="Porto delay",
        include_episodes=True,
    )
    _assert_success_envelope(payload, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    episode_values = [result["value"] for result in payload.get("results", []) if result.get("memory_type") == "episode"]
    ensure("Episode one: booked the Porto flight." in episode_values, "Append-only episode history must retain the first episode entry.")
    ensure("Episode two: rebooked after delay." in episode_values, "Append-only episode history must retain the later episode entry.")
    return "Episode writes are preserved append-only and remain retrievable when episodes are explicitly requested."


def case_memory_get_depth_full_default(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("get-depth-full")
    long_value = "Z" * 320
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="depth_probe",
        field="payload",
        value=long_value,
        provenance=ctx.provenance("get-depth-full"),
    )
    default_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="depth_probe",
        field="payload",
        memory_type="fact",
    )
    explicit_full = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="depth_probe",
        field="payload",
        memory_type="fact",
        depth="full",
    )
    _assert_success_envelope(default_get, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    _assert_success_envelope(explicit_full, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    ensure(
        default_get["record"].get("value") == long_value,
        "Omitting depth must preserve the v1.2.0 full-value lookup behavior.",
    )
    ensure(
        explicit_full["record"].get("value") == long_value,
        "depth=full must return the complete serialized value.",
    )
    return "memory_get defaults to depth=full and preserves the prior full-value contract."


def case_memory_get_depth_summary_truncation(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("get-depth-summary")
    long_value = "Σ" * 300
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="depth_probe",
        field="payload",
        value=long_value,
        provenance=ctx.provenance("get-depth-summary"),
    )
    payload = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="depth_probe",
        field="payload",
        memory_type="fact",
        depth="summary",
    )
    _assert_success_envelope(payload, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    summary_value = str(payload["record"].get("value", ""))
    ensure(summary_value.endswith("..."), "depth=summary must append an ellipsis when truncation occurs.")
    prefix = summary_value[:-3]
    ensure(
        _unicode_scalars(prefix) <= 256,
        "depth=summary must truncate value to at most 256 Unicode scalars before the ellipsis.",
    )
    ensure(
        _unicode_scalars(long_value) > 256,
        "depth=summary truncation test requires an input longer than 256 Unicode scalars.",
    )
    return "memory_get depth=summary truncates long values to 256 Unicode scalars plus an ellipsis."


def case_memory_get_depth_summary_preserves_metadata(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("get-depth-summary-metadata")
    long_value = "M" * 400
    written = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="depth_probe",
        field="payload",
        value=long_value,
        provenance=ctx.provenance("get-depth-summary-metadata"),
    )
    full_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="depth_probe",
        field="payload",
        memory_type="fact",
        depth="full",
    )
    summary_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="depth_probe",
        field="payload",
        memory_type="fact",
        depth="summary",
    )
    full_record = full_get["record"]
    summary_record = summary_get["record"]
    for key in GOVERNED_RECORD_KEYS:
        if key == "value":
            continue
        ensure(
            summary_record.get(key) == full_record.get(key),
            f"depth=summary must not change governed metadata field '{key}'.",
        )
    ensure(summary_record["valid_from_seq"] == written["seq"], "depth=summary must preserve validity metadata.")
    ensure(summary_record["event_id"] == written["event_id"], "depth=summary must preserve provenance identity.")
    return "depth=summary changes only the serialized value truncation, not subject identity or validity metadata."


def case_memory_remember_expires_at_future_recall(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("expires-future")
    payload = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="temporary_grant",
        field="access",
        value="allowed",
        expires_at="2099-12-31T23:59:59Z",
        provenance=ctx.provenance("expires-future"),
    )
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="temporary_grant",
        field="access",
        memory_type="fact",
    )
    _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    ensure(fetched["record"].get("value") == "allowed", "A not-yet-expired governed record must remain in current-state recall.")
    ensure(
        fetched["record"].get("expires_at") == "2099-12-31T23:59:59Z",
        "memory_get must return expires_at metadata when present on the stored version.",
    )
    historical = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="temporary_grant",
        field="access",
        memory_type="fact",
        as_of=ctx.as_of_from_seq(payload["seq"]),
    )
    ensure(
        historical["record"].get("value") == "allowed",
        "as_of evaluation before expiry must still return the governed value.",
    )
    return "Future expires_at values remain visible in current recall and historical as_of evaluation before expiry."


def case_memory_remember_expires_at_past_excluded(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("expires-past")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="temporary_grant",
        field="access",
        value="expired",
        expires_at="2020-01-01T00:00:00Z",
        provenance=ctx.provenance("expires-past"),
    )
    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="temporary_grant",
            field="access",
            memory_type="fact",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("Expired governed records must be excluded from default current-state lookup.")

    search = ctx.search(scope="repository", namespace=namespace, query="expired")
    values = [item.get("value") for item in search.get("results", [])]
    ensure("expired" not in values, "Expired governed records must be excluded from default current-state search.")
    return "Governed records whose expires_at is before the evaluation commit time are excluded from current recall and search."


def case_memory_remember_blocks_actions_constraint(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("blocks-actions-constraint")
    blocked = ["deploy_prod", "delete_database"]
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="constraint",
        topic="release_policy",
        field="guardrail",
        value="Production deploys require approval.",
        blocks_actions=blocked,
        provenance=ctx.provenance("blocks-actions-constraint"),
    )
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="release_policy",
        field="guardrail",
        memory_type="constraint",
    )
    ensure(
        fetched["record"].get("blocks_actions") == blocked,
        "Constraint recall must return persisted blocks_actions metadata unchanged.",
    )
    search = ctx.search(scope="repository", namespace=namespace, query="approval")
    matches = [
        item
        for item in search.get("results", [])
        if item.get("topic") == "release_policy" and item.get("field") == "guardrail"
    ]
    ensure(matches, "Search should return the constraint record for positive control.")
    ensure(
        matches[0].get("blocks_actions") == blocked,
        "memory_search must return blocks_actions metadata on recall.",
    )
    return "blocks_actions metadata is persisted and returned on constraint recall."


def case_memory_remember_blocks_actions_action_boundary_fact(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("blocks-actions-fact")
    blocked = ["merge_without_review"]
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="action_boundary",
        field="protected_branch",
        value="main",
        blocks_actions=blocked,
        provenance=ctx.provenance("blocks-actions-fact"),
    )
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="action_boundary",
        field="protected_branch",
        memory_type="fact",
    )
    ensure(
        fetched["record"].get("blocks_actions") == blocked,
        "action_boundary facts must return persisted blocks_actions metadata unchanged.",
    )
    return "blocks_actions is valid for fact records with topic=action_boundary and is returned on recall."


def case_blocks_actions_does_not_block_unrelated_mcp(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("blocks-actions-no-enforcement")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="constraint",
        topic="release_policy",
        field="guardrail",
        value="Block deploy_prod",
        blocks_actions=["deploy_prod"],
        provenance=ctx.provenance("blocks-actions-no-enforcement-block"),
    )
    unrelated = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="ops_note",
        field="status",
        value="deploy_prod attempted",
        provenance=ctx.provenance("blocks-actions-no-enforcement-write"),
    )
    ensure(unrelated["integration_status"] == "integrated", "The service must not reject unrelated MCP writes based on blocks_actions metadata.")
    return "blocks_actions metadata is caller-side enforcement only; the service does not block unrelated MCP requests."


def case_memory_remember_episode_observation(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("episode-observation")
    observation = {
        "kind": "tool_failure",
        "tool_name": "Bash",
        "exit_code": 1,
        "paths": ["/workspace/components/memory-service/tests/run_validation.py"],
        "stderr_excerpt": "ModuleNotFoundError: No module named 'memory_service'",
    }
    payload = ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="tool_observation",
        field="failure",
        value={
            "kind": "tool_failure",
            "tool_name": "Bash",
            "exit_code": 1,
            "paths": observation["paths"],
            "stderr_excerpt": observation["stderr_excerpt"],
            "summary": "Validation import failed",
        },
        observation=observation,
        episode_id="ep_tool_failure_1",
        provenance=ctx.provenance("episode-observation"),
    )
    ensure(payload["integration_status"] == "integrated", "Episode writes with observation must integrate successfully.")
    search = ctx.search(
        scope="session",
        namespace=namespace,
        query="ModuleNotFoundError",
        include_episodes=True,
    )
    episode_matches = [
        item
        for item in search.get("results", [])
        if item.get("memory_type") == "episode" and item.get("topic") == "tool_observation"
    ]
    ensure(episode_matches, "Structured episode observations must remain retrievable when episodes are requested.")
    value = episode_matches[0].get("value")
    if isinstance(value, dict):
        ensure(value.get("kind") == "tool_failure", "Episode value should include the structured observation kind.")
        ensure(value.get("tool_name") == "Bash", "Episode value should include the structured observation tool_name.")
    return "Episode writes accept structured observation metadata and remain retrievable through episode recall."


def case_memory_consolidate_unavailable(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("consolidate-unavailable")
    status = ctx.status(scope="repository", namespace=namespace)
    if status["consolidation"].get("available"):
        return "Consolidation is advertised as available; unavailable-branch assertion is not applicable for this run."
    try:
        ctx.consolidate(scope="repository", namespace=namespace)
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_consolidate", "consolidation_unavailable")
        return "Unsupported consolidation returns consolidation_unavailable through the public MCP tool."
    raise ValidationFailure("memory_consolidate should return consolidation_unavailable when consolidation is unsupported.")


def case_memory_review_unavailable(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("review-unavailable")
    status = ctx.status(scope="repository", namespace=namespace)
    if status["consolidation"].get("available"):
        return "Review is expected when consolidation is available; unavailable-branch assertion is not applicable for this run."
    try:
        ctx.review(scope="repository", namespace=namespace, action="list")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_review", "review_unavailable")
        return "Unsupported review returns review_unavailable through the public MCP tool."
    raise ValidationFailure("memory_review should return review_unavailable when review is unsupported.")


def case_memory_consolidate_success_shape(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("consolidate-shape")
    status = ctx.status(scope="repository", namespace=namespace)
    if not status["consolidation"].get("available"):
        return "Consolidation not advertised; MCP consolidate shape assertions are not applicable for this run."
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="account",
        field="status",
        value="active",
        provenance=ctx.provenance("consolidate-shape-fact"),
    )
    payload = ctx.consolidate(scope="repository", namespace=namespace, dry_run=True)
    _assert_success_envelope(
        payload,
        "memory_consolidate",
        ["scope", "namespace", "available", "dry_run", "last_run_at", "stats", "promotions"],
    )
    ensure(payload["available"] is True, "Successful memory_consolidate must report available=true.")
    ensure(payload["dry_run"] is True, "memory_consolidate must echo the dry_run request flag.")
    require_keys(payload["stats"], ["units_before", "units_after", "bytes_before", "bytes_after"], "memory_consolidate stats")
    ensure(isinstance(payload["promotions"], list), "memory_consolidate must return a promotions array.")
    for promotion in payload["promotions"]:
        _assert_promotion_proposal(promotion, "memory_consolidate promotion")
    return "memory_consolidate returns the normative success envelope with stats and promotions."


def case_memory_consolidate_dry_run_no_mutation(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("consolidate-dry-run")
    status_before = ctx.status(scope="repository", namespace=namespace)
    if not status_before["consolidation"].get("available"):
        return "Consolidation not advertised; dry_run mutation checks are not applicable for this run."
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="belief",
        topic="noise",
        field="note",
        value="low-salience belief one",
        provenance=ctx.provenance("consolidate-dry-run-belief-1"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="belief",
        topic="noise",
        field="note",
        value="low-salience belief two",
        provenance=ctx.provenance("consolidate-dry-run-belief-2"),
    )
    wal_before = ctx.status(scope="repository", namespace=namespace).get("wal_high_water_seq")
    last_run_before = status_before["consolidation"].get("last_run_at")
    pending_before = status_before["review_queue"].get("pending_count", 0)
    ctx.consolidate(scope="repository", namespace=namespace, dry_run=True)
    status_after = ctx.status(scope="repository", namespace=namespace)
    ensure(
        status_after.get("wal_high_water_seq") == wal_before,
        "dry_run consolidation must not mutate the append-only WAL.",
    )
    ensure(
        status_after["consolidation"].get("last_run_at") == last_run_before,
        "dry_run consolidation must not advance last_run_at.",
    )
    ensure(
        status_after["review_queue"].get("pending_count", 0) == pending_before,
        "dry_run consolidation must not mutate review-queue state.",
    )
    return "memory_consolidate dry_run reports planned results without mutating derived state or review-queue state."


def case_memory_consolidate_preserves_held_facts(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("consolidate-held-facts")
    status = ctx.status(scope="repository", namespace=namespace)
    if not status["consolidation"].get("available"):
        return "Consolidation not advertised; held-fact preservation checks are not applicable for this run."
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="account",
        field="status",
        value="active",
        provenance=ctx.provenance("consolidate-held-fact"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="belief",
        topic="account",
        field="status",
        value="might churn",
        provenance=ctx.provenance("consolidate-held-belief"),
    )
    before_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="account",
        field="status",
        memory_type="fact",
    )
    before_wal = ctx.status(scope="repository", namespace=namespace).get("wal_high_water_seq")
    ctx.consolidate(scope="repository", namespace=namespace, dry_run=False)
    after = ctx.status(scope="repository", namespace=namespace)
    after_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="account",
        field="status",
        memory_type="fact",
    )
    belief = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="account",
        field="status",
        memory_type="belief",
    )
    ensure(
        before_get["record"].get("value") == after_get["record"].get("value") == "active",
        "Consolidation must not remove, evict, or hide the current value of a held fact.",
    )
    ensure(after.get("wal_high_water_seq") == before_wal, "Consolidation must not mutate the append-only WAL.")
    ensure(belief["record"].get("value") == "might churn", "Consolidation must not auto-promote belief to fact.")
    return "Explicit MCP consolidation preserves held facts, leaves the WAL untouched, and does not auto-promote beliefs."


def case_memory_review_list_accept_reject_flow(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("review-flow")
    status = ctx.status(scope="repository", namespace=namespace)
    if not status["consolidation"].get("available"):
        return "Review flow requires consolidation support; not applicable when consolidation is unavailable."

    _seed_review_flow_consolidation_corpus(ctx, namespace)
    _require_consolidation_promotions(ctx, namespace, min_count=REVIEW_FLOW_MIN_PROMOTIONS, dry_run=True)

    consolidate = ctx.consolidate(scope="repository", namespace=namespace, dry_run=False)
    ensure(isinstance(consolidate.get("promotions"), list), "Consolidation must return a promotions array.")
    ensure(
        len(consolidate["promotions"]) >= REVIEW_FLOW_MIN_PROMOTIONS,
        "Non-dry consolidation must produce at least "
        f"{REVIEW_FLOW_MIN_PROMOTIONS} promotion proposals; observed {len(consolidate['promotions'])}.",
    )

    listed = ctx.review(scope="repository", namespace=namespace, action="list")
    _assert_success_envelope(listed, "memory_review", ["action", "scope", "namespace", "pending"])
    ensure(listed["action"] == "list", "memory_review must echo action=list.")
    pending = listed.get("pending", [])
    ensure(
        len(pending) >= REVIEW_FLOW_MIN_PROMOTIONS,
        "memory_review list must return at least "
        f"{REVIEW_FLOW_MIN_PROMOTIONS} pending promotion proposals after consolidation.",
    )
    for proposal in pending:
        _assert_promotion_proposal(proposal, "memory_review pending promotion")

    status_with_pending = ctx.status(scope="repository", namespace=namespace)
    ensure(
        status_with_pending["review_queue"]["pending_count"] >= len(pending),
        "memory_status.review_queue.pending_count must reflect pending promotion proposals.",
    )

    for proposal in pending:
        _assert_proposed_subject_not_in_current_state(
            ctx,
            proposal,
            scope="repository",
            namespace=namespace,
        )

    accept_id = pending[0]["review_id"]
    accepted = ctx.review(
        scope="repository",
        namespace=namespace,
        action="accept",
        review_id=accept_id,
    )
    _assert_success_envelope(
        accepted,
        "memory_review",
        ["action", "review_id", "integration_status", "subject", "event_id", "seq"],
    )
    ensure(accepted["action"] == "accept", "memory_review accept must echo action=accept.")
    ensure(accepted["integration_status"] == "integrated", "Accepted promotions must integrate through the normal write path.")
    require_keys(accepted["subject"], ["scope", "namespace", "topic", "field", "memory_type"], "memory_review accept subject")
    accepted_get = ctx.get(
        scope=accepted["subject"]["scope"],
        namespace=accepted["subject"]["namespace"],
        topic=accepted["subject"]["topic"],
        field=accepted["subject"]["field"],
        memory_type=accepted["subject"]["memory_type"],
    )
    ensure(
        accepted_get["record"].get("value") == pending[0]["value"],
        "Accepted promotion must become governed current state retrievable through memory_get.",
    )

    reject_id = pending[1]["review_id"]
    rejected = ctx.review(
        scope="repository",
        namespace=namespace,
        action="reject",
        review_id=reject_id,
    )
    _assert_success_envelope(rejected, "memory_review", ["action", "review_id", "review_status"])
    ensure(rejected["action"] == "reject", "memory_review reject must echo action=reject.")
    ensure(rejected["review_status"] == "rejected", "Rejected promotions must report review_status=rejected.")

    try:
        ctx.get(**_proposal_lookup_kwargs(pending[1], scope="repository", namespace=namespace))
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("Rejecting a promotion must not create governed current state for the proposed subject.")

    relisted = ctx.review(scope="repository", namespace=namespace, action="list")
    remaining_ids = {item.get("review_id") for item in relisted.get("pending", [])}
    ensure(accept_id not in remaining_ids, "Accepted promotions must leave the pending review queue.")
    ensure(reject_id not in remaining_ids, "Rejected promotions must leave the pending review queue.")
    return (
        "Review flow lists pending promotions, asserts pre-accept not_found for proposed subjects, "
        "integrates accepts via the write path, and removes rejects without governed writes."
    )


def case_memory_review_invalid_request(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("review-invalid")
    status = ctx.status(scope="repository", namespace=namespace)
    if not status["consolidation"].get("available"):
        return "Review invalid-request checks require review support; not applicable when review is unavailable."

    try:
        ctx.review(scope="repository", namespace=namespace, action="accept")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_review", "invalid_request")
    else:
        raise ValidationFailure("memory_review accept without review_id must be rejected as invalid_request.")

    try:
        ctx.review(scope="repository", namespace=namespace, action="not_a_real_action")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_review", "invalid_request")
        return "memory_review rejects unknown actions and missing review_id values with invalid_request."
    raise ValidationFailure("memory_review accepted an unknown action.")


def case_memory_consolidate_invalid_request(ctx: "ValidationHarness") -> str:
    try:
        ctx.consolidate(namespace=ctx.namespace("consolidate-missing-scope"))
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_consolidate", "invalid_request")
        return "memory_consolidate rejects requests that omit required scope or namespace."
    raise ValidationFailure("memory_consolidate accepted a request without scope.")


def case_hermes_prefetch_sync_turn_and_handle_tool_call(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("hermes-prefetch")
    session_id = "validation-hermes-session"
    with _temporary_workspace("memory-service-hermes-") as workspace_root:
        data_dir = workspace_root / ".ai-memory" / "data"
        with ctx.restarted(schema_mode="fresh", data_dir=data_dir) as seeded:
            seeded.remember(
                scope="user",
                namespace=namespace,
                memory_type="fact",
                topic="profile",
                field="city",
                value="Berlin",
                provenance=seeded.provenance("hermes-prefetch-seed"),
            )
        with _adapter_bridge(
            ctx,
            env_name=HERMES_ADAPTER_COMMAND_ENV,
            label="Hermes",
            initialize_arguments={
                "workspace_root": str(workspace_root),
                "data_dir": str(data_dir),
                "namespace": namespace,
                "session_id": session_id,
                "recall_mode": "hybrid",
                "prefetch_limit": 5,
                "sync_turn_enabled": True,
            },
        ) as hermes:
            prefetch = hermes.call("prefetch", {"query": "Berlin", "session_id": session_id})
            prefetch_result = prefetch.get("result")
            ensure(isinstance(prefetch_result, str) and bool(prefetch_result.strip()), "Hermes prefetch should return a bounded text block in hybrid mode.")
            ensure("Berlin" in prefetch_result, "Hermes prefetch should return the governed result value.")
            ensure(
                "profile" in prefetch_result or "city" in prefetch_result,
                "Hermes prefetch should format governed subject keys alongside the recalled value.",
            )

            sync_turn = hermes.call(
                "sync_turn",
                {
                    "user": "Remember the Berlin office travel preference.",
                    "assistant": "Stored the Berlin preference for later recall.",
                    "session_id": session_id,
                },
            )
            elapsed_ms = sync_turn.get("elapsed_ms")
            ensure(isinstance(elapsed_ms, (int, float)), "Hermes sync_turn validation bridge should report elapsed_ms.")
            ensure(
                float(elapsed_ms) <= HERMES_SYNC_TURN_MAX_MS,
                f"Hermes sync_turn should be non-blocking on the calling thread (<= {HERMES_SYNC_TURN_MAX_MS:.0f} ms); observed {elapsed_ms} ms.",
            )

            sync_turn_query = "Berlin office travel preference"
            session_turn_record: dict[str, Any] | None = None
            persist_deadline = time.monotonic() + HERMES_SYNC_TURN_PERSIST_TIMEOUT_S
            while time.monotonic() < persist_deadline:
                get_payload = hermes.call(
                    "handle_tool_call",
                    {
                        "name": "memory_get",
                        "args": {
                            "scope": "session",
                            "topic": "session_turn",
                            "field": "turn",
                            "memory_type": "episode",
                        },
                    },
                )
                fetched = _decode_json_string_result(
                    get_payload.get("result"),
                    "Hermes handle_tool_call(memory_get) after sync_turn",
                )
                if fetched.get("ok") is True and isinstance(fetched.get("record"), dict):
                    session_turn_record = fetched["record"]
                    break
                time.sleep(HERMES_SYNC_TURN_PERSIST_INTERVAL_S)
            ensure(
                session_turn_record is not None,
                "Hermes sync_turn should persist the completed turn as a session-scoped session_turn episode.",
            )
            turn_value = session_turn_record.get("value")
            if isinstance(turn_value, dict):
                user_text = str(turn_value.get("user", ""))
                assistant_text = str(turn_value.get("assistant", ""))
            else:
                user_text = assistant_text = str(turn_value)
            ensure(
                sync_turn_query in user_text or "Stored the Berlin preference" in assistant_text,
                "Hermes sync_turn episode should reflect the synced user or assistant turn content.",
            )

            remember_payload = hermes.call(
                "handle_tool_call",
                {
                    "name": "memory_remember",
                    "args": {
                        "scope": "repository",
                        "namespace": namespace,
                        "memory_type": "fact",
                        "topic": "adapter_round_trip",
                        "field": "status",
                        "value": "round-tripped",
                        "provenance": ctx.provenance("hermes-tool-remember"),
                    },
                },
            )
            remember = _decode_json_string_result(
                remember_payload.get("result"),
                "Hermes handle_tool_call(memory_remember)",
            )
            _assert_success_envelope(
                remember,
                "memory_remember",
                ["subject", "event_id", "seq", "integration_status", "current_version"],
            )

            get_payload = hermes.call(
                "handle_tool_call",
                {
                    "name": "memory_get",
                    "args": {
                        "scope": "repository",
                        "namespace": namespace,
                        "topic": "adapter_round_trip",
                        "field": "status",
                        "memory_type": "fact",
                    },
                },
            )
            fetched = _decode_json_string_result(get_payload.get("result"), "Hermes handle_tool_call(memory_get)")
            _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
            ensure(
                fetched["record"].get("value") == "round-tripped",
                "Hermes handle_tool_call should round-trip memory_remember into memory_get against the governed store.",
            )
    return (
        "Hermes prefetch returned governed results, sync_turn stayed non-blocking and persisted the session_turn episode, "
        "and handle_tool_call round-tripped memory_remember into memory_get."
    )


def case_hermes_recall_mode_schemas(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("hermes-recall-mode")
    session_id = "validation-hermes-recall-mode"
    with _temporary_workspace("memory-service-hermes-tools-") as workspace_root:
        data_dir = workspace_root / ".ai-memory" / "data"
        with _adapter_bridge(
            ctx,
            env_name=HERMES_ADAPTER_COMMAND_ENV,
            label="Hermes",
            initialize_arguments={
                "workspace_root": str(workspace_root),
                "data_dir": str(data_dir),
                "namespace": namespace,
                "session_id": session_id,
                "recall_mode": "tools",
                "prefetch_limit": 5,
                "sync_turn_enabled": True,
            },
        ) as hermes_tools:
            tools_payload = hermes_tools.call("get_tool_schemas")
            tool_schemas = tools_payload.get("result")
            ensure(isinstance(tool_schemas, list), "Hermes get_tool_schemas should return an array in tools mode.")
            observed_names: set[str] = set()
            for index, schema in enumerate(tool_schemas):
                ensure(isinstance(schema, dict), "Hermes tool schemas should be JSON objects.")
                ensure(schema.get("type") == "function", "Hermes tool schemas should use the OpenAI function-calling JSON shape.")
                function = schema.get("function")
                ensure(isinstance(function, dict), f"Hermes tool schema {index} should include a function object.")
                require_keys(function, ["name", "description", "parameters"], f"Hermes tool schema {index} function")
                observed_names.add(str(function["name"]))
            expected_names = {"memory_search", "memory_get", "memory_remember", "memory_forget"}
            ensure(
                observed_names == expected_names,
                f"Hermes tools mode should expose exactly {sorted(expected_names)}, observed {sorted(observed_names)}.",
            )

        with _adapter_bridge(
            ctx,
            env_name=HERMES_ADAPTER_COMMAND_ENV,
            label="Hermes",
            initialize_arguments={
                "workspace_root": str(workspace_root),
                "data_dir": str(data_dir),
                "namespace": namespace,
                "session_id": session_id,
                "recall_mode": "context",
                "prefetch_limit": 5,
                "sync_turn_enabled": True,
            },
        ) as hermes_context:
            context_payload = hermes_context.call("get_tool_schemas")
            ensure(
                context_payload.get("result") == [],
                "Hermes context mode should expose no provider tool schemas.",
            )
    return "Hermes tools mode exposed the documented four tool schemas, while context mode exposed none."


def case_openclaw_search_and_get_within_workspace(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("openclaw-sandbox")
    with _temporary_workspace("memory-service-openclaw-") as workspace_root:
        data_dir = workspace_root / ".ai-memory" / "data"
        with _adapter_bridge(
            ctx,
            env_name=OPENCLAW_ADAPTER_COMMAND_ENV,
            label="OpenClaw",
            initialize_arguments={
                "workspace_root": str(workspace_root),
                "data_dir": str(data_dir),
                "namespace": namespace,
                "import_workspace_markdown": False,
            },
        ) as empty_openclaw:
            empty_search = empty_openclaw.call(
                "memory_search",
                {"query": "Berlin", "scope": "repository", "namespace": namespace, "limit": 5},
            )
            empty_result = empty_search.get("result")
            ensure(isinstance(empty_result, dict), "OpenClaw memory_search should return a JSON object.")
            ensure(empty_result.get("results") == [], "OpenClaw memory_search should return empty results on a fresh workspace.")

            empty_get = empty_openclaw.call("memory_get", {"path": "memory/profile/city.md"})
            empty_record = _extract_openclaw_record(empty_get.get("result"), "OpenClaw memory_get on an empty store")
            ensure(
                empty_record is None,
                "OpenClaw memory_get should succeed with an empty result on a fresh store, not a fabricated populated record.",
            )

        with ctx.restarted(schema_mode="fresh", data_dir=data_dir) as seeded:
            seeded.remember(
                scope="repository",
                namespace=namespace,
                memory_type="fact",
                topic="profile",
                field="city",
                value="Berlin",
                provenance=seeded.provenance("openclaw-populated-seed"),
            )

        with _adapter_bridge(
            ctx,
            env_name=OPENCLAW_ADAPTER_COMMAND_ENV,
            label="OpenClaw",
            initialize_arguments={
                "workspace_root": str(workspace_root),
                "data_dir": str(data_dir),
                "namespace": namespace,
                "import_workspace_markdown": False,
            },
        ) as populated_openclaw:
            populated_search = populated_openclaw.call(
                "memory_search",
                {"query": "Berlin", "scope": "repository", "namespace": namespace, "limit": 5},
            )
            populated_result = populated_search.get("result")
            ensure(isinstance(populated_result, dict), "OpenClaw memory_search should return a JSON object.")
            results = populated_result.get("results")
            ensure(isinstance(results, list) and bool(results), "OpenClaw memory_search should return populated results after seeding the governed store.")
            hit = results[0]
            ensure(isinstance(hit, dict), "OpenClaw memory_search results should be JSON objects.")
            _assert_openclaw_search_result_shape(hit, "OpenClaw memory_search result")
            ensure("Berlin" in hit.get("snippet", ""), "OpenClaw populated search should surface the governed value in its snippet.")
            _assert_openclaw_path_in_workspace_or_alias(str(hit["path"]), workspace_root, "OpenClaw memory_search result")

            populated_get = populated_openclaw.call("memory_get", {"path": "memory/profile/city.md"})
            record = _extract_openclaw_record(populated_get.get("result"), "OpenClaw memory_get on a populated store")
            ensure(record is not None, "OpenClaw memory_get should return a populated record for a stored workspace memory path.")
            _assert_openclaw_search_result_shape(record, "OpenClaw memory_get record")
            ensure("Berlin" in record.get("snippet", ""), "OpenClaw memory_get should surface the governed value in its snippet.")
            _assert_openclaw_path_in_workspace_or_alias(str(record["path"]), workspace_root, "OpenClaw memory_get record")
    return "OpenClaw memory_search and memory_get succeeded on empty and populated stores and stayed inside the configured workspace sandbox."


def case_openclaw_rejects_outside_workspace_path(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("openclaw-outside-path")
    with _temporary_workspace("memory-service-openclaw-outside-") as workspace_root:
        data_dir = workspace_root / ".ai-memory" / "data"
        outside_path = workspace_root.parent / "outside-memory.md"
        outside_path.write_text("outside", encoding="utf-8")
        with _adapter_bridge(
            ctx,
            env_name=OPENCLAW_ADAPTER_COMMAND_ENV,
            label="OpenClaw",
            initialize_arguments={
                "workspace_root": str(workspace_root),
                "data_dir": str(data_dir),
                "namespace": namespace,
                "import_workspace_markdown": False,
            },
        ) as openclaw:
            try:
                openclaw.call("memory_get", {"path": str(outside_path)})
            except ToolCallError as exc:
                expect_error_code(exc, "invalid_request", "OpenClaw memory_get outside workspace path")
                return "OpenClaw rejected a path outside the configured workspace with invalid_request."
    raise ValidationFailure("OpenClaw accepted a memory_get path outside the configured workspace sandbox.")


def case_adapter_restart_persistence(ctx: "ValidationHarness") -> str:
    hermes_namespace = ctx.namespace("hermes-restart")
    hermes_session_id = "validation-hermes-restart"
    with _temporary_workspace("memory-service-hermes-restart-") as hermes_workspace:
        hermes_data_dir = hermes_workspace / ".ai-memory" / "data"
        with _adapter_bridge(
            ctx,
            env_name=HERMES_ADAPTER_COMMAND_ENV,
            label="Hermes",
            initialize_arguments={
                "workspace_root": str(hermes_workspace),
                "data_dir": str(hermes_data_dir),
                "namespace": hermes_namespace,
                "session_id": hermes_session_id,
                "recall_mode": "tools",
                "prefetch_limit": 5,
                "sync_turn_enabled": True,
            },
        ) as hermes_first:
            remember_payload = hermes_first.call(
                "handle_tool_call",
                {
                    "name": "memory_remember",
                    "args": {
                        "scope": "repository",
                        "namespace": hermes_namespace,
                        "memory_type": "fact",
                        "topic": "restart_probe",
                        "field": "value",
                        "value": "persisted through hermes restart",
                        "provenance": ctx.provenance("hermes-restart-write"),
                    },
                },
            )
            remember = _decode_json_string_result(
                remember_payload.get("result"),
                "Hermes restart memory_remember",
            )
            _assert_success_envelope(
                remember,
                "memory_remember",
                ["subject", "event_id", "seq", "integration_status", "current_version"],
            )
        with _adapter_bridge(
            ctx,
            env_name=HERMES_ADAPTER_COMMAND_ENV,
            label="Hermes",
            initialize_arguments={
                "workspace_root": str(hermes_workspace),
                "data_dir": str(hermes_data_dir),
                "namespace": hermes_namespace,
                "session_id": hermes_session_id,
                "recall_mode": "tools",
                "prefetch_limit": 5,
                "sync_turn_enabled": True,
            },
        ) as hermes_second:
            get_payload = hermes_second.call(
                "handle_tool_call",
                {
                    "name": "memory_get",
                    "args": {
                        "scope": "repository",
                        "namespace": hermes_namespace,
                        "topic": "restart_probe",
                        "field": "value",
                        "memory_type": "fact",
                    },
                },
            )
            fetched = _decode_json_string_result(get_payload.get("result"), "Hermes restart memory_get")
            _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
            ensure(
                fetched["record"].get("value") == "persisted through hermes restart",
                "Hermes should preserve governed state when restarted with the same data_dir.",
            )

    openclaw_namespace = ctx.namespace("openclaw-restart")
    with _temporary_workspace("memory-service-openclaw-restart-") as openclaw_workspace:
        openclaw_data_dir = openclaw_workspace / ".ai-memory" / "data"
        with ctx.restarted(schema_mode="fresh", data_dir=openclaw_data_dir) as seeded:
            seeded.remember(
                scope="repository",
                namespace=openclaw_namespace,
                memory_type="fact",
                topic="profile",
                field="city",
                value="Lisbon",
                provenance=seeded.provenance("openclaw-restart-seed"),
            )
        with _adapter_bridge(
            ctx,
            env_name=OPENCLAW_ADAPTER_COMMAND_ENV,
            label="OpenClaw",
            initialize_arguments={
                "workspace_root": str(openclaw_workspace),
                "data_dir": str(openclaw_data_dir),
                "namespace": openclaw_namespace,
                "import_workspace_markdown": False,
            },
        ) as openclaw_first:
            first_record = _extract_openclaw_record(
                openclaw_first.call("memory_get", {"path": "memory/profile/city.md"}).get("result"),
                "OpenClaw restart first read",
            )
            ensure(first_record is not None, "OpenClaw should read persisted governed state before restart.")
            ensure("Lisbon" in first_record.get("snippet", ""), "OpenClaw should surface the seeded governed value before restart.")
        with _adapter_bridge(
            ctx,
            env_name=OPENCLAW_ADAPTER_COMMAND_ENV,
            label="OpenClaw",
            initialize_arguments={
                "workspace_root": str(openclaw_workspace),
                "data_dir": str(openclaw_data_dir),
                "namespace": openclaw_namespace,
                "import_workspace_markdown": False,
            },
        ) as openclaw_second:
            second_record = _extract_openclaw_record(
                openclaw_second.call("memory_get", {"path": "memory/profile/city.md"}).get("result"),
                "OpenClaw restart second read",
            )
            ensure(second_record is not None, "OpenClaw should read persisted governed state after restart.")
            ensure("Lisbon" in second_record.get("snippet", ""), "OpenClaw should preserve governed state across restart with the same data_dir.")
    return "Both Hermes and OpenClaw preserved recalled state when restarted with the same data_dir."


def case_memory_profile_empty_success(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-empty")
    payload = ctx.profile(scope="user", namespace=namespace)
    _assert_success_envelope(payload, "memory_profile", PROFILE_SUCCESS_KEYS)
    ensure(payload["scope"] == "user", "memory_profile must evaluate user scope.")
    ensure(payload["manifest"] == "", "Empty store must return an empty manifest string.")
    ensure(payload["sections"] == [], "Empty store must return empty sections.")
    ensure(payload["citations"] == [], "Empty store must return empty citations.")
    return "memory_profile returns empty success envelope when no eligible records exist."


def case_memory_profile_populated_sections_citations(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-populated")
    remember = ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="ui_prefs",
        field="theme",
        value="dark mode preferred",
        provenance=ctx.provenance("profile-populated"),
    )
    payload = ctx.profile(scope="user", namespace=namespace, depth="full")
    _assert_success_envelope(payload, "memory_profile", PROFILE_SUCCESS_KEYS)
    ensure(bool(payload["manifest"].strip()), "Populated profile should return a non-empty manifest.")
    ensure(bool(payload["sections"]), "Populated profile should return at least one section.")
    ensure(bool(payload["citations"]), "Populated profile should return citations.")
    for section in payload["sections"]:
        require_keys(section, ["heading", "text", "citations"], "memory_profile section")
        ensure(bool(section["text"].strip()), "Profile section text should be non-empty when records exist.")
    for citation in payload["citations"]:
        _assert_citation_shape(citation, "memory_profile citation")
    cited_seqs = {citation["seq"] for citation in payload["citations"]}
    ensure(remember["seq"] in cited_seqs, "Profile citations must include the seeded preference record.")
    return "memory_profile sections and top-level citations reference populated preference records."


def case_memory_profile_budget_tokens_truncation(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-budget")
    long_value = "alpha-" + ("beta " * 80)
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="profile_budget",
        field="notes",
        value=long_value,
        provenance=ctx.provenance("profile-budget"),
    )
    full_payload = ctx.profile(scope="user", namespace=namespace, depth="full", budget_tokens=2048)
    trimmed_payload = ctx.profile(scope="user", namespace=namespace, depth="full", budget_tokens=64)
    _assert_success_envelope(full_payload, "memory_profile", PROFILE_SUCCESS_KEYS)
    _assert_success_envelope(trimmed_payload, "memory_profile", PROFILE_SUCCESS_KEYS)
    ensure(
        _unicode_scalars(trimmed_payload["manifest"]) <= _unicode_scalars(full_payload["manifest"]),
        "A smaller budget_tokens must not produce a longer manifest than a larger budget.",
    )
    ensure(
        _unicode_scalars(trimmed_payload["manifest"]) <= trimmed_payload["budget_tokens"],
        "Trimmed manifest must respect the requested budget_tokens scalar bound.",
    )
    return "memory_profile deterministically truncates manifest assembly to budget_tokens."


def case_memory_profile_belief_ranks_below_preference(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-salience")
    pref_marker = "PREFERENCE-" + ("x" * 30)
    belief_marker = "BELIEF-" + ("y" * 20)
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="rank_probe",
        field="priority_pref",
        value=pref_marker,
        provenance=ctx.provenance("profile-salience-pref"),
    )
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="belief",
        topic="rank_probe",
        field="priority_belief",
        value=belief_marker,
        provenance={
            "source": "profile_engine",
            "tool": "memory_remember",
            "actor": "validation",
            "request_id": "profile-salience-belief",
        },
        derived_from=["ep_validation_rank_probe"],
    )
    full_payload = ctx.profile(scope="user", namespace=namespace, depth="full", budget_tokens=2048)
    _assert_success_envelope(full_payload, "memory_profile", PROFILE_SUCCESS_KEYS)
    full_manifest = full_payload["manifest"]
    ensure(pref_marker in full_manifest, "Full manifest must include the preference record.")
    ensure(belief_marker in full_manifest, "Full manifest must include the belief record.")

    pref_section_len = _unicode_scalars(f"preference: rank_probe/priority_pref: {pref_marker}")
    belief_section_len = _unicode_scalars(f"belief: rank_probe/priority_belief: {belief_marker}")
    trim_budget = pref_section_len + belief_section_len + 1
    payload = ctx.profile(scope="user", namespace=namespace, depth="full", budget_tokens=trim_budget)
    _assert_success_envelope(payload, "memory_profile", PROFILE_SUCCESS_KEYS)
    manifest = payload["manifest"]
    ensure(
        _unicode_scalars(manifest) <= trim_budget,
        "Trimmed manifest must respect the requested budget_tokens scalar bound.",
    )
    pref_pos = manifest.find("PREFERENCE")
    belief_pos = manifest.find("BELIEF")
    ensure(pref_pos >= 0, "Preference record must appear in the trimmed manifest.")
    ensure(belief_pos >= 0, "Belief record must appear in the trimmed manifest; omission is not acceptable.")
    ensure(pref_pos < belief_pos, "belief with default salience must rank below preference when trimming.")
    return "memory_profile ranks belief below preference when trimming to budget_tokens."


def case_memory_profile_invalid_scope(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-invalid-scope")
    try:
        ctx.profile(scope="repository", namespace=namespace)
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_profile", "invalid_request")
        return "memory_profile rejects non-user scope with invalid_request."
    raise ValidationFailure("memory_profile accepted a non-user scope.")


def case_memory_reflect_empty_query(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("reflect-empty-query")
    try:
        ctx.reflect(scope="repository", namespace=namespace, query="")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_reflect", "invalid_request")
        return "memory_reflect rejects an empty query with invalid_request."
    raise ValidationFailure("memory_reflect accepted an empty query.")


def case_memory_reflect_empty_synthesis(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("reflect-empty")
    payload = ctx.reflect(scope="repository", namespace=namespace, query="What do we know about deployment?")
    _assert_success_envelope(payload, "memory_reflect", REFLECT_SUCCESS_KEYS)
    ensure(payload["synthesis"] == "", "Empty evidence must return synthesis=''.")
    ensure(payload["citations"] == [], "Empty evidence must return citations=[].")
    ensure(payload["evidence_count"] == 0, "Empty evidence must report evidence_count=0.")
    return "memory_reflect returns empty synthesis success when no evidence matches."


def case_memory_reflect_citations_shape(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("reflect-citations")
    remember = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="deploy_policy",
        field="owner",
        value="Platform team owns production deploys.",
        provenance=ctx.provenance("reflect-citations"),
    )
    payload = ctx.reflect(scope="repository", namespace=namespace, query="Who owns production deploys?")
    _assert_success_envelope(payload, "memory_reflect", REFLECT_SUCCESS_KEYS)
    ensure(payload["evidence_count"] >= 1, "memory_reflect should consider seeded evidence.")
    ensure(bool(payload["citations"]), "memory_reflect must return citations for used evidence.")
    for citation in payload["citations"]:
        _assert_citation_shape(citation, "memory_reflect citation")
        require_keys(citation, ["topic", "field", "memory_type", "seq"], "memory_reflect citation")
    cited_seqs = {citation["seq"] for citation in payload["citations"]}
    ensure(remember["seq"] in cited_seqs, "memory_reflect citations must include the seeded governed record.")
    return "memory_reflect returns citations with topic, field, memory_type, and seq for cited evidence."


def case_memory_reflect_namespace_isolation(ctx: "ValidationHarness") -> str:
    primary = ctx.namespace("reflect-ns-primary")
    other = ctx.namespace("reflect-ns-other")
    ctx.remember(
        scope="repository",
        namespace=primary,
        memory_type="fact",
        topic="isolation_probe",
        field="token",
        value="PRIMARY-NAMESPACE-VALUE",
        provenance=ctx.provenance("reflect-ns-primary"),
    )
    ctx.remember(
        scope="repository",
        namespace=other,
        memory_type="fact",
        topic="isolation_probe",
        field="token",
        value="OTHER-NAMESPACE-VALUE",
        provenance=ctx.provenance("reflect-ns-other"),
    )
    payload = ctx.reflect(
        scope="repository",
        namespace=primary,
        query="What is the isolation probe token?",
    )
    _assert_success_envelope(payload, "memory_reflect", REFLECT_SUCCESS_KEYS)
    for citation in payload.get("citations", []):
        ensure(citation.get("namespace") == primary, "memory_reflect must not cite records outside the requested namespace.")
    if payload.get("synthesis"):
        ensure("OTHER-NAMESPACE-VALUE" not in payload["synthesis"], "Synthesis must not leak evidence from another namespace.")
    return "memory_reflect citations are limited to the requested namespace."


def case_memory_reflect_no_silent_writes(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("reflect-readonly")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="readonly_probe",
        field="status",
        value="stable",
        provenance=ctx.provenance("reflect-readonly-seed"),
    )
    before = ctx.status(scope="repository", namespace=namespace)
    ctx.reflect(scope="repository", namespace=namespace, query="What is the readonly probe status?")
    after = ctx.status(scope="repository", namespace=namespace)
    ensure(
        before.get("wal_high_water_seq") == after.get("wal_high_water_seq"),
        "memory_reflect must not append WAL events or perform silent writes.",
    )
    return "memory_reflect is read-only and does not advance WAL high-water sequence."


def case_persona_id_isolation_default_context(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("persona-default")
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="persona_probe",
        field="secret",
        value="persona-A-only",
        persona_id="A",
        provenance=ctx.provenance("persona-a-write"),
    )
    try:
        ctx.get(
            scope="user",
            namespace=namespace,
            topic="persona_probe",
            field="secret",
            memory_type="preference",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("Default persona context must not see persona-tagged records.")

    search_payload = ctx.search(scope="user", namespace=namespace, query="persona-A-only")
    matches = _matching_search_results(
        search_payload,
        scope="user",
        namespace=namespace,
        topic="persona_probe",
        field="secret",
        memory_type="preference",
        value="persona-A-only",
    )
    ensure(not matches, "Default persona search must not return persona-tagged records.")
    return "Records tagged persona_id=A are invisible in the default persona context."


def case_persona_id_isolation_named_persona(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("persona-named")
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="persona_named_probe",
        field="secret",
        value="persona-A-named",
        persona_id="A",
        provenance=ctx.provenance("persona-named-a"),
    )
    try:
        ctx.get(
            scope="user",
            namespace=namespace,
            topic="persona_named_probe",
            field="secret",
            memory_type="preference",
            persona_id="B",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("persona_id=B must not read records tagged persona_id=A without share_to.")

    visible = ctx.get(
        scope="user",
        namespace=namespace,
        topic="persona_named_probe",
        field="secret",
        memory_type="preference",
        persona_id="A",
    )
    ensure(visible["record"].get("value") == "persona-A-named", "persona_id=A should read its tagged record.")

    blocked_search = ctx.search(
        scope="user",
        namespace=namespace,
        query="persona-A-named",
        persona_id="B",
    )
    blocked_matches = _matching_search_results(
        blocked_search,
        scope="user",
        namespace=namespace,
        topic="persona_named_probe",
        field="secret",
        memory_type="preference",
        value="persona-A-named",
    )
    ensure(not blocked_matches, "persona_id=B search must not return persona-A records without share_to.")

    allowed_search = ctx.search(
        scope="user",
        namespace=namespace,
        query="persona-A-named",
        persona_id="A",
    )
    allowed_matches = _matching_search_results(
        allowed_search,
        scope="user",
        namespace=namespace,
        topic="persona_named_probe",
        field="secret",
        memory_type="preference",
        value="persona-A-named",
    )
    ensure(allowed_matches, "persona_id=A search must return its tagged record.")
    return "Named persona B cannot read persona A records unless explicitly shared."


def case_share_to_visibility(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("persona-share")
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="unshared_probe",
        field="note",
        value="not-shared-with-B",
        persona_id="A",
        provenance=ctx.provenance("persona-unshared-write"),
    )
    try:
        ctx.get(
            scope="user",
            namespace=namespace,
            topic="unshared_probe",
            field="note",
            memory_type="preference",
            persona_id="B",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("share_to test requires persona isolation: B must not read unshared persona-A records.")

    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="shared_probe",
        field="note",
        value="shared-with-B",
        persona_id="A",
        share_to=["B"],
        provenance=ctx.provenance("persona-share-write"),
    )
    record = ctx.get(
        scope="user",
        namespace=namespace,
        topic="shared_probe",
        field="note",
        memory_type="preference",
        persona_id="B",
    )
    ensure(record["record"].get("value") == "shared-with-B", "share_to must grant read visibility to listed personas.")

    shared_search = ctx.search(
        scope="user",
        namespace=namespace,
        query="shared-with-B",
        persona_id="B",
    )
    shared_matches = _matching_search_results(
        shared_search,
        scope="user",
        namespace=namespace,
        topic="shared_probe",
        field="note",
        memory_type="preference",
        value="shared-with-B",
    )
    ensure(shared_matches, "share_to must make shared records visible through memory_search for persona B.")

    unshared_search = ctx.search(
        scope="user",
        namespace=namespace,
        query="not-shared-with-B",
        persona_id="B",
    )
    unshared_matches = _matching_search_results(
        unshared_search,
        scope="user",
        namespace=namespace,
        topic="unshared_probe",
        field="note",
        memory_type="preference",
        value="not-shared-with-B",
    )
    ensure(not unshared_matches, "persona B search must not return unshared persona-A records.")
    return "Records with share_to=['B'] are visible to persona_id=B reads and search."


def case_context_fencing_post_conditions(ctx: "ValidationHarness") -> str:
    injection_id = "inj-validation-001"
    user_fragment = "User asked about deployment safety."
    assistant_fragment = "Assistant confirmed the rollout plan."
    injection_block = (
        f"<!-- ai-memory:begin injection_id={injection_id} -->\n"
        "Previously injected recall block that must not be captured.\n"
        f"<!-- ai-memory:end injection_id={injection_id} -->"
    )
    transcript = f"{user_fragment}\n{injection_block}\n{assistant_fragment}"
    known_ids = [injection_id]
    fenced_once = ctx.apply_context_fencing(transcript, known_ids)
    _assert_context_fencing_post_conditions(
        fenced_once,
        known_injection_ids=known_ids,
        preserved_fragments=[user_fragment, assistant_fragment],
        context="apply_context_fencing first pass",
    )
    fenced_twice = ctx.apply_context_fencing(fenced_once, known_ids)
    ensure(fenced_twice == fenced_once, "apply_context_fencing must be idempotent.")

    unknown_id = "inj-validation-unknown"
    unknown_block = (
        f"<!-- ai-memory:begin injection_id={unknown_id} -->\n"
        "Unknown injection block that must still be stripped.\n"
        f"<!-- ai-memory:end injection_id={unknown_id} -->"
    )
    unknown_transcript = f"{user_fragment}\n{unknown_block}\n{assistant_fragment}"
    fenced_unknown = ctx.apply_context_fencing(unknown_transcript, known_ids)
    ensure(
        _injection_marker_pattern(unknown_id) not in fenced_unknown,
        "Context fencing must remove blocks with unknown injection_id when marker prefix matches.",
    )
    _assert_context_fencing_post_conditions(
        fenced_unknown,
        known_injection_ids=known_ids,
        preserved_fragments=[user_fragment, assistant_fragment],
        context="apply_context_fencing unknown injection_id",
    )
    return "Context fencing strips known and unknown injection blocks, preserves turn content, and is idempotent."


def _records_with_profile_engine_provenance(
    ctx: "ValidationHarness",
    namespace: str,
    *,
    scope: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for memory_type in ("preference", "belief", "fact", "procedure", "constraint"):
        payload = ctx.search(
            scope=scope,
            namespace=namespace,
            query="profile_engine",
            memory_types=[memory_type],
        )
        for result in payload.get("results", []):
            provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
            if provenance.get("source") == "profile_engine":
                matches.append(result)
    return matches


def case_profile_engine_disabled_no_extraction(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-engine-disabled")
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="session_turn",
        field="turn",
        value="User prefers concise answers.",
        episode_id="ep_disabled_probe",
        provenance=ctx.provenance("profile-engine-episode"),
    )
    try:
        ctx.run_profile_engine(scope="session", namespace=namespace)
    except ToolCallError as exc:
        expect_error_code(exc, "extraction_disabled", "run_profile_engine when disabled")
    session_matches = _records_with_profile_engine_provenance(ctx, namespace, scope="session")
    user_matches = _records_with_profile_engine_provenance(ctx, namespace, scope="user")
    ensure(
        not session_matches and not user_matches,
        "Disabled profile engine must not create provenance.source=profile_engine records in session or user scope.",
    )
    return "Profile engine extraction disabled leaves the namespace free of profile_engine provenance writes."


def case_profile_engine_enabled_belief_derived_from(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-engine-enabled")
    ctx.set_profile_engine_enabled(namespace, True)
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="session_turn",
        field="turn",
        value="User said they prefer dark themes and short answers.",
        episode_id="ep_enabled_probe",
        provenance=ctx.provenance("profile-engine-enabled-episode"),
    )
    ctx.run_profile_engine(scope="session", namespace=namespace)
    belief_record: dict[str, Any] | None = None
    deadline = time.monotonic() + PROFILE_ENGINE_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        for memory_type in ("belief",):
            payload = ctx.search(
                scope="user",
                namespace=namespace,
                query="profile_engine",
                memory_types=[memory_type],
            )
            for result in payload.get("results", []):
                provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
                if provenance.get("source") == "profile_engine" and result.get("memory_type") == "belief":
                    belief_record = result
                    break
            if belief_record is not None:
                break
        if belief_record is not None:
            break
        time.sleep(PROFILE_ENGINE_POLL_INTERVAL_S)
    ensure(belief_record is not None, "Enabled profile engine should create at least one belief record.")
    derived_from = belief_record.get("derived_from")
    ensure(isinstance(derived_from, list) and bool(derived_from), "Profile-engine belief must include derived_from[].")
    provenance = belief_record.get("provenance") if isinstance(belief_record.get("provenance"), dict) else {}
    ensure(provenance.get("source") == "profile_engine", "Profile-engine belief must carry provenance.source=profile_engine.")
    return "Enabled profile engine creates belief records with derived_from provenance."


def case_session_summary_bounded(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("session-summary")
    oversized = "S" * (SESSION_SUMMARY_MAX_SCALARS + 500)
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="procedure",
        topic="session_summary",
        field="body",
        value=oversized,
        provenance={
            "source": "session_summary",
            "tool": "memory_remember",
            "actor": "validation",
            "request_id": "session-summary-explicit",
        },
    )
    explicit = ctx.get(
        scope="session",
        namespace=namespace,
        topic="session_summary",
        field="body",
        memory_type="procedure",
    )
    explicit_record = explicit["record"]
    explicit_value = str(explicit_record.get("value", ""))
    explicit_seq = int(explicit_record.get("seq", 0))
    ensure(
        _unicode_scalars(explicit_value) <= SESSION_SUMMARY_MAX_SCALARS,
        f"Explicit session_summary write must be bounded to {SESSION_SUMMARY_MAX_SCALARS} Unicode scalars.",
    )
    ensure(
        explicit_value.startswith("..."),
        "Oversized explicit session_summary writes must use an ellipsis prefix when truncated.",
    )

    ctx.trigger_session_summary(
        scope="session",
        namespace=namespace,
        messages=[
            {"role": "user", "content": "Continue the rollout discussion."},
            {"role": "assistant", "content": "Captured the next rollout steps."},
        ],
    )
    triggered: dict[str, Any] | None = None
    deadline = time.monotonic() + PROFILE_ENGINE_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            candidate = ctx.get(
                scope="session",
                namespace=namespace,
                topic="session_summary",
                field="body",
                memory_type="procedure",
            )
        except ToolCallError:
            time.sleep(PROFILE_ENGINE_POLL_INTERVAL_S)
            continue
        candidate_record = candidate["record"]
        candidate_value = str(candidate_record.get("value", ""))
        candidate_seq = int(candidate_record.get("seq", 0))
        if candidate_value != explicit_value or candidate_seq != explicit_seq:
            triggered = candidate
            break
        time.sleep(PROFILE_ENGINE_POLL_INTERVAL_S)
    ensure(
        triggered is not None,
        "Session summary trigger must update topic=session_summary (seq or content change), not rely on pre-seeded remember.",
    )
    triggered_value = str(triggered["record"].get("value", ""))
    ensure(
        _unicode_scalars(triggered_value) <= SESSION_SUMMARY_MAX_SCALARS,
        f"Triggered session_summary must be bounded to {SESSION_SUMMARY_MAX_SCALARS} Unicode scalars.",
    )
    return "Session summary subject is bounded to 4096 Unicode scalars for explicit and triggered updates."


def case_memory_profile_persona_exclusion(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-persona")
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="persona_profile_probe",
        field="theme",
        value="persona-A-theme-only",
        persona_id="A",
        provenance=ctx.provenance("profile-persona-a"),
    )
    ctx.remember(
        scope="user",
        namespace=namespace,
        memory_type="preference",
        topic="default_profile_probe",
        field="editor",
        value="default-context-editor",
        provenance=ctx.provenance("profile-default"),
    )
    default_profile = ctx.profile(scope="user", namespace=namespace, depth="full")
    _assert_success_envelope(default_profile, "memory_profile", PROFILE_SUCCESS_KEYS)
    default_manifest = default_profile["manifest"]
    ensure("default-context-editor" in default_manifest, "Default memory_profile must include untagged records.")
    ensure(
        "persona-A-theme-only" not in default_manifest,
        "Default memory_profile manifest must exclude persona-tagged records.",
    )
    for section in default_profile.get("sections", []):
        ensure(
            "persona-A-theme-only" not in str(section.get("text", "")),
            "Default memory_profile sections must exclude persona-tagged records.",
        )

    persona_b_profile = ctx.profile(scope="user", namespace=namespace, depth="full", persona_id="B")
    _assert_success_envelope(persona_b_profile, "memory_profile", PROFILE_SUCCESS_KEYS)
    ensure(
        "persona-A-theme-only" not in persona_b_profile["manifest"],
        "persona_id=B memory_profile must not include persona-A records without share_to.",
    )
    return "memory_profile assembly excludes persona-tagged records outside the active persona context."


def case_memory_status_operator_metadata_updates(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("status-operator")
    before_user = ctx.status(scope="user", namespace=namespace)
    before_session = ctx.status(scope="session", namespace=namespace)
    require_keys(before_user["profile_engine"], ["enabled", "last_run_at"], "memory_status profile_engine before trigger")
    require_keys(before_session["session_summary"], ["topic", "last_updated_seq"], "memory_status session_summary before trigger")
    before_profile_run = before_user["profile_engine"].get("last_run_at")
    before_summary_seq = before_session["session_summary"].get("last_updated_seq")

    ctx.set_profile_engine_enabled(namespace, True)
    ctx.remember(
        scope="session",
        namespace=namespace,
        memory_type="episode",
        topic="session_turn",
        field="turn",
        value="preference:status_probe = concise answers",
        episode_id="ep_status_probe",
        provenance=ctx.provenance("status-profile-episode"),
    )
    ctx.run_profile_engine(scope="session", namespace=namespace)

    profile_engine_updated = False
    deadline = time.monotonic() + PROFILE_ENGINE_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        status = ctx.status(scope="user", namespace=namespace)
        last_run_at = status["profile_engine"].get("last_run_at")
        if last_run_at and last_run_at != before_profile_run:
            profile_engine_updated = True
            break
        time.sleep(PROFILE_ENGINE_POLL_INTERVAL_S)
    ensure(profile_engine_updated, "run_profile_engine must update memory_status.profile_engine.last_run_at.")

    ctx.trigger_session_summary(
        scope="session",
        namespace=namespace,
        messages=[{"role": "user", "content": "Status probe session summary update."}],
    )
    summary_updated = False
    deadline = time.monotonic() + PROFILE_ENGINE_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        status = ctx.status(scope="session", namespace=namespace)
        last_updated_seq = status["session_summary"].get("last_updated_seq")
        if last_updated_seq is not None and last_updated_seq != before_summary_seq:
            summary_updated = True
            break
        time.sleep(PROFILE_ENGINE_POLL_INTERVAL_S)
    ensure(summary_updated, "trigger_session_summary must update memory_status.session_summary.last_updated_seq.")
    return "memory_status profile_engine and session_summary operator metadata update after triggers."


def case_backward_compatibility_seven_tools(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("backward-compat")
    remember = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="compat_probe",
        field="status",
        value="v1.4.1-compatible",
        provenance=ctx.provenance("backward-compat-remember"),
    )
    search = ctx.search(scope="repository", namespace=namespace, query="compatible")
    _assert_success_envelope(search, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="compat_probe",
        field="status",
        memory_type="fact",
    )
    _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    ensure(fetched["record"].get("value") == "v1.4.1-compatible", "memory_get should return the remembered value.")
    status = ctx.status(scope="repository", namespace=namespace)
    _assert_success_envelope(
        status,
        "memory_status",
        ["scope", "namespace", "supported_scopes", "wal_high_water_seq", "semantic_units_in_sync", "index_status"],
    )
    try:
        ctx.consolidate(scope="repository", namespace=namespace, dry_run=True)
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_consolidate", "consolidation_unavailable")
    else:
        ensure(True, "consolidate dry_run accepted when supported.")
    try:
        ctx.review(scope="repository", namespace=namespace, action="list")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_review", "review_unavailable")
    else:
        ensure(True, "review list accepted when supported.")
    ctx.forget(
        scope="repository",
        namespace=namespace,
        topic="compat_probe",
        field="status",
        memory_type="fact",
        provenance=ctx.provenance("backward-compat-forget"),
    )
    ensure(remember["seq"] >= 1, "Original seven-tool remember path should remain functional.")
    return "Original seven MCP tools accept v1.4.1-shaped requests without Phase 4 fields."


def case_profile_unavailable_error(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("profile-unavailable")
    ctx.set_fault("profile_unavailable", True)
    try:
        ctx.profile(scope="user", namespace=namespace)
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_profile", "profile_unavailable")
    else:
        raise ValidationFailure("memory_profile should return profile_unavailable when the fault is armed.")
    finally:
        ctx.set_fault("profile_unavailable", False)
    return "memory_profile surfaces profile_unavailable through the standard error envelope."


def case_reflection_unavailable_error(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("reflection-unavailable")
    ctx.set_fault("reflection_unavailable", True)
    try:
        ctx.reflect(scope="repository", namespace=namespace, query="Summarize governed evidence.")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_reflect", "reflection_unavailable")
    else:
        raise ValidationFailure("memory_reflect should return reflection_unavailable when the fault is armed.")
    finally:
        ctx.set_fault("reflection_unavailable", False)
    return "memory_reflect surfaces reflection_unavailable through the standard error envelope."


def case_extraction_disabled_error(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("extraction-disabled")
    try:
        response = ctx.run_profile_engine(scope="session", namespace=namespace)
    except ToolCallError as exc:
        expect_error_code(exc, "extraction_disabled", "run_profile_engine when extraction disabled")
        return "Explicit profile-engine trigger returns extraction_disabled when extraction is disabled."
    code = ""
    if isinstance(response, dict):
        code = str(response.get("code") or (response.get("details") or {}).get("code") or "")
    ensure(code == "extraction_disabled", "run_profile_engine must surface extraction_disabled when extraction is disabled.")
    return "Explicit profile-engine trigger returns extraction_disabled when extraction is disabled."


def case_memory_status_phase5_operator_metadata_local_default(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("status-phase5")
    payload = ctx.status(scope="repository", namespace=namespace)
    _assert_success_envelope(
        payload,
        "memory_status",
        [
            "scope",
            "namespace",
            "supported_scopes",
            "wal_high_water_seq",
            "semantic_units_in_sync",
            "index_status",
            "consolidation",
            "review_queue",
            "profile_engine",
            "session_summary",
            "pii_scan",
            "retention",
            "fleet",
        ],
    )
    _assert_phase5_status_metadata(payload, "memory_status")
    ensure(payload["fleet"]["mode"] == "local", "Phase 5 default fleet mode must be local.")
    ensure(isinstance(payload["pii_scan"]["enabled"], bool), "memory_status pii_scan.enabled must be boolean.")
    ensure(
        payload["pii_scan"]["policy"] in {"redact", "block", "annotate"},
        "memory_status pii_scan.policy must expose the effective namespace policy.",
    )
    ensure(isinstance(payload["retention"]["legal_hold_count"], int), "memory_status retention.legal_hold_count must be an integer.")
    ensure(isinstance(payload["fleet"]["backend_reachable"], bool), "memory_status fleet.backend_reachable must be boolean.")
    return "memory_status exposes Phase 5 pii_scan, retention, and fleet metadata with local-first default mode."


def case_graph_derivation_subject_projection_and_depends_on(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("graph-derivation")
    ctx.set_graph_enabled(namespace, True)
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="London",
        provenance=ctx.provenance("graph-derivation-parent-first"),
    )
    dependent = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_summary",
        field="summary",
        value="Traveler summary bound to London home city.",
        extends=[{"topic": "travel_profile", "field": "home_city"}],
        provenance=ctx.provenance("graph-derivation-dependent"),
    )
    parent_update = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="Berlin",
        provenance=ctx.provenance("graph-derivation-parent-second"),
    )
    audit_points = [ctx.as_of_from_seq(dependent["seq"]), ctx.as_of_from_seq(parent_update["seq"])]
    payload = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        audit_points=audit_points,
        include_graph_edges=True,
    )
    _assert_success_envelope(payload, "memory_query_temporal", TEMPORAL_QUERY_SUCCESS_KEYS)
    trajectory = _find_trajectory(
        payload,
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        memory_type="fact",
    )
    points = trajectory.get("points")
    ensure(isinstance(points, list) and len(points) == 2, "Temporal graph validation should return one aligned point per audit point.")
    first_point = points[0]
    second_point = points[1]
    ensure(isinstance(first_point, dict) and isinstance(second_point, dict), "Temporal query points must be objects.")
    _assert_temporal_point_shape(first_point, "Temporal graph first point")
    _assert_temporal_point_shape(second_point, "Temporal graph second point")
    first_snapshot = first_point.get("graph_snapshot")
    second_snapshot = second_point.get("graph_snapshot")
    ensure(isinstance(first_snapshot, dict), "Graph-enabled temporal queries must return graph_snapshot at the first audit point.")
    ensure(isinstance(second_snapshot, dict), "Graph-enabled temporal queries must return graph_snapshot at the second audit point.")
    first_summary_entity = _find_graph_entity_by_label(first_snapshot, "travel_summary")
    first_profile_entity = _find_graph_entity_by_label(first_snapshot, "travel_profile")
    second_summary_entity = _find_graph_entity_by_label(second_snapshot, "travel_summary")
    second_profile_entity = _find_graph_entity_by_label(second_snapshot, "travel_profile")
    ensure(
        first_summary_entity["entity_id"] == second_summary_entity["entity_id"],
        "Subject projection should keep a stable entity_id for the same topic across audit points.",
    )
    ensure(
        first_profile_entity["entity_id"] == second_profile_entity["entity_id"],
        "Parent topic projection should keep a stable entity_id across audit points.",
    )
    second_edges = second_snapshot.get("edges")
    ensure(isinstance(second_edges, list), "graph_snapshot.edges must be an array.")
    depends_on_present = False
    for edge in second_edges:
        ensure(isinstance(edge, dict), "graph_snapshot edges must be objects.")
        _assert_graph_edge_shape(edge, "graph_snapshot edge")
        if (
            edge.get("edge_type") == "depends_on"
            and edge.get("from_entity_id") == second_summary_entity["entity_id"]
            and edge.get("to_entity_id") == second_profile_entity["entity_id"]
        ):
            depends_on_present = True
            break
    ensure(depends_on_present, "Direct extends projection must materialize a depends_on edge in the graph snapshot.")
    return "Ordinary memory_remember writes project stable topic entities and direct depends_on edges when graph support is enabled."


def case_graph_rebuild_preserves_single_subject_recall(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("graph-rebuild")
    ctx.set_graph_enabled(namespace, True)
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="London",
        provenance=ctx.provenance("graph-rebuild-parent-first"),
    )
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_summary",
        field="summary",
        value="Traveler summary bound to London home city.",
        extends=[{"topic": "travel_profile", "field": "home_city"}],
        provenance=ctx.provenance("graph-rebuild-dependent"),
    )
    parent_update = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="travel_profile",
        field="home_city",
        value="Berlin",
        provenance=ctx.provenance("graph-rebuild-parent-second"),
    )
    before_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        memory_type="fact",
        include_versions=True,
    )
    before_temporal = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        audit_points=[ctx.as_of_from_seq(parent_update["seq"])],
        include_graph_edges=True,
    )
    before_trajectory = _find_trajectory(
        before_temporal,
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        memory_type="fact",
    )
    before_points = before_trajectory.get("points")
    ensure(isinstance(before_points, list) and len(before_points) == 1, "Pre-rebuild temporal query should return one point for one audit point.")
    before_point = before_points[0]
    ensure(isinstance(before_point, dict), "Pre-rebuild temporal point must be an object.")
    before_snapshot = before_point.get("graph_snapshot")
    ensure(isinstance(before_snapshot, dict), "Pre-rebuild graph snapshot must be present when graph support is enabled.")
    before_signature = _graph_snapshot_signature(before_snapshot)
    before_versions = [
        (
            version.get("event_id"),
            version.get("seq"),
            version.get("valid_from_seq"),
            version.get("valid_to_seq"),
            version.get("value"),
        )
        for version in before_get.get("versions", [])
    ]

    ctx.rebuild_graph(namespace)

    after_get = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        memory_type="fact",
        include_versions=True,
    )
    after_temporal = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        audit_points=[ctx.as_of_from_seq(parent_update["seq"])],
        include_graph_edges=True,
    )
    after_trajectory = _find_trajectory(
        after_temporal,
        scope="repository",
        namespace=namespace,
        topic="travel_summary",
        field="summary",
        memory_type="fact",
    )
    after_points = after_trajectory.get("points")
    ensure(isinstance(after_points, list) and len(after_points) == 1, "Post-rebuild temporal query should return one point for one audit point.")
    after_point = after_points[0]
    ensure(isinstance(after_point, dict), "Post-rebuild temporal point must be an object.")
    after_snapshot = after_point.get("graph_snapshot")
    ensure(isinstance(after_snapshot, dict), "Post-rebuild graph snapshot must be present when graph support is enabled.")
    after_signature = _graph_snapshot_signature(after_snapshot)
    after_versions = [
        (
            version.get("event_id"),
            version.get("seq"),
            version.get("valid_from_seq"),
            version.get("valid_to_seq"),
            version.get("value"),
        )
        for version in after_get.get("versions", [])
    ]

    ensure(
        before_get["record"].get("value") == after_get["record"].get("value"),
        "Rebuilding the graph must not change single-subject current recall.",
    )
    ensure(before_versions == after_versions, "Rebuilding the graph must not rewrite the observable version history.")
    ensure(before_signature == after_signature, "Graph rebuild should reproduce the same entity and edge validity snapshot.")
    return "Graph rebuild leaves WAL-derived single-subject recall unchanged and reproduces the same observable graph snapshot."


def case_memory_query_temporal_matches_independent_memory_get(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("temporal-parity")
    first_tier = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="deployment_policy",
        field="tier",
        value="silver",
        provenance=ctx.provenance("temporal-parity-tier-first"),
    )
    first_region = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="deployment_policy",
        field="region",
        value="eu-west",
        provenance=ctx.provenance("temporal-parity-region"),
    )
    second_tier = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="deployment_policy",
        field="tier",
        value="gold",
        provenance=ctx.provenance("temporal-parity-tier-second"),
    )
    audit_points = [ctx.as_of_from_seq(first_region["seq"]), ctx.as_of_from_seq(second_tier["seq"])]
    payload = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="deployment_policy",
        memory_types=["fact"],
        audit_points=audit_points,
    )
    _assert_success_envelope(payload, "memory_query_temporal", TEMPORAL_QUERY_SUCCESS_KEYS)
    trajectories = payload.get("trajectories")
    ensure(isinstance(trajectories, list) and len(trajectories) >= 2, "Temporal query should return trajectories for both matching subject fields.")
    for field in ("tier", "region"):
        trajectory = _find_trajectory(
            payload,
            scope="repository",
            namespace=namespace,
            topic="deployment_policy",
            field=field,
            memory_type="fact",
        )
        subject = trajectory.get("subject")
        ensure(isinstance(subject, dict), "Temporal query trajectory subject must be an object.")
        require_keys(subject, TEMPORAL_SUBJECT_KEYS, "memory_query_temporal trajectory subject")
        points = trajectory.get("points")
        ensure(isinstance(points, list) and len(points) == len(audit_points), "Temporal query points must align with the supplied audit_points order.")
        for index, audit_point in enumerate(audit_points):
            point = points[index]
            ensure(isinstance(point, dict), "Temporal query points must be objects.")
            _assert_temporal_point_shape(point, "memory_query_temporal point")
            ensure(point["audit_point"] == audit_point, "Temporal query points must preserve the caller's audit_points order and encoding.")
            expected = ctx.get(
                scope="repository",
                namespace=namespace,
                topic="deployment_policy",
                field=field,
                memory_type="fact",
                as_of=audit_point,
            )
            expected_record = expected.get("record")
            ensure(isinstance(expected_record, dict), "Independent memory_get(as_of) should return a record for the seeded subject.")
            for key in ["value", "seq", "event_id", "valid_from_seq", "valid_to_seq", "recorded_at", "status"]:
                ensure(
                    point.get(key) == expected_record.get(key),
                    f"Temporal trajectory point for field '{field}' must match independent memory_get(as_of) on key '{key}'.",
                )
    ensure(first_tier["seq"] < second_tier["seq"], "Temporal parity setup should exercise a superseding sequence change.")
    return "memory_query_temporal returns point-by-point trajectories that match independent memory_get(as_of) for each subject in the topic partition."


def case_memory_query_temporal_empty_partition(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("temporal-empty")
    seed = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="unrelated_topic",
        field="status",
        value="seed",
        provenance=ctx.provenance("temporal-empty-seed"),
    )
    payload = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="missing_topic",
        audit_points=[ctx.as_of_from_seq(seed["seq"])],
    )
    _assert_success_envelope(payload, "memory_query_temporal", TEMPORAL_QUERY_SUCCESS_KEYS)
    ensure(payload.get("trajectories") == [], "Temporal query should return an empty trajectories array for an empty topic partition.")
    return "memory_query_temporal returns an empty success result when the requested topic partition has no subjects."


def case_memory_query_temporal_omits_retracted_subjects_by_default(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("temporal-retracted-hidden")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="policy_probe",
        field="status",
        value="temporary policy value",
        provenance=ctx.provenance("temporal-retracted-hidden-write"),
    )
    retract = ctx.forget(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="policy_probe",
        field="status",
        provenance=ctx.provenance("temporal-retracted-hidden-forget"),
    )
    payload = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="policy_probe",
        field="status",
        audit_points=[ctx.as_of_from_seq(retract["seq"])],
        include_retracted=False,
    )
    _assert_success_envelope(payload, "memory_query_temporal", TEMPORAL_QUERY_SUCCESS_KEYS)
    ensure(payload.get("trajectories") == [], "Temporal query should omit retracted-only subjects when include_retracted=false.")
    return "memory_query_temporal omits retracted-only subjects unless include_retracted is explicitly enabled."


def case_memory_query_temporal_surfaces_retracted_status_when_requested(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("temporal-retracted-visible")
    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="policy_probe",
        field="status",
        value="temporary policy value",
        provenance=ctx.provenance("temporal-retracted-visible-write"),
    )
    retract = ctx.forget(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="policy_probe",
        field="status",
        provenance=ctx.provenance("temporal-retracted-visible-forget"),
    )
    payload = ctx.query_temporal(
        scope="repository",
        namespace=namespace,
        topic="policy_probe",
        field="status",
        audit_points=[ctx.as_of_from_seq(retract["seq"])],
        include_retracted=True,
    )
    _assert_success_envelope(payload, "memory_query_temporal", TEMPORAL_QUERY_SUCCESS_KEYS)
    trajectory = _find_trajectory(
        payload,
        scope="repository",
        namespace=namespace,
        topic="policy_probe",
        field="status",
        memory_type="fact",
    )
    points = trajectory.get("points")
    ensure(isinstance(points, list) and len(points) == 1, "Retraction trajectory query should return one point for the requested audit point.")
    point = points[0]
    ensure(isinstance(point, dict), "Retraction trajectory point must be an object.")
    _assert_temporal_point_shape(point, "Retraction trajectory point")
    ensure(point["status"] == "retracted", "Temporal query should expose status='retracted' when include_retracted=true.")
    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="policy_probe",
            field="status",
            memory_type="fact",
            as_of=ctx.as_of_from_seq(retract["seq"]),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("Independent memory_get(as_of) at the retraction point should remain not_found for the retracted subject.")
    return "memory_query_temporal can surface a retracted trajectory point even when independent memory_get(as_of) remains not_found."


def _seed_temporal_query_subject(
    ctx: "ValidationHarness",
    namespace_suffix: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    namespace = ctx.namespace(namespace_suffix)
    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="temporal_invalid_probe",
        field="status",
        value="seed",
        provenance=ctx.provenance(namespace_suffix),
    )
    _assert_success_envelope(remembered, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    current_version = remembered.get("current_version")
    ensure(isinstance(current_version, dict), "Temporal invalid-request seed must include current_version metadata.")
    return namespace, remembered, current_version


def case_memory_query_temporal_invalid_empty_audit_points(ctx: "ValidationHarness") -> str:
    namespace, _, _ = _seed_temporal_query_subject(ctx, "temporal-invalid-empty-audit-points")
    try:
        ctx.query_temporal(
            scope="repository",
            namespace=namespace,
            topic="temporal_invalid_probe",
            audit_points=[],
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_query_temporal", "invalid_request")
        return "memory_query_temporal rejects an empty audit_points array with invalid_request."
    raise ValidationFailure("memory_query_temporal accepted an empty audit_points array.")


def case_memory_query_temporal_invalid_audit_point_both_selectors(ctx: "ValidationHarness") -> str:
    namespace, remembered, current_version = _seed_temporal_query_subject(ctx, "temporal-invalid-both-selectors")
    try:
        ctx.query_temporal(
            scope="repository",
            namespace=namespace,
            topic="temporal_invalid_probe",
            audit_points=[
                {
                    "seq": remembered["seq"],
                    "recorded_at": current_version["recorded_at"],
                }
            ],
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_query_temporal", "invalid_request")
        return "memory_query_temporal rejects audit points that provide both seq and recorded_at."
    raise ValidationFailure("memory_query_temporal accepted an audit point with both seq and recorded_at.")


def case_memory_query_temporal_invalid_audit_point_missing_selector(ctx: "ValidationHarness") -> str:
    namespace, _, _ = _seed_temporal_query_subject(ctx, "temporal-invalid-missing-selector")
    try:
        ctx.query_temporal(
            scope="repository",
            namespace=namespace,
            topic="temporal_invalid_probe",
            audit_points=[{}],
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_query_temporal", "invalid_request")
        return "memory_query_temporal rejects audit points that omit both seq and recorded_at."
    raise ValidationFailure("memory_query_temporal accepted an audit point with neither seq nor recorded_at.")


def case_memory_query_temporal_invalid_missing_topic(ctx: "ValidationHarness") -> str:
    namespace, remembered, _ = _seed_temporal_query_subject(ctx, "temporal-invalid-missing-topic")
    try:
        ctx.query_temporal(
            scope="repository",
            namespace=namespace,
            audit_points=[ctx.as_of_from_seq(remembered["seq"])],
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_query_temporal", "invalid_request")
        return "memory_query_temporal rejects requests that omit topic."
    raise ValidationFailure("memory_query_temporal accepted a request without topic.")


def case_pii_block_mode_rejects_enabled_fixture_and_disabled_mode_preserves_write(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("pii-block")
    fixture_value = "Contact alice@example.com for billing approvals."
    ctx.set_pii_scan(namespace, enabled=False, policy="block", categories=["email"])
    allowed = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="compliance_contact",
        field="email",
        value=fixture_value,
        provenance=ctx.provenance("pii-block-disabled"),
    )
    _assert_success_envelope(allowed, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])

    ctx.set_pii_scan(namespace, enabled=True, policy="block", categories=["email"])
    try:
        ctx.remember(
            scope="repository",
            namespace=namespace,
            memory_type="fact",
            topic="compliance_contact",
            field="backup_email",
            value=fixture_value,
            provenance=ctx.provenance("pii-block-enabled"),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_remember", "pii_blocked")
        return "Disabled PII scanning preserves the v1.5 write path, while enabled block mode rejects the deterministic email fixture."
    raise ValidationFailure("Enabled PII block mode should reject the deterministic regulated fixture with pii_blocked.")


def case_pii_redact_mode_persists_placeholder_value(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("pii-redact")
    raw_value = "Primary contact is alice@example.com."
    ctx.set_pii_scan(namespace, enabled=True, policy="redact", categories=["email"], placeholder="[REDACTED_PII]")
    remember = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="security_contact",
        field="email",
        value=raw_value,
        provenance=ctx.provenance("pii-redact"),
    )
    _assert_success_envelope(remember, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    current_version = remember.get("current_version")
    ensure(isinstance(current_version, dict), "Redacted remember response must include current_version.")
    ensure("[REDACTED_PII]" in str(current_version.get("value")), "Redacted remember response should expose the placeholder-substituted value.")
    ensure("alice@example.com" not in str(current_version.get("value")), "Redacted remember response must not leak the original email value.")
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="security_contact",
        field="email",
        memory_type="fact",
    )
    ensure("[REDACTED_PII]" in str(fetched["record"].get("value")), "Recalled value should reflect the redacted placeholder payload.")
    ensure("alice@example.com" not in str(fetched["record"].get("value")), "Recalled value must not expose the original regulated identifier.")
    return "PII redact mode stores and recalls the placeholder-substituted value rather than the original regulated text."


def case_audit_log_records_reads_writes_and_exports_jsonl(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("audit-export")
    ctx.set_audit_logging(namespace, enabled=True)
    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="audit_probe",
        field="status",
        value="logged write",
        provenance=ctx.provenance("audit-export-remember"),
    )
    _assert_success_envelope(remembered, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="audit_probe",
        field="status",
        memory_type="fact",
    )
    _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    exported = ctx.audit_export(scope="repository", namespace=namespace, format="jsonl")
    _assert_success_envelope(exported, "memory_audit_export", AUDIT_EXPORT_SUCCESS_KEYS)
    ensure(exported["format"] == "jsonl", "Audit export should report the effective jsonl format.")
    records = _parse_jsonl_records(exported.get("records"), "memory_audit_export records")
    ensure(exported["event_count"] == len(records), "memory_audit_export event_count must match the number of exported JSONL records.")
    remember_logged = False
    get_logged = False
    for record in records:
        require_keys(record, AUDIT_EVENT_KEYS, "memory_audit_export record")
        ensure(isinstance(record["actor"], dict), "Audit export actor must be an object.")
        if record.get("tool") == "memory_remember":
            remember_logged = remember_logged or record.get("event_kind") == "write"
            subject = record.get("subject")
            ensure(isinstance(subject, dict), "memory_remember audit records should include a subject object.")
            require_keys(subject, ["topic", "field", "memory_type"], "memory_remember audit subject")
        if record.get("tool") == "memory_get":
            get_logged = get_logged or record.get("event_kind") == "read"
            subject = record.get("subject")
            ensure(isinstance(subject, dict), "memory_get audit records should include a subject object.")
            require_keys(subject, ["topic", "field", "memory_type"], "memory_get audit subject")
    ensure(remember_logged, "Audit export should include a write record for memory_remember when audit logging is enabled.")
    ensure(get_logged, "Audit export should include a read record for memory_get when audit logging is enabled.")
    return "Enabled audit logging records read and write access and exports JSONL records with the required minimum keys."


def _seed_audit_export_history(
    ctx: "ValidationHarness",
    namespace_suffix: str,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    namespace = ctx.namespace(namespace_suffix)
    ctx.set_audit_logging(namespace, enabled=True)
    first = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="audit_bound_probe",
        field="status",
        value="initial",
        provenance=ctx.provenance(f"{namespace_suffix}-remember-first"),
    )
    second = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="audit_bound_probe",
        field="status",
        value="updated",
        provenance=ctx.provenance(f"{namespace_suffix}-remember-second"),
    )
    readback = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="audit_bound_probe",
        field="status",
        memory_type="fact",
    )
    other = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="audit_bound_probe_other",
        field="status",
        value="secondary",
        provenance=ctx.provenance(f"{namespace_suffix}-remember-third"),
    )
    ctx.forget(
        scope="repository",
        namespace=namespace,
        topic="audit_bound_probe",
        field="status",
        memory_type="fact",
        provenance=ctx.provenance(f"{namespace_suffix}-forget-first"),
    )
    ctx.forget(
        scope="repository",
        namespace=namespace,
        topic="audit_bound_probe_other",
        field="status",
        memory_type="fact",
        provenance=ctx.provenance(f"{namespace_suffix}-forget-second"),
    )
    first_current = first.get("current_version")
    second_current = second.get("current_version")
    ensure(isinstance(first_current, dict), "Audit export seed must expose current_version on the first write.")
    ensure(isinstance(second_current, dict), "Audit export seed must expose current_version on the second write.")
    _assert_success_envelope(readback, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    _assert_success_envelope(other, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    return namespace, first, second, first_current, second_current


def case_audit_export_invalid_since_bound(ctx: "ValidationHarness") -> str:
    namespace, first, _, _, first_current = _seed_audit_export_history(ctx, "audit-invalid-since")
    try:
        ctx.audit_export(
            scope="repository",
            namespace=namespace,
            since={"seq": first["seq"], "recorded_at": first_current["recorded_at"]},
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_audit_export", "invalid_request")
        return "memory_audit_export rejects malformed since bounds with invalid_request."
    raise ValidationFailure("memory_audit_export accepted a malformed since bound with both seq and recorded_at.")


def case_audit_export_invalid_until_bound(ctx: "ValidationHarness") -> str:
    namespace, _, _, _, _ = _seed_audit_export_history(ctx, "audit-invalid-until")
    try:
        ctx.audit_export(
            scope="repository",
            namespace=namespace,
            until={},
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_audit_export", "invalid_request")
        return "memory_audit_export rejects malformed until bounds with invalid_request."
    raise ValidationFailure("memory_audit_export accepted a malformed until bound with neither seq nor recorded_at.")


def case_audit_export_invalid_since_after_until(ctx: "ValidationHarness") -> str:
    namespace, first, second, _, _ = _seed_audit_export_history(ctx, "audit-invalid-order")
    ensure(first["seq"] < second["seq"], "Audit export invalid-order setup should produce increasing sequence numbers.")
    try:
        ctx.audit_export(
            scope="repository",
            namespace=namespace,
            since=ctx.as_of_from_seq(second["seq"]),
            until=ctx.as_of_from_seq(first["seq"]),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_audit_export", "invalid_request")
        return "memory_audit_export rejects comparable bounds where since resolves after until."
    raise ValidationFailure("memory_audit_export accepted a request whose since bound resolved after until.")


def case_audit_export_invalid_unknown_format(ctx: "ValidationHarness") -> str:
    namespace, _, _, _, _ = _seed_audit_export_history(ctx, "audit-invalid-format")
    try:
        ctx.audit_export(
            scope="repository",
            namespace=namespace,
            format="csv",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_audit_export", "invalid_request")
        return "memory_audit_export rejects unknown format values with invalid_request."
    raise ValidationFailure("memory_audit_export accepted an unknown export format.")


def case_audit_export_json_delete_filter_limit_and_truncation(ctx: "ValidationHarness") -> str:
    namespace, _, _, _, _ = _seed_audit_export_history(ctx, "audit-json-filter-limit")
    exported = ctx.audit_export(
        scope="repository",
        namespace=namespace,
        event_kinds=["delete"],
        format="json",
        limit=1,
    )
    _assert_success_envelope(exported, "memory_audit_export", AUDIT_EXPORT_SUCCESS_KEYS)
    ensure(exported["format"] == "json", "Audit export should report the effective json format.")
    ensure(exported["truncated"] is True, "Audit export should mark truncated=true when limit trims matching events.")
    records = exported.get("records")
    ensure(isinstance(records, list), "memory_audit_export should return an array when format=json.")
    ensure(exported["event_count"] == len(records) == 1, "Audit export event_count must match the limited json record array.")
    for record in records:
        ensure(isinstance(record, dict), "Audit export json records must be objects.")
        require_keys(record, AUDIT_EVENT_KEYS, "memory_audit_export json record")
        ensure(record["event_kind"] == "delete", "event_kinds filtering should exclude non-delete audit events from the export.")
    return "memory_audit_export supports json array exports, event_kinds filtering, limit, and truncated metadata."


def case_legal_hold_blocks_forget_and_retention_skips_held_subject(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("legal-hold")
    ctx.set_retention_policy(namespace, enabled=True, policies={"fact": "0s"})
    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="compliance_record",
        field="status",
        value="must retain",
        legal_hold=True,
        provenance=ctx.provenance("legal-hold-write"),
    )
    status = ctx.status(scope="repository", namespace=namespace)
    _assert_phase5_status_metadata(status, "memory_status")
    ensure(
        status["retention"]["legal_hold_count"] >= 1,
        "memory_status retention metadata should count the held subject.",
    )
    try:
        ctx.forget(
            scope="repository",
            namespace=namespace,
            memory_type="fact",
            topic="compliance_record",
            field="status",
            provenance=ctx.provenance("legal-hold-forget"),
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_forget", "legal_hold")
    else:
        raise ValidationFailure("memory_forget should fail with legal_hold when the current subject is held.")

    effective_time = _rfc3339_utc_plus_seconds(remembered["current_version"]["recorded_at"], 60)
    ctx.run_retention(scope="repository", namespace=namespace, effective_time=effective_time)
    retained = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="compliance_record",
        field="status",
        memory_type="fact",
    )
    ensure(retained["record"].get("value") == "must retain", "Retention should skip the held subject and keep it visible in current recall.")

    ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="compliance_record",
        field="status",
        value="must retain",
        legal_hold=False,
        provenance=ctx.provenance("legal-hold-clear"),
    )
    cleared = ctx.forget(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="compliance_record",
        field="status",
        provenance=ctx.provenance("legal-hold-forget-after-clear"),
    )
    _assert_success_envelope(cleared, "memory_forget", ["subject", "event_id", "seq", "retraction_status", "current_visibility"])
    ensure(cleared["retraction_status"] == "retracted", "Clearing legal hold should restore the normal forget contract.")
    return "Active legal_hold blocks memory_forget, retention skips the held subject, and clearing the hold restores the forget path."


def case_retention_eviction_removes_current_recall_but_preserves_as_of_history(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("retention-eviction")
    ctx.set_retention_policy(namespace, enabled=True, policies={"fact": "0s"})
    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="retention_probe",
        field="status",
        value="evict me from current recall",
        provenance=ctx.provenance("retention-eviction-write"),
    )
    effective_time = _rfc3339_utc_plus_seconds(remembered["current_version"]["recorded_at"], 60)
    ctx.run_retention(scope="repository", namespace=namespace, effective_time=effective_time)

    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="retention_probe",
            field="status",
            memory_type="fact",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "not_found")
    else:
        raise ValidationFailure("Retention eviction should remove the subject from default current-state lookup.")

    search = ctx.search(scope="repository", namespace=namespace, query="evict me from current recall")
    ensure(
        not _matching_search_results(
            search,
            scope="repository",
            namespace=namespace,
            topic="retention_probe",
            field="status",
            memory_type="fact",
            value="evict me from current recall",
        ),
        "Retention eviction should remove the subject from default current-state search.",
    )
    historical = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="retention_probe",
        field="status",
        memory_type="fact",
        as_of=ctx.as_of_from_seq(remembered["seq"]),
    )
    ensure(
        historical["record"].get("value") == "evict me from current recall",
        "Historical as_of recall before the eviction point must remain available after retention.",
    )
    return "Operator-triggered retention evicts current recall and search visibility without deleting prior as_of history."


def case_fleet_replica_lag_policy_returns_error_without_losing_local_write(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("fleet-lag")
    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="fleet_probe",
        field="status",
        value="local wal is authoritative",
        provenance=ctx.provenance("fleet-lag-write"),
    )
    ctx.set_fleet_config(
        namespace,
        mode="fleet_replica",
        backend_reachable=True,
        serve_reads_from_replica=True,
        max_staleness_seq=1,
        replica_lag_seq=5,
        lag_policy="error",
    )
    fleet_status = ctx.status(scope="repository", namespace=namespace)
    _assert_phase5_status_metadata(fleet_status, "memory_status")
    ensure(fleet_status["fleet"]["mode"] == "fleet_replica", "Fleet lag test must advertise fleet_replica mode in memory_status.")
    ensure(
        fleet_status["fleet"]["replica_lag_seq"] == 5,
        "Fleet lag test must expose the configured replica lag in memory_status.",
    )
    try:
        ctx.get(
            scope="repository",
            namespace=namespace,
            topic="fleet_probe",
            field="status",
            memory_type="fact",
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_get", "fleet_sync_unavailable")
    else:
        raise ValidationFailure("Lagged fleet replica reads should return fleet_sync_unavailable when operator policy is error.")

    ctx.set_fleet_config(
        namespace,
        mode="local",
        backend_reachable=False,
        serve_reads_from_replica=False,
        replica_lag_seq=0,
        lag_policy="fallback_local",
    )
    recovered = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="fleet_probe",
        field="status",
        memory_type="fact",
    )
    ensure(
        recovered["record"].get("value") == "local wal is authoritative",
        "Fleet replica lag must not lose the locally committed write after returning to local reads.",
    )
    ensure(remembered["seq"] >= 1, "Fleet lag setup should use a successfully committed local WAL write.")
    return "Lagged fleet-replica reads can return fleet_sync_unavailable by policy while the locally acknowledged write remains recoverable."


def case_fleet_replica_fallback_local_reads_return_local_state(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("fleet-fallback-local")
    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="fleet_probe",
        field="status",
        value="fallback local preserved this value",
        provenance=ctx.provenance("fleet-fallback-local-remember"),
    )
    _assert_success_envelope(remembered, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    ctx.set_fleet_config(
        namespace,
        mode="fleet_replica",
        backend_reachable=False,
        serve_reads_from_replica=True,
        max_staleness_seq=1,
        replica_lag_seq=5,
        lag_policy="fallback_local",
    )
    status = ctx.status(scope="repository", namespace=namespace)
    _assert_phase5_status_metadata(status, "memory_status")
    ensure(status["fleet"]["mode"] == "fleet_replica", "Fallback-local fleet test must advertise fleet_replica mode in memory_status.")
    recovered = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="fleet_probe",
        field="status",
        memory_type="fact",
    )
    _assert_success_envelope(recovered, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    ensure(
        recovered["record"].get("value") == "fallback local preserved this value",
        "fallback_local fleet policy should keep reads available from local WAL-derived state.",
    )
    return "fleet_replica fallback_local policy serves reads from local WAL-derived state when the replica is too stale."


def case_backward_compatibility_nine_tools_with_phase5_disabled(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("backward-compat-phase5")
    ctx.set_graph_enabled(namespace, False)
    ctx.set_pii_scan(namespace, enabled=False, policy="block", categories=["email"])
    ctx.set_audit_logging(namespace, enabled=False)
    ctx.set_retention_policy(namespace, enabled=False, policies={})
    ctx.set_fleet_config(namespace, mode="local", backend_reachable=False, serve_reads_from_replica=False, replica_lag_seq=0)

    remembered = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="compat_probe_v15",
        field="status",
        value="phase5 disabled keeps v1.5 surface stable",
        provenance=ctx.provenance("backward-compat-phase5-remember"),
    )
    _assert_success_envelope(remembered, "memory_remember", ["subject", "event_id", "seq", "integration_status", "current_version"])
    search = ctx.search(scope="repository", namespace=namespace, query="v1.5 surface stable")
    _assert_success_envelope(search, "memory_search", ["scope", "namespace", "evaluation_mode", "results"])
    ensure(
        _matching_search_results(
            search,
            scope="repository",
            namespace=namespace,
            topic="compat_probe_v15",
            field="status",
            memory_type="fact",
            value="phase5 disabled keeps v1.5 surface stable",
        ),
        "Phase 5 disabled should preserve v1.5 search visibility for the remembered compatibility probe.",
    )
    fetched = ctx.get(
        scope="repository",
        namespace=namespace,
        topic="compat_probe_v15",
        field="status",
        memory_type="fact",
    )
    _assert_success_envelope(fetched, "memory_get", ["scope", "namespace", "evaluation_mode", "record"])
    ensure(
        fetched["record"].get("value") == "phase5 disabled keeps v1.5 surface stable",
        "Phase 5 disabled should not alter direct v1.5 current-state recall.",
    )
    status = ctx.status(scope="repository", namespace=namespace)
    _assert_success_envelope(
        status,
        "memory_status",
        [
            "scope",
            "namespace",
            "supported_scopes",
            "wal_high_water_seq",
            "semantic_units_in_sync",
            "index_status",
            "consolidation",
            "review_queue",
            "profile_engine",
            "session_summary",
        ],
    )
    profile = ctx.profile(scope="user", namespace=namespace)
    _assert_success_envelope(profile, "memory_profile", PROFILE_SUCCESS_KEYS)
    reflect = ctx.reflect(scope="repository", namespace=namespace, query="What is the compatibility probe status?")
    _assert_success_envelope(reflect, "memory_reflect", REFLECT_SUCCESS_KEYS)
    try:
        ctx.consolidate(scope="repository", namespace=namespace, dry_run=True)
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_consolidate", "consolidation_unavailable")
    try:
        ctx.review(scope="repository", namespace=namespace, action="list")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_review", "review_unavailable")
    forgotten = ctx.forget(
        scope="repository",
        namespace=namespace,
        topic="compat_probe_v15",
        field="status",
        memory_type="fact",
        provenance=ctx.provenance("backward-compat-phase5-forget"),
    )
    _assert_success_envelope(forgotten, "memory_forget", ["subject", "event_id", "seq", "retraction_status", "current_visibility"])
    return "With Phase 5 disabled, the nine v1.5.0 tools keep their established remember/search/get/forget/status/profile/reflect/consolidate/review behavior."


def case_temporal_query_unavailable_error(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("temporal-unavailable")
    seed = ctx.remember(
        scope="repository",
        namespace=namespace,
        memory_type="fact",
        topic="temporal_error_probe",
        field="status",
        value="seed",
        provenance=ctx.provenance("temporal-unavailable-seed"),
    )
    ctx.set_fault("temporal_query_unavailable", True)
    try:
        ctx.query_temporal(
            scope="repository",
            namespace=namespace,
            topic="temporal_error_probe",
            audit_points=[ctx.as_of_from_seq(seed["seq"])],
        )
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_query_temporal", "temporal_query_unavailable")
    else:
        raise ValidationFailure("memory_query_temporal should return temporal_query_unavailable when the fault is armed.")
    finally:
        ctx.set_fault("temporal_query_unavailable", False)
    return "memory_query_temporal surfaces temporal_query_unavailable through the standard error envelope."


def case_audit_export_unavailable_error(ctx: "ValidationHarness") -> str:
    namespace = ctx.namespace("audit-export-unavailable")
    ctx.set_fault("audit_export_unavailable", True)
    try:
        ctx.audit_export(scope="repository", namespace=namespace, format="jsonl")
    except ToolCallError as exc:
        _assert_error_envelope(exc, "memory_audit_export", "audit_export_unavailable")
    else:
        raise ValidationFailure("memory_audit_export should return audit_export_unavailable when the fault is armed.")
    finally:
        ctx.set_fault("audit_export_unavailable", False)
    return "memory_audit_export surfaces audit_export_unavailable through the standard error envelope."


ALL_CASES = [
    BehaviorCase("Published MCP tool surface exposes exactly eleven tools.", "Tool Contracts", "10", case_tool_catalog),
    BehaviorCase("memory_remember appends and integrates a governed write.", "Tool Contracts", "10.1", case_memory_remember_success),
    BehaviorCase("memory_remember rejects unsupported scopes.", "Tool Contracts", "12", case_memory_remember_invalid_scope),
    BehaviorCase("memory_remember rejects malformed governed writes.", "Tool Contracts", "12", case_memory_remember_missing_required_field),
    BehaviorCase("memory_remember surfaces integration_failed without leaking partial state.", "Tool Contracts", "8.1, 12", case_integration_failed_error),
    BehaviorCase("memory_search returns current-state metadata and match explanation.", "Tool Contracts", "9.1, 10.2", case_memory_search_current_metadata),
    BehaviorCase("memory_search excludes superseded governed values from current results.", "Tool Contracts", "9.1, 10.2", case_memory_search_excludes_superseded_current),
    BehaviorCase("memory_search subject filters resolve same-field different-topic ambiguity without changing the governed corpus.", "Tool Contracts", "3, 9.1, 9.3, 10.2", case_memory_search_subject_filters_ambiguous_same_field),
    BehaviorCase("memory_search preserves the v1.1.1 behavior when subject is omitted.", "Tool Contracts", "3, 9.1, 10.2, 15", case_memory_search_without_subject_preserves_prior_contract),
    BehaviorCase("memory_search match_reason reflects an applied subject constraint.", "Tool Contracts", "10.2", case_memory_search_subject_match_reason),
    BehaviorCase("memory_search supports historical as_of evaluation.", "Audit", "9.2, 10.2", case_memory_search_as_of),
    BehaviorCase("memory_search composes subject filtering with as_of historical recall.", "Audit", "9.2, 10.2", case_memory_search_subject_with_as_of),
    BehaviorCase("memory_search rejects malformed as_of objects with both seq and recorded_at.", "Audit", "9.2, 10.2, 12", case_memory_search_invalid_as_of_shape),
    BehaviorCase("memory_search rejects malformed as_of objects with neither seq nor recorded_at.", "Audit", "9.2, 10.2, 12", case_memory_search_invalid_as_of_missing_selector),
    BehaviorCase("memory_search rejects malformed as_of objects whose seq is not an integer.", "Audit", "9.2, 10.2, 12", case_memory_search_invalid_as_of_non_integer_seq),
    BehaviorCase("memory_search rejects malformed as_of objects whose recorded_at is not RFC3339 UTC.", "Audit", "9.2, 10.2, 12", case_memory_search_invalid_as_of_non_rfc3339_recorded_at),
    BehaviorCase("memory_search rejects subject filters that omit both topic and field.", "Tool Contracts", "10.2, 12", case_memory_search_invalid_subject_missing_keys),
    BehaviorCase("memory_search rejects subject filters that contain unsupported keys.", "Tool Contracts", "10.2, 12", case_memory_search_invalid_subject_extra_keys),
    BehaviorCase("memory_search subject filtering never expands the governed corpus or reintroduces superseded values.", "Tool Contracts", "9.3, 10.2", case_memory_search_subject_partition_safety),
    BehaviorCase("Episode recall is opt-in for search.", "Tool Contracts", "8.4, 9.3, 10.2", case_memory_search_episodes_opt_in),
    BehaviorCase("memory_get returns current value and optional version history.", "Tool Contracts", "10.3", case_memory_get_current_and_versions),
    BehaviorCase("memory_get distinguishes not_found subjects.", "Tool Contracts", "10.3, 12", case_memory_get_not_found),
    BehaviorCase("memory_get returns provenance and validity metadata for governed records.", "Tool Contracts", "9.4, 10.3", case_memory_get_returns_normative_metadata),
    BehaviorCase("memory_forget retracts current-state visibility.", "Tool Contracts", "8.5, 10.4", case_memory_forget_retracts_current),
    BehaviorCase("memory_status exposes service health, consolidation, and review-queue metadata.", "Tool Contracts", "10.5", case_memory_status_shape),
    BehaviorCase("memory_status requires both scope and namespace.", "Tool Contracts", "10.5, 12", case_memory_status_requires_scope_and_namespace),
    BehaviorCase("Superseding types close the prior interval and expose only the latest open value.", "Supersession", "8.2", case_supersession_current_lookup),
    BehaviorCase("Belief entries stay separate from facts on the same topic and field.", "Supersession", "6.3, 13", case_belief_does_not_supersede_fact),
    BehaviorCase("Historical lookup returns the value current at a requested audit point.", "Audit", "9.2", case_as_of_after_supersession),
    BehaviorCase("Historical lookup after retraction preserves prior auditability while removing current visibility.", "Audit", "8.5, 9.2", case_as_of_after_retract),
    BehaviorCase("Schema-driven propagation updates dependent semantic units on ingest.", "Propagation", "8.6", case_schema_driven_propagation),
    BehaviorCase("Current-state search survives full index rebuild without recall drift.", "Index And Rebuild", "7.5, 11", case_index_rebuild_preserves_recall),
    BehaviorCase("Index outages degrade search only and leave direct lookup intact.", "Index And Rebuild", "7.7, 11, 12", case_index_unavailable_only_degrades_search),
    BehaviorCase("Namespaces isolate governed subjects within the same scope.", "Scope Isolation", "5.2", case_namespace_isolation),
    BehaviorCase("Scopes isolate governed subjects even with identical namespace and subject keys.", "Scope Isolation", "5.1, 5.3", case_scope_isolation),
    BehaviorCase("Consolidation availability is explicit rather than assumed.", "Consolidation", "7.9, 10.5", case_consolidation_optional_absence),
    BehaviorCase("Explicit consolidation is non-destructive and leaves the WAL untouched.", "Consolidation", "7.10, 10.5, 11, 13", case_consolidation_non_destructive),
    BehaviorCase("Fresh-create persistence supports empty startup and durable restart.", "Persistence", "11", case_fresh_create_persistence),
    BehaviorCase("In-place upgrade preserves prior governed state and permits new writes.", "Persistence", "11", case_upgrade_path_persistence),
    BehaviorCase("Episode writes remain append-only and retrievable.", "Persistence", "8.4, 10.2", case_episode_append_only_history),
    BehaviorCase("memory_get defaults to depth=full preserving the v1.2.0 contract.", "Tool Contracts", "10.3, 15", case_memory_get_depth_full_default),
    BehaviorCase("memory_get depth=summary truncates long values to 256 Unicode scalars plus ellipsis.", "Tool Contracts", "10.3", case_memory_get_depth_summary_truncation),
    BehaviorCase("memory_get depth=summary preserves subject identity and validity metadata.", "Tool Contracts", "10.3", case_memory_get_depth_summary_preserves_metadata),
    BehaviorCase("Future expires_at values remain in current recall and as_of evaluation before expiry.", "Lifecycle", "6.4, 9.1, 10.1", case_memory_remember_expires_at_future_recall),
    BehaviorCase("Expired governed records are excluded from default current recall and search.", "Lifecycle", "6.4, 9.1, 10.1", case_memory_remember_expires_at_past_excluded),
    BehaviorCase("blocks_actions metadata is persisted and returned on constraint recall.", "Lifecycle", "6.4, 9.4, 9.5, 10.1", case_memory_remember_blocks_actions_constraint),
    BehaviorCase("blocks_actions is valid for action_boundary facts and returned on recall.", "Lifecycle", "6.4, 9.5, 10.1", case_memory_remember_blocks_actions_action_boundary_fact),
    BehaviorCase("blocks_actions does not cause the service to block unrelated MCP requests.", "Lifecycle", "9.5, 13", case_blocks_actions_does_not_block_unrelated_mcp),
    BehaviorCase("Episode writes accept structured observation metadata.", "Lifecycle", "6.1, 6.4, 10.1", case_memory_remember_episode_observation),
    BehaviorCase("Unsupported consolidation returns consolidation_unavailable.", "Consolidation", "10.6, 12", case_memory_consolidate_unavailable),
    BehaviorCase("Unsupported review returns review_unavailable.", "Review", "10.7, 12", case_memory_review_unavailable),
    BehaviorCase("memory_consolidate returns stats and promotions when consolidation is supported.", "Consolidation", "6.5, 10.6", case_memory_consolidate_success_shape),
    BehaviorCase("memory_consolidate dry_run does not mutate derived state or review queue.", "Consolidation", "10.6", case_memory_consolidate_dry_run_no_mutation),
    BehaviorCase("MCP consolidation preserves held facts and does not auto-promote beliefs.", "Consolidation", "7.10, 10.6, 11, 13", case_memory_consolidate_preserves_held_facts),
    BehaviorCase("Review flow lists, accepts, and rejects consolidation promotion proposals.", "Review", "6.5, 10.7", case_memory_review_list_accept_reject_flow),
    BehaviorCase("memory_review rejects malformed actions and missing review_id.", "Review", "10.7, 12", case_memory_review_invalid_request),
    BehaviorCase("memory_consolidate rejects requests missing required scope or namespace.", "Consolidation", "10.6, 12", case_memory_consolidate_invalid_request),
    BehaviorCase("Hermes prefetch returns governed results, sync_turn is non-blocking, and handle_tool_call round-trips remember to get.", "Harness Adapters", "14.4", case_hermes_prefetch_sync_turn_and_handle_tool_call),
    BehaviorCase("Hermes recall_mode=tools exposes schemas while recall_mode=context exposes none.", "Harness Adapters", "14.2.2, 14.4", case_hermes_recall_mode_schemas),
    BehaviorCase("OpenClaw memory_search and memory_get succeed on empty and populated stores within the workspace sandbox.", "Harness Adapters", "14.3.1, 14.3.2, 14.4", case_openclaw_search_and_get_within_workspace),
    BehaviorCase("OpenClaw rejects paths outside the configured workspace.", "Harness Adapters", "14.3.2, 14.4", case_openclaw_rejects_outside_workspace_path),
    BehaviorCase("Hermes and OpenClaw preserve state when restarted with the same data_dir.", "Harness Adapters", "14.4", case_adapter_restart_persistence),
    BehaviorCase("memory_profile returns empty success when no eligible records exist.", "User Modeling", "10.8, 18", case_memory_profile_empty_success),
    BehaviorCase("memory_profile populated preferences appear in sections with matching citations.", "User Modeling", "10.8, 18", case_memory_profile_populated_sections_citations),
    BehaviorCase("memory_profile budget_tokens truncates manifest assembly deterministically.", "User Modeling", "10.8, 18", case_memory_profile_budget_tokens_truncation),
    BehaviorCase("memory_profile ranks belief below preference when trimming to budget_tokens.", "User Modeling", "10.8, 17.1, 18", case_memory_profile_belief_ranks_below_preference),
    BehaviorCase("memory_profile rejects non-user scope with invalid_request.", "User Modeling", "10.8, 12, 18", case_memory_profile_invalid_scope),
    BehaviorCase("memory_reflect rejects an empty query with invalid_request.", "Reflection", "10.9, 12, 18", case_memory_reflect_empty_query),
    BehaviorCase("memory_reflect returns empty synthesis success when evidence_count is zero.", "Reflection", "10.9, 18", case_memory_reflect_empty_synthesis),
    BehaviorCase("memory_reflect citations include topic, field, memory_type, and seq.", "Reflection", "10.9, 18", case_memory_reflect_citations_shape),
    BehaviorCase("memory_reflect does not cite records outside the requested namespace.", "Reflection", "10.9, 18", case_memory_reflect_namespace_isolation),
    BehaviorCase("memory_reflect does not perform silent WAL writes.", "Reflection", "10.9, 18", case_memory_reflect_no_silent_writes),
    BehaviorCase("persona_id-tagged records are invisible in the default persona context.", "Persona Scoping", "5.4, 17.3, 18", case_persona_id_isolation_default_context),
    BehaviorCase("persona_id=B cannot read persona_id=A records without share_to.", "Persona Scoping", "5.4, 17.3, 18", case_persona_id_isolation_named_persona),
    BehaviorCase("share_to grants cross-persona read visibility.", "Persona Scoping", "17.3, 18", case_share_to_visibility),
    BehaviorCase("memory_profile excludes persona-tagged records outside the active persona context.", "User Modeling", "10.8, 17.3, 18", case_memory_profile_persona_exclusion),
    BehaviorCase("memory_status updates profile_engine and session_summary operator metadata after triggers.", "Tool Contracts", "10.5, 17.1, 17.2, 18", case_memory_status_operator_metadata_updates),
    BehaviorCase("apply_context_fencing satisfies Section 17.4.2 post-conditions.", "Context Fencing", "17.4, 18", case_context_fencing_post_conditions),
    BehaviorCase("Disabled profile engine does not create profile_engine provenance records.", "Profile Engine", "17.1, 18", case_profile_engine_disabled_no_extraction),
    BehaviorCase("Enabled profile engine creates belief records with derived_from.", "Profile Engine", "17.1, 18", case_profile_engine_enabled_belief_derived_from),
    BehaviorCase("Session summary subject is bounded to 4096 Unicode scalars.", "Session Summary", "17.2, 18", case_session_summary_bounded),
    BehaviorCase("Original seven MCP tools remain v1.4.1-compatible when Phase 4 fields are omitted.", "Backward Compatibility", "17, 18", case_backward_compatibility_seven_tools),
    BehaviorCase("memory_profile returns profile_unavailable through the standard error envelope.", "Error Codes", "10.8, 12, 18", case_profile_unavailable_error),
    BehaviorCase("memory_reflect returns reflection_unavailable through the standard error envelope.", "Error Codes", "10.9, 12, 18", case_reflection_unavailable_error),
    BehaviorCase("Explicit profile-engine trigger returns extraction_disabled when extraction is disabled.", "Error Codes", "12, 17.1, 18", case_extraction_disabled_error),
    BehaviorCase("memory_status exposes pii_scan, retention, and fleet metadata with local-first defaults.", "Phase 5 Operator Visibility", "19.3.3, 19.5.3, 19.6.4", case_memory_status_phase5_operator_metadata_local_default),
    BehaviorCase("Graph-enabled temporal queries project stable topic entities and direct depends_on edges from standard governed writes.", "Temporal Graph", "19.1.4, 20", case_graph_derivation_subject_projection_and_depends_on),
    BehaviorCase("Rebuilding the graph preserves WAL-derived single-subject recall and graph snapshots.", "Temporal Graph", "19.1.3, 20", case_graph_rebuild_preserves_single_subject_recall),
    BehaviorCase("memory_query_temporal matches independent memory_get(as_of) across a multi-subject topic partition.", "Temporal Query", "10.10, 19.2, 20", case_memory_query_temporal_matches_independent_memory_get),
    BehaviorCase("memory_query_temporal returns empty trajectories for an empty topic partition.", "Temporal Query", "10.10, 19.2, 20", case_memory_query_temporal_empty_partition),
    BehaviorCase("memory_query_temporal omits retracted-only subjects when include_retracted is false.", "Temporal Query", "8.5.1, 10.10, 19.2, 20", case_memory_query_temporal_omits_retracted_subjects_by_default),
    BehaviorCase("memory_query_temporal surfaces status=retracted when include_retracted is true.", "Temporal Query", "8.5.1, 10.10, 19.2, 20", case_memory_query_temporal_surfaces_retracted_status_when_requested),
    BehaviorCase("memory_query_temporal rejects an empty audit_points array.", "Temporal Query", "10.10, 12, 19.2, 20", case_memory_query_temporal_invalid_empty_audit_points),
    BehaviorCase("memory_query_temporal rejects audit points with both seq and recorded_at.", "Temporal Query", "10.10, 12, 19.2, 20", case_memory_query_temporal_invalid_audit_point_both_selectors),
    BehaviorCase("memory_query_temporal rejects audit points with neither seq nor recorded_at.", "Temporal Query", "10.10, 12, 19.2, 20", case_memory_query_temporal_invalid_audit_point_missing_selector),
    BehaviorCase("memory_query_temporal rejects requests missing topic.", "Temporal Query", "10.10, 12, 19.2, 20", case_memory_query_temporal_invalid_missing_topic),
    BehaviorCase("PII block mode rejects the deterministic regulated fixture only when scanning is enabled.", "PII Governance", "12, 19.3, 20", case_pii_block_mode_rejects_enabled_fixture_and_disabled_mode_preserves_write),
    BehaviorCase("PII redact mode stores and recalls placeholder-substituted values.", "PII Governance", "19.3.2, 20", case_pii_redact_mode_persists_placeholder_value),
    BehaviorCase("Enabled audit logging records reads and writes and exports JSONL audit records.", "Audit Log", "10.11, 19.4, 20", case_audit_log_records_reads_writes_and_exports_jsonl),
    BehaviorCase("memory_audit_export rejects malformed since bounds.", "Audit Log", "10.11, 12, 19.4, 20", case_audit_export_invalid_since_bound),
    BehaviorCase("memory_audit_export rejects malformed until bounds.", "Audit Log", "10.11, 12, 19.4, 20", case_audit_export_invalid_until_bound),
    BehaviorCase("memory_audit_export rejects requests whose since bound resolves after until.", "Audit Log", "10.11, 12, 19.4, 20", case_audit_export_invalid_since_after_until),
    BehaviorCase("memory_audit_export rejects unknown format values.", "Audit Log", "10.11, 12, 19.4, 20", case_audit_export_invalid_unknown_format),
    BehaviorCase("memory_audit_export supports json export filters, limit, and truncated metadata.", "Audit Log", "10.11, 19.4, 20", case_audit_export_json_delete_filter_limit_and_truncation),
    BehaviorCase("legal_hold blocks forget and causes retention to skip the held subject until cleared.", "Retention And Legal Hold", "8.5, 12, 19.5, 20", case_legal_hold_blocks_forget_and_retention_skips_held_subject),
    BehaviorCase("Operator-triggered retention evicts current recall without deleting as_of history.", "Retention And Legal Hold", "19.5.1, 20", case_retention_eviction_removes_current_recall_but_preserves_as_of_history),
    BehaviorCase("Lagged fleet-replica reads can return fleet_sync_unavailable without losing local WAL writes.", "Fleet", "12, 19.6, 20", case_fleet_replica_lag_policy_returns_error_without_losing_local_write),
    BehaviorCase("fleet_replica fallback_local policy preserves read availability from local state.", "Fleet", "19.6, 20", case_fleet_replica_fallback_local_reads_return_local_state),
    BehaviorCase("When Phase 5 features are disabled, the nine v1.5.0 tools keep their established behavior.", "Backward Compatibility", "19, 20", case_backward_compatibility_nine_tools_with_phase5_disabled),
    BehaviorCase("memory_query_temporal returns temporal_query_unavailable through the standard error envelope.", "Error Codes", "10.10, 12, 19.2, 20", case_temporal_query_unavailable_error),
    BehaviorCase("memory_audit_export returns audit_export_unavailable through the standard error envelope.", "Error Codes", "10.11, 12, 19.4, 20", case_audit_export_unavailable_error),
    # Phase 6 connect CLI and catalog cases (spec v1.7.0 Section 21.8)
    *[BehaviorCase(behavior, group, spec_section, fn) for behavior, group, spec_section, fn in CONNECT_CASES],
]