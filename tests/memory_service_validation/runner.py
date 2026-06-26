from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import BehaviorResult, ServiceStartup, SuiteArtifact, ToolCallError, ValidationFailure, ensure, require_keys
from .control import ValidationControlClient
from .mcp import McpStdioClient
from .scenarios import ALL_CASES


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "artifacts" / "latest-results.json"
SUITE_COMMAND = "python3 tests/run_validation.py"
COMMAND_ENV = "MEMORY_SERVICE_VALIDATION_COMMAND"
HERMES_COMMAND_ENV = "MEMORY_SERVICE_VALIDATION_HERMES_COMMAND"
OPENCLAW_COMMAND_ENV = "MEMORY_SERVICE_VALIDATION_OPENCLAW_COMMAND"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VALIDATION_COMMAND = f"python3 {REPO_ROOT / 'app/run_stdio_server.py'}"
DEFAULT_HERMES_BRIDGE_COMMAND = (
    f"python3 {REPO_ROOT / 'adapters/hermes/run_validation_bridge.py'}"
)
DEFAULT_OPENCLAW_BRIDGE_COMMAND = (
    f"python3 {REPO_ROOT / 'adapters/openclaw/run_validation_bridge.py'}"
)


def _default_validation_commands() -> dict[str, str]:
    return {
        COMMAND_ENV: DEFAULT_VALIDATION_COMMAND,
        HERMES_COMMAND_ENV: DEFAULT_HERMES_BRIDGE_COMMAND,
        OPENCLAW_COMMAND_ENV: DEFAULT_OPENCLAW_BRIDGE_COMMAND,
    }


