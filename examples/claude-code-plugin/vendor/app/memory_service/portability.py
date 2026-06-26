from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .service import MemoryService

EXPORT_FORMAT_VERSION = "1.0"
COMPONENT_VERSION = "1.2.0"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_BULLET_RE = re.compile(r"^[\-\*]\s+")
_KV_RE = re.compile(r"^([^:]+):\s*(.+)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclass(frozen=True)
class MarkdownMemoryDraft:
    topic: str
    field: str
    memory_type: str
    value: str
    section_title: str


def slugify(text: str, *, max_length: int = 64) -> str:
    slug = _SLUG_RE.sub("_", text.strip().lower()).strip("_")
    if not slug:
        raise ValueError("heading text must contain at least one alphanumeric character")
    return slug[:max_length]


def field_from_text(text: str, *, max_length: int = 48) -> str:
    words = re.findall(r"[A-Za-z0-9_\-]+", text.strip())
    if not words:
        raise ValueError("memory text must contain at least one word")
    candidate = "_".join(words[:6]).lower()
    return candidate[:max_length]


def iter_current_records(service: MemoryService) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for scope, namespace in service.storage.all_namespaces():
        for record in service.storage.namespace_records_for_index(scope, namespace):
            records.append(record)
    records.sort(
        key=lambda item: (
            item["scope"],
            item["namespace"],
            item["topic"],
            item.get("field") or "",
            item["memory_type"],
            item["seq"],
        )
    )
    return records


def export_jsonl_records(records: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for record in records:
        payload = {
            "export_format_version": EXPORT_FORMAT_VERSION,
            "component_version": COMPONENT_VERSION,
            "scope": record["scope"],
            "namespace": record["namespace"],
            "topic": record["topic"],
            "field": record["field"],
            "memory_type": record["memory_type"],
            "value": record["value"],
            "seq": record["seq"],
            "valid_from_seq": record["valid_from_seq"],
            "valid_to_seq": record["valid_to_seq"],
            "recorded_at": record["recorded_at"],
            "event_id": record["event_id"],
            "status": record["status"],
            "provenance": record["provenance"],
            "extends": record.get("extends") or [],
        }
        lines.append(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")


def export_markdown_profile(records: list[dict[str, Any]]) -> str:
    if not records:
        return "# Memory profile export\n\n_No current governed memories._\n"

    lines = [
        "# Memory profile export",
        "",
        f"Component version: `{COMPONENT_VERSION}`",
        "",
    ]
    current_group: tuple[str, str, str] | None = None
    for record in records:
        group = (record["scope"], record["namespace"], record["topic"])
        if group != current_group:
            current_group = group
            lines.extend(
                [
                    f"## {record['scope']} / {record['namespace']} / {record['topic']}",
                    "",
                ]
            )
        field_label = record["field"] or "(episode)"
        lines.extend(
            [
                f"### {field_label} ({record['memory_type']})",
                "",
                _render_value(record["value"]),
                "",
            ]
        )
    return "\n".join(lines)


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return f"```json\n{json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True)}\n```"


def parse_markdown_memories(
    text: str,
    *,
    default_topic: str,
    default_memory_type: str,
) -> list[MarkdownMemoryDraft]:
    drafts: list[MarkdownMemoryDraft] = []
    current_topic = default_topic
    current_section = default_topic
    current_type = default_memory_type

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("<!--"):
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            title = heading.group(2).strip()
            current_section = title
            if heading.group(1) == "#":
                current_topic = slugify(title)
            lowered = title.lower()
            if "preference" in lowered or title.upper() == "USER.md":
                current_type = "preference"
            elif "procedure" in lowered or lowered.startswith("how to"):
                current_type = "procedure"
            elif "constraint" in lowered:
                current_type = "constraint"
            else:
                current_type = default_memory_type
            continue

        body = line.strip()
        if _BULLET_RE.match(body):
            body = _BULLET_RE.sub("", body).strip()
        if not body:
            continue

        kv = _KV_RE.match(body)
        if kv:
            field = slugify(kv.group(1))
            value = kv.group(2).strip()
            memory_type = current_type
        else:
            field = field_from_text(body)
            value = body
            memory_type = current_type

        drafts.append(
            MarkdownMemoryDraft(
                topic=current_topic,
                field=field,
                memory_type=memory_type,
                value=value,
                section_title=current_section,
            )
        )

    return drafts


def import_markdown_file(
    service: MemoryService,
    *,
    path: Path,
    scope: str,
    namespace: str,
    default_topic: str,
    default_memory_type: str,
    source_kind: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    drafts = parse_markdown_memories(
        text,
        default_topic=default_topic,
        default_memory_type=default_memory_type,
    )
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, draft in enumerate(drafts):
        payload = {
            "scope": scope,
            "namespace": namespace,
            "topic": draft.topic,
            "field": draft.field,
            "memory_type": draft.memory_type,
            "value": draft.value,
            "provenance": {
                "source": source_kind,
                "tool": "import_markdown",
                "actor": "portability-cli",
                "request_id": f"{path.name}:{index}",
                "section": draft.section_title,
            },
        }
        if dry_run:
            imported.append({"draft": draft.__dict__, "payload": payload})
            continue
        result = service.memory_remember(payload)
        if not result.get("ok"):
            errors.append(
                {
                    "field": draft.field,
                    "topic": draft.topic,
                    "code": str(result.get("error", {}).get("code", "unknown")),
                    "message": str(result.get("error", {}).get("message", "import failed")),
                }
            )
            continue
        imported.append(
            {
                "topic": draft.topic,
                "field": draft.field,
                "memory_type": draft.memory_type,
                "seq": result["seq"],
                "event_id": result["event_id"],
            }
        )

    return {
        "path": str(path),
        "scope": scope,
        "namespace": namespace,
        "dry_run": dry_run,
        "draft_count": len(drafts),
        "imported_count": len(imported),
        "error_count": len(errors),
        "imported": imported,
        "errors": errors,
    }


def import_jsonl_file(
    service: MemoryService,
    *,
    path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        remember_payload = {
            "scope": payload["scope"],
            "namespace": payload["namespace"],
            "topic": payload["topic"],
            "field": payload.get("field"),
            "memory_type": payload["memory_type"],
            "value": payload["value"],
            "extends": payload.get("extends") or [],
            "provenance": payload.get("provenance")
            or {
                "source": "jsonl",
                "tool": "import_markdown",
                "actor": "portability-cli",
                "request_id": f"{path.name}:{line_number}",
            },
        }
        if dry_run:
            imported.append({"line": line_number, "payload": remember_payload})
            continue
        result = service.memory_remember(remember_payload)
        if not result.get("ok"):
            errors.append(
                {
                    "line": str(line_number),
                    "code": str(result.get("error", {}).get("code", "unknown")),
                    "message": str(result.get("error", {}).get("message", "import failed")),
                }
            )
            continue
        imported.append({"line": line_number, "seq": result["seq"], "event_id": result["event_id"]})

    return {
        "path": str(path),
        "dry_run": dry_run,
        "imported_count": len(imported),
        "error_count": len(errors),
        "imported": imported,
        "errors": errors,
    }


def _markdown_base_name(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".md.sample"):
        return name[: -len(".md.sample")]
    if name.endswith(".md"):
        return name[: -len(".md")]
    return path.stem.lower()


def default_namespace_for_markdown(path: Path) -> str:
    base = _markdown_base_name(path)
    if base == "user":
        return "default"
    if base == "memory":
        return slugify(path.parent.name or "project")
    return slugify(base)


def default_topic_for_markdown(path: Path) -> str:
    base = _markdown_base_name(path)
    if base == "user":
        return "user_profile"
    if base == "memory":
        return "project_memory"
    return slugify(base)


def default_memory_type_for_markdown(path: Path) -> str:
    base = _markdown_base_name(path)
    if base == "user":
        return "preference"
    return "fact"


def scope_for_markdown(path: Path, override: str | None = None) -> str:
    if override:
        return override
    base = _markdown_base_name(path)
    if base == "user":
        return "user"
    if base == "memory":
        return "repository"
    return "repository"
