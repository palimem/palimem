from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from typing import Any

from .consolidation import plan_safe_merge
from .domain import (
    SUPPORTED_MEMORY_TYPES,
    SUPPORTED_SCOPES,
    SUPERSEDING_TYPES,
    VERSIONED_TYPES,
    InvalidRequestError,
    SubjectKey,
    normalize_search_query,
    normalize_extends,
    ordered_unique,
    parent_edge_key,
    parse_as_of,
    parse_search_subject,
    parse_rfc3339_utc,
    replace_bound_value,
    require_string,
    serialize_json,
    subject_from_request,
    truncate_summary_value,
    utc_now_rfc3339,
    validate_blocks_actions,
    validate_depth,
    validate_expires_at,
    validate_memory_type,
    validate_observation,
    validate_provenance,
    validate_scope,
)
from .errors import IndexUnavailableError, IntegrationFailedError, MemoryServiceError, NotFoundError
from .indexing import IndexManager, TfidfRetriever
from .storage import Storage


class FaultState:
    def __init__(self) -> None:
        self.integration_fail_next = False


class MemoryService:
    def __init__(self, data_dir: Path, schema_mode: str, upgrade_fixture: str | None = None):
        self.data_dir = data_dir
        self.schema_mode = schema_mode
        self.upgrade_fixture = upgrade_fixture
        self.storage = Storage(data_dir)
        self.index_manager = IndexManager(data_dir / "indexes")
        self.retriever = TfidfRetriever()
        self.faults = FaultState()
        try:
            self._bootstrap_schema_mode()
            self._rebuild_all_indexes()
        except Exception:
            self.storage.close()
            raise

    def close(self) -> None:
        self.storage.close()

    def set_fault(self, name: str, enabled: bool) -> None:
        if name != "integration_fail_next":
            raise InvalidRequestError("unsupported fault name")
        self.faults.integration_fail_next = enabled

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
        ]

    def dispatch(self, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        arguments = arguments or {}
        try:
            if tool_name == "memory_remember":
                return self.memory_remember(arguments)
            if tool_name == "memory_search":
                return self.memory_search(arguments)
            if tool_name == "memory_get":
                return self.memory_get(arguments)
            if tool_name == "memory_forget":
                return self.memory_forget(arguments)
            if tool_name == "memory_status":
                return self.memory_status(arguments)
            if tool_name == "memory_consolidate":
                return self.memory_consolidate(arguments)
            if tool_name == "memory_review":
                return self.memory_review(arguments)
            raise InvalidRequestError(f"Unknown tool: {tool_name}")
        except MemoryServiceError as exc:
            return self.error_envelope(tool_name, exc.code, exc.message)
        except sqlite3.Error as exc:
            return self.error_envelope(tool_name, "integration_failed", f"storage error: {exc}")

    def memory_remember(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = subject_from_request(payload)
        value = payload.get("value")
        extends = normalize_extends(payload.get("extends"))
        provenance = validate_provenance(payload.get("provenance"))
        episode_id = payload.get("episode_id")
        if episode_id is not None:
            episode_id = require_string("episode_id", episode_id)
        expires_at = validate_expires_at(payload.get("expires_at"), subject.memory_type)
        blocks_actions = validate_blocks_actions(subject.memory_type, subject.topic, payload.get("blocks_actions"))
        observation, value = validate_observation(subject.memory_type, payload.get("observation"), value)
        current_record = self._write_memory(
            subject,
            value,
            extends,
            provenance,
            episode_id,
            expires_at=expires_at,
            blocks_actions=blocks_actions,
            observation=observation,
        )
        return {
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

    def memory_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = validate_scope(payload.get("scope"))
        namespace = require_string("namespace", payload.get("namespace"))
        query = normalize_search_query(payload.get("query"))
        subject_filter = parse_search_subject(payload.get("subject"))
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

        evaluation_mode, evaluation_seq = self._resolve_evaluation(as_of)
        evaluation_time = self._evaluation_time(as_of, evaluation_seq)

        candidates = []
        if evaluation_mode == "current":
            index_state = self.storage.get_namespace_status(scope, namespace)["index_state"]
            if index_state == "unavailable":
                raise IndexUnavailableError("indexed search is unavailable for the requested namespace.")
            indexed_records = self._load_current_indexed_records(scope, namespace, index_state)
            candidates.extend(self._filter_records_for_scope_namespace(indexed_records, scope, namespace))
            if include_episodes:
                episode_rows = self.storage.get_rows_for_search(scope, namespace, None, True, ["episode"])
                candidates.extend(
                    [
                        self._record_for_row(row, status="current") | {"_search_text": self._search_text_for_row(row)}
                        for row in episode_rows
                    ]
                )
        else:
            rows = self.storage.get_rows_for_search(
                scope,
                namespace,
                evaluation_seq,
                include_episodes,
                normalized_types,
                evaluation_time=evaluation_time,
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
        include_versions = bool(payload.get("include_versions", False))
        depth = validate_depth(payload.get("depth"))
        as_of = parse_as_of(payload.get("as_of"))
        evaluation_mode, evaluation_seq = self._resolve_evaluation(as_of)
        evaluation_time = self._evaluation_time(as_of, evaluation_seq)
        row = self.storage.get_subject_at(subject, evaluation_seq, evaluation_time=evaluation_time)
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
            versions = self.storage.get_subject_versions(subject)
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

    def memory_forget(self, payload: dict[str, Any]) -> dict[str, Any]:
        subject = subject_from_request(payload)
        provenance = validate_provenance(payload.get("provenance"))
        event = self._retract_memory(subject, provenance)
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

    def memory_review(self, payload: dict[str, Any]) -> dict[str, Any]:
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
        current_record = self._write_memory(subject, promotion["value"], [], provenance, None)
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
            }
            self.storage.insert_wal_event(cursor, event)

            if subject.memory_type in SUPERSEDING_TYPES or subject.memory_type in VERSIONED_TYPES:
                self.storage.close_open_versions(cursor, subject, seq)

            bindings = self._bindings_for_extends(subject.scope, subject.namespace, extends)
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
                "salience": None if subject.memory_type == "episode" else 1.0,
                "extends_json": serialize_json(extends),
                "bindings_json": serialize_json(bindings),
                "layer": "episode" if subject.memory_type == "episode" else "semantic_unit",
                "expires_at": expires_at,
                "blocks_actions_json": serialize_json(blocks_actions) if blocks_actions else None,
            }
            self.storage.insert_semantic_version(cursor, version)
            self._propagate_dependents(cursor, event, subject)

        self._post_commit_index_sync(subject.scope, subject.namespace)
        current_row = self.storage.get_subject_at_seq(subject, seq)
        if current_row is None:
            raise IntegrationFailedError("semantic integration did not produce a current version.")
        return self._record_for_row(current_row, status="current")

    def _retract_memory(self, subject: SubjectKey, provenance: dict[str, Any]) -> dict[str, Any]:
        if self.faults.integration_fail_next:
            self.faults.integration_fail_next = False
            raise IntegrationFailedError("fault injection forced the next integration to fail.")
        if self.storage.get_subject_at(subject, None) is None:
            raise NotFoundError("No current value exists for the requested subject at the requested recall point.")
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
            }
            self.storage.insert_wal_event(cursor, event)
            self.storage.close_open_versions(cursor, subject, seq)
            self._propagate_dependents(cursor, event, subject)
        self._post_commit_index_sync(subject.scope, subject.namespace)
        return event

    def _propagate_dependents(self, cursor: sqlite3.Cursor, parent_event: dict[str, Any], subject: SubjectKey) -> None:
        if subject.memory_type == "episode":
            return
        dependents = self.storage.get_dependents_for_parent(subject.scope, subject.namespace, subject.topic, subject.field)
        for dependent in dependents:
            if dependent["memory_type"] == "episode":
                continue
            dependent_subject = SubjectKey(
                scope=dependent["scope"],
                namespace=dependent["namespace"],
                topic=dependent["topic"],
                field=dependent["field"],
                memory_type=dependent["memory_type"],
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

    def _resolve_evaluation(self, as_of: dict[str, Any] | None) -> tuple[str, int | None]:
        if as_of is None:
            return "current", None
        if "seq" in as_of:
            return "as_of", as_of["seq"]
        return "as_of", self.storage.resolve_recorded_at_seq(as_of["recorded_at"])

    def _evaluation_time(self, as_of: dict[str, Any] | None, evaluation_seq: int | None) -> str:
        as_of_recorded_at = as_of.get("recorded_at") if as_of else None
        return self.storage.get_evaluation_time(evaluation_seq, as_of_recorded_at)

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
