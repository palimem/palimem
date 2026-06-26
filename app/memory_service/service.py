from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3
import threading
from typing import Any

from .audit import AuditConfig, build_actor, export_id_for, to_jsonl
from .consolidation import plan_safe_merge
from .domain import (
    DEFAULT_GOVERNED_MEMORY_TYPES,
    SUPPORTED_MEMORY_TYPES,
    SUPPORTED_SCOPES,
    SUPERSEDING_TYPES,
    VERSIONED_TYPES,
    InvalidRequestError,
    SubjectKey,
    build_search_text,
    citation_from_record,
    normalize_search_query,
    normalize_extends,
    parent_edge_key,
    parse_as_of,
    parse_derived_from,
    parse_persona_id,
    parse_search_subject,
    parse_rfc3339_utc,
    parse_share_to,
    profile_assembly_rank,
    replace_bound_value,
    require_string,
    serialize_json,
    subject_from_request,
    truncate_session_summary_value,
    truncate_summary_value,
    truncate_to_budget,
    utc_now_rfc3339,
    validate_blocks_actions,
    validate_derived_from_for_write,
    validate_depth,
    validate_expires_at,
    validate_memory_type,
    validate_observation,
    validate_profile_budget,
    validate_profile_depth,
    validate_provenance,
    validate_legal_hold,
    validate_scope,
    PROFILE_MEMORY_TYPES,
    record_visible_to_persona,
)
from .errors import (
    IndexUnavailableError,
    IntegrationFailedError,
    LegalHoldError,
    MemoryServiceError,
    NotFoundError,
    AuditExportUnavailableError,
    PiiBlockedError,
    ProfileUnavailableError,
    ReflectionUnavailableError,
    TemporalQueryUnavailableError,
    FleetSyncUnavailableError,
)
from .fleet import FleetBackendConfig, FleetStatus, validate_fleet_mode
from .graph import GraphIndex, derive_graph_snapshot
from .indexing import IndexManager, TfidfRetriever
from .pii import PiiScanConfig, PiiScanner
from .profile_engine import ProfileEngine
from .reflection import gather_reflect_evidence, reflect_citations, synthesize_reflect
from .retention import normalize_retention_ttl, retention_now, validate_retention_memory_type
from .storage import Storage


class FaultState:
    def __init__(self) -> None:
        self.integration_fail_next = False
        self.profile_unavailable = False
        self.reflection_unavailable = False
        self.temporal_query_unavailable = False
        self.audit_export_unavailable = False


