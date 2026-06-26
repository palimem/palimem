from __future__ import annotations

import json
import os
from typing import Any

from .domain import citation_from_record


def gather_reflect_evidence(service: Any, payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    """Collect governed evidence for memory_reflect via search path."""
    search_payload = {
        "scope": payload["scope"],
        "namespace": payload["namespace"],
        "query": payload["query"],
        "limit": payload.get("limit", 10),
        "memory_types": payload.get("memory_types"),
        "subject": payload.get("subject"),
        "as_of": payload.get("as_of"),
        "persona_id": payload.get("persona_id"),
    }
    result = service.memory_search(search_payload)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", {}).get("message", "search failed"))
    evaluation_mode = result.get("evaluation_mode", "current")
    evidence = list(result.get("results") or [])
    return evaluation_mode, evidence, str(payload["query"])


def synthesize_reflect(query: str, evidence: list[dict[str, Any]]) -> str:
    mode = os.environ.get("MEMORY_SERVICE_VALIDATION_REFLECT_MODE", "").strip().lower()
    if mode == "deterministic" or os.environ.get("MEMORY_SERVICE_VALIDATION_DATA_DIR"):
        return _deterministic_synthesis(query, evidence)
    return _deterministic_synthesis(query, evidence)


def _deterministic_synthesis(query: str, evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return ""
    lines = [f"Reflection for: {query}"]
    for record in evidence:
        field = record.get("field") or "(episode)"
        value = record.get("value")
        if isinstance(value, str):
            rendered = value.strip()
        else:
            rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
        lines.append(
            f"- [{record.get('memory_type')}] {record.get('topic')}/{field}: {rendered}"
        )
    return "\n".join(lines)


def reflect_citations(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [citation_from_record(record) for record in evidence]
