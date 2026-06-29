#!/usr/bin/env python3
"""Host smoke: exercise Hermes validation bridge JSON-lines protocol end-to-end."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ADAPTER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = ADAPTER_ROOT.parent.parent
BRIDGE = ADAPTER_ROOT / "run_validation_bridge.py"
APP = REPO_ROOT / "app"


def request(process: subprocess.Popen[str], payload: dict) -> dict:
    assert process.stdin and process.stdout
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line.strip():
        stderr = process.stderr.read() if process.stderr else ""
        detail = stderr.strip() or "hermes bridge closed stdout unexpectedly"
        raise RuntimeError(detail)
    response = json.loads(line)
    if response.get("ok") is False:
        raise RuntimeError(json.dumps(response.get("error", response)))
    return response


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hermes-host-smoke-") as workspace_dir:
        workspace = Path(workspace_dir)
        data_dir = workspace / ".ai-memory" / "data"
        namespace = workspace.name
        env = {**dict(__import__("os").environ), "PYTHONPATH": f"{BRIDGE.parent}:{APP}"}
        process = subprocess.Popen(
            [sys.executable, str(BRIDGE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            request(
                process,
                {
                    "id": "1",
                    "action": "initialize",
                    "arguments": {
                        "workspace_root": str(workspace),
                        "data_dir": str(data_dir),
                        "namespace": namespace,
                        "session_id": "host-smoke",
                        "recall_mode": "hybrid",
                        "prefetch_limit": 5,
                        "sync_turn_enabled": True,
                    },
                },
            )
            remember = request(
                process,
                {
                    "id": "2",
                    "action": "call",
                    "method": "handle_tool_call",
                    "arguments": {
                        "name": "memory_remember",
                        "args": {
                            "scope": "user",
                            "namespace": namespace,
                            "memory_type": "fact",
                            "topic": "host_smoke",
                            "field": "status",
                            "value": "bridge-ok",
                            "provenance": {
                                "source": "smoke",
                                "tool": "host-bridge",
                                "actor": "test",
                                "request_id": "h1",
                            },
                        },
                    },
                },
            )
            envelope = json.loads(remember["result"])
            if not envelope.get("ok"):
                raise RuntimeError(f"remember failed: {envelope}")

            sync = request(
                process,
                {
                    "id": "3",
                    "action": "call",
                    "method": "sync_turn",
                    "arguments": {
                        "user": "host smoke turn",
                        "assistant": "stored",
                        "session_id": "host-smoke",
                    },
                },
            )
            elapsed = float(sync.get("elapsed_ms", 999))
            if elapsed > 50:
                raise RuntimeError(f"sync_turn exceeded 50ms on calling thread: {elapsed}")

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                got = request(
                    process,
                    {
                        "id": "4",
                        "action": "call",
                        "method": "handle_tool_call",
                        "arguments": {
                            "name": "memory_get",
                            "args": {
                                "scope": "session",
                                "topic": "session_turn",
                                "field": "turn",
                                "memory_type": "episode",
                            },
                        },
                    },
                )
                record = json.loads(got["result"])
                if record.get("ok") and record.get("record"):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("sync_turn episode not visible via bridge")

            request(process, {"id": "5", "action": "shutdown"})
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()

    print("hermes host bridge smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
