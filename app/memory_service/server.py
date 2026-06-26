from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

from .control import ControlPlane
from .errors import InvalidRequestError
from .service import MemoryService


@dataclass
class ServerConfig:
    ready_file: Path
    control_dir: Path
    data_dir: Path
    schema_mode: str
    namespace_seed: str
    upgrade_fixture: str | None


@dataclass
class ProductionServerConfig:
    data_dir: Path


def load_config() -> ServerConfig:
    env = _required_environment()
    schema_mode = env["MEMORY_SERVICE_VALIDATION_SCHEMA_MODE"]
    upgrade_fixture = env.get("MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE")
    if schema_mode == "upgrade_from_v1_0_1" and not upgrade_fixture:
        raise InvalidRequestError("MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE is required for upgrade_from_v1_0_1.")
    return ServerConfig(
        ready_file=Path(env["MEMORY_SERVICE_VALIDATION_READY_FILE"]),
        control_dir=Path(env["MEMORY_SERVICE_VALIDATION_CONTROL_DIR"]),
        data_dir=Path(env["MEMORY_SERVICE_VALIDATION_DATA_DIR"]),
        schema_mode=schema_mode,
        namespace_seed=env["MEMORY_SERVICE_VALIDATION_NAMESPACE_SEED"],
        upgrade_fixture=upgrade_fixture,
    )


def load_production_config() -> ProductionServerConfig:
    import os

    data_dir = os.environ.get("MEMORY_SERVICE_DATA_DIR", "/data")
    return ProductionServerConfig(data_dir=Path(data_dir))


def main() -> None:
    try:
        config = load_config()
        service = MemoryService(config.data_dir, config.schema_mode, config.upgrade_fixture)
        control = ControlPlane(service, config.control_dir)
        control.start()
        write_ready_file(config.ready_file, service.readiness_payload())
        try:
            StdioMcpServer(service).serve_forever()
        finally:
            control.stop()
            service.close()
    except Exception as exc:
        sys.stderr.write(f"memory-service startup failed: {exc}\n")
        sys.stderr.flush()
        raise SystemExit(1)


def main_production() -> None:
    try:
        config = load_production_config()
        service = MemoryService(config.data_dir, "auto", None)
        try:
            StdioMcpServer(service).serve_forever()
        finally:
            service.close()
    except Exception as exc:
        sys.stderr.write(f"memory-service startup failed: {exc}\n")
        sys.stderr.flush()
        raise SystemExit(1)


def write_ready_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")


class StdioMcpServer:
    def __init__(self, service: MemoryService):
        self.service = service
        self.stdin = sys.stdin.buffer
        self.stdout = sys.stdout.buffer

    def serve_forever(self) -> None:
        while True:
            message = self._read_message()
            if message is None:
                return
            response = self._handle_message(message)
            if response is not None:
                self._write_message(response)

    def _read_message(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = self.stdin.readline()
            if not line:
                return None
            stripped = line.decode("utf-8").strip()
            if not stripped:
                break
            name, _, value = stripped.partition(":")
            headers[name.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        body = self.stdin.read(length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def _write_message(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self.stdout.write(header)
        self.stdout.write(body)
        self.stdout.flush()

    def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "memory-service", "version": "1.7.0"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.service.tool_definitions()}}
        if method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            envelope = self.service.dispatch(tool_name, arguments, request_id=request_id)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "structuredContent": envelope,
                    "content": [{"type": "text", "text": json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)}],
                    "isError": not envelope.get("ok", False),
                },
            }
        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def _required_environment() -> dict[str, str]:
    import os

    required = {
        "MEMORY_SERVICE_VALIDATION_READY_FILE",
        "MEMORY_SERVICE_VALIDATION_CONTROL_DIR",
        "MEMORY_SERVICE_VALIDATION_DATA_DIR",
        "MEMORY_SERVICE_VALIDATION_SCHEMA_MODE",
        "MEMORY_SERVICE_VALIDATION_NAMESPACE_SEED",
    }
    values: dict[str, str] = {}
    missing = []
    for name in required:
        value = os.environ.get(name)
        if not value:
            missing.append(name)
            continue
        values[name] = value
    upgrade_fixture = os.environ.get("MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE")
    if upgrade_fixture:
        values["MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE"] = upgrade_fixture
    if missing:
        raise InvalidRequestError("Missing required environment variables: " + ", ".join(sorted(missing)))
    return values
