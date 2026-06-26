from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ToolCallError


def _read_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8").strip()
        if not decoded:
            break
        key, value = decoded.split(":", 1)
        headers[key.lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    body = stream.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


@dataclass(slots=True)
class PendingResponse:
    event: threading.Event
    payload: dict[str, Any] | None = None


class McpStdioClient:
    def __init__(self, command: str, env: dict[str, str], cwd: Path | None = None) -> None:
        self.command = command
        self.env = env
        self.cwd = cwd
        self.process: subprocess.Popen[bytes] | None = None
        self._pending: dict[int, PendingResponse] = {}
        self._next_request_id = 1
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._lock = threading.Lock()

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            shell=True,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()

    def _stderr_loop(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            self._stderr_lines.put(line.decode("utf-8", errors="replace").rstrip())

    def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            message = _read_message(self.process.stdout)
            if message is None:
                return
            if "id" in message:
                pending = self._pending.get(int(message["id"]))
                if pending is not None:
                    pending.payload = message
                    pending.event.set()

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        encoded = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        self.process.stdin.write(header)
        self.process.stdin.write(encoded)
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None, timeout_seconds: float = 15.0) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        pending = PendingResponse(event=threading.Event())
        self._pending[request_id] = pending
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        if not pending.event.wait(timeout_seconds):
            raise TimeoutError(f"Timed out waiting for MCP response to '{method}'")
        payload = pending.payload or {}
        self._pending.pop(request_id, None)
        if "error" in payload:
            error = payload["error"]
            raise ToolCallError(str(error.get("code", "jsonrpc_error")), error.get("message", "MCP request failed"), payload)
        return payload.get("result", {})

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "memory-service-validation", "version": "1.3.0"},
            },
            timeout_seconds=30.0,
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list")
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        payload = self._decode_tool_payload(result)
        if result.get("isError") or payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            raise ToolCallError(
                str(error.get("code", "tool_error")),
                error.get("message", f"Tool call '{name}' failed."),
                payload,
            )
        return payload

    def _decode_tool_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        content = result.get("content") or []
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    return {"message": text}
                if isinstance(decoded, dict):
                    return decoded
        return {}

    def stderr_snapshot(self) -> str:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._stderr_lines.get_nowait())
            except queue.Empty:
                break
        return "\n".join(lines)

    def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            time.sleep(0.1)
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
