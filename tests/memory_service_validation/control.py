from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .contracts import ToolCallError


class ValidationControlClient:
    def __init__(self, control_dir: Path, timeout_seconds: float = 15.0) -> None:
        self.control_dir = control_dir
        self.timeout_seconds = timeout_seconds
        self.requests_dir = self.control_dir / "requests"
        self.responses_dir = self.control_dir / "responses"
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)

    def send(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        request_path = self.requests_dir / f"{request_id}.json"
        response_path = self.responses_dir / f"{request_id}.json"
        request = {
            "request_id": request_id,
            "action": action,
            "payload": payload or {},
        }
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if response_path.exists():
                response = json.loads(response_path.read_text(encoding="utf-8"))
                if response.get("status") == "error":
                    raise ToolCallError(
                        response.get("code", "control_error"),
                        response.get("message", "Validation control request failed."),
                        response,
                    )
                return response
            time.sleep(0.1)
        raise TimeoutError(f"Timed out waiting for validation control action '{action}'")
