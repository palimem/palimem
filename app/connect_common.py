"""Shared helpers for editor connect scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

NPM_PACKAGE = "@palimem/mcp"
NPX_PACKAGE = "github:palimem/palimem"
NPX_BIN = "palimem-mcp"
DEFAULT_DATA_DIR = ".ai-memory/data"


def add_launcher_arg(parser) -> None:
    parser.add_argument(
        "--launcher",
        choices=["local", "npx"],
        default="local",
        help="MCP launcher: local node script (default) or npx @palimem/mcp.",
    )


def resolve_local_mcp_script(project_root: Path) -> Path:
    candidates = [
        project_root / "app" / "scripts" / "memory-service-mcp.js",
        project_root
        / "components"
        / "memory-service"
        / "app"
        / "scripts"
        / "memory-service-mcp.js",
        Path(__file__).resolve().parent / "scripts" / "memory-service-mcp.js",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def memory_service_entry(
    data_dir: str,
    project_root: Path,
    *,
    launcher: str = "local",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if launcher == "npx":
        entry: dict[str, Any] = {
            "command": "npx",
            "args": ["-y", NPX_PACKAGE, NPX_BIN],
            "env": {"MEMORY_SERVICE_DATA_DIR": data_dir},
        }
    else:
        mcp_script = resolve_local_mcp_script(project_root)
        entry = {
            "command": "node",
            "args": [str(mcp_script)],
            "env": {"MEMORY_SERVICE_DATA_DIR": data_dir},
        }
    if extra:
        entry.update(extra)
    return entry
