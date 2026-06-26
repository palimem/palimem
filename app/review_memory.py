#!/usr/bin/env python3
"""Operator CLI for memory_review: list, export review.md, accept, reject."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from memory_service.service import MemoryService


def _render_review_markdown(scope: str, namespace: str, pending: list[dict[str, Any]]) -> str:
    lines = [
        "# Memory review queue",
        "",
        f"- **Scope:** `{scope}`",
        f"- **Namespace:** `{namespace}`",
        f"- **Pending:** {len(pending)}",
        "",
    ]
    if not pending:
        lines.append("_No pending promotion proposals._")
        lines.append("")
        return "\n".join(lines)

    for item in pending:
        lines.extend(
            [
                f"## {item.get('review_id', 'unknown')}",
                "",
                f"- **Proposed type:** `{item.get('proposed_memory_type')}`",
                f"- **Subject:** `{item.get('topic')}` / `{item.get('field')}`",
                f"- **Rationale:** {item.get('rationale', '')}",
                "",
                "### Proposed value",
                "",
                "```",
                str(item.get("value", "")),
                "```",
                "",
                "### Actions",
                "",
                f"- Accept: `ai-memory review accept --review-id {item.get('review_id')} --data-dir <dir>`",
                f"- Reject: `ai-memory review reject --review-id {item.get('review_id')} --data-dir <dir>`",
                "",
            ]
        )
    return "\n".join(lines)


def _service(data_dir: Path) -> MemoryService:
    return MemoryService(data_dir, "auto", None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review consolidation promotion proposals.")
    parser.add_argument("action", choices=("list", "export", "accept", "reject"))
    parser.add_argument("--data-dir", default=os.environ.get("MEMORY_SERVICE_DATA_DIR"))
    parser.add_argument("--scope", default="repository")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--output", type=Path, help="Write review.md (export only).")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--review-id", help="Required for accept/reject.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.data_dir:
        raise SystemExit("--data-dir is required (or set MEMORY_SERVICE_DATA_DIR).")
    if args.action in {"accept", "reject"} and not args.review_id:
        raise SystemExit("--review-id is required for accept/reject.")

    service = _service(Path(args.data_dir))
    try:
        if args.action == "list":
            result = service.memory_review(
                {
                    "scope": args.scope,
                    "namespace": args.namespace,
                    "action": "list",
                    "limit": args.limit,
                }
            )
            sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
            return 0 if result.get("ok") else 1

        if args.action == "export":
            result = service.memory_review(
                {
                    "scope": args.scope,
                    "namespace": args.namespace,
                    "action": "list",
                    "limit": args.limit,
                }
            )
            if not result.get("ok"):
                sys.stderr.write(json.dumps(result) + "\n")
                return 1
            markdown = _render_review_markdown(args.scope, args.namespace, result.get("pending", []))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(markdown, encoding="utf-8")
                sys.stderr.write(
                    json.dumps({"ok": True, "path": str(args.output), "pending": len(result.get("pending", []))}) + "\n"
                )
            else:
                sys.stdout.write(markdown)
            return 0

        if args.action == "accept":
            result = service.memory_review(
                {
                    "scope": args.scope,
                    "namespace": args.namespace,
                    "action": "accept",
                    "review_id": args.review_id,
                }
            )
            sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
            return 0 if result.get("ok") else 1

        result = service.memory_review(
            {
                "scope": args.scope,
                "namespace": args.namespace,
                "action": "reject",
                "review_id": args.review_id,
            }
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
        return 0 if result.get("ok") else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