class MemoryService:
    def __init__(self, data_dir: Path, schema_mode: str, upgrade_fixture: str | None = None):
        self.data_dir = data_dir
        self.schema_mode = schema_mode
        self.upgrade_fixture = upgrade_fixture
        self.storage = Storage(data_dir)
        self.index_manager = IndexManager(data_dir / "indexes")
        self.graph_index = GraphIndex(data_dir / "graph")
        self.retriever = TfidfRetriever()
        self.pii_scanner = PiiScanner()
        self.faults = FaultState()
        self._graph_enabled: dict[tuple[str, str], bool] = {}
        self._fleet_lag_policy: dict[tuple[str, str], str] = {}
        self._fleet_backend_config: dict[tuple[str, str], FleetBackendConfig] = {}
        self._fleet_backend_cache: dict[str, Storage] = {}
        self.profile_engine = ProfileEngine(self)
        try:
            self._bootstrap_schema_mode()
            self._rebuild_all_indexes()
        except Exception:
            self.storage.close()
            raise

    def close(self) -> None:
        for backend_storage in self._fleet_backend_cache.values():
            backend_storage.close()
        self.storage.close()

    def set_fault(self, name: str, enabled: bool) -> None:
        if name == "integration_fail_next":
            self.faults.integration_fail_next = enabled
            return
        if name == "profile_unavailable":
            self.faults.profile_unavailable = enabled
            return
        if name == "reflection_unavailable":
            self.faults.reflection_unavailable = enabled
            return
        if name == "temporal_query_unavailable":
            self.faults.temporal_query_unavailable = enabled
            return
        if name == "audit_export_unavailable":
            self.faults.audit_export_unavailable = enabled
            return
        raise InvalidRequestError("unsupported fault name")

    def readiness_payload(self) -> dict[str, Any]:
        return {
            "ready": True,
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
                "memory_audit_export",
            ],
            "supported_scopes": list(SUPPORTED_SCOPES),
            "schema_mode": self.schema_mode,
            "consolidation_available": True,
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._tool_definition("memory_remember", "Append and integrate governed memory."),
            self._tool_definition("memory_search", "Search current or historical governed memory."),
            self._tool_definition("memory_get", "Direct lookup by subject key."),
            self._tool_definition("memory_forget", "Retract memory from current recall."),
            self._tool_definition("memory_status", "Report namespace status and index health."),
            self._tool_definition("memory_consolidate", "Run explicit consolidation maintenance for one namespace."),
            self._tool_definition("memory_review", "List, accept, or reject consolidation promotion proposals."),
            self._tool_definition("memory_profile", "Assemble a bounded user-profile manifest for injection."),
            self._tool_definition("memory_reflect", "Synthesize across governed evidence with citations."),
            self._tool_definition("memory_query_temporal", "Query belief trajectories across audit points."),
            self._tool_definition("memory_audit_export", "Export append-only operator audit records."),
        ]

    def dispatch(self, tool_name: str, arguments: dict[str, Any] | None, *, request_id: Any | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        try:
            if tool_name == "memory_remember":
                envelope = self.memory_remember(arguments, request_id=request_id)
            elif tool_name == "memory_search":
                envelope = self.memory_search(arguments)
            elif tool_name == "memory_get":
                envelope = self.memory_get(arguments)
            elif tool_name == "memory_forget":
                envelope = self.memory_forget(arguments, request_id=request_id)
            elif tool_name == "memory_status":
                envelope = self.memory_status(arguments)
            elif tool_name == "memory_consolidate":
                envelope = self.memory_consolidate(arguments)
            elif tool_name == "memory_review":
                envelope = self.memory_review(arguments, request_id=request_id)
            elif tool_name == "memory_profile":
                envelope = self.memory_profile(arguments)
            elif tool_name == "memory_reflect":
                envelope = self.memory_reflect(arguments)
            elif tool_name == "memory_query_temporal":
                envelope = self.memory_query_temporal(arguments)
            elif tool_name == "memory_audit_export":
                envelope = self.memory_audit_export(arguments, request_id=request_id)
            else:
                raise InvalidRequestError(f"Unknown tool: {tool_name}")
            self._emit_read_audit(tool_name, arguments, envelope, request_id=request_id)
            return envelope
        except MemoryServiceError as exc:
            return self.error_envelope(tool_name, exc.code, exc.message)
        except sqlite3.Error as exc:
            return self.error_envelope(tool_name, "integration_failed", f"storage error: {exc}")

    def memory_remember(self, payload: dict[str, Any], *, request_id: Any | None = None) -> dict[str, Any]:
        subject = subject_from_request(payload, include_persona=True)
        value = payload.get("value")
        extends = normalize_extends(payload.get("extends"))
        provenance = validate_provenance(payload.get("provenance"))
        share_to = parse_share_to(payload.get("share_to"))
        derived_from = validate_derived_from_for_write(
            subject.memory_type,
            provenance,
            parse_derived_from(payload.get("derived_from")),
        )
        episode_id = payload.get("episode_id")
        if episode_id is not None:
            episode_id = require_string("episode_id", episode_id)
        expires_at = validate_expires_at(payload.get("expires_at"), subject.memory_type)
        blocks_actions = validate_blocks_actions(subject.memory_type, subject.topic, payload.get("blocks_actions"))
        observation, value = validate_observation(subject.memory_type, payload.get("observation"), value)
        legal_hold = validate_legal_hold(payload.get("legal_hold"))
        try:
            pii_result = self._run_pii_scan(subject, value)
        except MemoryServiceError:
            raise
        except Exception as exc:
            raise MemoryServiceError("integration_failed", f"PII scan failed before WAL append: {exc}") from exc
        if pii_result.blocked:
            raise PiiBlockedError("PII scan blocked the write under the configured namespace policy.")
        value = pii_result.value
        if observation is not None and isinstance(value, dict):
            observation = {key: value[key] for key in observation.keys() if key in value}
        if subject.topic == "session_summary" and subject.field == "body" and subject.scope == "session":
            value = truncate_session_summary_value(value)
        current_record = self._write_memory(
            subject,
            value,
            extends,
            provenance,
            episode_id,
            expires_at=expires_at,
            blocks_actions=blocks_actions,
            observation=observation,
            share_to=share_to,
            derived_from=derived_from,
            legal_hold=legal_hold,
            audit_context=self._write_audit_context("memory_remember", payload, subject, request_id=request_id),
        )
        if subject.topic == "session_summary" and subject.field == "body" and subject.scope == "session":
            self.storage.set_session_summary_seq(subject.scope, subject.namespace, current_record["seq"])
        response = {
            "ok": True,
            "tool": "memory_remember",
            "subject": subject.as_dict(),
            "event_id": current_record["event_id"],
            "seq": current_record["seq"],
            "integration_status": "integrated",
            "current_version": {
                "value": current_record["value"],
                "event_id": current_record["event_id"],
                "seq": current_record["seq"],
                "valid_from_seq": current_record["valid_from_seq"],
                "valid_to_seq": current_record["valid_to_seq"],
                "recorded_at": current_record["recorded_at"],
            },
        }
        if current_record.get("legal_hold"):
            response["current_version"]["legal_hold"] = True
        if pii_result.metadata.get("enabled") and pii_result.metadata.get("categories"):
            response["pii_scan"] = pii_result.metadata
        return response

    def memory_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        source_storage, read_source = self._storage_for_read(scope, namespace)
        query = normalize_search_query(payload.get("query"))
        subject_filter = parse_search_subject(payload.get("subject"))
        read_persona_id = parse_persona_id(payload.get("persona_id"))
        include_episodes = bool(payload.get("include_episodes", False))
        as_of = parse_as_of(payload.get("as_of"))
        memory_types = payload.get("memory_types")
        normalized_types: list[str] | None = None
        if memory_types is not None:
            if not isinstance(memory_types, list):
                raise InvalidRequestError("memory_types must be an array when provided.")
            normalized_types = [validate_memory_type(item) for item in memory_types]
        limit = payload.get("limit", 10)
        if not isinstance(limit, int) or limit <= 0:
            raise InvalidRequestError("limit must be a positive integer when provided.")

        evaluation_mode, evaluation_seq = self._resolve_evaluation(as_of, storage=source_storage)
        evaluation_time = self._evaluation_time(as_of, evaluation_seq, storage=source_storage)

        candidates = []
        if evaluation_mode == "current" and read_source == "local":
            index_state = source_storage.get_namespace_status(scope, namespace)["index_state"]
            if index_state == "unavailable":
                raise IndexUnavailableError("indexed search is unavailable for the requested namespace.")
            indexed_records = self._load_current_indexed_records(scope, namespace, index_state)
            indexed_records = self._filter_records_for_scope_namespace(indexed_records, scope, namespace)
            indexed_records = self._filter_records_for_persona(indexed_records, read_persona_id)
            candidates.extend(
                [
                    record | {"_search_text": build_search_text(record)}
                    for record in indexed_records
                ]
            )
            if include_episodes:
                episode_rows = self.storage.get_rows_for_search(
                    scope, namespace, None, True, ["episode"], read_persona_id=read_persona_id
                )
                candidates.extend(
                    [
                        self._record_for_row(row, status="current") | {"_search_text": self._search_text_for_row(row)}
                        for row in episode_rows
                    ]
                )
        else:
            rows = source_storage.get_rows_for_search(
                scope,
                namespace,
                evaluation_seq,
                include_episodes,
                normalized_types,
                evaluation_time=evaluation_time,
                read_persona_id=read_persona_id,
            )
            candidates = [
                self._record_for_row(row, status="historical") | {"_search_text": self._search_text_for_row(row)}
                for row in rows
                if self._row_matches_scope_namespace(row, scope, namespace)
            ]

        if normalized_types:
            candidates = [candidate for candidate in candidates if candidate["memory_type"] in normalized_types]
        if subject_filter:
            candidates = [candidate for candidate in candidates if self._candidate_matches_subject(candidate, subject_filter)]

        ranked = self.retriever.rank(query, candidates)[:limit]
        results = []
        for candidate in ranked:
            record = {key: value for key, value in candidate.items() if not key.startswith("_")}
            record["match_reason"] = self._match_reason_with_subject(candidate["match_reason"], subject_filter)
            results.append(record)

        return {
            "ok": True,
            "tool": "memory_search",
            "scope": scope,
            "namespace": namespace,
            "evaluation_mode": evaluation_mode,
            "results": results,
        }

    def memory_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = subject_from_request(payload)
        source_storage, _ = self._storage_for_read(subject.scope, subject.namespace)
        read_persona_id = parse_persona_id(payload.get("persona_id"))
        include_versions = bool(payload.get("include_versions", False))
        depth = validate_depth(payload.get("depth"))
        as_of = parse_as_of(payload.get("as_of"))
        evaluation_mode, evaluation_seq = self._resolve_evaluation(as_of, storage=source_storage)
        evaluation_time = self._evaluation_time(as_of, evaluation_seq, storage=source_storage)
        row = source_storage.get_subject_at(
            subject, evaluation_seq, evaluation_time=evaluation_time, read_persona_id=read_persona_id
        )
        if row is None:
            raise NotFoundError("No current value exists for the requested subject at the requested recall point.")
        if not self._row_matches_scope_namespace(row, subject.scope, subject.namespace):
            raise IntegrationFailedError("retrieval returned a record outside the requested scope.")
        record = self._record_for_row(row, status="historical" if evaluation_mode == "as_of" else "current")
        if depth == "summary":
            record = dict(record)
            record["value"] = truncate_summary_value(record["value"])
        response = {
            "ok": True,
            "tool": "memory_get",
            "scope": subject.scope,
            "namespace": subject.namespace,
            "evaluation_mode": evaluation_mode,
            "record": record,
        }
        if include_versions:
            versions = source_storage.get_subject_versions(subject)
            response["versions"] = [
                {
                    "value": self._record_for_row(version, status="historical")["value"],
                    "event_id": version["event_id"],
                    "seq": version["seq"],
                    "valid_from_seq": version["valid_from_seq"],
                    "valid_to_seq": version["valid_to_seq"],
                    "recorded_at": version["recorded_at"],
                }
                for version in versions
            ]
        return response

    def memory_forget(self, payload: dict[str, Any], *, request_id: Any | None = None) -> dict[str, Any]:
        subject = subject_from_request(payload, include_persona=True)
        provenance = validate_provenance(payload.get("provenance"))
        event = self._retract_memory(
            subject,
            provenance,
            audit_context=self._write_audit_context("memory_forget", payload, subject, request_id=request_id),
        )
        return {
            "ok": True,
            "tool": "memory_forget",
            "subject": subject.as_dict(),
            "event_id": event["event_id"],
            "seq": event["seq"],
            "retraction_status": "retracted",
            "current_visibility": "hidden",
        }

    def memory_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("scope") is None or payload.get("namespace") is None:
            raise InvalidRequestError("memory_status requires both scope and namespace.")
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        status = self.storage.get_namespace_status(scope, namespace)
        wal = self.storage.get_high_water_seq(scope, namespace)
        pii_config = PiiScanConfig.from_dict(self.storage.get_pii_scan_config(scope, namespace))
        retention_policies = self.storage.get_retention_policies(scope, namespace)
        fleet_status = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace))
        return {
            "ok": True,
            "tool": "memory_status",
            "scope": scope,
            "namespace": namespace,
            "supported_scopes": list(SUPPORTED_SCOPES),
            "wal_high_water_seq": wal,
            "semantic_units_in_sync": True,
            "index_status": {"state": status["index_state"]},
            "consolidation": {
                "available": True,
                "last_run_at": status["last_consolidation_at"],
            },
            "review_queue": {
                "pending_count": self.storage.count_pending_reviews(scope, namespace),
            },
            "profile_engine": {
                "enabled": self.profile_engine.is_enabled(scope, namespace),
                "last_run_at": status["profile_engine_last_run_at"],
            },
            "session_summary": {
                "topic": "session_summary",
                "last_updated_seq": status["session_summary_last_updated_seq"],
            },
            "pii_scan": {
                "enabled": pii_config.enabled,
                "policy": pii_config.policy,
            },
            "retention": {
                "enabled": bool(retention_policies),
                "policies": retention_policies,
                "legal_hold_count": self.storage.count_legal_holds(scope, namespace),
            },
            "fleet": {
                "mode": fleet_status.mode,
                "backend_reachable": fleet_status.backend_reachable,
                "last_synced_seq": fleet_status.last_synced_seq,
                "replica_lag_seq": fleet_status.replica_lag_seq,
                "serve_reads_from_replica": fleet_status.serve_reads_from_replica,
                "max_staleness_seq": fleet_status.max_staleness_seq,
                "lag_policy": self._fleet_lag_policy.get((scope, namespace), "fallback_local"),
            },
        }

    def memory_consolidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("scope") is None or payload.get("namespace") is None:
            raise InvalidRequestError("memory_consolidate requires both scope and namespace.")
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        dry_run = bool(payload.get("dry_run", False))
        result = self._execute_consolidation(scope, namespace, dry_run=dry_run)
        return {
            "ok": True,
            "tool": "memory_consolidate",
            "scope": scope,
            "namespace": namespace,
            "available": True,
            "dry_run": dry_run,
            "last_run_at": result["last_run_at"],
            "stats": result["stats"],
            "promotions": result["promotions"],
        }

    def memory_review(self, payload: dict[str, Any], *, request_id: Any | None = None) -> dict[str, Any]:
        if payload.get("scope") is None or payload.get("namespace") is None:
            raise InvalidRequestError("memory_review requires both scope and namespace.")
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        action = require_string("action", payload.get("action"))
        if action not in {"list", "accept", "reject"}:
            raise InvalidRequestError("memory_review action must be list, accept, or reject.")
        if action == "list":
            limit = payload.get("limit", 50)
            if not isinstance(limit, int) or limit <= 0:
                raise InvalidRequestError("limit must be a positive integer when provided.")
            return {
                "ok": True,
                "tool": "memory_review",
                "action": "list",
                "scope": scope,
                "namespace": namespace,
                "pending": self.storage.list_pending_reviews(scope, namespace, limit),
            }
        review_id = payload.get("review_id")
        if not isinstance(review_id, str) or not review_id.strip():
            raise InvalidRequestError("review_id is required for accept and reject.")
        promotion = self.storage.get_pending_review(scope, namespace, review_id)
        if promotion is None:
            raise InvalidRequestError("review_id does not identify a pending promotion.")
        if action == "reject":
            self.storage.resolve_review(scope, namespace, review_id, "rejected")
            return {
                "ok": True,
                "tool": "memory_review",
                "action": "reject",
                "review_id": review_id,
                "review_status": "rejected",
            }
        subject = SubjectKey(
            scope=scope,
            namespace=namespace,
            topic=promotion["topic"],
            field=promotion.get("field"),
            memory_type=promotion["proposed_memory_type"],
        )
        provenance = {
            "source": "memory_review",
            "tool": "memory_review",
            "actor": "operator",
            "request_id": review_id,
            "review_id": review_id,
        }
        current_record = self._write_memory(
            subject,
            promotion["value"],
            [],
            provenance,
            None,
            audit_context=self._write_audit_context("memory_review", payload, subject, request_id=request_id),
        )
        self.storage.resolve_review(scope, namespace, review_id, "accepted")
        return {
            "ok": True,
            "tool": "memory_review",
            "action": "accept",
            "review_id": review_id,
            "integration_status": "integrated",
            "subject": subject.as_dict(),
            "event_id": current_record["event_id"],
            "seq": current_record["seq"],
        }

    def memory_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.faults.profile_unavailable:
            raise ProfileUnavailableError("profile assembly cannot run due to implementation outage.")
        scope = validate_scope(payload.get("scope"))
        if scope != "user":
            raise InvalidRequestError("memory_profile requires scope user.")
        namespace = require_string("namespace", payload.get("namespace"))
        read_persona_id = parse_persona_id(payload.get("persona_id"))
        depth = validate_profile_depth(payload.get("depth"))
        budget_tokens = validate_profile_budget(payload.get("budget_tokens"))
        memory_types = payload.get("memory_types")
        if memory_types is None:
            normalized_types = list(PROFILE_MEMORY_TYPES)
        else:
            if not isinstance(memory_types, list):
                raise InvalidRequestError("memory_types must be an array when provided.")
            normalized_types = [validate_memory_type(item) for item in memory_types]

        records = self.storage.current_semantic_records(scope, namespace)
        records = self._filter_records_for_persona(records, read_persona_id)
        records = [record for record in records if record["memory_type"] in normalized_types]
        records.sort(key=lambda record: (profile_assembly_rank(record["memory_type"], record.get("salience")), record["seq"]))

        sections: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        seen_citations: set[tuple[Any, ...]] = set()
        manifest_parts: list[str] = []
        remaining = budget_tokens

        for record in records:
            heading = f"{record['memory_type']}: {record['topic']}/{record['field']}"
            value = record["value"]
            if depth == "summary":
                value = truncate_summary_value(value)
            if isinstance(value, str):
                text = value
            else:
                text = serialize_json(value)
            section_text = f"{heading}: {text}"
            section_len = len(list(section_text))
            if section_len > remaining and manifest_parts:
                break
            if section_len > remaining:
                section_text = truncate_to_budget(section_text, remaining)
                section_len = len(list(section_text))
            if section_len <= 0:
                continue
            citation = citation_from_record(record)
            citation_key = tuple(citation.items())
            section_citations = [citation]
            if citation_key not in seen_citations:
                seen_citations.add(citation_key)
                citations.append(citation)
            sections.append({"heading": heading, "text": section_text, "citations": section_citations})
            manifest_parts.append(section_text)
            remaining -= section_len
            if remaining <= 0:
                break

        manifest = "\n".join(manifest_parts)
        return {
            "ok": True,
            "tool": "memory_profile",
            "scope": scope,
            "namespace": namespace,
            "persona_id": read_persona_id,
            "depth": depth,
            "budget_tokens": budget_tokens,
            "manifest": manifest,
            "sections": sections,
            "citations": citations,
        }

    def memory_reflect(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.faults.reflection_unavailable:
            raise ReflectionUnavailableError("reflection synthesis cannot run because reflection is unsupported.")
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise InvalidRequestError("query must be a non-empty string.")
        reflect_payload = dict(payload)
        reflect_payload["scope"] = scope
        reflect_payload["namespace"] = namespace
        reflect_payload["query"] = query.strip()
        evaluation_mode, evidence, echoed_query = gather_reflect_evidence(self, reflect_payload)
        synthesis = synthesize_reflect(echoed_query, evidence)
        return {
            "ok": True,
            "tool": "memory_reflect",
            "scope": scope,
            "namespace": namespace,
            "evaluation_mode": evaluation_mode,
            "query": echoed_query,
            "synthesis": synthesis,
            "citations": reflect_citations(evidence),
            "evidence_count": len(evidence),
        }

    def memory_query_temporal(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.faults.temporal_query_unavailable:
            raise TemporalQueryUnavailableError("temporal trajectory queries are unavailable.")
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        source_storage, _ = self._storage_for_read(scope, namespace)
        topic = require_string("topic", payload.get("topic"))
        field = payload.get("field")
        if field is not None:
            field = require_string("field", field)
        read_persona_id = parse_persona_id(payload.get("persona_id"))
        requested_types = payload.get("memory_types")
        if requested_types is None:
            normalized_types = list(DEFAULT_GOVERNED_MEMORY_TYPES)
        else:
            if not isinstance(requested_types, list):
                raise InvalidRequestError("memory_types must be an array when provided.")
            normalized_types = [validate_memory_type(item) for item in requested_types]
        audit_points_raw = payload.get("audit_points")
        if not isinstance(audit_points_raw, list) or not audit_points_raw:
            raise InvalidRequestError("audit_points must be a non-empty array.")
        audit_points = [parse_as_of(item) for item in audit_points_raw]
        include_retracted = bool(payload.get("include_retracted", False))
        include_graph_edges = bool(payload.get("include_graph_edges", False)) and self._graph_enabled_for(scope, namespace)

        partition_rows = source_storage.get_topic_partition_rows(
            scope,
            namespace,
            topic,
            field=field,
            memory_types=normalized_types,
            persona_id=read_persona_id,
        )
        subjects: dict[tuple[str, str | None, str, str | None], SubjectKey] = {}
        for row in partition_rows:
            subject = SubjectKey(
                scope=scope,
                namespace=namespace,
                topic=row["topic"],
                field=row["field"],
                memory_type=row["memory_type"],
                persona_id=row["persona_id"] if "persona_id" in row.keys() else None,
            )
            subjects[(subject.topic, subject.field, subject.memory_type, subject.persona_id)] = subject

        graph_snapshots: dict[int, dict[str, Any]] = {}
        if include_graph_edges:
            for index, audit_point in enumerate(audit_points):
                _, evaluation_seq = self._resolve_evaluation(audit_point, storage=source_storage)
                evaluation_time = self._evaluation_time(audit_point, evaluation_seq, storage=source_storage)
                rows = source_storage.get_rows_for_search(
                    scope,
                    namespace,
                    evaluation_seq,
                    False,
                    normalized_types,
                    evaluation_time=evaluation_time,
                    read_persona_id=read_persona_id,
                )
                records = [
                    self._record_for_row(row, status="historical")
                    for row in rows
                    if row["topic"] == topic and (field is None or row["field"] == field)
                ]
                graph_snapshots[index] = derive_graph_snapshot(records)

        trajectories: list[dict[str, Any]] = []
        for subject_key in sorted(subjects.values(), key=lambda item: (item.field or "", item.memory_type, item.persona_id or "")):
            points: list[dict[str, Any]] = []
            visible = False
            for index, audit_point in enumerate(audit_points):
                _, evaluation_seq = self._resolve_evaluation(audit_point, storage=source_storage)
                evaluation_time = self._evaluation_time(audit_point, evaluation_seq, storage=source_storage)
                row = source_storage.get_subject_at(
                    subject_key,
                    evaluation_seq,
                    evaluation_time=evaluation_time,
                    read_persona_id=read_persona_id,
                )
                point: dict[str, Any] = {"audit_point": audit_point, "value": None}
                if row is not None:
                    record = self._record_for_row(
                        row,
                        status="historical",
                    )
                    point.update(
                        {
                            "value": record["value"],
                            "status": record["status"],
                            "seq": record["seq"],
                            "event_id": record["event_id"],
                            "valid_from_seq": record["valid_from_seq"],
                            "valid_to_seq": record["valid_to_seq"],
                            "recorded_at": record["recorded_at"],
                        }
                    )
                    visible = True
                else:
                    last_event = source_storage.get_latest_subject_event(subject_key, evaluation_seq)
                    status = "absent"
                    if last_event is not None and last_event["kind"] == "retract":
                        status = "retracted" if include_retracted else "absent"
                        if include_retracted:
                            point["seq"] = last_event["seq"]
                            point["event_id"] = last_event["event_id"]
                            point["valid_from_seq"] = None
                            point["valid_to_seq"] = None
                            point["recorded_at"] = last_event["recorded_at"]
                    point.setdefault("seq", None)
                    point.setdefault("event_id", None)
                    point.setdefault("valid_from_seq", None)
                    point.setdefault("valid_to_seq", None)
                    point.setdefault("recorded_at", None)
                    point["status"] = status
                if include_graph_edges:
                    point["graph_snapshot"] = graph_snapshots[index]
                points.append(point)
            if visible or (include_retracted and any(point["status"] == "retracted" for point in points)):
                trajectories.append(
                    {
                        "subject": subject_key.as_dict(),
                        "points": points,
                    }
                )

        return {
            "ok": True,
            "tool": "memory_query_temporal",
            "scope": scope,
            "namespace": namespace,
            "topic": topic,
            "trajectories": trajectories,
        }

    def memory_audit_export(self, payload: dict[str, Any], *, request_id: Any | None = None) -> dict[str, Any]:
        del request_id
        if self.faults.audit_export_unavailable:
            raise AuditExportUnavailableError("audit export is unavailable.")
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        since = self._parse_audit_bound("since", payload.get("since"))
        until = self._parse_audit_bound("until", payload.get("until"))
        self._validate_audit_bounds(since, until)
        event_kinds = payload.get("event_kinds") or ["read", "write", "delete"]
        if not isinstance(event_kinds, list) or not event_kinds:
            raise InvalidRequestError("event_kinds must be a non-empty array when provided.")
        normalized_event_kinds = []
        for item in event_kinds:
            if item not in {"read", "write", "delete"}:
                raise InvalidRequestError("event_kinds entries must be read, write, or delete.")
            normalized_event_kinds.append(str(item))
        fmt = payload.get("format", "jsonl")
        if fmt not in {"json", "jsonl"}:
            raise InvalidRequestError("format must be json or jsonl.")
        limit = payload.get("limit", 1000)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise InvalidRequestError("limit must be a positive integer when provided.")

        records, truncated = self.storage.list_audit_events(
            scope,
            namespace,
            since_seq=since.get("seq") if since and "seq" in since else None,
            until_seq=until.get("seq") if until and "seq" in until else None,
            since_recorded_at=since.get("recorded_at") if since and "recorded_at" in since else None,
            until_recorded_at=until.get("recorded_at") if until and "recorded_at" in until else None,
            event_kinds=normalized_event_kinds,
            limit=limit,
        )
        export_id = export_id_for(
            scope=scope,
            namespace=namespace,
            fmt=fmt,
            limit=limit,
            event_count=len(records),
            first_audit_id=records[0]["audit_id"] if records else None,
            last_audit_id=records[-1]["audit_id"] if records else None,
            since=since,
            until=until,
        )
        return {
            "ok": True,
            "tool": "memory_audit_export",
            "scope": scope,
            "namespace": namespace,
            "format": fmt,
            "event_count": len(records),
            "records": records if fmt == "json" else to_jsonl(records),
            "truncated": truncated,
            "export_id": export_id,
        }

    def update_session_summary(
        self,
        scope: str,
        namespace: str,
        body: str,
        *,
        persona_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "scope": scope,
            "namespace": namespace,
            "memory_type": "procedure",
            "topic": "session_summary",
            "field": "body",
            "value": body,
            "persona_id": persona_id,
            "provenance": provenance
            or {
                "source": "session_summary",
                "tool": "session_summary",
                "actor": "system",
                "request_id": f"summary-{namespace}",
            },
        }
        return self.memory_remember(payload)

    def run_profile_engine(
        self,
        scope: str,
        namespace: str,
        *,
        persona_id: str | None = None,
        require_enabled: bool = True,
        async_run: bool = False,
        source_scope: str | None = None,
        source_namespace: str | None = None,
    ) -> dict[str, Any]:
        if async_run:
            self.profile_engine.run_async(
                scope,
                namespace,
                persona_id=persona_id,
                source_scope=source_scope,
                source_namespace=source_namespace,
            )
            return {"status": "enqueued"}
        return self.profile_engine.run_sync(
            scope,
            namespace,
            persona_id=persona_id,
            require_enabled=require_enabled,
            source_scope=source_scope,
            source_namespace=source_namespace,
        )

    @staticmethod
    def build_session_summary_body(messages: list[dict[str, Any]]) -> str:
        snippets: list[str] = []
        for message in messages:
            role = message.get("role", "unknown")
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                snippets.append(f"{role}: {content.strip()}")
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                if parts:
                    snippets.append(f"{role}: {' '.join(parts)}")
        return "\n".join(snippets)

    def trigger_session_summary_async(self, scope: str, namespace: str, body: str) -> None:
        thread = threading.Thread(
            target=self._trigger_session_summary_safe,
            args=(scope, namespace, body),
            name=f"session-summary-{namespace}",
            daemon=True,
        )
        thread.start()

    def _trigger_session_summary_safe(self, scope: str, namespace: str, body: str) -> None:
        try:
            if body.strip():
                self.update_session_summary(scope, namespace, body)
        except Exception:
            pass

    def rebuild_indexes(self, scope: str | None = None, namespace: str | None = None) -> None:
        targets = []
        if scope and namespace:
            targets.append((scope, namespace))
        elif namespace:
            for candidate_scope in SUPPORTED_SCOPES:
                targets.append((candidate_scope, namespace))
        else:
            targets.extend(self.storage.all_namespaces())
        deduped_targets = []
        seen: set[tuple[str, str]] = set()
        for target in targets:
            if target in seen:
                continue
            seen.add(target)
            deduped_targets.append(target)
        for current_scope, current_namespace in deduped_targets:
            records = self.storage.namespace_records_for_index(current_scope, current_namespace)
            self.index_manager.rebuild(current_scope, current_namespace, records)
            self.storage.upsert_namespace_status(current_scope, current_namespace, "current", utc_now_rfc3339())

    def set_index_availability(self, namespace: str, available: bool, scope: str | None = None) -> None:
        scopes = [scope] if scope else list(SUPPORTED_SCOPES)
        for current_scope in scopes:
            if available:
                self.rebuild_indexes(scope=current_scope, namespace=namespace)
            else:
                self.storage.upsert_namespace_status(current_scope, namespace, "unavailable", None)

    def run_consolidation(self, scope: str, namespace: str) -> dict[str, Any]:
        result = self._execute_consolidation(scope, namespace, dry_run=False)
        return {
            "available": True,
            "last_run_at": result["last_run_at"],
            "scope": scope,
            "namespace": namespace,
            "stats": result["stats"],
            "promotions": result["promotions"],
        }

    def rebuild_graph(self, scope: str, namespace: str) -> dict[str, Any]:
        snapshot = self.graph_index.rebuild(scope, namespace, self.storage.current_semantic_records(scope, namespace))
        return {
            "scope": scope,
            "namespace": namespace,
            "entity_count": len(snapshot["entities"]),
            "edge_count": len(snapshot["edges"]),
        }

    def set_graph_enabled(self, scope: str, namespace: str, enabled: bool) -> dict[str, Any]:
        self._graph_enabled[(scope, namespace)] = enabled
        return {
            "scope": scope,
            "namespace": namespace,
            "enabled": enabled,
        }

    def configure_pii_scan(self, scope: str, namespace: str, payload: dict[str, Any]) -> dict[str, Any]:
        config = PiiScanConfig.from_dict(payload)
        if config.policy not in {"redact", "block", "annotate"}:
            raise InvalidRequestError("pii_scan policy must be redact, block, or annotate.")
        self.storage.set_pii_scan_config(scope, namespace, config.as_dict())
        return self.storage.get_pii_scan_config(scope, namespace)

    def configure_audit(self, scope: str, namespace: str, *, enabled: bool, fail_closed: bool = False) -> dict[str, Any]:
        self.storage.set_audit_config(scope, namespace, enabled, fail_closed)
        return self.storage.get_audit_config(scope, namespace)

    def set_retention_policy(self, scope: str, namespace: str, memory_type: str, ttl_seconds: int | None) -> dict[str, Any]:
        normalized_type = validate_retention_memory_type(memory_type)
        normalized_ttl = normalize_retention_ttl(ttl_seconds) if ttl_seconds is not None else None
        self.storage.set_retention_policy(scope, namespace, normalized_type, normalized_ttl)
        return {
            "scope": scope,
            "namespace": namespace,
            "memory_type": normalized_type,
            "ttl_seconds": normalized_ttl,
            "policies": self.storage.get_retention_policies(scope, namespace),
        }

    def replace_retention_policies(self, scope: str, namespace: str, policies: dict[str, int]) -> dict[str, Any]:
        existing = {item["memory_type"] for item in self.storage.get_retention_policies(scope, namespace)}
        for memory_type in sorted(existing - set(policies.keys())):
            self.storage.set_retention_policy(scope, namespace, memory_type, None)
        for memory_type, ttl_seconds in policies.items():
            self.storage.set_retention_policy(scope, namespace, memory_type, normalize_retention_ttl(ttl_seconds))
        return {
            "scope": scope,
            "namespace": namespace,
            "enabled": bool(policies),
            "policies": self.storage.get_retention_policies(scope, namespace),
        }

    def run_retention(self, scope: str, namespace: str, *, now: str | None = None) -> dict[str, Any]:
        effective_now = retention_now(now)
        policies = {item["memory_type"]: item["ttl_seconds"] for item in self.storage.get_retention_policies(scope, namespace)}
        if not policies:
            return {
                "scope": scope,
                "namespace": namespace,
                "evaluated_at": effective_now,
                "evicted_count": 0,
                "evicted_subjects": [],
            }
        evicted_subjects: list[dict[str, Any]] = []
        with self.storage.transaction() as cursor:
            for row in self.storage.get_rows_for_retention(scope, namespace):
                ttl_seconds = policies.get(row["memory_type"])
                if ttl_seconds is None:
                    continue
                if "legal_hold" in row.keys() and bool(row["legal_hold"]):
                    continue
                recorded_at = parse_rfc3339_utc(str(row["recorded_at"]))
                expires_at = recorded_at + timedelta(seconds=ttl_seconds)
                if effective_now < expires_at.isoformat().replace("+00:00", "Z"):
                    continue
                subject = SubjectKey(
                    scope=row["scope"],
                    namespace=row["namespace"],
                    topic=row["topic"],
                    field=row["field"],
                    memory_type=row["memory_type"],
                    persona_id=row["persona_id"] if "persona_id" in row.keys() else None,
                )
                seq = self.storage.next_seq(cursor)
                recorded_at_value = self.storage.next_recorded_at(cursor, effective_now)
                event_id = f"evt_{seq:06d}"
                event = {
                    "event_id": event_id,
                    "seq": seq,
                    "recorded_at": recorded_at_value,
                    "scope": subject.scope,
                    "namespace": subject.namespace,
                    "kind": "retention_evict",
                    "memory_type": subject.memory_type,
                    "topic": subject.topic,
                    "field": subject.field,
                    "value_json": None,
                    "episode_id": None,
                    "extends_json": serialize_json([]),
                    "provenance_json": serialize_json(
                        {
                            "source": "retention",
                            "tool": "retention_control",
                            "actor": "operator",
                            "request_id": f"retention-{scope}-{namespace}-{seq}",
                        }
                    ),
                    "persona_id": subject.persona_id,
                    "legal_hold": False,
                }
                self.storage.insert_wal_event(cursor, event)
                self.storage.close_open_versions(cursor, subject, seq)
                evicted_subjects.append(subject.as_dict())
        if evicted_subjects:
            self._post_commit_index_sync(scope, namespace)
            self._post_commit_fleet_sync(scope, namespace)
        return {
            "scope": scope,
            "namespace": namespace,
            "evaluated_at": effective_now,
            "evicted_count": len(evicted_subjects),
            "evicted_subjects": evicted_subjects,
        }

    def set_fleet_status(self, scope: str, namespace: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace)).as_dict()
        merged = dict(current)
        merged.update(payload)
        merged["mode"] = validate_fleet_mode(merged.get("mode", "local"))
        lag_policy = self._normalize_fleet_lag_policy(payload.get("lag_policy", self._fleet_lag_policy.get((scope, namespace))))
        backend_key = (scope, namespace)
        backend_config = FleetBackendConfig.from_payload(
            payload,
            current=self._fleet_backend_config.get(backend_key),
        )
        if merged["mode"] == "local":
            merged["backend_reachable"] = False
        status = FleetStatus.from_dict(merged)
        self.storage.set_fleet_status(scope, namespace, status.as_dict())
        self._fleet_lag_policy[(scope, namespace)] = lag_policy
        if backend_config is None:
            self._fleet_backend_config.pop(backend_key, None)
        else:
            self._fleet_backend_config[backend_key] = backend_config
        if status.mode == "fleet_replica" and backend_config is not None:
            self._refresh_fleet_status(scope, namespace)
        return self._fleet_status_response(scope, namespace)

    def fleet_push(
        self,
        scope: str,
        namespace: str,
        *,
        since_seq: int | None = None,
        until_seq: int | None = None,
    ) -> dict[str, Any]:
        self._require_fleet_replica_mode(scope, namespace)
        since_seq = self._optional_seq_bound("since_seq", since_seq)
        until_seq = self._optional_seq_bound("until_seq", until_seq)
        if since_seq is not None and until_seq is not None and since_seq >= until_seq:
            raise InvalidRequestError("since_seq must be less than until_seq when both are provided.")
        try:
            backend_storage = self._fleet_backend_storage(scope, namespace)
            remote_high_water = backend_storage.get_high_water_seq(scope, namespace)
            lower_bound = max(since_seq or 0, remote_high_water + 1)
            rows = self.storage.get_wal_event_rows(scope, namespace, since_seq=lower_bound, until_seq=until_seq)
            inserted = backend_storage.import_wal_event_rows(rows)
            backend_storage.replace_semantic_version_rows(
                scope,
                namespace,
                self.storage.get_semantic_version_rows(scope, namespace),
            )
            status_snapshot = self.storage.get_namespace_status_snapshot(scope, namespace)
            if status_snapshot is not None:
                backend_storage.replace_namespace_status(status_snapshot)
            self._refresh_fleet_status(scope, namespace)
        except MemoryServiceError:
            raise
        except Exception as exc:
            self._persist_fleet_runtime_status(scope, namespace, backend_reachable=False)
            raise FleetSyncUnavailableError(f"fleet replica push failed: {exc}") from exc
        response = self._fleet_status_response(scope, namespace)
        response.update(
            {
                "scope": scope,
                "namespace": namespace,
                "pushed_count": inserted,
                "pushed_seq_range": [rows[0]["seq"], rows[-1]["seq"]] if rows else None,
            }
        )
        return response

    def fleet_pull(
        self,
        scope: str,
        namespace: str,
        *,
        since_seq: int | None = None,
        until_seq: int | None = None,
    ) -> dict[str, Any]:
        self._require_fleet_replica_mode(scope, namespace)
        since_seq = self._optional_seq_bound("since_seq", since_seq)
        until_seq = self._optional_seq_bound("until_seq", until_seq)
        if since_seq is not None and until_seq is not None and since_seq >= until_seq:
            raise InvalidRequestError("since_seq must be less than until_seq when both are provided.")
        try:
            backend_storage = self._fleet_backend_storage(scope, namespace)
            local_high_water = self.storage.get_high_water_seq(scope, namespace)
            lower_bound = max(since_seq or 0, local_high_water + 1)
            rows = backend_storage.get_wal_event_rows(scope, namespace, since_seq=lower_bound, until_seq=until_seq)
            inserted = self.storage.import_wal_event_rows(rows)
            if rows:
                self.storage.replace_semantic_version_rows(
                    scope,
                    namespace,
                    backend_storage.get_semantic_version_rows(scope, namespace),
                )
                status_snapshot = backend_storage.get_namespace_status_snapshot(scope, namespace)
                if status_snapshot is not None:
                    self.storage.replace_namespace_status(status_snapshot)
                self._post_commit_index_sync(scope, namespace)
            self._refresh_fleet_status(scope, namespace)
        except MemoryServiceError:
            raise
        except Exception as exc:
            self._persist_fleet_runtime_status(scope, namespace, backend_reachable=False)
            raise FleetSyncUnavailableError(f"fleet replica pull failed: {exc}") from exc
        response = self._fleet_status_response(scope, namespace)
        response.update(
            {
                "scope": scope,
                "namespace": namespace,
                "pulled_count": inserted,
                "pulled_seq_range": [rows[0]["seq"], rows[-1]["seq"]] if rows else None,
            }
        )
        return response

    def _execute_consolidation(self, scope: str, namespace: str, *, dry_run: bool) -> dict[str, Any]:
        status = self.storage.get_namespace_status(scope, namespace)
        last_run_at = status["last_consolidation_at"]
        base_records = self.storage.current_semantic_records(scope, namespace)
        existing_hidden = self.storage.get_consolidation_hidden(scope, namespace)
        existing_summaries = self.storage.get_consolidation_summary_records(scope, namespace)
        plan = plan_safe_merge(base_records, existing_hidden, existing_summaries)
        if not dry_run:
            self.storage.replace_consolidation_state(
                scope,
                namespace,
                plan["hidden_units"],
                plan["summary_units"],
                plan["promotions"],
            )
            last_run_at = utc_now_rfc3339()
            self.storage.set_last_consolidation(scope, namespace, last_run_at)
            self._post_commit_index_sync(scope, namespace)
        return {
            "last_run_at": last_run_at,
            "stats": plan["stats"],
            "promotions": plan["promotions"],
        }

    def _bootstrap_schema_mode(self) -> None:
        if self.schema_mode in {"auto", "fresh"}:
            return
        if self.schema_mode != "upgrade_from_v1_0_1":
            raise InvalidRequestError("schema mode must be auto, fresh, or upgrade_from_v1_0_1.")
        if self.upgrade_fixture != "v1_0_1_minimal":
            raise InvalidRequestError("upgrade_from_v1_0_1 requires fixture v1_0_1_minimal.")
        if self.storage.get_high_water_seq("repository", "upgrade-fixture") > 0:
            return
        self._write_memory(
            SubjectKey(
                scope="repository",
                namespace="upgrade-fixture",
                topic="migrated_profile",
                field="city",
                memory_type="fact",
            ),
            "Berlin",
            [],
            {"source": "migration", "tool": "fixture", "actor": "system", "request_id": "upgrade-fixture"},
            None,
        )

    def _rebuild_all_indexes(self) -> None:
        for scope, namespace in self.storage.all_namespaces():
            self.rebuild_indexes(scope=scope, namespace=namespace)

    def _run_pii_scan(self, subject: SubjectKey, value: Any):
        config = PiiScanConfig.from_dict(self.storage.get_pii_scan_config(subject.scope, subject.namespace))
        return self.pii_scanner.scan(value, memory_type=subject.memory_type, config=config)

    def _write_audit_context(
        self,
        tool: str,
        payload: dict[str, Any],
        subject: SubjectKey,
        *,
        request_id: Any | None = None,
    ) -> dict[str, Any] | None:
        audit_config = AuditConfig.from_dict(self.storage.get_audit_config(subject.scope, subject.namespace))
        if not audit_config.enabled:
            return None
        return {
            "tool": tool,
            "event_kind": "write" if tool in {"memory_remember", "memory_review"} else "delete",
            "scope": subject.scope,
            "namespace": subject.namespace,
            "subject": subject.as_dict(),
            "actor": build_actor(payload, request_id),
            "fail_closed": audit_config.fail_closed,
        }

    def _emit_read_audit(
        self,
        tool_name: str,
        payload: dict[str, Any],
        response: dict[str, Any],
        *,
        request_id: Any | None = None,
    ) -> None:
        if tool_name not in {
            "memory_get",
            "memory_search",
            "memory_profile",
            "memory_reflect",
            "memory_query_temporal",
            "memory_audit_export",
        }:
            return
        if not response.get("ok", False):
            return
        scope = response.get("scope")
        namespace = response.get("namespace")
        if not isinstance(scope, str) or not isinstance(namespace, str):
            return
        audit_config = AuditConfig.from_dict(self.storage.get_audit_config(scope, namespace))
        if not audit_config.enabled:
            return
        subject = None
        if tool_name == "memory_get" and isinstance(response.get("record"), dict):
            subject = {
                "topic": response["record"].get("topic"),
                "field": response["record"].get("field"),
                "memory_type": response["record"].get("memory_type"),
            }
        elif tool_name == "memory_query_temporal":
            subject = {"topic": response.get("topic"), "field": payload.get("field"), "memory_type": None}
        try:
            self._append_audit_event(
                {
                    "event_kind": "read",
                    "scope": scope,
                    "namespace": namespace,
                    "tool": tool_name,
                    "actor": build_actor(payload, request_id),
                    "subject": subject,
                    "wal_seq": None,
                    "wal_event_id": None,
                    "outcome": "success",
                    "error_code": None,
                }
            )
        except Exception:
            if audit_config.fail_closed:
                raise IntegrationFailedError("audit logging failed while fail_closed was enabled.")

    def _append_audit_event(self, event: dict[str, Any]) -> None:
        with self.storage.transaction() as cursor:
            audit_id = self.storage.next_audit_id(cursor, event["scope"], event["namespace"])
            self.storage.insert_audit_event(
                cursor,
                event
                | {
                    "audit_id": audit_id,
                    "recorded_at": utc_now_rfc3339(),
                },
            )

    def _parse_audit_bound(self, name: str, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise InvalidRequestError(f"{name} must be an integer seq, RFC3339 UTC string, or an as_of-style object.")
        if isinstance(value, int):
            return {"seq": value}
        if isinstance(value, str):
            parse_rfc3339_utc(value)
            return {"recorded_at": value}
        if isinstance(value, dict):
            return parse_as_of(value)
        raise InvalidRequestError(f"{name} must be an integer seq, RFC3339 UTC string, or an as_of-style object.")

    def _validate_audit_bounds(self, since: dict[str, Any] | None, until: dict[str, Any] | None) -> None:
        if since is None or until is None:
            return
        if "seq" in since and "seq" in until and since["seq"] > until["seq"]:
            raise InvalidRequestError("since must not resolve after until.")
        if "recorded_at" in since and "recorded_at" in until and since["recorded_at"] > until["recorded_at"]:
            raise InvalidRequestError("since must not resolve after until.")

    def _write_memory(
        self,
        subject: SubjectKey,
        value: Any,
        extends: list[dict[str, str | None]],
        provenance: dict[str, Any],
        episode_id: str | None,
        *,
        expires_at: str | None = None,
        blocks_actions: list[str] | None = None,
        observation: dict[str, Any] | None = None,
        share_to: list[str] | None = None,
        derived_from: list[str] | None = None,
        salience: float | None = None,
        legal_hold: bool = False,
        audit_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if subject.memory_type == "episode" and extends:
            raise InvalidRequestError("episode records are append-only and cannot declare extends relationships.")
        if self.faults.integration_fail_next:
            self.faults.integration_fail_next = False
            raise IntegrationFailedError("fault injection forced the next integration to fail.")

        kind = self._event_kind(subject.memory_type)
        with self.storage.transaction() as cursor:
            seq = self.storage.next_seq(cursor)
            recorded_at = self.storage.next_recorded_at(cursor, utc_now_rfc3339())
            event_id = f"evt_{seq:06d}"
            event = {
                "event_id": event_id,
                "seq": seq,
                "recorded_at": recorded_at,
                "scope": subject.scope,
                "namespace": subject.namespace,
                "kind": kind,
                "memory_type": subject.memory_type,
                "topic": subject.topic,
                "field": subject.field,
                "value_json": serialize_json(value),
                "episode_id": episode_id,
                "extends_json": serialize_json(extends),
                "provenance_json": serialize_json(provenance),
                "expires_at": expires_at,
                "blocks_actions_json": serialize_json(blocks_actions) if blocks_actions else None,
                "observation_json": serialize_json(observation) if observation else None,
                "persona_id": subject.persona_id,
                "share_to_json": serialize_json(share_to) if share_to else None,
                "derived_from_json": serialize_json(derived_from) if derived_from else None,
                "legal_hold": legal_hold,
            }
            self.storage.insert_wal_event(cursor, event)

            if subject.memory_type in SUPERSEDING_TYPES or subject.memory_type in VERSIONED_TYPES:
                self.storage.close_open_versions(cursor, subject, seq)

            bindings = self._bindings_for_extends(subject.scope, subject.namespace, extends)
            default_salience = None if subject.memory_type == "episode" else 1.0
            if salience is None and subject.memory_type == "belief" and provenance.get("source") == "profile_engine":
                from .domain import PROFILE_ENGINE_BELIEF_SALIENCE

                salience = PROFILE_ENGINE_BELIEF_SALIENCE
            version = {
                "scope": subject.scope,
                "namespace": subject.namespace,
                "topic": subject.topic,
                "field": subject.field,
                "memory_type": subject.memory_type,
                "value_json": serialize_json(value),
                "seq": seq,
                "valid_from_seq": seq,
                "valid_to_seq": None,
                "recorded_at": recorded_at,
                "episode_id": episode_id,
                "event_id": event_id,
                "provenance_json": serialize_json(provenance),
                "salience": salience if salience is not None else default_salience,
                "extends_json": serialize_json(extends),
                "bindings_json": serialize_json(bindings),
                "layer": "episode" if subject.memory_type == "episode" else "semantic_unit",
                "expires_at": expires_at,
                "blocks_actions_json": serialize_json(blocks_actions) if blocks_actions else None,
                "persona_id": subject.persona_id,
                "share_to_json": serialize_json(share_to) if share_to else None,
                "derived_from_json": serialize_json(derived_from) if derived_from else None,
                "legal_hold": legal_hold,
            }
            self.storage.insert_semantic_version(cursor, version)
            self._propagate_dependents(cursor, event, subject)
            if audit_context is not None:
                try:
                    self.storage.insert_audit_event(
                        cursor,
                        {
                            "audit_id": self.storage.next_audit_id(cursor, subject.scope, subject.namespace),
                            "recorded_at": recorded_at,
                            "event_kind": audit_context["event_kind"],
                            "scope": subject.scope,
                            "namespace": subject.namespace,
                            "tool": audit_context["tool"],
                            "actor": audit_context["actor"],
                            "subject": audit_context["subject"],
                            "wal_seq": seq,
                            "wal_event_id": event_id,
                            "outcome": "success",
                            "error_code": None,
                        },
                    )
                except Exception:
                    if audit_context.get("fail_closed"):
                        raise

        self._post_commit_index_sync(subject.scope, subject.namespace)
        self._post_commit_fleet_sync(subject.scope, subject.namespace)
        current_row = self.storage.get_subject_at_seq(subject, seq)
        if current_row is None:
            raise IntegrationFailedError("semantic integration did not produce a current version.")
        return self._record_for_row(current_row, status="current")

    def _retract_memory(
        self,
        subject: SubjectKey,
        provenance: dict[str, Any],
        *,
        audit_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.faults.integration_fail_next:
            self.faults.integration_fail_next = False
            raise IntegrationFailedError("fault injection forced the next integration to fail.")
        open_subject = self.storage.get_open_subject(subject)
        if open_subject is None:
            raise NotFoundError("No current value exists for the requested subject at the requested recall point.")
        if "legal_hold" in open_subject.keys() and bool(open_subject["legal_hold"]):
            raise LegalHoldError("The requested subject is under legal hold and cannot be retracted.")
        with self.storage.transaction() as cursor:
            seq = self.storage.next_seq(cursor)
            recorded_at = self.storage.next_recorded_at(cursor, utc_now_rfc3339())
            event_id = f"evt_{seq:06d}"
            event = {
                "event_id": event_id,
                "seq": seq,
                "recorded_at": recorded_at,
                "scope": subject.scope,
                "namespace": subject.namespace,
                "kind": "retract",
                "memory_type": subject.memory_type,
                "topic": subject.topic,
                "field": subject.field,
                "value_json": None,
                "episode_id": None,
                "extends_json": serialize_json([]),
                "provenance_json": serialize_json(provenance),
                "persona_id": subject.persona_id,
                "legal_hold": False,
            }
            self.storage.insert_wal_event(cursor, event)
            self.storage.close_open_versions(cursor, subject, seq)
            self._propagate_dependents(cursor, event, subject)
            if audit_context is not None:
                try:
                    self.storage.insert_audit_event(
                        cursor,
                        {
                            "audit_id": self.storage.next_audit_id(cursor, subject.scope, subject.namespace),
                            "recorded_at": recorded_at,
                            "event_kind": audit_context["event_kind"],
                            "scope": subject.scope,
                            "namespace": subject.namespace,
                            "tool": audit_context["tool"],
                            "actor": audit_context["actor"],
                            "subject": audit_context["subject"],
                            "wal_seq": seq,
                            "wal_event_id": event_id,
                            "outcome": "success",
                            "error_code": None,
                        },
                    )
                except Exception:
                    if audit_context.get("fail_closed"):
                        raise
        self._post_commit_index_sync(subject.scope, subject.namespace)
        self._post_commit_fleet_sync(subject.scope, subject.namespace)
        return event

    def _propagate_dependents(self, cursor: sqlite3.Cursor, parent_event: dict[str, Any], subject: SubjectKey) -> None:
        if subject.memory_type == "episode":
            return
        dependents = self.storage.get_dependents_for_parent(subject.scope, subject.namespace, subject.topic, subject.field)
        parent_persona = subject.persona_id
        for dependent in dependents:
            if dependent["memory_type"] == "episode":
                continue
            dep_persona = dependent["persona_id"] if "persona_id" in dependent.keys() else None
            if parent_persona != dep_persona:
                continue
            dependent_subject = SubjectKey(
                scope=dependent["scope"],
                namespace=dependent["namespace"],
                topic=dependent["topic"],
                field=dependent["field"],
                memory_type=dependent["memory_type"],
                persona_id=dep_persona,
            )
            self.storage.close_open_versions(cursor, dependent_subject, parent_event["seq"])
            extends = self._deserialize_json(dependent["extends_json"]) or []
            bindings = self.storage.row_bindings(dependent)
            key = parent_edge_key(subject.topic, subject.field)
            old_binding = bindings.get(key)
            new_value = self._deserialize_json(dependent["value_json"])
            parent_value = self._deserialize_json(parent_event["value_json"])
            parent_text = self._serialize_value_for_binding(parent_value)
            if old_binding is not None:
                new_value = replace_bound_value(new_value, old_binding, parent_text)
            elif parent_text is not None:
                new_value = replace_bound_value(new_value, None, parent_text)
            new_bindings = self._bindings_for_extends(subject.scope, subject.namespace, extends, override_key=key, override_value=parent_text)
            derived_provenance = {
                "source": "propagation",
                "tool": "memory_remember",
                "actor": "system",
                "request_id": parent_event["event_id"],
                "trigger_event_id": parent_event["event_id"],
                "derived_from": dependent["event_id"],
            }
            self.storage.insert_semantic_version(
                cursor,
                {
                    "scope": dependent_subject.scope,
                    "namespace": dependent_subject.namespace,
                    "topic": dependent_subject.topic,
                    "field": dependent_subject.field,
                    "memory_type": dependent_subject.memory_type,
                    "value_json": serialize_json(new_value),
                    "seq": parent_event["seq"],
                    "valid_from_seq": parent_event["seq"],
                    "valid_to_seq": None,
                    "recorded_at": parent_event["recorded_at"],
                    "episode_id": dependent["episode_id"],
                    "event_id": parent_event["event_id"],
                    "provenance_json": serialize_json(derived_provenance),
                    "salience": dependent["salience"],
                    "extends_json": dependent["extends_json"],
                    "bindings_json": serialize_json(new_bindings),
                    "layer": dependent["layer"],
                    "expires_at": dependent["expires_at"] if "expires_at" in dependent.keys() else None,
                    "blocks_actions_json": dependent["blocks_actions_json"] if "blocks_actions_json" in dependent.keys() else None,
                    "persona_id": dep_persona,
                    "share_to_json": dependent["share_to_json"] if "share_to_json" in dependent.keys() else None,
                    "derived_from_json": dependent["derived_from_json"] if "derived_from_json" in dependent.keys() else None,
                    "legal_hold": bool(dependent["legal_hold"]) if "legal_hold" in dependent.keys() else False,
                },
            )

    def _bindings_for_extends(
        self,
        scope: str,
        namespace: str,
        extends: list[dict[str, str | None]],
        override_key: str | None = None,
        override_value: str | None = None,
    ) -> dict[str, str | None]:
        parent_rows = self.storage.get_current_parent_rows(scope, namespace, extends)
        bindings: dict[str, str | None] = {}
        for edge in extends:
            key = parent_edge_key(str(edge["topic"]), edge.get("field"))
            if key == override_key:
                bindings[key] = override_value
                continue
            parent = parent_rows.get(key)
            bindings[key] = self._serialize_value_for_binding(self._deserialize_json(parent["value_json"])) if parent else None
        return bindings

    def _resolve_evaluation(
        self,
        as_of: dict[str, Any] | None,
        *,
        storage: Storage | None = None,
    ) -> tuple[str, int | None]:
        source_storage = storage or self.storage
        if as_of is None:
            return "current", None
        if "seq" in as_of:
            return "as_of", as_of["seq"]
        return "as_of", source_storage.resolve_recorded_at_seq(as_of["recorded_at"])

    def _evaluation_time(
        self,
        as_of: dict[str, Any] | None,
        evaluation_seq: int | None,
        *,
        storage: Storage | None = None,
    ) -> str:
        source_storage = storage or self.storage
        as_of_recorded_at = as_of.get("recorded_at") if as_of else None
        return source_storage.get_evaluation_time(evaluation_seq, as_of_recorded_at)

    def _record_for_row(self, row: sqlite3.Row, status: str) -> dict[str, Any]:
        return self.storage.row_to_record(row, status=status)

    def _search_text_for_row(self, row: sqlite3.Row) -> str:
        record = self._record_for_row(row, status="current")
        from .domain import build_search_text

        return build_search_text(record)

    def _candidate_matches_subject(self, candidate: dict[str, Any], subject_filter: dict[str, str]) -> bool:
        for key, expected in subject_filter.items():
            if candidate.get(key) != expected:
                return False
        return True

    def _match_reason_with_subject(self, base_reason: str, subject_filter: dict[str, str] | None) -> str:
        if not subject_filter:
            return base_reason
        ordered_parts = [f"{key}={subject_filter[key]}" for key in ("topic", "field") if key in subject_filter]
        return f"{base_reason}; subject filter matched: {', '.join(ordered_parts)}"

    def _filter_records_for_persona(
        self,
        records: list[dict[str, Any]],
        read_persona_id: str | None,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for record in records:
            stored_persona = record.get("persona_id")
            share_to = record.get("share_to")
            if record_visible_to_persona(stored_persona, share_to, read_persona_id):
                filtered.append(record)
        return filtered

    def _filter_records_for_scope_namespace(
        self,
        records: list[dict[str, Any]],
        scope: str,
        namespace: str,
    ) -> list[dict[str, Any]]:
        return [record for record in records if self._record_matches_scope_namespace(record, scope, namespace)]

    def _record_matches_scope_namespace(self, record: dict[str, Any], scope: str, namespace: str) -> bool:
        return record.get("scope") == scope and record.get("namespace") == namespace

    def _row_matches_scope_namespace(self, row: sqlite3.Row, scope: str, namespace: str) -> bool:
        return row["scope"] == scope and row["namespace"] == namespace

    def _graph_enabled_for(self, scope: str, namespace: str) -> bool:
        return self._graph_enabled.get((scope, namespace), False)

    def _normalize_fleet_lag_policy(self, value: Any) -> str:
        if value is None:
            return "fallback_local"
        if value == "fallback":
            return "fallback_local"
        if value not in {"fallback_local", "error"}:
            raise InvalidRequestError("lag_policy must be fallback_local or error.")
        return str(value)

    def _optional_seq_bound(self, name: str, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidRequestError(f"{name} must be a non-negative integer when provided.")
        return value

    def _storage_for_read(self, scope: str, namespace: str) -> tuple[Storage, str]:
        status = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace))
        if status.mode != "fleet_replica" or not status.serve_reads_from_replica:
            return self.storage, "local"
        backend_storage = self._fleet_backend_storage(scope, namespace, required=False)
        lag_policy = self._fleet_lag_policy.get((scope, namespace), "fallback_local")
        if backend_storage is None:
            if (
                status.max_staleness_seq is not None
                and status.replica_lag_seq is not None
                and status.replica_lag_seq > status.max_staleness_seq
                and lag_policy == "error"
            ):
                raise FleetSyncUnavailableError("fleet replica backend is unavailable under the configured operator policy.")
            return self.storage, "local"
        status = self._refresh_fleet_status(scope, namespace)
        if not status.backend_reachable:
            if lag_policy == "error":
                raise FleetSyncUnavailableError("fleet replica backend is unavailable under the configured operator policy.")
            return self.storage, "local"
        if (
            status.max_staleness_seq is not None
            and status.replica_lag_seq is not None
            and status.replica_lag_seq > status.max_staleness_seq
        ):
            if lag_policy == "error":
                raise FleetSyncUnavailableError(
                    "fleet replica lag exceeds max_staleness_seq under the configured operator policy."
                )
            return self.storage, "local"
        return backend_storage, "replica"

    def _fleet_status_response(self, scope: str, namespace: str) -> dict[str, Any]:
        response = self.storage.get_fleet_status(scope, namespace) | {
            "lag_policy": self._fleet_lag_policy.get((scope, namespace), "fallback_local")
        }
        backend_config = self._fleet_backend_config.get((scope, namespace))
        if backend_config is not None:
            response["backend_path"] = backend_config.backend_path
            response["sync_on_write"] = backend_config.sync_on_write
        return response

    def _persist_fleet_runtime_status(
        self,
        scope: str,
        namespace: str,
        *,
        backend_reachable: bool,
        last_synced_seq: int | None = None,
        replica_lag_seq: int | None = None,
    ) -> FleetStatus:
        current = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace)).as_dict()
        current["backend_reachable"] = backend_reachable
        current["last_synced_seq"] = last_synced_seq
        current["replica_lag_seq"] = replica_lag_seq
        status = FleetStatus.from_dict(current)
        self.storage.set_fleet_status(scope, namespace, status.as_dict())
        return status

    def _refresh_fleet_status(self, scope: str, namespace: str) -> FleetStatus:
        status = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace))
        if status.mode != "fleet_replica":
            return self._persist_fleet_runtime_status(scope, namespace, backend_reachable=False)
        backend_storage = self._fleet_backend_storage(scope, namespace, required=False)
        if backend_storage is None:
            return status
        try:
            local_high_water = self.storage.get_high_water_seq(scope, namespace)
            remote_high_water = backend_storage.get_high_water_seq(scope, namespace)
        except Exception:
            return self._persist_fleet_runtime_status(scope, namespace, backend_reachable=False)
        return self._persist_fleet_runtime_status(
            scope,
            namespace,
            backend_reachable=True,
            last_synced_seq=remote_high_water if remote_high_water > 0 else None,
            replica_lag_seq=max(local_high_water - remote_high_water, 0),
        )

    def _require_fleet_replica_mode(self, scope: str, namespace: str) -> None:
        status = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace))
        if status.mode != "fleet_replica":
            raise InvalidRequestError("fleet push/pull requires fleet mode fleet_replica.")

    def _fleet_backend_storage(self, scope: str, namespace: str, *, required: bool = True) -> Storage | None:
        config = self._fleet_backend_config.get((scope, namespace))
        if config is None:
            if required:
                raise FleetSyncUnavailableError("fleet replica backend is not configured for the namespace.")
            return None
        backend_storage = self._fleet_backend_cache.get(config.backend_path)
        if backend_storage is not None:
            return backend_storage
        try:
            backend_storage = Storage(Path(config.backend_path))
        except Exception as exc:
            if required:
                raise FleetSyncUnavailableError(f"fleet replica backend is unavailable: {exc}") from exc
            return None
        self._fleet_backend_cache[config.backend_path] = backend_storage
        return backend_storage

    def _post_commit_fleet_sync(self, scope: str, namespace: str) -> None:
        status = FleetStatus.from_dict(self.storage.get_fleet_status(scope, namespace))
        if status.mode != "fleet_replica":
            return
        backend_config = self._fleet_backend_config.get((scope, namespace))
        if backend_config is None:
            return
        if not backend_config.sync_on_write:
            self._refresh_fleet_status(scope, namespace)
            return
        try:
            self.fleet_push(scope, namespace)
        except Exception:
            self._persist_fleet_runtime_status(scope, namespace, backend_reachable=False)
            return

    def _load_current_indexed_records(self, scope: str, namespace: str, index_state: str) -> list[dict[str, Any]]:
        if index_state == "stale":
            self._rebuild_indexes_for_search(scope, namespace)
            return self.index_manager.load(scope, namespace)

        indexed_records = self.index_manager.load(scope, namespace)
        if not indexed_records and index_state == "current":
            self._rebuild_indexes_for_search(scope, namespace)
            indexed_records = self.index_manager.load(scope, namespace)
        return indexed_records

    def _rebuild_indexes_for_search(self, scope: str, namespace: str) -> None:
        try:
            self.rebuild_indexes(scope=scope, namespace=namespace)
        except Exception as exc:
            raise IndexUnavailableError("indexed search is unavailable for the requested namespace.") from exc

    def _serialize_value_for_binding(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return serialize_json(value)

    def _deserialize_json(self, value: str | None) -> Any:
        from .domain import deserialize_json

        return deserialize_json(value)

    def _post_commit_index_sync(self, scope: str, namespace: str) -> None:
        status = self.storage.get_namespace_status(scope, namespace)
        if status["index_state"] == "unavailable":
            return
        try:
            self.rebuild_indexes(scope=scope, namespace=namespace)
        except Exception:
            self.storage.upsert_namespace_status(scope, namespace, "stale", None)

    def _event_kind(self, memory_type: str) -> str:
        if memory_type == "belief":
            return "belief_write"
        if memory_type == "episode":
            return "episode_append"
        return "write"

    def _tool_definition(self, name: str, description: str) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "additionalProperties": True,
                "properties": {},
            },
        }

    def error_envelope(self, tool: str, code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "tool": tool, "error": {"code": code, "message": message}}
