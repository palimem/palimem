from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ToolCallError


@dataclass(slots=True)
class PendingAdapterResponse:
    event: threading.Event
    payload: dict[str, Any] | None = None


class AdapterBridgeClient:
    """JSON-lines stdio client for validation-only adapter bridge commands."""

    def __init__(self, command: str, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env or os.environ.copy()
        self.process: subprocess.Popen[str] | None = None
        self._pending: dict[str, PendingAdapterResponse] = {}
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
            text=True,
            bufsize=1,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()

    def _stderr_loop(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            self._stderr_lines.put(line.rstrip())

    def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            payload_text = line.strip()
            if not payload_text:
                continue
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            request_id = str(payload.get("id", ""))
            pending = self._pending.get(request_id)
            if pending is not None:
                pending.payload = payload
                pending.event.set()

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def request(
        self,
        action: str,
        *,
        method: str | None = None,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        with self._lock:
            request_id = str(self._next_request_id)
            self._next_request_id += 1
        pending = PendingAdapterResponse(event=threading.Event())
        self._pending[request_id] = pending
        payload: dict[str, Any] = {"id": request_id, "action": action}
        if method is not None:
            payload["method"] = method
        if arguments is not None:
            payload["arguments"] = arguments
        self._send(payload)
        if not pending.event.wait(timeout_seconds):
            stderr = self.stderr_snapshot()
            message = f"Timed out waiting for adapter bridge response to '{action}'"
            if method:
                message += f" ({method})"
            if stderr:
                message += f"\n{stderr}"
            raise TimeoutError(message)
        response = pending.payload or {}
        self._pending.pop(request_id, None)
        if response.get("ok") is False:
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            raise ToolCallError(
                str(error.get("code", "adapter_error")),
                error.get("message", f"Adapter bridge '{action}' failed."),
                response,
            )
        return response

    def initialize(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("initialize", arguments=arguments, timeout_seconds=30.0)

    def call(self, method: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("call", method=method, arguments=arguments or {})

    def shutdown(self) -> dict[str, Any] | None:
        if self.process is None or self.process.poll() is not None:
            return None
        try:
            return self.request("shutdown", timeout_seconds=5.0)
        except Exception:
            return None

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
            self.shutdown()
        finally:
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
