from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any

from .context_fencing import apply_context_fencing
from .domain import require_string, validate_scope
from .errors import MemoryServiceError
from .retention import normalize_retention_ttl, retention_now, validate_retention_memory_type
from .service import MemoryService


class ControlPlane:
    def __init__(self, service: MemoryService, control_dir: Path):
        self.service = service
        self.control_dir = control_dir
        self.requests_dir = control_dir / "requests"
        self.responses_dir = control_dir / "responses"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._processed: set[str] = set()

    def start(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="memory-service-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            for path in sorted(self.requests_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    request_id = require_string("request_id", payload.get("request_id"))
                    if request_id in self._processed:
                        continue
                    response = self._handle_request(payload)
                    self.responses_dir.joinpath(f"{request_id}.json").write_text(
                        json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                        encoding="utf-8",
                    )
                    self._processed.add(request_id)
                except Exception as exc:
                    request_id = payload.get("request_id", path.stem) if isinstance(payload, dict) else path.stem
                    error = {
                        "request_id": request_id,
                        "status": "error",
                        "code": "integration_failed",
                        "message": str(exc),
                    }
                    self.responses_dir.joinpath(f"{request_id}.json").write_text(
                        json.dumps(error, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                        encoding="utf-8",
                    )
            time.sleep(0.1)

    def _handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = require_string("request_id", payload.get("request_id"))
        action = require_string("action", payload.get("action"))
        request_payload = payload.get("payload") or {}
        try:
            details = self._dispatch_action(action, request_payload)
            return {"request_id": request_id, "status": "ok", "details": details}
        except MemoryServiceError as exc:
            return {
                "request_id": request_id,
                "status": "error",
                "code": exc.code,
                "message": exc.message,
            }

    def _dispatch_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        namespace = payload.get("namespace")
        scope = payload.get("scope")
        if action == "rebuild_indexes":
            require_string("namespace", namespace)
            if scope is not None:
                validate_scope(scope)
            self.service.rebuild_indexes(scope=scope, namespace=namespace)
            return {"namespace": namespace, "scope": scope}
        if action == "set_index_availability":
            require_string("namespace", namespace)
            available = payload.get("available")
            if available is None:
                available = payload.get("enabled")
            if available is None and payload.get("state") is not None:
                available = payload.get("state") != "unavailable"
            if not isinstance(available, bool):
                raise MemoryServiceError("invalid_request", "set_index_availability requires a boolean available/enabled flag.")
            if scope is not None:
                validate_scope(scope)
            self.service.set_index_availability(namespace=namespace, available=available, scope=scope)
            return {"namespace": namespace, "scope": scope, "available": available}
        if action == "rebuild_graph":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            return self.service.rebuild_graph(scope_value, namespace)
        if action == "set_graph_enabled":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise MemoryServiceError("invalid_request", "set_graph_enabled requires a boolean enabled flag.")
            return self.service.set_graph_enabled(scope_value, namespace, enabled)
        if action == "run_consolidation":
            require_string("namespace", namespace)
            if scope is not None:
                validate_scope(scope)
                return self.service.run_consolidation(scope=scope, namespace=namespace)
            results = []
            for candidate_scope in ("user", "session", "repository"):
                if self.service.storage.get_high_water_seq(candidate_scope, namespace) <= 0:
                    continue
                results.append(self.service.run_consolidation(scope=candidate_scope, namespace=namespace))
            if not results:
                return self.service.run_consolidation(scope="repository", namespace=namespace)
            return results[-1]
        if action == "set_fault":
            name = require_string("name", payload.get("name"))
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise MemoryServiceError("invalid_request", "set_fault requires a boolean enabled flag.")
            self.service.set_fault(name, enabled)
            return {"name": name, "enabled": enabled}
        if action == "run_profile_engine":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "user"
            persona_id = payload.get("persona_id")
            if persona_id is not None and (not isinstance(persona_id, str) or not persona_id.strip()):
                raise MemoryServiceError("invalid_request", "persona_id must be a non-empty string when provided.")
            async_run = bool(payload.get("async", False))
            source_scope_raw = payload.get("source_scope")
            source_namespace_raw = payload.get("source_namespace")
            source_scope = validate_scope(source_scope_raw) if source_scope_raw is not None else None
            source_namespace = (
                require_string("source_namespace", source_namespace_raw)
                if source_namespace_raw is not None
                else None
            )
            return self.service.run_profile_engine(
                scope_value,
                namespace,
                persona_id=persona_id,
                require_enabled=True,
                async_run=async_run,
                source_scope=source_scope,
                source_namespace=source_namespace,
            )
        if action == "set_profile_engine_enabled":
            require_string("namespace", namespace)
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise MemoryServiceError("invalid_request", "set_profile_engine_enabled requires a boolean enabled flag.")
            self.service.profile_engine.set_enabled(
                validate_scope(scope) if scope is not None else "user",
                namespace,
                enabled,
            )
            return {"namespace": namespace, "enabled": enabled}
        if action == "set_pii_scan":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise MemoryServiceError("invalid_request", "set_pii_scan requires a boolean enabled flag.")
            config_payload = {
                "enabled": enabled,
                "policy": payload.get("policy"),
                "placeholder": payload.get("placeholder"),
                "categories": payload.get("categories"),
                "enabled_memory_types": payload.get("enabled_memory_types"),
                "government_id_patterns": payload.get("government_id_patterns"),
                "financial_account_patterns": payload.get("financial_account_patterns"),
                "free_text_names": payload.get("free_text_names"),
            }
            return self.service.configure_pii_scan(scope_value, namespace, config_payload)
        if action == "set_audit_logging":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            enabled = payload.get("enabled")
            fail_closed = payload.get("fail_closed", False)
            if not isinstance(enabled, bool) or not isinstance(fail_closed, bool):
                raise MemoryServiceError(
                    "invalid_request",
                    "set_audit_logging requires boolean enabled and fail_closed flags.",
                )
            return self.service.configure_audit(scope_value, namespace, enabled=enabled, fail_closed=fail_closed)
        if action == "set_retention_policy":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            if "policies" in payload or "enabled" in payload:
                enabled = payload.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise MemoryServiceError("invalid_request", "set_retention_policy requires boolean enabled when provided.")
                if not enabled:
                    return self.service.replace_retention_policies(scope_value, namespace, {})
                policies_raw = payload.get("policies")
                if not isinstance(policies_raw, dict):
                    raise MemoryServiceError("invalid_request", "set_retention_policy requires a policies object when enabled.")
                policies: dict[str, int] = {}
                for raw_memory_type, raw_ttl in policies_raw.items():
                    memory_type = validate_retention_memory_type(raw_memory_type)
                    policies[memory_type] = normalize_retention_ttl(raw_ttl)
                return self.service.replace_retention_policies(scope_value, namespace, policies)
            memory_type = validate_retention_memory_type(payload.get("memory_type"))
            ttl_seconds_raw = payload.get("ttl_seconds")
            ttl_seconds = None if ttl_seconds_raw is None else normalize_retention_ttl(ttl_seconds_raw)
            return self.service.set_retention_policy(scope_value, namespace, memory_type, ttl_seconds)
        if action == "run_retention":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            now = payload.get("effective_time")
            if now is None:
                now = payload.get("now")
            return self.service.run_retention(scope_value, namespace, now=retention_now(now) if now is not None else None)
        if action in {"set_fleet_mode", "set_fleet_status", "set_fleet_config"}:
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            status_payload = {
                key: payload[key]
                for key in (
                    "mode",
                    "backend_reachable",
                    "last_synced_seq",
                    "replica_lag_seq",
                    "serve_reads_from_replica",
                    "max_staleness_seq",
                    "lag_policy",
                    "backend_path",
                    "backend_id",
                    "sync_on_write",
                    "clear_backend",
                )
                if key in payload
            }
            return self.service.set_fleet_status(scope_value, namespace, status_payload)
        if action == "fleet_push":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            return self.service.fleet_push(
                scope_value,
                namespace,
                since_seq=payload.get("since_seq"),
                until_seq=payload.get("until_seq"),
            )
        if action == "fleet_pull":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "repository"
            return self.service.fleet_pull(
                scope_value,
                namespace,
                since_seq=payload.get("since_seq"),
                until_seq=payload.get("until_seq"),
            )
        if action == "apply_context_fencing":
            text = payload.get("text")
            if not isinstance(text, str):
                raise MemoryServiceError("invalid_request", "apply_context_fencing requires text.")
            known_ids = payload.get("known_injection_ids")
            if not isinstance(known_ids, list):
                raise MemoryServiceError("invalid_request", "apply_context_fencing requires known_injection_ids array.")
            fenced_text = apply_context_fencing(text, [str(item) for item in known_ids])
            return {"fenced_text": fenced_text}
        if action == "trigger_session_summary":
            require_string("namespace", namespace)
            scope_value = validate_scope(scope) if scope is not None else "session"
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                raise MemoryServiceError("invalid_request", "messages must be an array when provided.")
            body = self.service.build_session_summary_body(messages)
            self.service.trigger_session_summary_async(scope_value, namespace, body)
            return {"scope": scope_value, "namespace": namespace, "status": "enqueued"}
        raise MemoryServiceError("invalid_request", f"unsupported control-plane action: {action}")
