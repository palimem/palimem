from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .domain import SUPPORTED_MEMORY_TYPES, parse_rfc3339_utc, utc_now_rfc3339
from .errors import InvalidRequestError


@dataclass(frozen=True)
class RetentionPolicy:
    memory_type: str
    ttl_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return {"memory_type": self.memory_type, "ttl_seconds": self.ttl_seconds}


TTL_RE = re.compile(r"^(?P<amount>\d+)(?P<unit>[smhd])$")


def normalize_retention_ttl(value: Any) -> int:
    if isinstance(value, bool):
        raise InvalidRequestError("ttl_seconds must be a non-negative integer or TTL string.")
    if isinstance(value, int):
        if value < 0:
            raise InvalidRequestError("ttl_seconds must be a non-negative integer or TTL string.")
        return value
    if isinstance(value, str):
        match = TTL_RE.match(value.strip().lower())
        if match is None:
            raise InvalidRequestError("ttl_seconds must be a non-negative integer or TTL string.")
        amount = int(match.group("amount"))
        unit = match.group("unit")
        multiplier = {
            "s": 1,
            "m": 60,
            "h": 60 * 60,
            "d": 24 * 60 * 60,
        }[unit]
        return amount * multiplier
    raise InvalidRequestError("ttl_seconds must be a non-negative integer or TTL string.")


def validate_retention_memory_type(memory_type: Any) -> str:
    if memory_type not in SUPPORTED_MEMORY_TYPES:
        raise InvalidRequestError("memory_type is missing or unsupported.")
    return str(memory_type)


def retention_now(value: Any) -> str:
    if value is None:
        return utc_now_rfc3339()
    if not isinstance(value, str):
        raise InvalidRequestError("now must be an RFC3339 UTC timestamp ending in Z.")
    parse_rfc3339_utc(value)
    return value
