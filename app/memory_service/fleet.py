from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidRequestError

SUPPORTED_FLEET_MODES = ("local", "fleet_replica")


@dataclass(frozen=True)
class FleetStatus:
    mode: str = "local"
    backend_reachable: bool = False
    last_synced_seq: int | None = None
    replica_lag_seq: int | None = None
    serve_reads_from_replica: bool = False
    max_staleness_seq: int | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "FleetStatus":
        payload = payload or {}
        mode = str(payload.get("mode", "local"))
        validate_fleet_mode(mode)
        return cls(
            mode=mode,
            backend_reachable=bool(payload.get("backend_reachable", False)),
            last_synced_seq=_optional_int(payload.get("last_synced_seq")),
            replica_lag_seq=_optional_int(payload.get("replica_lag_seq")),
            serve_reads_from_replica=bool(payload.get("serve_reads_from_replica", False)),
            max_staleness_seq=_optional_int(payload.get("max_staleness_seq")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "backend_reachable": self.backend_reachable,
            "last_synced_seq": self.last_synced_seq,
            "replica_lag_seq": self.replica_lag_seq,
            "serve_reads_from_replica": self.serve_reads_from_replica,
            "max_staleness_seq": self.max_staleness_seq,
        }


@dataclass(frozen=True)
class FleetBackendConfig:
    backend_path: str
    sync_on_write: bool = True

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        current: "FleetBackendConfig | None" = None,
    ) -> "FleetBackendConfig | None":
        if payload.get("clear_backend"):
            return None
        raw_backend_path = payload.get("backend_path")
        raw_backend_id = payload.get("backend_id")
        if raw_backend_path is not None and raw_backend_id is not None:
            raise InvalidRequestError("Provide either backend_path or backend_id, not both.")
        if raw_backend_path is None and raw_backend_id is None:
            if current is None:
                return None
            sync_on_write = payload.get("sync_on_write", current.sync_on_write)
            if not isinstance(sync_on_write, bool):
                raise InvalidRequestError("sync_on_write must be a boolean when provided.")
            return cls(backend_path=current.backend_path, sync_on_write=sync_on_write)
        if raw_backend_path is not None:
            if not isinstance(raw_backend_path, str) or not raw_backend_path.strip():
                raise InvalidRequestError("backend_path must be a non-empty string when provided.")
            backend_path = raw_backend_path.strip()
        else:
            if not isinstance(raw_backend_id, str) or not raw_backend_id.strip():
                raise InvalidRequestError("backend_id must be a non-empty string when provided.")
            backend_path = str(Path("/tmp/memory-service-fleet") / raw_backend_id.strip())
        sync_on_write = payload.get("sync_on_write", current.sync_on_write if current is not None else True)
        if not isinstance(sync_on_write, bool):
            raise InvalidRequestError("sync_on_write must be a boolean when provided.")
        return cls(backend_path=backend_path, sync_on_write=sync_on_write)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_path": self.backend_path,
            "sync_on_write": self.sync_on_write,
        }


def validate_fleet_mode(value: Any) -> str:
    if value not in SUPPORTED_FLEET_MODES:
        raise InvalidRequestError("fleet mode must be local or fleet_replica.")
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidRequestError("fleet sequence values must be non-negative integers when provided.")
    return value
