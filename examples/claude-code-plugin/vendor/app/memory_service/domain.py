from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable

from .errors import InvalidRequestError, InvalidScopeError

SUPPORTED_SCOPES = ("user", "session", "repository")
SUPPORTED_MEMORY_TYPES = (
    "preference",
    "fact",
    "procedure",
    "constraint",
    "episode",
    "belief",
)
SUPERSEDING_TYPES = {"preference", "fact", "constraint"}
VERSIONED_TYPES = {"procedure", "belief"}
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class SubjectKey:
    scope: str
    namespace: str
    topic: str
    field: str | None
    memory_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "namespace": self.namespace,
            "topic": self.topic,
            "field": self.field,
            "memory_type": self.memory_type,
        }


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rfc3339_utc(value: str) -> datetime:
    if not isinstance(value, str) or not RFC3339_UTC_RE.match(value):
        raise InvalidRequestError("recorded_at must be an RFC3339 UTC timestamp ending in Z.")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def validate_scope(scope: Any) -> str:
    if scope not in SUPPORTED_SCOPES:
        raise InvalidScopeError("scope must be one of user, session, or repository.")
    return str(scope)


def validate_memory_type(memory_type: Any) -> str:
    if memory_type not in SUPPORTED_MEMORY_TYPES:
        raise InvalidRequestError("memory_type is missing or unsupported.")
    return str(memory_type)


def require_string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f"{name} is required and must be a non-empty string.")
    return value


def normalize_field(memory_type: str, value: Any) -> str | None:
    if memory_type == "episode":
        if value is None:
            return None
        return require_string("field", value)
    return require_string("field", value)


