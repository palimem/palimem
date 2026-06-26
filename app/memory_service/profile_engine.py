from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import threading
from typing import Any, Protocol

from .context_fencing import KNOWN_INJECTION_IDS, apply_context_fencing
from .domain import PROFILE_ENGINE_BELIEF_SALIENCE, SubjectKey, utc_now_rfc3339
from .errors import ExtractionDisabledError


@dataclass(frozen=True)
class EpisodeCandidate:
    episode_id: str
    value: Any
    recorded_at: str


@dataclass(frozen=True)
class ProfileEngineConfig:
    namespace: str
    scope: str
    persona_id: str | None = None
    max_candidates: int = 50


@dataclass(frozen=True)
class ProfileExtractionItem:
    memory_type: str
    topic: str
    field: str
    value: Any
    derived_from: list[str]
    confidence: float | None = None


@dataclass(frozen=True)
class ProfileExtractionResult:
    items: list[ProfileExtractionItem]


class ProfileExtractor(Protocol):
    def extract_profile(self, candidates: list[EpisodeCandidate], config: ProfileEngineConfig) -> ProfileExtractionResult:
        ...


class DeterministicProfileExtractor:
    """Validation-mode stub: derive preference/belief records from episode text deterministically."""

    _PREFERENCE_RE = re.compile(r"preference:(\S+)\s*=\s*(.+)", re.IGNORECASE)
    _BELIEF_RE = re.compile(r"belief:(\S+)\s*=\s*(.+)", re.IGNORECASE)
    _FACT_RE = re.compile(r"fact:(\S+)\s*=\s*(.+)", re.IGNORECASE)

    def extract_profile(self, candidates: list[EpisodeCandidate], config: ProfileEngineConfig) -> ProfileExtractionResult:
        del config
        items: list[ProfileExtractionItem] = []
        for episode in candidates:
            text = self._episode_text(episode.value)
            lowered = text.lower()
            for match in self._PREFERENCE_RE.finditer(text):
                field, value = match.group(1), match.group(2).strip()
                items.append(
                    ProfileExtractionItem(
                        memory_type="preference",
                        topic="profile_engine",
                        field=field,
                        value=value,
                        derived_from=[episode.episode_id],
                    )
                )
            for match in self._BELIEF_RE.finditer(text):
                field, value = match.group(1), match.group(2).strip()
                items.append(
                    ProfileExtractionItem(
                        memory_type="belief",
                        topic="profile_engine",
                        field=field,
                        value=value,
                        derived_from=[episode.episode_id],
                    )
                )
            for match in self._FACT_RE.finditer(text):
                field, value = match.group(1), match.group(2).strip()
                items.append(
                    ProfileExtractionItem(
                        memory_type="fact",
                        topic="profile_engine",
                        field=field,
                        value=value,
                        derived_from=[episode.episode_id],
                    )
                )
            if "prefer" in lowered and not any(item.derived_from == [episode.episode_id] for item in items):
                items.append(
                    ProfileExtractionItem(
                        memory_type="belief",
                        topic="profile_engine",
                        field="stated_preference",
                        value=text.strip(),
                        derived_from=[episode.episode_id],
                    )
                )
        return ProfileExtractionResult(items=items)

    def _episode_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            parts = []
            for key in ("user", "assistant", "text", "body"):
                item = value.get(key)
                if isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return json.dumps(value, ensure_ascii=True, sort_keys=True)


