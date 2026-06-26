#!/usr/bin/env python3
"""Hook-facing CLI: write governed memory without MCP (used by Claude Code lifecycle hooks)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from memory_service.errors import NotFoundError
from memory_service.service import MemoryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write memory records for harness lifecycle hooks.")
    parser.add_argument("--data-dir", required=True, help="MEMORY_SERVICE_DATA_DIR")
    parser.add_argument(
        "--stdin-json",
        action="store_true",
        help="Read a full memory_remember payload from stdin.",
    )
    parser.add_argument("--scope", choices=("user", "session", "repository"))
    parser.add_argument("--namespace")
    parser.add_argument("--memory-type")
    parser.add_argument("--topic")
    parser.add_argument("--field")
    parser.add_argument("--value")
    parser.add_argument("--episode-id")
    parser.add_argument("--expires-at")
    parser.add_argument("--skip-if-exists", action="store_true", help="No-op when subject already has current value.")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.stdin_json:
        raw = sys.stdin.read()
        if not raw.strip():
            raise SystemExit("stdin-json requested but stdin was empty")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise SystemExit("stdin JSON must be an object")
        return payload

    required = ("scope", "namespace", "memory_type", "topic", "value")
    missing = [name for name in required if getattr(args, name.replace("-", "_")) is None]
    if missing:
        raise SystemExit(f"Missing required flags: {', '.join(missing)} (or use --stdin-json)")

    payload: dict[str, Any] = {
        "scope": args.scope,
        "namespace": args.namespace,
        "memory_type": args.memory_type,
        "topic": args.topic,
        "field": args.field,
        "value": args.value,
        "provenance": {
            "source": "claude-code-hook",
            "tool": "hook_remember.py",
            "actor": "hook",
            "request_id": args.episode_id or hashlib.sha256(f"{args.topic}:{args.field}:{args.value}".encode()).hexdigest()[:16],
        },
    }
    if args.episode_id:
        payload["episode_id"] = args.episode_id
    if args.expires_at:
        payload["expires_at"] = args.expires_at
    return payload


def _subject_exists(service: MemoryService, payload: dict[str, Any]) -> bool:
    from memory_service.domain import subject_from_request

    subject = subject_from_request(payload)
    if subject.memory_type == "episode" and not subject.field:
        lookup = {
            "scope": subject.scope,
            "namespace": subject.namespace,
            "topic": subject.topic,
            "memory_type": "episode",
        }
    else:
        lookup = {
            "scope": subject.scope,
            "namespace": subject.namespace,
            "topic": subject.topic,
            "field": subject.field,
            "memory_type": subject.memory_type,
        }
    try:
        result = service.memory_get(lookup)
        return bool(result.get("ok"))
    except NotFoundError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = _payload_from_args(args)
    data_dir = Path(args.data_dir)
    service = MemoryService(data_dir, "auto", None)
    try:
        if args.skip_if_exists and _subject_exists(service, payload):
            if not args.quiet:
                sys.stdout.write(json.dumps({"ok": True, "skipped": True}) + "\n")
            return 0
        result = service.memory_remember(payload)
        if not result.get("ok"):
            sys.stderr.write(json.dumps(result) + "\n")
            return 1
        if not args.quiet:
            sys.stdout.write(json.dumps(result) + "\n")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
