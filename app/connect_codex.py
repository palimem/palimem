#!/usr/bin/env python3
"""Merge memory-service MCP registration into OpenAI Codex config.toml."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


SERVER_NAME = "memory-service"
DEFAULT_DATA_DIR = ".ai-memory/data"

# Section header in TOML
_MCP_SECTION = f"mcp_servers.{SERVER_NAME}"


def _default_global_config() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home / "config.toml"


def _memory_service_entry(data_dir: str, project_root: Path) -> dict[str, Any]:
    mcp_script = project_root / "components" / "memory-service" / "app" / "scripts" / "memory-service-mcp.js"
    if not mcp_script.is_file():
        mcp_script = Path(__file__).resolve().parent / "scripts" / "memory-service-mcp.js"
    return {
        "enabled": True,
        "command": "node",
        "args": [str(mcp_script)],
        "env": {
            "MEMORY_SERVICE_DATA_DIR": data_dir,
        },
    }


# ---------------------------------------------------------------------------
# Minimal TOML helpers — we only need to read/write the [mcp_servers.*]
# sections without a full TOML parser dependency.
# ---------------------------------------------------------------------------

def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _entry_to_toml_block(entry: dict[str, Any]) -> str:
    lines: list[str] = [f"[mcp_servers.{SERVER_NAME}]"]
    lines.append(f'enabled = {str(entry["enabled"]).lower()}')
    lines.append(f'command = {_toml_str(entry["command"])}')
    args_toml = "[" + ", ".join(_toml_str(a) for a in entry["args"]) + "]"
    lines.append(f"args = {args_toml}")
    lines.append("")
    lines.append(f"[mcp_servers.{SERVER_NAME}.env]")
    for k, v in entry["env"].items():
        lines.append(f"{k} = {_toml_str(v)}")
    lines.append("")
    return "\n".join(lines)


def _section_pattern(section: str) -> re.Pattern[str]:
    """Return regex that matches from [section] to the next top-level section (or EOF)."""
    escaped = re.escape(section)
    return re.compile(
        rf"^\[{escaped}\][^\[]*(?:^\[[^\[{{]][^\]]*\][^\[]*)*",
        re.MULTILINE,
    )


def _has_section(toml_text: str, section: str) -> bool:
    return bool(re.search(rf"^\[{re.escape(section)}\]", toml_text, re.MULTILINE))


def _remove_section_and_subsections(toml_text: str, base_section: str) -> str:
    """Remove [base_section] and any [base_section.*] blocks."""
    escaped = re.escape(base_section)
    pattern = re.compile(
        rf"(\n|\A)\[{escaped}(?:\.[^\]]+)?\][^\[]*",
        re.MULTILINE,
    )
    cleaned = pattern.sub(lambda m: "\n" if m.group(1) == "\n" else "", toml_text)
    return cleaned.strip()


def merge_toml(existing_text: str, entry: dict[str, Any], *, replace: bool) -> str:
    main_section = f"mcp_servers.{SERVER_NAME}"
    env_section = f"{main_section}.env"

    has_main = _has_section(existing_text, main_section)
    has_env = _has_section(existing_text, env_section)

    if (has_main or has_env) and not replace:
        sys.stderr.write(
            f"Server {SERVER_NAME!r} already exists in config.toml; pass --replace to overwrite.\n"
        )
        raise SystemExit(1)

    if has_main or has_env:
        existing_text = _remove_section_and_subsections(existing_text, main_section)

    block = _entry_to_toml_block(entry)
    separator = "\n\n" if existing_text.strip() else ""
    return (existing_text.rstrip() + separator + block).rstrip() + "\n"


def _load_toml(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def write_config(path: Path, content: str, dry_run: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        print(content)
        return
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register memory-service in Codex config.toml."
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Target config.toml (default: ~/.codex/config.toml).",
    )
    parser.add_argument(
        "--project-config",
        type=Path,
        help="Write project .codex/config.toml instead of global.",
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
        help="Print merged TOML instead of writing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    data_dir = args.data_dir
    if not Path(data_dir).is_absolute():
        data_dir = str((project_root / data_dir).resolve())

    target: Path
    if args.project_config:
        target = args.project_config
    elif args.config:
        target = args.config
    else:
        target = _default_global_config()

    entry = _memory_service_entry(data_dir, project_root)
    existing_text = _load_toml(target)

    try:
        merged = merge_toml(existing_text, entry, replace=args.replace)
    except SystemExit:
        return 1

    write_config(target, merged, args.dry_run)

    if not args.dry_run:
        print(json.dumps({"ok": True, "config": str(target), "server": SERVER_NAME}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
