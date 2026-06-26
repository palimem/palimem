#!/usr/bin/env python3
"""Validation-only JSON-lines stdio bridge for the Hermes MemoryProvider adapter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ADAPTER_ROOT = Path(__file__).resolve().parent
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from ai_memory_hermes.provider import AiMemoryProvider  # noqa: E402


class HermesValidationBridge:
    def __init__(self) -> None:
        self._provider = AiMemoryProvider()
        self._hermes_home: Path | None = None
        self._session_id = ""

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("id", ""))
        action = str(request.get("action", ""))
        try:
            if action == "initialize":
                result = self._initialize(request.get("arguments") or {})
                return {"id": request_id, "ok": True, "result": result}
            if action == "call":
                method = str(request.get("method", ""))
                arguments = request.get("arguments") or {}
                if method == "sync_turn":
                    started = time.perf_counter()
                    self._provider.sync_turn(
                        str(arguments.get("user") or ""),
                        str(arguments.get("assistant") or ""),
                        session_id=str(arguments.get("session_id") or self._session_id),
                        messages=arguments.get("messages") if isinstance(arguments.get("messages"), list) else None,
                    )
                    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
                    return {"id": request_id, "ok": True, "result": None, "elapsed_ms": elapsed_ms}
                result = self._call(method, arguments)
                return {"id": request_id, "ok": True, "result": result}
            if action == "shutdown":
                self._provider.shutdown()
                return {"id": request_id, "ok": True, "result": None}
            return self._error(request_id, "invalid_request", f"Unsupported action: {action}")
        except Exception as exc:
            return self._error(request_id, "integration_failed", str(exc))

    def _initialize(self, arguments: dict[str, Any]) -> None:
        workspace_root = Path(str(arguments["workspace_root"])).expanduser().resolve()
        data_dir = Path(str(arguments["data_dir"])).expanduser().resolve()
        session_id = str(arguments.get("session_id") or "validation-session")
        config_values = {
            "data_dir": str(data_dir),
            "namespace": str(arguments.get("namespace") or workspace_root.name or "workspace"),
            "recall_mode": str(arguments.get("recall_mode") or "hybrid"),
            "prefetch_limit": arguments.get("prefetch_limit", 5),
            "sync_turn_enabled": arguments.get("sync_turn_enabled", True),
            "mirror_builtin_memory": arguments.get("mirror_builtin_memory", False),
        }
        self._hermes_home = Path(tempfile.mkdtemp(prefix="hermes-validation-"))
        self._provider.save_config(config_values, str(self._hermes_home))
        os.environ["MEMORY_SERVICE_DATA_DIR"] = str(data_dir)
        self._session_id = session_id
        self._provider.initialize(
            session_id,
            workspace_root=str(workspace_root),
            hermes_home=str(self._hermes_home),
        )
        return None

    def _call(self, method: str, arguments: dict[str, Any]) -> Any:
        if method == "prefetch":
            return self._provider.prefetch(
                str(arguments.get("query") or ""),
                session_id=str(arguments.get("session_id") or self._session_id),
            )
        if method == "sync_turn":
            self._provider.sync_turn(
                str(arguments.get("user") or ""),
                str(arguments.get("assistant") or ""),
                session_id=str(arguments.get("session_id") or self._session_id),
                messages=arguments.get("messages") if isinstance(arguments.get("messages"), list) else None,
            )
            return None
        if method == "get_tool_schemas":
            return self._provider.get_tool_schemas()
        if method == "handle_tool_call":
            tool_name = str(arguments.get("name") or "")
            tool_args = arguments.get("args") if isinstance(arguments.get("args"), dict) else {}
            return self._provider.handle_tool_call(
                tool_name,
                tool_args,
                session_id=str(arguments.get("session_id") or self._session_id),
            )
        if method == "system_prompt_block":
            return self._provider.system_prompt_block()
        raise ValueError(f"Unsupported Hermes bridge method: {method}")

    @staticmethod
    def _error(request_id: str, code: str, message: str) -> dict[str, Any]:
        return {
            "id": request_id,
            "ok": False,
            "error": {"code": code, "message": message},
        }


def main() -> int:
    bridge = HermesValidationBridge()
    for line in sys.stdin:
        payload_text = line.strip()
        if not payload_text:
            continue
        try:
            request = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"invalid bridge request json: {exc}\n")
            continue
        if not isinstance(request, dict):
            sys.stderr.write("invalid bridge request: expected a JSON object\n")
            continue
        response = bridge.handle(request)
        sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        if request.get("action") == "shutdown":
            break
    bridge._provider.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
