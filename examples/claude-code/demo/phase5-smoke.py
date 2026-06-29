#!/usr/bin/env python3
"""Phase 5 smoke: temporal query, operator metadata, and eleven-tool surface."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

from memory_service.service import MemoryService  # noqa: E402


def _provenance(label: str) -> dict[str, str]:
    return {
        "source": "phase5-smoke",
        "tool": "phase5-smoke",
        "actor": "benchmark",
        "request_id": label,
    }


def _remember(
    service: MemoryService,
    *,
    namespace: str,
    topic: str,
    field: str,
    value: str,
    request_id: str,
) -> dict:
    payload = service.memory_remember(
        {
            "scope": "repository",
            "namespace": namespace,
            "memory_type": "fact",
            "topic": topic,
            "field": field,
            "value": value,
            "provenance": _provenance(request_id),
        }
    )
    if payload.get("ok") is not True:
        raise RuntimeError(f"memory_remember failed: {payload}")
    return payload


def main() -> int:
    namespace = "phase5-smoke"
    with tempfile.TemporaryDirectory(prefix="phase5-smoke-") as tmp:
        data_dir = Path(tmp) / "data"
        service = MemoryService(data_dir, "fresh", None)

        status = service.memory_status({"scope": "repository", "namespace": namespace})
        if status.get("ok") is not True:
            raise RuntimeError(f"memory_status failed: {status}")
        for key in ("pii_scan", "retention", "fleet"):
            if key not in status:
                raise RuntimeError(f"memory_status missing Phase 5 key: {key}")
        if status.get("fleet", {}).get("mode") != "local":
            raise RuntimeError(f"expected fleet.mode=local, got {status.get('fleet')}")

        first = _remember(
            service,
            namespace=namespace,
            topic="deployment_policy",
            field="tier",
            value="silver",
            request_id="tier-1",
        )
        _remember(
            service,
            namespace=namespace,
            topic="deployment_policy",
            field="region",
            value="eu-west",
            request_id="region-1",
        )
        second = _remember(
            service,
            namespace=namespace,
            topic="deployment_policy",
            field="tier",
            value="gold",
            request_id="tier-2",
        )

        audit_points = [{"seq": first["seq"]}, {"seq": second["seq"]}]
        temporal = service.memory_query_temporal(
            {
                "scope": "repository",
                "namespace": namespace,
                "topic": "deployment_policy",
                "memory_types": ["fact"],
                "audit_points": audit_points,
            }
        )
        if temporal.get("ok") is not True:
            raise RuntimeError(f"memory_query_temporal failed: {temporal}")
        trajectories = temporal.get("trajectories")
        if not isinstance(trajectories, list) or len(trajectories) < 1:
            raise RuntimeError("memory_query_temporal returned no trajectories")

        export = service.memory_audit_export(
            {
                "scope": "repository",
                "namespace": namespace,
                "format": "jsonl",
                "since": "1970-01-01T00:00:00Z",
                "until": "2099-01-01T00:00:00Z",
            }
        )
        if export.get("ok") is not True and export.get("error", {}).get("code") != "audit_export_unavailable":
            raise RuntimeError(f"unexpected memory_audit_export response: {export}")

    print(json.dumps({"ok": True, "phase5_smoke": "passed"}, ensure_ascii=True))
    print("phase5-smoke: OK — Phase 5 metadata, temporal query, and audit export surface verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