class ValidationHarness:
    def __init__(self, startup: ServiceStartup, workspace_root: Path) -> None:
        self.startup = startup
        self.workspace_root = workspace_root
        self.namespace_seed = startup.namespace_seed
        self._client: McpStdioClient | None = None
        self._control: ValidationControlClient | None = None
        self._owns_data_dir = False

    def start(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "MEMORY_SERVICE_VALIDATION_READY_FILE": str(self.startup.ready_file),
                "MEMORY_SERVICE_VALIDATION_CONTROL_DIR": str(self.startup.control_dir),
                "MEMORY_SERVICE_VALIDATION_DATA_DIR": str(self.startup.data_dir),
                "MEMORY_SERVICE_VALIDATION_SCHEMA_MODE": self.startup.schema_mode,
                "MEMORY_SERVICE_VALIDATION_NAMESPACE_SEED": self.startup.namespace_seed,
            }
        )
        if self.startup.upgrade_fixture:
            env["MEMORY_SERVICE_VALIDATION_UPGRADE_FIXTURE"] = self.startup.upgrade_fixture
        self.startup.control_dir.mkdir(parents=True, exist_ok=True)
        self.startup.data_dir.mkdir(parents=True, exist_ok=True)
        self._control = ValidationControlClient(self.startup.control_dir)
        self._client = McpStdioClient(self.startup.command, env=env, cwd=self.workspace_root)
        self._client.start()
        self._await_ready_file(self.startup.ready_file)
        self._client.initialize()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _await_ready_file(self, ready_file: Path, timeout_seconds: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if ready_file.exists():
                payload = json.loads(ready_file.read_text(encoding="utf-8"))
                require_keys(payload, ["ready", "protocol", "tool_names"], "validation ready file")
                ensure(payload["ready"] is True, "Validation startup contract must report ready=true before tests run.")
                ensure(payload["protocol"] == "mcp-stdio", "Validation startup contract must expose the MCP server over stdio.")
                return payload
            time.sleep(0.1)
        stderr = self._client.stderr_snapshot() if self._client is not None else ""
        raise TimeoutError(f"Timed out waiting for readiness file '{ready_file}'.\n{stderr}".strip())

    def list_tools(self) -> list[dict[str, Any]]:
        assert self._client is not None
        return self._client.list_tools()

    def namespace(self, label: str) -> str:
        return f"{self.namespace_seed}-{label}-{uuid.uuid4().hex[:8]}"

    def provenance(self, request_id: str) -> dict[str, str]:
        return {
            "source": "mcp",
            "tool": "validation-harness",
            "actor": "validation-agent",
            "request_id": request_id,
        }

    def as_of_from_seq(self, seq: int) -> dict[str, int]:
        return {"seq": seq}

    def as_of_from_recorded_at(self, recorded_at: str) -> dict[str, str]:
        return {"recorded_at": recorded_at}

    def remember(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_remember", arguments)

    def search(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_search", arguments)

    def get(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_get", arguments)

    def forget(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_forget", arguments)

    def status(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_status", arguments)

    def consolidate(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_consolidate", arguments)

    def review(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_review", arguments)

    def profile(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_profile", arguments)

    def reflect(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_reflect", arguments)

    def query_temporal(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_query_temporal", arguments)

    def audit_export(self, **arguments: Any) -> dict[str, Any]:
        return self._call_tool("memory_audit_export", arguments)

    def rebuild_indexes(self, namespace: str) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send("rebuild_indexes", {"namespace": namespace})

    def rebuild_graph(self, namespace: str) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send("rebuild_graph", {"namespace": namespace})

    def set_index_availability(self, namespace: str, available: bool) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send("set_index_availability", {"namespace": namespace, "available": available})

    def set_graph_enabled(self, namespace: str, enabled: bool) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send("set_graph_enabled", {"namespace": namespace, "enabled": enabled})

    def set_pii_scan(
        self,
        namespace: str,
        *,
        enabled: bool,
        policy: str,
        categories: list[str] | None = None,
        placeholder: str | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"namespace": namespace, "enabled": enabled, "policy": policy}
        if categories is not None:
            payload["categories"] = categories
        if placeholder is not None:
            payload["placeholder"] = placeholder
        return self._control.send("set_pii_scan", payload)

    def set_audit_logging(
        self,
        namespace: str,
        *,
        enabled: bool,
        fail_closed: bool | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"namespace": namespace, "enabled": enabled}
        if fail_closed is not None:
            payload["fail_closed"] = fail_closed
        return self._control.send("set_audit_logging", payload)

    def run_consolidation(self, namespace: str) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send("run_consolidation", {"namespace": namespace})

    def set_retention_policy(
        self,
        namespace: str,
        *,
        enabled: bool,
        policies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"namespace": namespace, "enabled": enabled, "policies": policies or {}}
        return self._control.send("set_retention_policy", payload)

    def run_retention(
        self,
        scope: str,
        namespace: str,
        *,
        effective_time: str | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"scope": scope, "namespace": namespace}
        if effective_time is not None:
            payload["effective_time"] = effective_time
        return self._control.send("run_retention", payload)

    def set_fault(self, name: str, enabled: bool) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send("set_fault", {"name": name, "enabled": enabled})

    def set_fleet_config(
        self,
        namespace: str,
        *,
        mode: str,
        backend_reachable: bool | None = None,
        serve_reads_from_replica: bool | None = None,
        max_staleness_seq: int | None = None,
        replica_lag_seq: int | None = None,
        lag_policy: str | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"namespace": namespace, "mode": mode}
        if backend_reachable is not None:
            payload["backend_reachable"] = backend_reachable
        if serve_reads_from_replica is not None:
            payload["serve_reads_from_replica"] = serve_reads_from_replica
        if max_staleness_seq is not None:
            payload["max_staleness_seq"] = max_staleness_seq
        if replica_lag_seq is not None:
            payload["replica_lag_seq"] = replica_lag_seq
        if lag_policy is not None:
            payload["lag_policy"] = lag_policy
        return self._control.send("set_fleet_config", payload)

    def run_profile_engine(
        self,
        scope: str,
        namespace: str,
        *,
        persona_id: str | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"scope": scope, "namespace": namespace}
        if persona_id is not None:
            payload["persona_id"] = persona_id
        return self._control.send("run_profile_engine", payload)

    def set_profile_engine_enabled(self, namespace: str, enabled: bool) -> dict[str, Any]:
        assert self._control is not None
        return self._control.send(
            "set_profile_engine_enabled",
            {"namespace": namespace, "enabled": enabled},
        )

    def trigger_session_summary(
        self,
        scope: str,
        namespace: str,
        *,
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        assert self._control is not None
        payload: dict[str, Any] = {"scope": scope, "namespace": namespace}
        if messages is not None:
            payload["messages"] = messages
        return self._control.send("trigger_session_summary", payload)

    def apply_context_fencing(self, text: str, known_injection_ids: list[str]) -> str:
        assert self._control is not None
        response = self._control.send(
            "apply_context_fencing",
            {"text": text, "known_injection_ids": known_injection_ids},
        )
        details = response.get("details") if isinstance(response.get("details"), dict) else {}
        fenced = details.get("fenced_text")
        ensure(isinstance(fenced, str), "apply_context_fencing must return details.fenced_text as a string.")
        return fenced

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        return self._client.call_tool(name, arguments)

    @contextmanager
    def restarted(
        self,
        schema_mode: str,
        upgrade_fixture: str | None = None,
        reuse_data_dir: bool = False,
        data_dir: Path | None = None,
    ) -> Iterator["ValidationHarness"]:
        selected_data_dir = data_dir
        if selected_data_dir is None:
            selected_data_dir = self.startup.data_dir if reuse_data_dir else self.startup.data_dir.parent / f"{schema_mode}-{uuid.uuid4().hex[:8]}"
        ready_file = self.startup.ready_file.parent / f"ready-{uuid.uuid4().hex[:8]}.json"
        control_dir = self.startup.control_dir.parent / f"control-{uuid.uuid4().hex[:8]}"
        restarted = ValidationHarness(
            ServiceStartup(
                command=self.startup.command,
                ready_file=ready_file,
                control_dir=control_dir,
                data_dir=selected_data_dir,
                schema_mode=schema_mode,
                upgrade_fixture=upgrade_fixture,
                namespace_seed=self.startup.namespace_seed,
            ),
            workspace_root=self.workspace_root,
        )
        restarted.start()
        try:
            yield restarted
        finally:
            restarted.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run black-box validation against the memory-service MCP surface.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON artifact path for machine-readable behavior results.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for env_name, default_value in _default_validation_commands().items():
        if not os.environ.get(env_name):
            os.environ[env_name] = default_value
    command = os.environ.get(COMMAND_ENV)
    if not command:
        print(f"Set {COMMAND_ENV} to the application-owned validation startup entrypoint before running the suite.")
        return 2

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="memory-service-validation-") as temp_dir:
        temp_root = Path(temp_dir)
        harness = ValidationHarness(
            ServiceStartup(
                command=command,
                ready_file=temp_root / "ready.json",
                control_dir=temp_root / "control",
                data_dir=temp_root / "data",
                namespace_seed="memory-service-validation",
            ),
            workspace_root=Path(__file__).resolve().parents[2],
        )
        suite = SuiteArtifact(
            spec_version="1.7.0",
            generated_at=datetime.now(timezone.utc).isoformat(),
            run_command=SUITE_COMMAND,
        )

        try:
            harness.start()
            for case in ALL_CASES:
                try:
                    notes = case.execute(harness)
                except (AssertionError, ValidationFailure, ToolCallError, TimeoutError) as exc:
                    suite.results.append(
                        BehaviorResult(
                            behavior=case.behavior,
                            status="fail",
                            reason=str(exc),
                            group=case.group,
                            spec_section=case.spec_section,
                        )
                    )
                except Exception as exc:  # pragma: no cover - defensive harness failure path
                    suite.results.append(
                        BehaviorResult(
                            behavior=case.behavior,
                            status="fail",
                            reason=f"Unexpected harness failure: {exc}",
                            group=case.group,
                            spec_section=case.spec_section,
                        )
                    )
                else:
                    suite.results.append(
                        BehaviorResult(
                            behavior=case.behavior,
                            status="pass",
                            notes=notes,
                            group=case.group,
                            spec_section=case.spec_section,
                        )
                    )
        finally:
            harness.close()

        output_path.write_text(json.dumps(suite.to_dict(), indent=2), encoding="utf-8")
        failing = [result for result in suite.results if result.status == "fail"]
        return 1 if failing else 0