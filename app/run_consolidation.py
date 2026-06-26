#!/usr/bin/env python3
"""Operator CLI: run memory_consolidate on a schedule or at session end."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memory_service.service import MemoryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run explicit memory consolidation for one namespace.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--scope", default="repository")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--export-review",
        type=Path,
        help="After consolidation, export pending promotions to review.md at this path.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = MemoryService(Path(args.data_dir), "auto", None)
    try:
        result = service.memory_consolidate(
            {
                "scope": args.scope,
                "namespace": args.namespace,
                "dry_run": args.dry_run,
            }
        )
        if not result.get("ok"):
            sys.stderr.write(json.dumps(result) + "\n")
            return 1

        if args.export_review and not args.dry_run:
            from review_memory import _render_review_markdown

            pending = service.memory_review(
                {
                    "scope": args.scope,
                    "namespace": args.namespace,
                    "action": "list",
                    "limit": 100,
                }
            ).get("pending", [])
            args.export_review.parent.mkdir(parents=True, exist_ok=True)
            args.export_review.write_text(
                _render_review_markdown(args.scope, args.namespace, pending),
                encoding="utf-8",
            )

        if not args.quiet:
            sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
