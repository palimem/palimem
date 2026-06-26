from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


DEFAULT_PLACEHOLDER = "[REDACTED_PII]"
DEFAULT_POLICY = "block"
DEFAULT_CATEGORIES = (
    "email",
    "phone",
    "government_id",
    "financial_account",
    "ip_address",
    "free_text_name",
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
PHONE_RE = re.compile(r"(?:\+\d{8,15}\b|\b(?:\d{3}[-.\s]?){2}\d{4}\b|\(\d{3}\)\s*\d{3}[-.\s]?\d{4}\b)")
IPV4_RE = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
IPV6_RE = re.compile(
    r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b|\b::(?:[A-Fa-f0-9]{1,4}:){0,5}[A-Fa-f0-9]{1,4}\b"
)


@dataclass(frozen=True)
class PiiDetection:
    category: str
    start: int
    end: int
    match: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "start": self.start,
            "end": self.end,
            "match": self.match,
        }


@dataclass(frozen=True)
class PiiScanConfig:
    enabled: bool = False
    policy: str = DEFAULT_POLICY
    placeholder: str = DEFAULT_PLACEHOLDER
    categories: tuple[str, ...] = DEFAULT_CATEGORIES
    enabled_memory_types: tuple[str, ...] = ()
    government_id_patterns: tuple[str, ...] = ()
    financial_account_patterns: tuple[str, ...] = ()
    free_text_names: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "PiiScanConfig":
        payload = payload or {}
        enabled = bool(payload.get("enabled", False))
        policy_value = payload.get("policy")
        placeholder_value = payload.get("placeholder")
        policy = str(policy_value) if policy_value is not None else DEFAULT_POLICY
        placeholder = str(placeholder_value) if placeholder_value is not None else DEFAULT_PLACEHOLDER
        categories = tuple(str(item) for item in (payload.get("categories") or DEFAULT_CATEGORIES))
        enabled_memory_types = tuple(str(item) for item in (payload.get("enabled_memory_types") or ()))
        government_id_patterns = tuple(str(item) for item in (payload.get("government_id_patterns") or ()))
        financial_account_patterns = tuple(str(item) for item in (payload.get("financial_account_patterns") or ()))
        free_text_names = tuple(str(item) for item in (payload.get("free_text_names") or ()))
        return cls(
            enabled=enabled,
            policy=policy,
            placeholder=placeholder,
            categories=categories,
            enabled_memory_types=enabled_memory_types,
            government_id_patterns=government_id_patterns,
            financial_account_patterns=financial_account_patterns,
            free_text_names=free_text_names,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "policy": self.policy,
            "placeholder": self.placeholder,
            "categories": list(self.categories),
            "enabled_memory_types": list(self.enabled_memory_types),
            "government_id_patterns": list(self.government_id_patterns),
            "financial_account_patterns": list(self.financial_account_patterns),
            "free_text_names": list(self.free_text_names),
        }

    def applies_to(self, memory_type: str) -> bool:
        if not self.enabled:
            return False
        if not self.enabled_memory_types:
            return True
        return memory_type in self.enabled_memory_types


@dataclass(frozen=True)
class PiiScanResult:
    value: Any
    metadata: dict[str, Any]
    blocked: bool = False


class PiiScanner:
    def scan(self, value: Any, *, memory_type: str, config: PiiScanConfig) -> PiiScanResult:
        if not config.applies_to(memory_type):
            return PiiScanResult(value=value, metadata={"enabled": False}, blocked=False)
        document = _serialize_value(value)
        detections = self._detect(document, config)
        if not detections:
            return PiiScanResult(
                value=value,
                metadata={"enabled": True, "policy": config.policy, "categories": [], "detections": []},
                blocked=False,
            )

        metadata = {
            "enabled": True,
            "policy": config.policy,
            "placeholder": config.placeholder,
            "categories": sorted({item.category for item in detections}),
            "detections": [item.as_dict() for item in detections],
        }
        if config.policy == "block":
            return PiiScanResult(value=value, metadata=metadata, blocked=True)
        if config.policy == "annotate":
            return PiiScanResult(value=value, metadata=metadata, blocked=False)
        redacted_document = _apply_redactions(document, detections, config.placeholder)
        metadata["redacted"] = True
        return PiiScanResult(
            value=_deserialize_value(redacted_document, value),
            metadata=metadata,
            blocked=False,
        )

    def _detect(self, document: str, config: PiiScanConfig) -> list[PiiDetection]:
        detections: list[PiiDetection] = []
        categories = set(config.categories)
        if "email" in categories:
            detections.extend(_regex_detections("email", EMAIL_RE, document))
        if "phone" in categories:
            detections.extend(_regex_detections("phone", PHONE_RE, document))
        if "government_id" in categories:
            detections.extend(_custom_pattern_detections("government_id", config.government_id_patterns, document))
        if "financial_account" in categories:
            detections.extend(
                _custom_pattern_detections("financial_account", config.financial_account_patterns, document)
            )
        if "ip_address" in categories:
            detections.extend(_regex_detections("ip_address", IPV4_RE, document))
            detections.extend(_regex_detections("ip_address", IPV6_RE, document))
        if "free_text_name" in categories:
            detections.extend(_name_list_detections(config.free_text_names, document))
        return _dedupe_detections(detections)


def _serialize_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _deserialize_value(document: str, template: Any) -> Any:
    if isinstance(template, str):
        return document
    return json.loads(document)


def _regex_detections(category: str, pattern: re.Pattern[str], document: str) -> list[PiiDetection]:
    return [
        PiiDetection(category=category, start=match.start(), end=match.end(), match=match.group(0))
        for match in pattern.finditer(document)
    ]


def _custom_pattern_detections(category: str, patterns: tuple[str, ...], document: str) -> list[PiiDetection]:
    detections: list[PiiDetection] = []
    for pattern in patterns:
        compiled = re.compile(pattern)
        detections.extend(_regex_detections(category, compiled, document))
    return detections


def _name_list_detections(names: tuple[str, ...], document: str) -> list[PiiDetection]:
    detections: list[PiiDetection] = []
    for name in names:
        if not name:
            continue
        pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
        detections.extend(_regex_detections("free_text_name", pattern, document))
    return detections


def _dedupe_detections(detections: list[PiiDetection]) -> list[PiiDetection]:
    ordered: list[PiiDetection] = []
    seen: set[tuple[str, int, int, str]] = set()
    for detection in sorted(detections, key=lambda item: (item.start, item.end, item.category, item.match)):
        key = (detection.category, detection.start, detection.end, detection.match)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(detection)
    return ordered


def _apply_redactions(document: str, detections: list[PiiDetection], placeholder: str) -> str:
    merged: list[tuple[int, int]] = []
    for detection in sorted(detections, key=lambda item: (item.start, item.end)):
        if not merged or detection.start > merged[-1][1]:
            merged.append((detection.start, detection.end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], detection.end))

    redacted = document
    for start, end in reversed(merged):
        redacted = redacted[:start] + placeholder + redacted[end:]
    return redacted
