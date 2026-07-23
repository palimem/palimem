#!/usr/bin/env python3
"""Merge memory-service MCP registration into Windsurf mcp_config.json.

Windsurf supports only global MCP config; project-level scoping is not available.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


from connect_common import DEFAULT_DATA_DIR, add_launcher_arg, memory_service_entry


SERVER_NAME = "memory-service"


def _default_global_config() -> Path:
    codeium_home = Path(os.environ.get("CODEIUM_HOME", Path.home() / ".codeium"))
    return codeium_home / "windsurf" / "mcp_config.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return data


def _normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    if "mcpServers" in data:
        return data
    if any(isinstance(value, dict) and "command" in value for value in data.values()):
        return {"mcpServers": data}
    return data


def merge_config(
    existing: dict[str, Any],
    entry: dict[str, Any],
    *,
    replace: bool,
) -> dict[str, Any]:
    config = _normalize_config(existing)
    servers = dict(config.get("mcpServers") or {})
    if SERVER_NAME in servers and not replace:
        raise SystemExit(1)
    servers[SERVER_NAME] = entry
    config["mcpServers"] = servers
    return config


def write_config(path: Path, config: dict[str, Any], dry_run: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, indent=2, ensure_ascii=True) + "\n"
    if dry_run:
        print(payload)
        return
    path.write_text(payload, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register memory-service in Windsurf global MCP config."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Target mcp_config.json (default: ~/.codeium/windsurf/mcp_config.json).",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root for resolving memory-service-mcp.js path.",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help="Relative or absolute MEMORY_SERVICE_DATA_DIR written into env.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Overwrite existing memory-service entry.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print merged JSON instead of writing.",
    )
    add_launcher_arg(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = args.config or _default_global_config()
    project_root = args.project_root.resolve()
    data_dir = args.data_dir
    if not Path(data_dir).is_absolute():
        data_dir = str((project_root / data_dir).resolve())

    entry = memory_service_entry(data_dir, project_root, launcher=args.launcher)
    try:
        merged = merge_config(_load_json(target), entry, replace=args.replace)
    except SystemExit:
        import sys
        sys.stderr.write(
            f"Server {SERVER_NAME!r} already exists in {target}; pass --replace to overwrite.\n"
        )
        return 1

    write_config(target, merged, args.dry_run)

    if not args.dry_run:
        print(json.dumps({"ok": True, "config": str(target), "server": SERVER_NAME}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
