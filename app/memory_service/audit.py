from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = False
    fail_closed: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AuditConfig":
        payload = payload or {}
        return cls(enabled=bool(payload.get("enabled", False)), fail_closed=bool(payload.get("fail_closed", False)))

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "fail_closed": self.fail_closed}


def build_actor(payload: dict[str, Any], request_id: Any | None) -> dict[str, Any]:
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        actor_id = provenance.get("actor")
        source = provenance.get("source")
        provenance_request_id = provenance.get("request_id")
        return {
            "source": source or "mcp",
            "actor_id": actor_id or "agent",
            "request_id": _string_or_none(provenance_request_id) or _string_or_none(request_id),
        }
    return {"source": "mcp", "actor_id": "agent", "request_id": _string_or_none(request_id)}


def export_id_for(
    *,
    scope: str,
    namespace: str,
    fmt: str,
    limit: int,
    event_count: int,
    first_audit_id: str | None,
    last_audit_id: str | None,
    since: dict[str, Any] | None,
    until: dict[str, Any] | None,
) -> str:
    payload = {
        "scope": scope,
        "namespace": namespace,
        "format": fmt,
        "limit": limit,
        "event_count": event_count,
        "first_audit_id": first_audit_id,
        "last_audit_id": last_audit_id,
        "since": since,
        "until": until,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    return "exp_" + digest.hexdigest()[:16]


def to_jsonl(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) for record in records)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
