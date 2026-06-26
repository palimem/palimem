#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory_service.portability import (
    default_memory_type_for_markdown,
    default_topic_for_markdown,
    import_markdown_file,
    slugify,
)
from memory_service.service import MemoryService

LOOKUP_MEMORY_TYPES = ("fact", "preference", "procedure", "constraint", "belief", "episode")


@dataclass(frozen=True)
class PluginConfig:
    workspace_root: Path
    data_dir: Path
    namespace: str
    import_workspace_markdown: bool
    session_key: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw bridge for memory-service adapters.")
    parser.add_argument("action", choices=("memory_search", "memory_get"))
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--namespace")
    parser.add_argument("--import-workspace-markdown", action="store_true")
    parser.add_argument("--session-key", default="")
    parser.add_argument("--payload", default="{}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(args.payload)
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    data_dir = _resolve_data_dir(workspace_root, args.data_dir)
    namespace = str(args.namespace or workspace_root.name or "workspace").strip() or "workspace"
    config = PluginConfig(
        workspace_root=workspace_root,
        data_dir=data_dir,
        namespace=namespace,
        import_workspace_markdown=bool(args.import_workspace_markdown),
        session_key=args.session_key,
    )
    service = MemoryService(config.data_dir, "auto", None)
    try:
        if config.import_workspace_markdown:
            _sync_workspace_markdown(service, config)
        if args.action == "memory_search":
            result = _memory_search(service, config, payload)
        else:
            result = _memory_get(service, config, payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
        return 0
    finally:
        service.close()


def _resolve_data_dir(workspace_root: Path, value: str | None) -> Path:
    if value and value.strip():
        data_dir = Path(value).expanduser()
        if data_dir.is_absolute():
            return data_dir.resolve()
        return (workspace_root / data_dir).resolve()
    return (workspace_root / ".ai-memory" / "data").resolve()


def _sync_workspace_markdown(service: MemoryService, config: PluginConfig) -> None:
    state_path = config.data_dir / "openclaw_import_state.json"
    previous = _load_json_object(state_path)
    current: dict[str, str] = {}
    changed = False
    for path in _iter_workspace_markdown(config.workspace_root):
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rel_path = path.relative_to(config.workspace_root).as_posix()
        current[rel_path] = digest
        if previous.get(rel_path) == digest:
            continue
        import_markdown_file(
            service,
            path=path,
            scope="repository",
            namespace=config.namespace,
            default_topic=default_topic_for_markdown(path),
            default_memory_type=default_memory_type_for_markdown(path),
            source_kind=path.name,
        )
        changed = True
    removed = set(previous) - set(current)
    if changed or removed:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(current, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _iter_workspace_markdown(workspace_root: Path) -> list[Path]:
    paths: list[Path] = []
    memory_md = workspace_root / "MEMORY.md"
    if memory_md.is_file():
        paths.append(memory_md)
    memory_dir = workspace_root / "memory"
    if memory_dir.is_dir():
        paths.extend(sorted(path for path in memory_dir.rglob("*.md") if path.is_file()))
    return paths


def _load_json_object(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _memory_search(service: MemoryService, config: PluginConfig, payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    if not query:
        return _search_error("invalid_request", "query is required")
    scope = str(payload.get("scope") or "repository")
    namespace = str(payload.get("namespace") or _namespace_for_scope(config, scope))
    limit = payload.get("limit", payload.get("maxResults", 10))
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        return _search_error("invalid_request", "limit must be a positive integer")
    service_payload = {
        "scope": scope,
        "namespace": namespace,
        "query": query,
        "limit": limit,
        "include_episodes": bool(payload.get("include_episodes", scope == "session")),
    }
    response = service.dispatch("memory_search", service_payload)
    if not response.get("ok"):
        return _search_error(
            str(response.get("error", {}).get("code", "integration_failed")),
            str(response.get("error", {}).get("message", "memory search failed")),
        )
    results = []
    for index, record in enumerate(response.get("results") or []):
        path = _record_alias_path(record)
        snippet = _render_value(record.get("value"))
        line_count = max(1, len(snippet.splitlines()) or 1)
        score = round(max(0.0, 1.0 - (index * 0.05)), 4)
        results.append(
            {
                "path": path,
                "snippet": snippet,
                "score": score,
                "startLine": 1,
                "endLine": line_count,
                "source": "memory",
                "citation": f"{path}#L1-L{line_count}",
            }
        )
    return {"results": results}


def _memory_get(service: MemoryService, config: PluginConfig, payload: dict[str, Any]) -> dict[str, Any]:
    rel_path = payload.get("path")
    start_line = _positive_int(payload.get("from"))
    line_count = _positive_int(payload.get("lines"))
    if isinstance(rel_path, str) and rel_path.strip():
        sandbox = _validate_workspace_path(config.workspace_root, rel_path)
        if not sandbox["ok"]:
            return sandbox["result"]
        validated_path = str(sandbox["path"])
        alias_record = _record_from_alias_path(service, config, payload, validated_path)
        if alias_record is not None:
            return _excerpt_result(
                path=validated_path,
                text=_record_to_markdown(alias_record),
                start_line=start_line,
                line_count=line_count,
            )
        actual_path = config.workspace_root / validated_path
        if actual_path.is_file():
            try:
                return _excerpt_result(
                    path=validated_path,
                    text=actual_path.read_text(encoding="utf-8"),
                    start_line=start_line,
                    line_count=line_count,
                )
            except (OSError, UnicodeDecodeError) as exc:
                return _read_error(validated_path, "integration_failed", str(exc))
        return {"path": validated_path, "text": ""}
    topic = payload.get("topic")
    memory_type = payload.get("memory_type")
    if not isinstance(topic, str) or not topic.strip():
        return _read_error("", "invalid_request", "path or topic is required")
    scope = str(payload.get("scope") or "repository")
    namespace = str(payload.get("namespace") or _namespace_for_scope(config, scope))
    record = _record_from_subject(
        service,
        scope=scope,
        namespace=namespace,
        topic=topic,
        field=payload.get("field"),
        memory_type=str(memory_type) if memory_type else None,
    )
    alias_path = _subject_alias_path(topic, payload.get("field"))
    if record is None:
        return {"path": alias_path, "text": ""}
    return _excerpt_result(
        path=alias_path,
        text=_record_to_markdown(record),
        start_line=start_line,
        line_count=line_count,
    )


def _namespace_for_scope(config: PluginConfig, scope: str) -> str:
    if scope == "session":
        token = slugify(config.session_key or "default")
        return f"{config.namespace}__session__{token}"
    return config.namespace


def _validate_workspace_path(workspace_root: Path, raw_path: str) -> dict[str, Any]:
    candidate = Path(raw_path).expanduser()
    try:
        if candidate.is_absolute():
            resolved = candidate.resolve()
            rel_path = resolved.relative_to(workspace_root).as_posix()
        else:
            rel_path = PurePosixPath(raw_path).as_posix()
            resolved = (workspace_root / rel_path).resolve()
            resolved.relative_to(workspace_root)
    except Exception:
        return {
            "ok": False,
            "result": _read_error(raw_path, "invalid_request", "path must stay under the configured workspace root"),
        }
    normalized = PurePosixPath(rel_path).as_posix()
    if normalized == "MEMORY.md" or normalized.startswith("memory/") or normalized.startswith(".ai-memory/"):
        return {"ok": True, "path": PurePosixPath(normalized)}
    return {
        "ok": False,
        "result": _read_error(raw_path, "invalid_request", "path is outside the OpenClaw memory sandbox"),
    }


def _record_from_alias_path(
    service: MemoryService,
    config: PluginConfig,
    payload: dict[str, Any],
    rel_path: str,
) -> dict[str, Any] | None:
    pure = PurePosixPath(rel_path)
    if len(pure.parts) != 3 or pure.parts[0] != "memory" or pure.suffix != ".md":
        return None
    topic_token = pure.parts[1]
    field_part = pure.stem
    field_token = None if field_part == "index" else field_part
    scope = str(payload.get("scope") or "repository")
    namespace = str(payload.get("namespace") or _namespace_for_scope(config, scope))
    search_payload = {
        "scope": scope,
        "namespace": namespace,
        "query": topic_token,
        "limit": 50,
        "include_episodes": True,
    }
    response = service.dispatch("memory_search", search_payload)
    if not response.get("ok"):
        return None
    for record in response.get("results") or []:
        if slugify(str(record.get("topic") or "memory")) != topic_token:
            continue
        record_field = record.get("field")
        record_field_token = None if record_field in (None, "") else slugify(str(record_field))
        if record_field_token == field_token:
            return dict(record)
        if field_token is None and record_field_token is None:
            return dict(record)
    return None


def _record_from_subject(
    service: MemoryService,
    *,
    scope: str,
    namespace: str,
    topic: Any,
    field: Any,
    memory_type: str | None,
) -> dict[str, Any] | None:
    memory_types = (memory_type,) if memory_type else LOOKUP_MEMORY_TYPES
    for candidate_type in memory_types:
        lookup = {
            "scope": scope,
            "namespace": namespace,
            "topic": topic,
            "field": field,
            "memory_type": candidate_type,
        }
        response = service.dispatch("memory_get", lookup)
        if response.get("ok"):
            return dict(response["record"])
    return None


def _record_alias_path(record: dict[str, Any]) -> str:
    return _subject_alias_path(record.get("topic"), record.get("field"))


def _subject_alias_path(topic: Any, field: Any) -> str:
    topic_slug = slugify(str(topic or "memory"))
    field_slug = "index" if field in (None, "") else slugify(str(field))
    return f"memory/{topic_slug}/{field_slug}.md"


def _record_to_markdown(record: dict[str, Any]) -> str:
    value = record.get("value")
    if isinstance(value, str):
        rendered_value = value.strip()
    else:
        rendered_value = "```json\n" + json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n```"
    field = record.get("field") or "(episode)"
    lines = [
        f"# {record.get('topic')} / {field}",
        "",
        f"- scope: {record.get('scope')}",
        f"- namespace: {record.get('namespace')}",
        f"- memory_type: {record.get('memory_type')}",
        f"- seq: {record.get('seq')}",
        f"- recorded_at: {record.get('recorded_at')}",
        "",
        rendered_value,
    ]
    return "\n".join(lines).strip() + "\n"


def _excerpt_result(path: str, text: str, start_line: int | None, line_count: int | None) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines:
        return {"path": path, "text": ""}
    requested_from = start_line or 1
    requested_lines = line_count or 50
    start_index = max(0, requested_from - 1)
    end_index = min(len(lines), start_index + requested_lines)
    excerpt = "\n".join(lines[start_index:end_index])
    result: dict[str, Any] = {
        "path": path,
        "text": excerpt,
        "from": start_index + 1,
        "lines": max(0, end_index - start_index),
    }
    if start_index > 0 or end_index < len(lines):
        result["truncated"] = True
    if end_index < len(lines):
        result["nextFrom"] = end_index + 1
    return result


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _search_error(code: str, message: str) -> dict[str, Any]:
    return {
        "results": [],
        "disabled": True,
        "code": code,
        "error": f"{code}: {message}",
    }


def _read_error(path: str, code: str, message: str) -> dict[str, Any]:
    return {
        "path": path,
        "text": "",
        "disabled": True,
        "code": code,
        "error": f"{code}: {message}",
    }


if __name__ == "__main__":
    raise SystemExit(main())
