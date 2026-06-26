from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


class ValidationFailure(AssertionError):
    """Raised when an observed behavior does not satisfy the published spec."""


class ToolCallError(RuntimeError):
    def __init__(self, code: str, message: str, payload: Any | None = None) -> None:
        self.code = code
        self.payload = payload
        super().__init__(message)


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def require_keys(payload: dict[str, Any], keys: list[str], context: str) -> None:
    missing = [key for key in keys if key not in payload]
    ensure(not missing, f"{context} is missing required field(s): {', '.join(missing)}")


def expect_error_code(exc: ToolCallError, expected_code: str, context: str) -> None:
    ensure(
        exc.code == expected_code,
        f"{context} should fail with code '{expected_code}', received '{exc.code}'",
    )


@dataclass(slots=True)
class BehaviorResult:
    behavior: str
    status: str
    reason: str | None = None
    notes: str | None = None
    group: str | None = None
    spec_section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass(slots=True)
class BehaviorCase:
    behavior: str
    group: str
    spec_section: str
    execute: Callable[["ValidationContext"], str | None]


@dataclass(slots=True)
class SuiteArtifact:
    spec_version: str
    generated_at: str
    run_command: str
    results: list[BehaviorResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_version": self.spec_version,
            "generated_at": self.generated_at,
            "run_command": self.run_command,
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(slots=True)
class ServiceStartup:
    command: str
    ready_file: Path
    control_dir: Path
    data_dir: Path
    schema_mode: str = "fresh"
    upgrade_fixture: str | None = None
    namespace_seed: str = "validation"


class ValidationContext:
    """Protocol-like base to keep type checkers satisfied without runtime imports."""

    namespace_seed: str
