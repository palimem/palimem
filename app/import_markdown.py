#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memory_service.portability import (
    default_memory_type_for_markdown,
    default_namespace_for_markdown,
    default_topic_for_markdown,
    import_jsonl_file,
    import_markdown_file,
    scope_for_markdown,
)
from memory_service.service import MemoryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Markdown MEMORY.md / USER.md files or JSONL exports into a memory-service data directory."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory containing memory_service.sqlite3 (same as MEMORY_SERVICE_DATA_DIR).",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Markdown (.md) or JSONL (.jsonl) files to import.",
    )
    parser.add_argument(
        "--scope",
        choices=("user", "session", "repository"),
        help="Override scope for Markdown imports.",
    )
    parser.add_argument(
        "--namespace",
        help="Override namespace for Markdown imports.",
    )
    parser.add_argument(
        "--topic",
        help="Override default topic for Markdown imports without explicit headings.",
    )
    parser.add_argument(
        "--memory-type",
        choices=("preference", "fact", "procedure", "constraint", "episode", "belief"),
        help="Override default memory type for Markdown imports.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse inputs and show planned writes without mutating storage.",
    )
    return parser


def _is_markdown_input(path: Path) -> bool:
    suffixes = path.suffixes
    if not suffixes:
        return False
    if suffixes[-1] == ".md":
        return True
    return len(suffixes) >= 2 and suffixes[-2] == ".md" and suffixes[-1] == ".sample"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    service = MemoryService(data_dir, "auto", None)
    results: list[dict[str, object]] = []
    try:
        for input_path in args.inputs:
            suffix = input_path.suffix.lower()
            if suffix == ".jsonl":
                results.append(
                    import_jsonl_file(service, path=input_path, dry_run=args.dry_run)
                )
                continue
            if _is_markdown_input(input_path):
                results.append(
                    import_markdown_file(
                        service,
                        path=input_path,
                        scope=scope_for_markdown(input_path, args.scope),
                        namespace=args.namespace or default_namespace_for_markdown(input_path),
                        default_topic=args.topic or default_topic_for_markdown(input_path),
                        default_memory_type=args.memory_type or default_memory_type_for_markdown(input_path),
                        source_kind=input_path.name,
                        dry_run=args.dry_run,
                    )
                )
                continue
            raise SystemExit(f"Unsupported import file type: {input_path}")

        error_count = sum(int(result["error_count"]) for result in results)
        summary = {
            "ok": error_count == 0,
            "dry_run": args.dry_run,
            "results": results,
        }
        sys.stdout.write(json.dumps(summary, ensure_ascii=True, indent=2) + "\n")
        return 0 if error_count == 0 else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
