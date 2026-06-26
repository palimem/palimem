#!/usr/bin/env python3
"""Hook/demo CLI: search governed memory without MCP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from memory_service.service import MemoryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search memory for hook demos and smoke tests.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--scope", required=True, choices=("user", "session", "repository"))
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--include-episodes", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = MemoryService(Path(args.data_dir), "auto", None)
    try:
        result = service.memory_search(
            {
                "scope": args.scope,
                "namespace": args.namespace,
                "query": args.query,
                "include_episodes": args.include_episodes,
                "limit": args.limit,
            }
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
        return 0 if result.get("ok") else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
