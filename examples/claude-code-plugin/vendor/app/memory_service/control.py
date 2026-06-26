from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any

from .domain import require_string, validate_scope
from .errors import MemoryServiceError
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
        raise MemoryServiceError("invalid_request", f"unsupported control-plane action: {action}")
