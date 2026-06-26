#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memory_service.portability import (
    export_jsonl_records,
    export_markdown_profile,
    iter_current_records,
)
from memory_service.service import MemoryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export current governed memory records to JSONL and/or Markdown profile files."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory containing memory_service.sqlite3 (same as MEMORY_SERVICE_DATA_DIR).",
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        help="Write JSONL export to this path.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Write Markdown profile export to this path.",
    )
    parser.add_argument(
        "--stdout",
        choices=("jsonl", "markdown"),
        help="Print export to stdout instead of writing a file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.jsonl and not args.markdown and not args.stdout:
        parser.error("Provide --jsonl, --markdown, and/or --stdout.")

    data_dir = Path(args.data_dir)
    service = MemoryService(data_dir, "auto", None)
    try:
        records = iter_current_records(service)
        jsonl_text = export_jsonl_records(records)
        markdown_text = export_markdown_profile(records)

        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            args.jsonl.write_text(jsonl_text, encoding="utf-8")
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(markdown_text, encoding="utf-8")
        if args.stdout == "jsonl":
            sys.stdout.write(jsonl_text)
        elif args.stdout == "markdown":
            sys.stdout.write(markdown_text)

        summary = {
            "ok": True,
            "record_count": len(records),
            "jsonl_path": str(args.jsonl) if args.jsonl else None,
            "markdown_path": str(args.markdown) if args.markdown else None,
        }
        sys.stderr.write(json.dumps(summary, ensure_ascii=True) + "\n")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
