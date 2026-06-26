#!/usr/bin/env python3
"""Validation-only JSON-lines stdio bridge for the OpenClaw memory plugin adapter."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ADAPTER_ROOT = Path(__file__).resolve().parent
if str(ADAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_ROOT))

from bridge import (  # noqa: E402
    PluginConfig,
    _memory_get,
    _memory_search,
    _sync_workspace_markdown,
)
from memory_service.service import MemoryService  # noqa: E402

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


@dataclass
class OpenClawBridgeState:
    config: PluginConfig | None = None
    service: MemoryService | None = None


def _open_service(config: PluginConfig) -> MemoryService:
    return MemoryService(config.data_dir, "auto", None)


def _initialize(arguments: dict[str, Any]) -> OpenClawBridgeState:
    workspace_root = Path(str(arguments["workspace_root"])).expanduser().resolve()
    data_dir = Path(str(arguments["data_dir"])).expanduser().resolve()
    namespace = str(arguments.get("namespace") or workspace_root.name or "workspace").strip() or "workspace"
    config = PluginConfig(
        workspace_root=workspace_root,
        data_dir=data_dir,
        namespace=namespace,
        import_workspace_markdown=bool(arguments.get("import_workspace_markdown", False)),
        session_key=str(arguments.get("session_key") or ""),
    )
    service = _open_service(config)
    if config.import_workspace_markdown:
        _sync_workspace_markdown(service, config)
    return OpenClawBridgeState(config=config, service=service)


def _shutdown(state: OpenClawBridgeState) -> None:
    if state.service is not None:
        state.service.close()
    state.service = None
    state.config = None


def _as_bridge_error(request_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _normalize_get_result(raw: dict[str, Any]) -> dict[str, Any] | None:
    code = raw.get("code")
    if raw.get("disabled") and code == "invalid_request":
        raise ValueError(str(raw.get("error") or "invalid_request"))
    text = str(raw.get("text") or "")
    if not text.strip():
        return None
    path = str(raw.get("path") or "")
    lines = text.splitlines() or [""]
    snippet = text.strip()
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return {
        "path": path,
        "snippet": snippet,
        "score": 1.0,
        "startLine": 1,
        "endLine": max(1, len(lines)),
    }


def _call(state: OpenClawBridgeState, method: str, arguments: dict[str, Any]) -> Any:
    if state.config is None or state.service is None:
        raise RuntimeError("OpenClaw validation bridge is not initialized")
    if method == "memory_search":
        result = _memory_search(state.service, state.config, arguments)
        if result.get("disabled"):
            code = str(result.get("code") or "integration_failed")
            message = str(result.get("error") or "memory search failed")
            raise ValueError(f"{code}: {message}")
        return result
    if method == "memory_get":
        raw = _memory_get(state.service, state.config, arguments)
        if raw.get("disabled") and raw.get("code") == "invalid_request":
            raise ValueError(str(raw.get("error") or "invalid_request"))
        return _normalize_get_result(raw)
    raise ValueError(f"Unsupported OpenClaw bridge method: {method}")


def handle(state: OpenClawBridgeState, request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("id", ""))
    action = str(request.get("action", ""))
    try:
        if action == "initialize":
            initialized = _initialize(request.get("arguments") or {})
            state.config = initialized.config
            state.service = initialized.service
            result = None
        elif action == "call":
            result = _call(state, str(request.get("method", "")), request.get("arguments") or {})
        elif action == "shutdown":
            _shutdown(state)
            result = None
        else:
            return _as_bridge_error(request_id, "invalid_request", f"Unsupported action: {action}")
    except ValueError as exc:
        message = str(exc)
        code = "invalid_request"
        if message.startswith("invalid_request:"):
            message = message.split(":", 1)[1].strip()
        elif ":" in message:
            prefix, remainder = message.split(":", 1)
            if prefix in {"invalid_request", "integration_failed", "not_found"}:
                code = prefix
                message = remainder.strip()
        return _as_bridge_error(request_id, code, message)
    except Exception as exc:
        return _as_bridge_error(request_id, "integration_failed", str(exc))

    return {"id": request_id, "ok": True, "result": result}


def main() -> int:
    state = OpenClawBridgeState()
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
        response = handle(state, request)
        sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        if request.get("action") == "shutdown":
            break
    _shutdown(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