class ProfileEngine:
    def __init__(self, service: Any, extractor: ProfileExtractor | None = None) -> None:
        self._service = service
        self._extractor = extractor or self._default_extractor()
        self._global_enabled = self._read_global_enabled()
        self._lock = threading.Lock()

    def is_enabled(self, scope: str, namespace: str) -> bool:
        if self._global_enabled:
            return True
        if namespace in self._service.storage.get_profile_engine_enabled_namespaces():
            return True
        status = self._service.storage.get_namespace_status(scope, namespace)
        return bool(status.get("profile_engine_enabled"))

    def set_enabled(self, scope: str, namespace: str, enabled: bool) -> None:
        self._service.storage.set_profile_engine_enabled_namespace(namespace, enabled)
        for candidate_scope in ("user", "session", "repository"):
            self._service.storage.set_profile_engine_enabled(candidate_scope, namespace, enabled)

    def run_async(
        self,
        scope: str,
        namespace: str,
        *,
        persona_id: str | None = None,
        source_scope: str | None = None,
        source_namespace: str | None = None,
    ) -> None:
        thread = threading.Thread(
            target=self._run_safe,
            args=(scope, namespace),
            kwargs={
                "persona_id": persona_id,
                "source_scope": source_scope,
                "source_namespace": source_namespace,
            },
            name=f"profile-engine-{namespace}",
            daemon=True,
        )
        thread.start()

    def run_sync(
        self,
        scope: str,
        namespace: str,
        *,
        persona_id: str | None = None,
        require_enabled: bool = True,
        source_scope: str | None = None,
        source_namespace: str | None = None,
    ) -> dict[str, Any]:
        if require_enabled and not self.is_enabled(scope, namespace):
            raise ExtractionDisabledError("profile engine extraction is disabled for this namespace.")
        return self._run(
            scope,
            namespace,
            persona_id=persona_id,
            source_scope=source_scope,
            source_namespace=source_namespace,
        )

    def _run_safe(
        self,
        scope: str,
        namespace: str,
        *,
        persona_id: str | None = None,
        source_scope: str | None = None,
        source_namespace: str | None = None,
    ) -> None:
        try:
            if not self.is_enabled(scope, namespace):
                return
            self._run(
                scope,
                namespace,
                persona_id=persona_id,
                source_scope=source_scope,
                source_namespace=source_namespace,
            )
        except Exception:
            pass

    def _run(
        self,
        scope: str,
        namespace: str,
        *,
        persona_id: str | None = None,
        source_scope: str | None = None,
        source_namespace: str | None = None,
    ) -> dict[str, Any]:
        episode_scope = source_scope or scope
        episode_namespace = source_namespace or namespace
        with self._lock:
            rows = self._service.storage.list_episodes(
                episode_scope,
                episode_namespace,
                limit=50,
                persona_id=persona_id,
            )
            candidates: list[EpisodeCandidate] = []
            for row in rows:
                episode_id = row["episode_id"] or row["event_id"]
                value = self._service._deserialize_json(row["value_json"])
                fenced = apply_context_fencing(self._value_as_text(value), list(KNOWN_INJECTION_IDS))
                candidates.append(
                    EpisodeCandidate(
                        episode_id=episode_id,
                        value=fenced,
                        recorded_at=row["recorded_at"],
                    )
                )
            config = ProfileEngineConfig(namespace=namespace, scope=scope, persona_id=persona_id)
            result = self._extractor.extract_profile(candidates, config)
            written = 0
            promoted = 0
            write_scope = "user"
            for index, item in enumerate(result.items):
                if item.memory_type == "fact":
                    review_id = f"pe_{namespace}_{item.topic}_{item.field}_{index}"
                    promotion = {
                        "review_id": review_id,
                        "proposed_memory_type": "fact",
                        "topic": item.topic,
                        "field": item.field,
                        "value": item.value,
                        "rationale": "profile engine proposed fact promotion",
                        "source_seqs": [],
                    }
                    with self._service.storage.transaction() as cursor:
                        self._service.storage.insert_review_promotion(cursor, write_scope, namespace, promotion)
                    promoted += 1
                    continue
                if item.memory_type not in {"preference", "belief"}:
                    continue
                subject = SubjectKey(
                    scope=write_scope,
                    namespace=namespace,
                    topic=item.topic,
                    field=item.field,
                    memory_type=item.memory_type,
                    persona_id=persona_id,
                )
                provenance = {
                    "source": "profile_engine",
                    "tool": "profile_engine",
                    "actor": "system",
                    "request_id": f"pe-{namespace}-{index}",
                }
                salience = PROFILE_ENGINE_BELIEF_SALIENCE if item.memory_type == "belief" else None
                self._service._write_memory(
                    subject,
                    item.value,
                    [],
                    provenance,
                    None,
                    derived_from=item.derived_from,
                    salience=salience,
                )
                written += 1
            timestamp = utc_now_rfc3339()
            self._service.storage.set_profile_engine_last_run(write_scope, namespace, timestamp)
            return {"written": written, "promoted": promoted, "last_run_at": timestamp}

    def _default_extractor(self) -> ProfileExtractor:
        return DeterministicProfileExtractor()

    def _read_global_enabled(self) -> bool:
        value = os.environ.get("MEMORY_SERVICE_PROFILE_ENGINE_ENABLED", "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _value_as_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            parts = [str(v) for v in value.values() if isinstance(v, str)]
            return "\n".join(parts)
        return str(value)