def normalize_extends(value: Any) -> list[dict[str, str | None]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidRequestError("extends must be an array when provided.")
    normalized: list[dict[str, str | None]] = []
    for item in value:
        if not isinstance(item, dict):
            raise InvalidRequestError("each extends entry must be an object.")
        topic = require_string("extends.topic", item.get("topic"))
        field = item.get("field")
        if field is not None and (not isinstance(field, str) or not field.strip()):
            raise InvalidRequestError("extends.field must be a non-empty string when provided.")
        normalized.append({"topic": topic, "field": field})
    return normalized


def validate_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidRequestError("provenance is required and must be an object.")
    return value


def parse_as_of(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidRequestError("as_of must be an object with exactly one of seq or recorded_at.")
    keys = set(value.keys())
    if keys != {"seq"} and keys != {"recorded_at"}:
        raise InvalidRequestError("as_of must contain exactly one of seq or recorded_at.")
    if "seq" in value:
        if isinstance(value["seq"], bool) or not isinstance(value["seq"], int):
            raise InvalidRequestError("as_of.seq must be an integer.")
        return {"seq": value["seq"]}
    parse_rfc3339_utc(value["recorded_at"])
    return {"recorded_at": value["recorded_at"]}


def parse_search_subject(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidRequestError("subject must be an object containing topic, field, or both.")
    keys = set(value.keys())
    allowed_keys = {"topic", "field"}
    if not keys or not keys.issubset(allowed_keys):
        raise InvalidRequestError("subject must contain only topic and/or field.")

    normalized: dict[str, str] = {}
    if "topic" in value:
        normalized["topic"] = require_string("subject.topic", value.get("topic"))
    if "field" in value:
        normalized["field"] = require_string("subject.field", value.get("field"))
    if not normalized:
        raise InvalidRequestError("subject must contain at least one of topic or field.")
    return normalized


def normalize_search_query(value: Any) -> str:
    if isinstance(value, str):
        return require_string("query", value)
    if isinstance(value, (dict, list)):
        if not value:
            raise InvalidRequestError("query must be a non-empty string or a non-empty structured query object.")
        return serialize_json(value)
    raise InvalidRequestError("query must be a non-empty string or a structured query object.")


def serialize_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def deserialize_json(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def parent_edge_key(topic: str, field: str | None) -> str:
    return f"{topic}|{field or ''}"


def subject_from_request(payload: dict[str, Any]) -> SubjectKey:
    memory_type = validate_memory_type(payload.get("memory_type"))
    return SubjectKey(
        scope=validate_scope(payload.get("scope")),
        namespace=require_string("namespace", payload.get("namespace")),
        topic=require_string("topic", payload.get("topic")),
        field=normalize_field(memory_type, payload.get("field")),
        memory_type=memory_type,
    )


def search_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z0-9_\-]+", query.lower()) if term]


def build_search_text(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("topic") or ""),
        str(record.get("field") or ""),
        str(record.get("memory_type") or ""),
        json.dumps(record.get("value"), ensure_ascii=True, sort_keys=True),
    ]
    return " ".join(part for part in parts if part).strip()


def replace_bound_value(target: Any, old_text: str | None, new_text: str | None) -> Any:
    replaced, mutated = _replace_bound_value(target, old_text, new_text)
    if mutated:
        return replaced
    if new_text is None:
        return replaced
    if isinstance(target, str):
        if new_text in target:
            return target
        return f"{target} {new_text}".strip()
    if isinstance(target, dict):
        clone = dict(target)
        clone.setdefault("extends_values", [])
        if new_text not in clone["extends_values"]:
            clone["extends_values"].append(new_text)
        return clone
    if isinstance(target, list):
        clone = list(target)
        clone.append(new_text)
        return clone
    return target


def _replace_bound_value(target: Any, old_text: str | None, new_text: str | None) -> tuple[Any, bool]:
    if isinstance(target, str):
        if old_text and old_text in target:
            replacement = "" if new_text is None else new_text
            return target.replace(old_text, replacement), True
        return target, new_text in target
    if isinstance(target, list):
        changed = False
        values = []
        for item in target:
            updated, item_changed = _replace_bound_value(item, old_text, new_text)
            values.append(updated)
            changed = changed or item_changed
        return values, changed
    if isinstance(target, dict):
        changed = False
        values = {}
        for key, item in target.items():
            updated, item_changed = _replace_bound_value(item, old_text, new_text)
            values[key] = updated
            changed = changed or item_changed
        return values, changed
    return target, False


def ordered_unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def validate_expires_at(value: Any, memory_type: str) -> str | None:
    if value is None:
        return None
    if memory_type == "episode":
        raise InvalidRequestError("expires_at is not valid for episode records.")
    if not isinstance(value, str):
        raise InvalidRequestError("expires_at must be an RFC3339 UTC timestamp ending in Z.")
    parse_rfc3339_utc(value)
    return value


def validate_blocks_actions(memory_type: str, topic: str, value: Any) -> list[str] | None:
    if value is None:
        return None
    if memory_type not in {"constraint", "fact"} or (memory_type == "fact" and topic != "action_boundary"):
        raise InvalidRequestError("blocks_actions is valid only for constraint or fact with topic action_boundary.")
    if not isinstance(value, list) or not value:
        raise InvalidRequestError("blocks_actions must be a non-empty array of action-name strings.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidRequestError("blocks_actions entries must be non-empty strings.")
        normalized.append(item)
    return normalized


def validate_observation(memory_type: str, observation: Any, value: Any) -> tuple[dict[str, Any] | None, Any]:
    if observation is None:
        return None, value
    if memory_type != "episode":
        raise InvalidRequestError("observation is valid only for episode writes.")
    if not isinstance(observation, dict):
        raise InvalidRequestError("observation must be an object when provided.")
    kind = observation.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise InvalidRequestError("observation.kind is required and must be a non-empty string.")
    normalized: dict[str, Any] = {"kind": kind}
    for key in ("tool_name", "stderr_excerpt"):
        if key in observation:
            item = observation[key]
            if not isinstance(item, str):
                raise InvalidRequestError(f"observation.{key} must be a string when provided.")
            normalized[key] = item
    if "exit_code" in observation:
        exit_code = observation["exit_code"]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise InvalidRequestError("observation.exit_code must be an integer when provided.")
        normalized["exit_code"] = exit_code
    if "paths" in observation:
        paths = observation["paths"]
        if not isinstance(paths, list) or not paths:
            raise InvalidRequestError("observation.paths must be a non-empty array when provided.")
        normalized["paths"] = [require_string("observation.paths[]", item) for item in paths]
    if not isinstance(value, dict):
        raise InvalidRequestError("value must be a JSON object when observation is supplied.")
    merged = dict(value)
    for key, item in normalized.items():
        merged[key] = item
    return normalized, merged


def validate_depth(value: Any) -> str:
    if value is None:
        return "full"
    if value not in {"full", "summary"}:
        raise InvalidRequestError("depth must be full or summary.")
    return str(value)


def truncate_summary_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    scalars = list(value)
    if len(scalars) <= 256:
        return value
    return "".join(scalars[:256]) + "..."


def is_expired(expires_at: str | None, evaluation_time: str) -> bool:
    if expires_at is None:
        return False
    return expires_at < evaluation_time
