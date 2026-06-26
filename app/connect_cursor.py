#!/usr/bin/env python3
"""Merge memory-service MCP registration into Cursor mcp.json."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


SERVER_NAME = "memory-service"
DEFAULT_DATA_DIR = ".ai-memory/data"


def _default_global_config() -> Path:
    cursor_home = Path(os.environ.get("CURSOR_HOME", Path.home() / ".cursor"))
    return cursor_home / "mcp.json"


def _memory_service_entry(data_dir: str, project_root: Path) -> dict[str, Any]:
    mcp_script = project_root / "components" / "memory-service" / "app" / "scripts" / "memory-service-mcp.js"
    if not mcp_script.is_file():
        mcp_script = Path(__file__).resolve().parent / "scripts" / "memory-service-mcp.js"
    return {
        "command": "node",
        "args": [str(mcp_script)],
        "env": {
            "MEMORY_SERVICE_DATA_DIR": data_dir,
        },
    }


def _memory_service_entry_project(data_dir: str, project_root: Path) -> dict[str, Any]:
    """Return entry with ${workspaceFolder}-relative args for project config."""
    mcp_script = project_root / "components" / "memory-service" / "app" / "scripts" / "memory-service-mcp.js"
    if not mcp_script.is_file():
        mcp_script = Path(__file__).resolve().parent / "scripts" / "memory-service-mcp.js"
    return {
        "command": "node",
        "args": [str(mcp_script)],
        "env": {
            "MEMORY_SERVICE_DATA_DIR": data_dir,
        },
    }


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
        raise SystemExit(
            1,
        )
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
        description="Register memory-service in Cursor MCP config."
    )
    parser.add_argument(
        "--global-config",
        type=Path,
        help="Target global mcp.json (default: ~/.cursor/mcp.json).",
    )
    parser.add_argument(
        "--project-config",
        type=Path,
        help="Also write project .cursor/mcp.json (parent dirs created).",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    data_dir = args.data_dir
    if not Path(data_dir).is_absolute():
        data_dir = str((project_root / data_dir).resolve())

    entry = _memory_service_entry(data_dir, project_root)

    wrote_any = False

    # Write global config unless --project-config is specified without --global
    global_target = args.global_config or _default_global_config()
    try:
        merged = merge_config(_load_json(global_target), entry, replace=args.replace)
    except SystemExit:
        import sys
        sys.stderr.write(
            f"Server {SERVER_NAME!r} already exists in {global_target}; pass --replace to overwrite.\n"
        )
        return 1
    write_config(global_target, merged, args.dry_run)
    wrote_any = True
    primary_target = global_target

    if args.project_config:
        project_entry = _memory_service_entry_project(data_dir, project_root)
        try:
            project_merged = merge_config(
                _load_json(args.project_config), project_entry, replace=args.replace
            )
        except SystemExit:
            import sys
            sys.stderr.write(
                f"Server {SERVER_NAME!r} already exists in {args.project_config}; pass --replace to overwrite.\n"
            )
            return 1
        write_config(args.project_config, project_merged, args.dry_run)
        primary_target = args.project_config

    if not args.dry_run and wrote_any:
        import json as _json
        print(_json.dumps({"ok": True, "config": str(primary_target), "server": SERVER_NAME}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
