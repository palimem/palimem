"""Shared helpers for Palimem research benchmarks."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
DATA_DIR = Path(__file__).resolve().parent / "data"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from memory_service.service import MemoryService  # noqa: E402


def provenance(label: str) -> dict[str, str]:
    return {
        "source": "benchmark",
        "tool": "benchmarks",
        "actor": "benchmark",
        "request_id": label,
    }


def needle_hit(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def open_service(tmp_path: Path) -> MemoryService:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return MemoryService(data_dir, "fresh", None)


def remember(
    service: MemoryService,
    *,
    scope: str,
    namespace: str,
    memory_type: str,
    topic: str,
    field: str,
    value: Any,
    request_id: str,
) -> dict[str, Any]:
    payload = service.memory_remember(
        {
            "scope": scope,
            "namespace": namespace,
            "memory_type": memory_type,
            "topic": topic,
            "field": field,
            "value": value,
            "provenance": provenance(request_id),
        }
    )
    if payload.get("ok") is not True:
        raise RuntimeError(f"memory_remember failed: {payload}")
    return payload


def search_texts(service: MemoryService, *, scope: str, namespace: str, query: str, limit: int = 10) -> list[str]:
    payload = service.memory_search(
        {
            "scope": scope,
            "namespace": namespace,
            "query": query,
            "limit": limit,
        }
    )
    if payload.get("ok") is not True:
        return []
    texts: list[str] = []
    for result in payload.get("results", []):
        value = result.get("value")
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            texts.append(json.dumps(value, ensure_ascii=True))
    return texts


def search_hits(
    service: MemoryService,
    *,
    scope: str,
    namespace: str,
    query: str,
    needle: str,
    exclude: str | None = None,
) -> bool:
    texts = search_texts(service, scope=scope, namespace=namespace, query=query)
    if exclude and any(needle_hit(text, exclude) for text in texts):
        return False
    return any(needle_hit(text, needle) for text in texts)


def profile_hits(service: MemoryService, *, scope: str, namespace: str, needle: str) -> bool:
    payload = service.memory_profile(
        {
            "scope": scope,
            "namespace": namespace,
            "depth": "full",
            "budget_tokens": 4096,
        }
    )
    if payload.get("ok") is not True:
        return False
    manifest = str(payload.get("manifest", ""))
    if needle_hit(manifest, needle):
        return True
    for section in payload.get("sections", []):
        if needle_hit(str(section.get("text", "")), needle):
            return True
    return False


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


@dataclass
class BenchmarkCaseResult:
    name: str
    status: str
    notes: str
    metrics: dict[str, Any]


def write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def timed_search(service: MemoryService, *, scope: str, namespace: str, query: str) -> float:
    started = time.perf_counter()
    service.memory_search({"scope": scope, "namespace": namespace, "query": query, "limit": 10})
    return (time.perf_counter() - started) * 1000.0


def case_to_dict(case: BenchmarkCaseResult) -> dict[str, Any]:
    return asdict(case)
