from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

APP_ROOT = Path(__file__).resolve().parents[3] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory_service.context_fencing import KNOWN_INJECTION_IDS, apply_context_fencing
from memory_service.domain import utc_now_rfc3339
from memory_service.portability import field_from_text, import_markdown_file, parse_markdown_memories, slugify
from memory_service.service import MemoryService

LOGGER = logging.getLogger(__name__)
SUPPORTED_SCOPES = ("user", "session", "repository")
TOOL_NAMES = ("memory_search", "memory_get", "memory_remember", "memory_forget")


try:
    from agent.memory_provider import MemoryProvider
except ImportError:

    class MemoryProvider:
        @property
        def name(self) -> str:
            raise NotImplementedError


@dataclass(frozen=True)
class ProviderConfig:
    data_dir: Path
    namespace: str
    recall_mode: str
    mirror_builtin_memory: bool
    prefetch_limit: int
    sync_turn_enabled: bool
    profile_engine_enabled: bool
    context_fencing_enabled: bool


def register(ctx: Any) -> None:
    ctx.register_memory_provider(AiMemoryProvider())


class AiMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._config: ProviderConfig | None = None
        self._service: MemoryService | None = None
        self._session_id = ""
        self._hermes_home = Path.home() / ".hermes"
        self._workspace_root = Path.cwd()
        self._user_identity = ""
        self._persona_id: str | None = None
        self._writer_queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._writer_thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return "ai-memory"

    def is_available(self) -> bool:
        return (APP_ROOT / "memory_service" / "service.py").is_file()

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self.shutdown()
        self._session_id = session_id
        self._hermes_home = Path(str(kwargs.get("hermes_home") or (Path.home() / ".hermes"))).expanduser()
        self._workspace_root = self._resolve_workspace_root(kwargs)
        self._user_identity = self._resolve_user_identity(kwargs)
        self._persona_id = self._resolve_persona_id(kwargs)
        config_values = self._load_saved_config(self._hermes_home)
        self._config = self._build_config(config_values)
        self._service = MemoryService(self._config.data_dir, "auto", None)
        self._start_writer_thread()
        if self._config.profile_engine_enabled:
            self._service.profile_engine.set_enabled(
                "user",
                self._namespace_for_scope("user"),
                True,
            )
        if self._config.mirror_builtin_memory:
            self._import_builtin_profile()

    def system_prompt_block(self) -> str:
        config = self._require_config()
        lines = [
            "ai-memory is the active governed memory provider.",
            "Use repository scope for project facts, session scope for this conversation, and user scope for durable preferences.",
        ]
        profile = self._require_service().dispatch(
            "memory_profile",
            {
                "scope": "user",
                "namespace": self._namespace_for_scope("user"),
                "persona_id": self._effective_persona_id(),
                "depth": "summary",
                "budget_tokens": 2048,
            },
        )
        if profile.get("ok") and profile.get("manifest"):
            lines.append(profile["manifest"])
        else:
            profile_records = self._search_scope(
                scope="user",
                query="profile preferences procedures constraints facts",
                limit=min(3, config.prefetch_limit),
            )
            if profile_records:
                lines.append("Current user memory:")
                for record in profile_records:
                    lines.append(f"- {self._format_record_line(record, include_match_reason=False)}")
        return self._truncate_block("\n".join(lines), 2048)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        config = self._require_config()
        if config.recall_mode == "tools" or not query.strip():
            return ""
        total_limit = max(1, config.prefetch_limit)
        user_limit = max(1, total_limit // 2)
        session_limit = max(0, total_limit - user_limit)
        records: list[dict[str, Any]] = []
        records.extend(self._search_scope(scope="user", query=query, limit=user_limit))
        if session_limit:
            records.extend(self._search_scope(scope="session", query=query, limit=session_limit, session_id=session_id))
        if not records:
            return ""
        injection_id = "prefetch"
        lines = [
            f"<!-- ai-memory:begin injection_id={injection_id} -->",
        ]
        for record in records[:total_limit]:
            lines.append(f"- {self._format_record_line(record, include_match_reason=True)}")
        lines.append(f"<!-- ai-memory:end injection_id={injection_id} -->")
        return self._truncate_block("\n".join(lines), 4096)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        del query, session_id

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        config = self._require_config()
        if not config.sync_turn_enabled:
            return
        if not user_content.strip() and not assistant_content.strip():
            return
        effective_session = session_id or self._session_id
        user_text = user_content
        assistant_text = assistant_content
        if config.context_fencing_enabled:
            known_ids = self._known_injection_ids()
            user_text = apply_context_fencing(user_text, known_ids)
            assistant_text = apply_context_fencing(assistant_text, known_ids)
        payload = {
            "scope": "session",
            "namespace": self._namespace_for_scope("session", session_id=effective_session),
            "memory_type": "episode",
            "topic": "session_turn",
            "field": "turn",
            "value": {
                "user": user_text,
                "assistant": assistant_text,
                "message_count": len(messages or []),
            },
            "episode_id": effective_session or None,
            "persona_id": self._effective_persona_id(),
            "provenance": self._provenance("sync_turn"),
        }
        self._enqueue_write(lambda: self._require_service().memory_remember(payload))

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        config = self._require_config()
        if config.recall_mode == "context":
            return []
        raw_schemas = [
            self._search_tool_schema(),
            self._get_tool_schema(),
            self._remember_tool_schema(),
            self._forget_tool_schema(),
        ]
        return [{"type": "function", "function": schema} for schema in raw_schemas]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if tool_name not in TOOL_NAMES:
            return json.dumps(
                {
                    "ok": False,
                    "tool": tool_name,
                    "error": {"code": "invalid_request", "message": f"Unsupported tool: {tool_name}"},
                },
                ensure_ascii=True,
            )
        if args is not None and not isinstance(args, dict):
            return json.dumps(
                {
                    "ok": False,
                    "tool": tool_name,
                    "error": {"code": "invalid_request", "message": "Tool arguments must be a JSON object."},
                },
                ensure_ascii=True,
            )
        payload = dict(args or {})
        scope = str(payload.get("scope") or "repository")
        payload.setdefault("scope", scope)
        payload.setdefault(
            "namespace",
            self._namespace_for_scope(scope, session_id=str(kwargs.get("session_id") or payload.get("session_id") or "")),
        )
        if tool_name in {"memory_remember", "memory_forget"} and "provenance" not in payload:
            payload["provenance"] = self._provenance(tool_name)
        persona_id = self._effective_persona_id()
        if persona_id and "persona_id" not in payload:
            payload["persona_id"] = persona_id
        result = self._require_service().dispatch(tool_name, payload)
        return json.dumps(result, ensure_ascii=True)

    def shutdown(self) -> None:
        if self._writer_thread is not None and self._writer_thread.is_alive():
            self._writer_queue.put(None)
            self._writer_thread.join(timeout=2.0)
        self._writer_thread = None
        if self._service is not None:
            self._service.close()
        self._service = None

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        service = self._service
        config = self._config
        if service is None or config is None:
            return
        payload = {
            "scope": "repository",
            "namespace": self._namespace_for_scope("repository"),
            "dry_run": True,
        }
        result = service.dispatch("memory_consolidate", payload)
        LOGGER.info("ai-memory consolidation dry-run: %s", json.dumps(result, ensure_ascii=True))

        session_namespace = self._namespace_for_scope("session")
        user_namespace = self._namespace_for_scope("user")
        summary_text = self._build_session_summary(messages)
        if summary_text:
            self._enqueue_write(
                lambda: service.update_session_summary(
                    "session",
                    session_namespace,
                    summary_text,
                    persona_id=self._effective_persona_id(),
                )
            )
        if config.profile_engine_enabled:
            self._enqueue_write(
                lambda: service.run_profile_engine(
                    "user",
                    user_namespace,
                    persona_id=self._effective_persona_id(),
                    require_enabled=True,
                    async_run=True,
                    source_scope="session",
                    source_namespace=session_namespace,
                )
            )

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        del parent_session_id, reset, rewound, kwargs
        self._session_id = new_session_id

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        snippets = self._extract_user_snippets(messages)
        namespace = self._namespace_for_scope("repository")
        for snippet in snippets:
            field = f"snippet_{hashlib.sha1(snippet.encode('utf-8')).hexdigest()[:12]}"
            self._remember_if_changed(
                {
                    "scope": "repository",
                    "namespace": namespace,
                    "memory_type": "fact",
                    "topic": "compaction_checkpoint",
                    "field": field,
                    "value": snippet,
                    "provenance": self._provenance("on_pre_compress"),
                }
            )
        self._remember_if_changed(
            {
                "scope": "repository",
                "namespace": namespace,
                "memory_type": "fact",
                "topic": "compaction_checkpoint",
                "field": "last_flush",
                "value": {
                    "at": utc_now_rfc3339(),
                    "session_id": self._session_id or None,
                    "message_count": len(messages),
                    "snippet_count": len(snippets),
                },
                "provenance": self._provenance("on_pre_compress"),
            }
        )
        if not snippets:
            return ""
        return "ai-memory checkpointed recent user snippets before compression."

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        config = self._config
        if config is None or not config.mirror_builtin_memory:
            return
        normalized_target = target.strip().lower()
        if normalized_target not in {"memory", "user"}:
            return
        scope = "user" if normalized_target == "user" else "repository"
        default_topic = "user_profile" if normalized_target == "user" else "project_memory"
        default_memory_type = "preference" if normalized_target == "user" else "fact"
        namespace = self._namespace_for_scope(scope)
        if action == "remove":
            self._mirror_remove(metadata or {}, scope=scope, namespace=namespace, default_memory_type=default_memory_type)
            return
        content_text = content
        if self._config and self._config.context_fencing_enabled:
            content_text = apply_context_fencing(content, self._known_injection_ids())
        if not content_text.strip():
            return
        drafts = parse_markdown_memories(
            content_text,
            default_topic=default_topic,
            default_memory_type=default_memory_type,
        )
        if not drafts:
            try:
                draft_field = field_from_text(content_text)
            except ValueError:
                draft_field = f"entry_{hashlib.sha1(content_text.encode('utf-8')).hexdigest()[:12]}"
            drafts = [
                type(
                    "Draft",
                    (),
                    {
                        "topic": default_topic,
                        "field": draft_field,
                        "memory_type": default_memory_type,
                        "value": content_text.strip(),
                        "section_title": target,
                    },
                )()
            ]
        for index, draft in enumerate(drafts):
            payload = {
                "scope": scope,
                "namespace": namespace,
                "memory_type": draft.memory_type,
                "topic": draft.topic,
                "field": draft.field,
                "value": draft.value,
                "persona_id": self._effective_persona_id(),
                "provenance": self._provenance(
                    "on_memory_write",
                    {
                        "source": f"hermes-{normalized_target}",
                        "request_id": f"{normalized_target}:{index}:{uuid.uuid4().hex[:8]}",
                        "section": getattr(draft, "section_title", target),
                    },
                ),
            }
            self._enqueue_write(lambda payload=payload: self._require_service().memory_remember(payload))

    def backup_paths(self) -> list[str]:
        if self._config is None:
            return []
        return [str(self._config.data_dir)]

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "data_dir",
                "description": "SQLite data directory for memory-service.",
                "default": "<workspace>/.ai-memory/data",
            },
            {
                "key": "namespace",
                "description": "Default repository namespace.",
                "default": "<workspace basename>",
            },
            {
                "key": "recall_mode",
                "description": "Context injection, tools, or both.",
                "choices": ["hybrid", "context", "tools"],
                "default": "hybrid",
            },
            {
                "key": "mirror_builtin_memory",
                "description": "Mirror MEMORY.md / USER.md writes into governed storage.",
                "default": False,
            },
            {
                "key": "prefetch_limit",
                "description": "Maximum results injected into Hermes prefetch context.",
                "default": 5,
            },
            {
                "key": "sync_turn_enabled",
                "description": "Persist completed turns as session-scope episodes.",
                "default": True,
            },
            {
                "key": "profile_engine_enabled",
                "description": "Opt-in background profile extraction.",
                "default": False,
            },
            {
                "key": "context_fencing_enabled",
                "description": "Strip prior injection markers before auto-capture.",
                "default": True,
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        path = self._provider_config_path(Path(hermes_home).expanduser())
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(values, ensure_ascii=True, indent=2) + "\n"
        path.write_text(payload, encoding="utf-8")

    def _resolve_workspace_root(self, kwargs: dict[str, Any]) -> Path:
        for key in ("workspace_root", "cwd", "project_root", "agent_workspace_root"):
            value = kwargs.get(key)
            if isinstance(value, str) and value.strip():
                return Path(value).expanduser().resolve()
        if isinstance(kwargs.get("agent_workspace"), str) and kwargs["agent_workspace"].strip():
            return Path(str(kwargs["agent_workspace"])).expanduser().resolve()
        return Path.cwd().resolve()

    def _resolve_user_identity(self, kwargs: dict[str, Any]) -> str:
        for key in ("user_id", "user_id_alt", "agent_identity", "platform_user_id"):
            value = kwargs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _build_config(self, raw_values: dict[str, Any]) -> ProviderConfig:
        data_dir_value = os.environ.get("MEMORY_SERVICE_DATA_DIR") or raw_values.get("data_dir")
        if isinstance(data_dir_value, str) and data_dir_value.strip():
            data_dir = Path(data_dir_value).expanduser()
            if not data_dir.is_absolute():
                data_dir = (self._workspace_root / data_dir).resolve()
        else:
            data_dir = (self._workspace_root / ".ai-memory" / "data").resolve()
        namespace = str(raw_values.get("namespace") or self._workspace_root.name or "workspace").strip()
        if not namespace:
            namespace = "workspace"
        recall_mode = str(raw_values.get("recall_mode") or "hybrid").strip().lower()
        if recall_mode not in {"hybrid", "context", "tools"}:
            recall_mode = "hybrid"
        prefetch_limit = raw_values.get("prefetch_limit", 5)
        try:
            prefetch_limit = int(prefetch_limit)
        except (TypeError, ValueError):
            prefetch_limit = 5
        if prefetch_limit <= 0:
            prefetch_limit = 5
        return ProviderConfig(
            data_dir=data_dir,
            namespace=namespace,
            recall_mode=recall_mode,
            mirror_builtin_memory=self._coerce_bool(raw_values.get("mirror_builtin_memory", False)),
            prefetch_limit=prefetch_limit,
            sync_turn_enabled=self._coerce_bool(raw_values.get("sync_turn_enabled", True)),
            profile_engine_enabled=self._coerce_bool(raw_values.get("profile_engine_enabled", False)),
            context_fencing_enabled=self._coerce_bool(raw_values.get("context_fencing_enabled", True)),
        )

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _provider_config_path(self, hermes_home: Path) -> Path:
        return hermes_home / "plugins" / "memory" / "ai-memory" / "config.json"

    def _load_saved_config(self, hermes_home: Path) -> dict[str, Any]:
        path = self._provider_config_path(hermes_home)
        if not path.is_file():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return data

    def _namespace_for_scope(self, scope: str, *, session_id: str = "") -> str:
        config = self._require_config()
        base = config.namespace
        if scope == "repository":
            return base
        if scope == "session":
            token = slugify(session_id or self._session_id or "default")
            return f"{base}__session__{token}"
        if self._user_identity:
            return f"{base}__user__{slugify(self._user_identity)}"
        return base

    def _provenance(self, tool_name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "source": "hermes",
            "tool": tool_name,
            "actor": "memory-provider",
            "request_id": uuid.uuid4().hex[:16],
        }
        if extra:
            payload.update(extra)
        return payload

    def _resolve_persona_id(self, kwargs: dict[str, Any]) -> str | None:
        for key in ("persona_id", "profile_id", "agent_persona_id"):
            value = kwargs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _effective_persona_id(self) -> str | None:
        return self._persona_id

    def _known_injection_ids(self) -> list[str]:
        return list(KNOWN_INJECTION_IDS)

    def _build_session_summary(self, messages: list[dict[str, Any]]) -> str:
        config = self._config
        snippets: list[str] = []
        for message in messages[-12:]:
            role = message.get("role")
            text = self._message_text(message).strip()
            if not text:
                continue
            if config and config.context_fencing_enabled:
                text = apply_context_fencing(text, self._known_injection_ids())
            snippets.append(f"{role}: {text[:400]}")
        return "\n".join(snippets)

    def _search_scope(
        self,
        *,
        scope: str,
        query: str,
        limit: int,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        payload = {
            "scope": scope,
            "namespace": self._namespace_for_scope(scope, session_id=session_id),
            "query": query,
            "limit": max(1, limit),
            "include_episodes": scope == "session",
        }
        persona_id = self._effective_persona_id()
        if persona_id:
            payload["persona_id"] = persona_id
        result = self._require_service().dispatch("memory_search", payload)
        if not result.get("ok"):
            LOGGER.debug("ai-memory search returned error: %s", result)
            return []
        return list(result.get("results") or [])

    def _format_record_line(self, record: dict[str, Any], *, include_match_reason: bool) -> str:
        field = record.get("field") or "(episode)"
        value = self._render_value(record.get("value"))
        parts = [f"{record.get('scope')}:{record.get('topic')}.{field} [{record.get('memory_type')}] = {value}"]
        if include_match_reason and record.get("match_reason"):
            parts.append(f"match={record['match_reason']}")
        return "; ".join(parts)

    def _render_value(self, value: Any) -> str:
        if isinstance(value, str):
            rendered = value.strip()
        else:
            rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
        return self._truncate_block(rendered, 240)

    def _truncate_block(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    def _start_writer_thread(self) -> None:
        self._writer_thread = threading.Thread(target=self._writer_loop, name="ai-memory-sync", daemon=True)
        self._writer_thread.start()

    def _enqueue_write(self, operation: Callable[[], None]) -> None:
        if self._writer_thread is None or not self._writer_thread.is_alive():
            self._start_writer_thread()
        self._writer_queue.put(operation)

    def _writer_loop(self) -> None:
        while True:
            operation = self._writer_queue.get()
            if operation is None:
                self._writer_queue.task_done()
                break
            try:
                operation()
            except Exception:
                LOGGER.exception("ai-memory background write failed")
            finally:
                self._writer_queue.task_done()

    def _remember_if_changed(self, payload: dict[str, Any]) -> None:
        lookup = {
            "scope": payload["scope"],
            "namespace": payload["namespace"],
            "topic": payload["topic"],
            "field": payload["field"],
            "memory_type": payload["memory_type"],
        }
        current = self._require_service().dispatch("memory_get", lookup)
        if current.get("ok") and current.get("record", {}).get("value") == payload.get("value"):
            return
        self._require_service().memory_remember(payload)

    def _extract_user_snippets(self, messages: list[dict[str, Any]]) -> list[str]:
        snippets: list[str] = []
        for message in messages[-24:]:
            if message.get("role") != "user":
                continue
            text = self._message_text(message)
            trimmed = text.strip()
            if len(trimmed) < 24:
                continue
            snippets.append(trimmed[:1200])
        return snippets[-6:]

    def _message_text(self, message: dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "\n".join(parts)
        return ""

    def _mirror_remove(
        self,
        metadata: dict[str, Any],
        *,
        scope: str,
        namespace: str,
        default_memory_type: str,
    ) -> None:
        topic = metadata.get("topic")
        field = metadata.get("field")
        if not isinstance(topic, str) or not topic.strip():
            return
        payload = {
            "scope": scope,
            "namespace": namespace,
            "topic": topic,
            "field": field,
            "memory_type": metadata.get("memory_type") or default_memory_type,
            "provenance": self._provenance("on_memory_write", {"source": "hermes-remove"}),
        }
        self._enqueue_write(lambda payload=payload: self._require_service().memory_forget(payload))

    def _import_builtin_profile(self) -> None:
        profile_path = self._hermes_home / "USER.md"
        if not profile_path.is_file():
            return
        result = import_markdown_file(
            self._require_service(),
            path=profile_path,
            scope="user",
            namespace=self._namespace_for_scope("user"),
            default_topic="user_profile",
            default_memory_type="preference",
            source_kind="USER.md",
        )
        LOGGER.info("ai-memory imported USER.md on initialize: %s", json.dumps(result, ensure_ascii=True))

    def _require_service(self) -> MemoryService:
        if self._service is None:
            raise RuntimeError("ai-memory provider is not initialized")
        return self._service

    def _require_config(self) -> ProviderConfig:
        if self._config is None:
            raise RuntimeError("ai-memory provider is not initialized")
        return self._config

    def _search_tool_schema(self) -> dict[str, Any]:
        return {
            "name": "memory_search",
            "description": "Search governed memory for relevant current or historical records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": list(SUPPORTED_SCOPES)},
                    "namespace": {"type": "string"},
                    "query": {"type": "string"},
                    "memory_types": {"type": "array", "items": {"type": "string"}},
                    "subject": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "field": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "as_of": {
                        "type": "object",
                        "properties": {
                            "seq": {"type": "integer"},
                            "recorded_at": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "include_episodes": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }

    def _get_tool_schema(self) -> dict[str, Any]:
        return {
            "name": "memory_get",
            "description": "Look up one governed memory record by subject key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": list(SUPPORTED_SCOPES)},
                    "namespace": {"type": "string"},
                    "topic": {"type": "string"},
                    "field": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "include_versions": {"type": "boolean"},
                    "depth": {"type": "string", "enum": ["full", "summary"]},
                    "as_of": {
                        "type": "object",
                        "properties": {
                            "seq": {"type": "integer"},
                            "recorded_at": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["topic", "memory_type"],
                "additionalProperties": False,
            },
        }

    def _remember_tool_schema(self) -> dict[str, Any]:
        return {
            "name": "memory_remember",
            "description": "Write governed memory to the shared ai-memory store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": list(SUPPORTED_SCOPES)},
                    "namespace": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "topic": {"type": "string"},
                    "field": {"type": "string"},
                    "value": {},
                    "episode_id": {"type": "string"},
                    "extends": {"type": "array"},
                    "expires_at": {"type": "string"},
                    "blocks_actions": {"type": "array", "items": {"type": "string"}},
                    "observation": {"type": "object"},
                    "provenance": {"type": "object"},
                },
                "required": ["memory_type", "topic", "value"],
                "additionalProperties": False,
            },
        }

    def _forget_tool_schema(self) -> dict[str, Any]:
        return {
            "name": "memory_forget",
            "description": "Retract a governed memory subject from current recall.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": list(SUPPORTED_SCOPES)},
                    "namespace": {"type": "string"},
                    "topic": {"type": "string"},
                    "field": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "provenance": {"type": "object"},
                },
                "required": ["topic", "memory_type"],
                "additionalProperties": False,
            },
        }
