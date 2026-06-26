"""Phase 6 connect CLI and integration catalog validation scenarios for spec v1.7.0 Section 21.8.

These scenarios exercise `ai-memory connect <harness>` via subprocess against temporary
directories.  They do not require a running MCP server and do not inspect application
internals — the CLI is treated as a black box whose exit code, stdout, and produced config
files are the only observable surfaces.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contracts import ValidationFailure, ensure

if TYPE_CHECKING:
    from .runner import ValidationHarness

# ---------------------------------------------------------------------------
# Filesystem constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
SPEC_DIR = REPO_ROOT / "spec"

CONNECT_COPILOT = APP_DIR / "connect_copilot.py"
CONNECT_CURSOR = APP_DIR / "connect_cursor.py"
CONNECT_WINDSURF = APP_DIR / "connect_windsurf.py"
CONNECT_CODEX = APP_DIR / "connect_codex.py"
AI_MEMORY_JS = APP_DIR / "scripts" / "ai-memory.js"
MCP_JS = APP_DIR / "scripts" / "memory-service-mcp.js"
INTEGRATIONS_YAML = SPEC_DIR / "integrations.yaml"

PYTHON = sys.executable

# P0 harness IDs required by Section 21.3
_P0_HARNESS_IDS = frozenset({
    "claude-code",
    "claude-code-plugin",
    "copilot-cli",
    "codex",
    "hermes",
    "openclaw",
    "cursor",
    "windsurf",
})

# P1 harness IDs that should carry tier D in v1.7.0
_P1_HARNESS_IDS = frozenset({
    "vscode-copilot-agent",
    "copilot-ide",
    "gemini-cli",
})

# Tier-B harnesses that must declare a connect_command in the catalog
_TIER_B_CLI_HARNESS_IDS = frozenset({
    "copilot-cli",
    "codex",
    "cursor",
    "windsurf",
})

# ---------------------------------------------------------------------------
# Optional TOML reader (Python 3.11+ stdlib; graceful fallback to text match)
# ---------------------------------------------------------------------------

try:
    import tomllib as _tomllib  # type: ignore[import-not-found]
    _HAS_TOMLLIB = True
except ImportError:
    _tomllib = None  # type: ignore[assignment]
    _HAS_TOMLLIB = False


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run_connect(
    script: Path,
    args: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a connect Python script as a subprocess, returning the result."""
    if not script.is_file():
        raise ValidationFailure(
            f"Connect script not found: {script}. "
            "Application Agent must deliver this script before Phase 6 connect tests can pass."
        )
    cmd = [PYTHON, str(script), *args]
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        env=merged_env,
    )


def _assert_success_result(
    result: subprocess.CompletedProcess[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Assert exit 0, optionally assert well-formed JSON success on stdout."""
    ensure(
        result.returncode == 0,
        f"Expected exit 0 (success), got {result.returncode}.\nstderr: {result.stderr[:600]}",
    )
    if dry_run:
        return {}
    stdout = result.stdout.strip()
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(
            f"Expected JSON success object on stdout, got: {stdout[:300]}"
        ) from exc
    ensure(payload.get("ok") is True, f"Success JSON must have ok=true. Got: {payload}")
    ensure("config" in payload, f"Success JSON must include 'config'. Got: {payload}")
    ensure(
        payload.get("server") == "memory-service",
        f"Success JSON must have server='memory-service'. Got: {payload}",
    )
    return payload


def _assert_operational_failure(result: subprocess.CompletedProcess[str]) -> None:
    """Assert exit 1 (operational failure: existing entry, invalid file, etc.)."""
    ensure(
        result.returncode == 1,
        f"Expected exit 1 (operational failure), got {result.returncode}.\nstderr: {result.stderr[:600]}",
    )


def _assert_usage_error(result: subprocess.CompletedProcess[str]) -> None:
    """Assert exit 2 (usage error: unknown subcommand or harness)."""
    ensure(
        result.returncode == 2,
        f"Expected exit 2 (usage error), got {result.returncode}.\nstderr: {result.stderr[:600]}",
    )


# ---------------------------------------------------------------------------
# Config assertion helpers
# ---------------------------------------------------------------------------


def _read_json_config(path: Path) -> dict[str, Any]:
    """Read and parse a JSON config file, asserting it is a JSON object."""
    ensure(path.is_file(), f"Expected config file to exist at {path}, but it was not written.")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationFailure(f"Config file is not valid JSON: {path}.\nError: {exc}") from exc
    ensure(isinstance(data, dict), f"Config file must be a JSON object, got: {type(data).__name__}")
    return data


def _assert_mcp_server_entry(config: dict[str, Any]) -> dict[str, Any]:
    """Assert the config has a well-formed mcpServers.memory-service entry."""
    ensure(
        "mcpServers" in config,
        f"Config must have a top-level 'mcpServers' key. Keys present: {list(config.keys())}",
    )
    servers = config["mcpServers"]
    ensure(isinstance(servers, dict), f"mcpServers must be a JSON object. Got: {type(servers).__name__}")
    ensure(
        "memory-service" in servers,
        f"mcpServers must contain 'memory-service'. Keys present: {list(servers.keys())}",
    )
    entry = servers["memory-service"]
    ensure(isinstance(entry, dict), f"mcpServers.memory-service must be a JSON object.")
    ensure(
        entry.get("command") == "node",
        f"memory-service entry must set command='node'. Got: {entry.get('command')!r}",
    )
    args = entry.get("args")
    ensure(isinstance(args, list) and args, "memory-service entry must include a non-empty 'args' list.")
    env_block = entry.get("env")
    ensure(
        isinstance(env_block, dict),
        f"memory-service entry must include an 'env' object. Got: {env_block!r}",
    )
    data_dir = env_block.get("MEMORY_SERVICE_DATA_DIR")
    ensure(
        isinstance(data_dir, str) and data_dir,
        f"memory-service env must include MEMORY_SERVICE_DATA_DIR. Got: {data_dir!r}",
    )
    ensure(
        Path(data_dir).is_absolute(),
        f"MEMORY_SERVICE_DATA_DIR must be an absolute path. Got: {data_dir!r}",
    )
    return entry


def _assert_mcp_server_entry_not_present(config: dict[str, Any]) -> None:
    """Assert the config does NOT contain a memory-service entry (dry-run guard)."""
    servers = config.get("mcpServers", {})
    ensure(
        "memory-service" not in servers,
        "Dry-run must not write the memory-service entry to disk.",
    )


# ---------------------------------------------------------------------------
# Catalog YAML parser (minimal, standard-library-only)
# ---------------------------------------------------------------------------


def _parse_integrations_yaml(path: Path) -> list[dict[str, Any]]:
    """
    Parse integrations.yaml into a list of integration record dicts.

    Only scalar top-level fields per entry are extracted (harness_id, display_name,
    tier, tier_target, connect_command, docs_path, example_path, smoke_entrypoint).
    List-valued fields (mechanisms, config_paths) are skipped.
    """
    text = path.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    in_list_field = False

    for raw_line in text.splitlines():
        # Detect start of a new integration entry
        stripped = raw_line.strip()
        if stripped.startswith("- harness_id:"):
            if "harness_id" in current:
                entries.append(current)
            current = {}
            in_list_field = False
            val = stripped.removeprefix("- harness_id:").strip()
            current["harness_id"] = val

        elif not current:
            # Haven't started first entry yet
            continue

        elif stripped.startswith("- "):
            # A list item — we're inside a list-valued field; skip
            in_list_field = True
            continue

        elif ":" in stripped and not stripped.startswith("#"):
            key, _, value_raw = stripped.partition(":")
            key = key.strip()
            value = value_raw.strip()

            # If this line is indented at the field level (4 spaces), it's a field
            indent = len(raw_line) - len(raw_line.lstrip())
            if indent == 4:
                in_list_field = False
                if value == "null":
                    current[key] = None
                else:
                    # Strip surrounding quotes if any
                    current[key] = value.strip("\"'")
            elif indent > 4 and not in_list_field:
                # Nested scalar (e.g. env under a block) — skip for now
                pass

    if "harness_id" in current:
        entries.append(current)

    return entries


# ===========================================================================
# Phase 6 scenario functions
# ===========================================================================


# ---------------------------------------------------------------------------
# connect copilot scenarios (P6-C1 through P6-C4)
# ---------------------------------------------------------------------------


def case_connect_copilot_empty_config(ctx: "ValidationHarness") -> str:
    """P6-C1: connect copilot writes a well-formed mcpServers entry into a non-existent config."""
    with tempfile.TemporaryDirectory(prefix="p6-copilot-c1-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / ".copilot" / "mcp-config.json"
        project_root = REPO_ROOT  # use real repo root so mcp-script path resolves
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_COPILOT,
            [
                "--config", str(config_path),
                "--project-root", str(project_root),
                "--data-dir", data_dir,
            ],
        )
        payload = _assert_success_result(result)
        ensure(
            Path(payload["config"]) == config_path,
            f"Success JSON 'config' must match the target path. Got: {payload['config']!r}",
        )
        config = _read_json_config(config_path)
        entry = _assert_mcp_server_entry(config)
        ensure(
            entry["env"]["MEMORY_SERVICE_DATA_DIR"] == data_dir,
            f"MEMORY_SERVICE_DATA_DIR must match supplied --data-dir. "
            f"Got: {entry['env']['MEMORY_SERVICE_DATA_DIR']!r}, want: {data_dir!r}",
        )

    return (
        "connect copilot writes a valid mcpServers.memory-service entry into an empty "
        "config file with exit 0 and JSON success output."
    )


def case_connect_copilot_refuses_clobber(ctx: "ValidationHarness") -> str:
    """P6-C2: connect copilot exits 1 when memory-service entry already exists and --replace is omitted."""
    with tempfile.TemporaryDirectory(prefix="p6-copilot-c2-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp-config.json"
        existing_config = {
            "mcpServers": {
                "memory-service": {
                    "type": "stdio",
                    "command": "node",
                    "args": ["/old/path/memory-service-mcp.js"],
                    "env": {"MEMORY_SERVICE_DATA_DIR": "/old/data"},
                },
                "other-tool": {"command": "other", "args": []},
            }
        }
        config_path.write_text(json.dumps(existing_config), encoding="utf-8")

        result = _run_connect(
            CONNECT_COPILOT,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", str(tmp_path / "data"),
            ],
        )
        _assert_operational_failure(result)
        # Verify the original config was not mutated
        surviving = _read_json_config(config_path)
        old_data_dir = surviving["mcpServers"]["memory-service"]["env"]["MEMORY_SERVICE_DATA_DIR"]
        ensure(
            old_data_dir == "/old/data",
            "connect copilot must not mutate the config when refusing clobber.",
        )

    return (
        "connect copilot exits 1 with a human-readable message and leaves the existing "
        "config unchanged when --replace is omitted and a memory-service entry already exists."
    )


def case_connect_copilot_replace_flag_overwrites_preserves_others(ctx: "ValidationHarness") -> str:
    """P6-C3: connect copilot --replace overwrites memory-service entry and preserves other entries."""
    with tempfile.TemporaryDirectory(prefix="p6-copilot-c3-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp-config.json"
        new_data_dir = str(tmp_path / "new-data")
        existing_config = {
            "mcpServers": {
                "memory-service": {
                    "command": "node",
                    "args": ["/old/path.js"],
                    "env": {"MEMORY_SERVICE_DATA_DIR": "/old/data"},
                },
                "another-tool": {
                    "command": "python3",
                    "args": ["-m", "another"],
                    "env": {},
                },
            }
        }
        config_path.write_text(json.dumps(existing_config), encoding="utf-8")

        result = _run_connect(
            CONNECT_COPILOT,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", new_data_dir,
                "--replace",
            ],
        )
        _assert_success_result(result)
        config = _read_json_config(config_path)
        entry = _assert_mcp_server_entry(config)
        ensure(
            entry["env"]["MEMORY_SERVICE_DATA_DIR"] == new_data_dir,
            "Replaced entry must carry the new MEMORY_SERVICE_DATA_DIR.",
        )
        # Other entries must be preserved
        ensure(
            "another-tool" in config["mcpServers"],
            "connect copilot --replace must preserve other mcpServers entries.",
        )
        other = config["mcpServers"]["another-tool"]
        ensure(
            other.get("command") == "python3",
            "The preserved another-tool entry must be unchanged.",
        )

    return (
        "connect copilot --replace overwrites only the memory-service entry and "
        "leaves all other mcpServers entries intact."
    )


def case_connect_copilot_dry_run(ctx: "ValidationHarness") -> str:
    """P6-C4: connect copilot --dry-run prints merged JSON to stdout without writing the config file."""
    with tempfile.TemporaryDirectory(prefix="p6-copilot-c4-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp-config.json"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_COPILOT,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
                "--dry-run",
            ],
        )
        ensure(
            result.returncode == 0,
            f"connect copilot --dry-run must exit 0. Got: {result.returncode}. stderr: {result.stderr[:400]}",
        )
        # Config file must NOT have been written
        ensure(
            not config_path.exists(),
            f"connect copilot --dry-run must not write the config file. Found: {config_path}",
        )
        # stdout must be valid JSON with mcpServers
        stdout = result.stdout.strip()
        try:
            printed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValidationFailure(
                f"connect copilot --dry-run must print valid JSON to stdout. Got: {stdout[:300]}"
            ) from exc
        _assert_mcp_server_entry(printed)

    return (
        "connect copilot --dry-run prints the merged mcpServers JSON to stdout and "
        "does not create or modify the config file on disk."
    )


# ---------------------------------------------------------------------------
# connect cursor scenarios (P6-C5 through P6-C6)
# ---------------------------------------------------------------------------


def case_connect_cursor_project_target(ctx: "ValidationHarness") -> str:
    """P6-C5: connect cursor writes project .cursor/mcp.json with absolute MEMORY_SERVICE_DATA_DIR.

    The cursor script always writes the global config and optionally writes a project config
    when --project-config is supplied.  We redirect both to temp paths to keep the test
    hermetic (avoids writing to the real ~/.cursor/).
    """
    with tempfile.TemporaryDirectory(prefix="p6-cursor-c5-") as tmp:
        tmp_path = Path(tmp)
        global_config = tmp_path / "cursor-global.json"   # redirect global to temp
        project_config = tmp_path / "cursor-project.json"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_CURSOR,
            [
                "--global-config", str(global_config),
                "--project-config", str(project_config),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
            ],
        )
        payload = _assert_success_result(result)
        # Success JSON should point to the project config (it is the primary target)
        ensure(
            Path(payload["config"]) == project_config,
            f"Success JSON 'config' must point to project config when --project-config is used. "
            f"Got: {payload['config']!r}",
        )
        config = _read_json_config(project_config)
        entry = _assert_mcp_server_entry(config)
        ensure(
            Path(entry["env"]["MEMORY_SERVICE_DATA_DIR"]).is_absolute(),
            "connect cursor project config must use an absolute MEMORY_SERVICE_DATA_DIR.",
        )

    return (
        "connect cursor writes a valid project .cursor/mcp.json with "
        "absolute MEMORY_SERVICE_DATA_DIR when --project-config is supplied."
    )


def case_connect_cursor_global_target(ctx: "ValidationHarness") -> str:
    """P6-C6: connect cursor writes global ~/.cursor/mcp.json with absolute MEMORY_SERVICE_DATA_DIR."""
    with tempfile.TemporaryDirectory(prefix="p6-cursor-c6-") as tmp:
        tmp_path = Path(tmp)
        global_config = tmp_path / ".cursor" / "mcp.json"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_CURSOR,
            [
                "--global-config", str(global_config),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
            ],
        )
        payload = _assert_success_result(result)
        ensure(
            Path(payload["config"]) == global_config,
            f"Success JSON 'config' must match the global config path. Got: {payload['config']!r}",
        )
        config = _read_json_config(global_config)
        entry = _assert_mcp_server_entry(config)
        ensure(
            Path(entry["env"]["MEMORY_SERVICE_DATA_DIR"]).is_absolute(),
            "connect cursor global config must use an absolute MEMORY_SERVICE_DATA_DIR.",
        )

    return (
        "connect cursor writes a valid global ~/.cursor/mcp.json with "
        "absolute MEMORY_SERVICE_DATA_DIR when --global-config is supplied."
    )


# ---------------------------------------------------------------------------
# connect windsurf scenarios (P6-C7 through P6-C8)
# ---------------------------------------------------------------------------


def case_connect_windsurf_global_mcp_config(ctx: "ValidationHarness") -> str:
    """P6-C7: connect windsurf writes ~/.codeium/windsurf/mcp_config.json with valid mcpServers JSON."""
    with tempfile.TemporaryDirectory(prefix="p6-windsurf-c7-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / ".codeium" / "windsurf" / "mcp_config.json"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_WINDSURF,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
            ],
        )
        _assert_success_result(result)
        config = _read_json_config(config_path)
        _assert_mcp_server_entry(config)
        # Windsurf spec: global only — assert config is valid JSON with mcpServers key
        ensure(
            "mcpServers" in config,
            "connect windsurf config must include a top-level 'mcpServers' key.",
        )

    return (
        "connect windsurf writes a valid ~/.codeium/windsurf/mcp_config.json with "
        "a well-formed mcpServers.memory-service entry and exit 0."
    )


def case_connect_windsurf_refuses_clobber(ctx: "ValidationHarness") -> str:
    """P6-C8: connect windsurf exits 1 when memory-service entry already exists and --replace is omitted."""
    with tempfile.TemporaryDirectory(prefix="p6-windsurf-c8-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp_config.json"
        existing_config = {
            "mcpServers": {
                "memory-service": {
                    "command": "node",
                    "args": ["/old/path.js"],
                    "env": {"MEMORY_SERVICE_DATA_DIR": "/old/data"},
                }
            }
        }
        config_path.write_text(json.dumps(existing_config), encoding="utf-8")

        result = _run_connect(
            CONNECT_WINDSURF,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", str(tmp_path / "data"),
            ],
        )
        _assert_operational_failure(result)

    return (
        "connect windsurf exits 1 and refuses to clobber an existing memory-service "
        "entry when --replace is not supplied."
    )


# ---------------------------------------------------------------------------
# connect codex scenarios (P6-C9 through P6-C10)
# ---------------------------------------------------------------------------


def _assert_toml_has_mcp_servers_section(toml_path: Path) -> None:
    """Assert that a TOML file contains an [mcp_servers.memory-service] section."""
    text = toml_path.read_text(encoding="utf-8")

    if _HAS_TOMLLIB:
        try:
            data = _tomllib.loads(text)  # type: ignore[union-attr]
        except Exception as exc:
            raise ValidationFailure(f"connect codex produced invalid TOML at {toml_path}: {exc}") from exc
        mcp = data.get("mcp_servers", {})
        ensure(
            isinstance(mcp, dict) and "memory-service" in mcp,
            f"TOML must contain [mcp_servers.memory-service]. Keys in mcp_servers: {list(mcp.keys())}",
        )
        entry = mcp["memory-service"]
        ensure(
            isinstance(entry, dict),
            "mcp_servers.memory-service must be a TOML table.",
        )
        # command key
        ensure(
            entry.get("command") == "node",
            f"mcp_servers.memory-service must set command = 'node'. Got: {entry.get('command')!r}",
        )
        # data dir — may be under [mcp_servers.memory-service.env] or as a direct key
        env_block = entry.get("env", {})
        data_dir = env_block.get("MEMORY_SERVICE_DATA_DIR") if isinstance(env_block, dict) else None
        if data_dir is None:
            data_dir = entry.get("MEMORY_SERVICE_DATA_DIR")
        ensure(
            isinstance(data_dir, str) and data_dir,
            "mcp_servers.memory-service must include MEMORY_SERVICE_DATA_DIR.",
        )
        ensure(
            Path(data_dir).is_absolute(),
            f"MEMORY_SERVICE_DATA_DIR in codex TOML must be absolute. Got: {data_dir!r}",
        )
    else:
        # Fallback: basic string checks when tomllib is unavailable
        ensure(
            "[mcp_servers.memory-service]" in text or '[mcp_servers."memory-service"]' in text,
            f"TOML file must contain [mcp_servers.memory-service] section. Content:\n{text[:400]}",
        )
        ensure(
            'command = "node"' in text or "command = 'node'" in text,
            "TOML mcp_servers.memory-service must set command = 'node'.",
        )
        ensure(
            "MEMORY_SERVICE_DATA_DIR" in text,
            "TOML mcp_servers.memory-service must include MEMORY_SERVICE_DATA_DIR.",
        )


def case_connect_codex_toml_merge(ctx: "ValidationHarness") -> str:
    """P6-C9: connect codex merges memory-service config under [mcp_servers.memory-service] in TOML."""
    with tempfile.TemporaryDirectory(prefix="p6-codex-c9-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_CODEX,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
            ],
        )
        _assert_success_result(result)
        ensure(config_path.is_file(), f"connect codex must write config to {config_path}")
        _assert_toml_has_mcp_servers_section(config_path)

    return (
        "connect codex writes a valid TOML config with an [mcp_servers.memory-service] "
        "section containing command='node' and absolute MEMORY_SERVICE_DATA_DIR."
    )


def case_connect_codex_refuses_clobber(ctx: "ValidationHarness") -> str:
    """P6-C10: connect codex exits 1 when memory-service TOML entry exists and --replace is omitted."""
    with tempfile.TemporaryDirectory(prefix="p6-codex-c10-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        existing_toml = (
            '[mcp_servers.memory-service]\n'
            'command = "node"\n'
            'args = ["/old/path.js"]\n\n'
            '[mcp_servers.memory-service.env]\n'
            'MEMORY_SERVICE_DATA_DIR = "/old/data"\n'
        )
        config_path.write_text(existing_toml, encoding="utf-8")

        result = _run_connect(
            CONNECT_CODEX,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", str(tmp_path / "data"),
            ],
        )
        _assert_operational_failure(result)

    return (
        "connect codex exits 1 and refuses to clobber an existing "
        "[mcp_servers.memory-service] section when --replace is not supplied."
    )


# ---------------------------------------------------------------------------
# Unknown harness → exit 2 (P6-C11)
# ---------------------------------------------------------------------------


def case_connect_unknown_harness_exits_2(ctx: "ValidationHarness") -> str:
    """P6-C11: ai-memory connect with an unsupported harness name exits with code 2."""
    if not _node_available():
        return (
            "[SKIPPED: Node.js not available on PATH] "
            "Cannot exercise the ai-memory.js exit-2 routing path. "
            "Install Node.js ≥18 and re-run to fully validate this behavior."
        )
    result = subprocess.run(
        ["node", str(AI_MEMORY_JS), "connect", "nonexistent-harness-xyz"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    _assert_usage_error(result)
    ensure(
        "nonexistent-harness-xyz" in result.stderr or "Supported harnesses" in result.stderr,
        "ai-memory connect must print the unknown harness name or supported harness list to stderr. "
        f"Got stderr: {result.stderr[:300]}",
    )
    return (
        "ai-memory connect with an unsupported harness name exits with code 2 "
        "and identifies the unknown harness or lists supported harnesses in stderr."
    )


def _node_available() -> bool:
    """Return True when the `node` binary is reachable on PATH."""
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Integration catalog consistency (P6-CAT1 through P6-CAT2)
# ---------------------------------------------------------------------------


def case_catalog_p0_entries_at_tier_a_or_b(ctx: "ValidationHarness") -> str:
    """P6-CAT1: integrations.yaml contains all P0 harness IDs and each is at tier A or B."""
    ensure(
        INTEGRATIONS_YAML.is_file(),
        f"integrations.yaml not found at expected path: {INTEGRATIONS_YAML}",
    )
    entries = _parse_integrations_yaml(INTEGRATIONS_YAML)
    found_ids = {e["harness_id"] for e in entries}

    missing = _P0_HARNESS_IDS - found_ids
    ensure(
        not missing,
        f"integrations.yaml must contain all Phase 6 P0 harness IDs. Missing: {sorted(missing)}",
    )

    by_id = {e["harness_id"]: e for e in entries}
    tier_violations: list[str] = []
    for hid in _P0_HARNESS_IDS:
        entry = by_id[hid]
        tier = entry.get("tier", "")
        if tier not in {"A", "B"}:
            tier_violations.append(f"{hid!r} has tier={tier!r} (expected A or B)")

    ensure(
        not tier_violations,
        "All P0 harness entries must be at tier A or B in integrations.yaml:\n"
        + "\n".join(tier_violations),
    )

    return (
        f"integrations.yaml contains all {len(_P0_HARNESS_IDS)} P0 harness IDs "
        "and each is at tier A or B per Section 21.2."
    )


def case_catalog_tier_b_cli_harnesses_have_connect_command(ctx: "ValidationHarness") -> str:
    """P6-CAT2: Tier-B CLI-managed harnesses carry a non-null connect_command in integrations.yaml."""
    ensure(
        INTEGRATIONS_YAML.is_file(),
        f"integrations.yaml not found at: {INTEGRATIONS_YAML}",
    )
    entries = _parse_integrations_yaml(INTEGRATIONS_YAML)
    by_id = {e["harness_id"]: e for e in entries}

    violations: list[str] = []
    for hid in _TIER_B_CLI_HARNESS_IDS:
        if hid not in by_id:
            violations.append(f"{hid!r} is missing from the catalog")
            continue
        entry = by_id[hid]
        connect_command = entry.get("connect_command")
        if not connect_command:
            violations.append(
                f"{hid!r} has tier={entry.get('tier')!r} but connect_command is null/missing"
            )

    ensure(
        not violations,
        "Tier-B CLI-managed harnesses must declare a non-null connect_command:\n"
        + "\n".join(violations),
    )

    return (
        "All tier-B CLI-managed harnesses in integrations.yaml declare a "
        "non-null connect_command consistent with Section 21.4."
    )


# ---------------------------------------------------------------------------
# MCP backward-compatibility marker (P6-MCP1)
# ---------------------------------------------------------------------------


def case_mcp_surface_unchanged_with_phase6(ctx: "ValidationHarness") -> str:
    """P6-MCP1: Phase 6 connect CLI does not add or remove tools from the eleven-tool MCP surface."""
    tools = ctx.list_tools()
    tool_names = {t["name"] for t in tools}
    expected_tools = {
        "memory_remember",
        "memory_search",
        "memory_get",
        "memory_forget",
        "memory_status",
        "memory_consolidate",
        "memory_review",
        "memory_profile",
        "memory_reflect",
        "memory_query_temporal",
        "memory_audit_export",
    }
    extra = tool_names - expected_tools
    missing = expected_tools - tool_names
    ensure(
        not extra,
        f"Phase 6 must not introduce new MCP tools beyond the published eleven. "
        f"Unexpected tools: {sorted(extra)}",
    )
    ensure(
        not missing,
        f"Phase 6 must not remove any of the eleven published MCP tools. "
        f"Missing tools: {sorted(missing)}",
    )
    return (
        "Phase 6 connect CLI artifacts leave the eleven-tool MCP surface "
        "exactly as specified in Section 10 / v1.6.0."
    )


# ===========================================================================
# CF-1: Smoke script scenarios (Section 21.5, 21.6, 21.8)
# ===========================================================================

# Canonical smoke script paths for P0 tier B+ entries.
# These paths are used directly and do not depend on integrations.yaml
# smoke_entrypoint field, which some entries leave null pending catalog update.
_SMOKE_SCRIPTS: dict[str, Path] = {
    "claude-code": REPO_ROOT / "examples/claude-code/demo/phase3-smoke.sh",
    "copilot-cli": REPO_ROOT / "examples/copilot/demo/copilot-smoke.sh",
    "codex": REPO_ROOT / "examples/codex/demo/codex-smoke.sh",
    "cursor": REPO_ROOT / "examples/cursor/demo/cursor-smoke.sh",
    "windsurf": REPO_ROOT / "examples/windsurf/demo/windsurf-smoke.sh",
}

# Smoke scripts that drive node memory-service-mcp.js and therefore require Node
_SMOKE_REQUIRES_NODE: frozenset[str] = frozenset({
    "copilot-cli", "codex", "cursor", "windsurf",
})


def _run_smoke(harness_id: str) -> str:
    """
    Run the named harness smoke script as a non-interactive subprocess.

    Returns a human-readable pass message.  Raises ValidationFailure on any error.
    Skips with an explanatory message when the script does not exist or a required
    runtime (Node.js for mcp-stdio scripts) is not available.
    """
    script = _SMOKE_SCRIPTS.get(harness_id)
    if script is None:
        raise ValidationFailure(f"No known smoke script path for harness {harness_id!r}.")

    if not script.is_file():
        raise ValidationFailure(
            f"Smoke script not found at expected path: {script}. "
            "Application Agent must deliver this script."
        )

    if harness_id in _SMOKE_REQUIRES_NODE and not _node_available():
        return (
            f"[SKIPPED: Node.js not available] Cannot run {harness_id} smoke script "
            f"({script.name}) — it requires 'node' on PATH. "
            "Install Node.js ≥18 and re-run to fully validate."
        )

    env = os.environ.copy()
    env["MEMORY_SERVICE_PYTHON"] = PYTHON

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        raise ValidationFailure(
            f"{harness_id} smoke script exited {result.returncode} (expected 0).\n"
            f"stdout: {result.stdout[-800:]}\n"
            f"stderr: {result.stderr[-400:]}"
        )
    return (
        f"{harness_id} smoke script ({script.name}) exited 0 — "
        "memory_status responded and all eleven tools are present."
    )


def case_smoke_claude_code(ctx: "ValidationHarness") -> str:
    """P6-SMK1: claude-code phase3 smoke script exits 0 (Hermes + OpenClaw hybrid search)."""
    return _run_smoke("claude-code")


def case_smoke_copilot_cli(ctx: "ValidationHarness") -> str:
    """P6-SMK2: copilot-cli smoke script exits 0 (memory_status via node MCP stdio)."""
    return _run_smoke("copilot-cli")


def case_smoke_codex(ctx: "ValidationHarness") -> str:
    """P6-SMK3: codex smoke script exits 0 (memory_status via node MCP stdio)."""
    return _run_smoke("codex")


def case_smoke_cursor(ctx: "ValidationHarness") -> str:
    """P6-SMK4: cursor smoke script exits 0 (memory_status + eleven-tool list via node MCP stdio)."""
    return _run_smoke("cursor")


def case_smoke_windsurf(ctx: "ValidationHarness") -> str:
    """P6-SMK5: windsurf smoke script exits 0 (memory_status via node MCP stdio)."""
    return _run_smoke("windsurf")


# ===========================================================================
# SG-1: --replace tests for windsurf and codex (preserve other entries)
# ===========================================================================


def case_connect_windsurf_replace_preserves_others(ctx: "ValidationHarness") -> str:
    """P6-C12: connect windsurf --replace overwrites memory-service and preserves other mcpServers entries."""
    with tempfile.TemporaryDirectory(prefix="p6-windsurf-c12-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp_config.json"
        new_data_dir = str(tmp_path / "new-data")
        existing_config = {
            "mcpServers": {
                "memory-service": {
                    "command": "node",
                    "args": ["/old/path.js"],
                    "env": {"MEMORY_SERVICE_DATA_DIR": "/old/data"},
                },
                "github-copilot": {
                    "command": "python3",
                    "args": ["-m", "copilot_mcp"],
                    "env": {},
                },
            }
        }
        config_path.write_text(json.dumps(existing_config), encoding="utf-8")

        result = _run_connect(
            CONNECT_WINDSURF,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", new_data_dir,
                "--replace",
            ],
        )
        _assert_success_result(result)
        config = _read_json_config(config_path)
        entry = _assert_mcp_server_entry(config)
        ensure(
            entry["env"]["MEMORY_SERVICE_DATA_DIR"] == new_data_dir,
            "Replaced windsurf entry must carry the new MEMORY_SERVICE_DATA_DIR.",
        )
        ensure(
            "github-copilot" in config["mcpServers"],
            "connect windsurf --replace must preserve other mcpServers entries.",
        )
        ensure(
            config["mcpServers"]["github-copilot"].get("command") == "python3",
            "The preserved github-copilot entry must be unchanged.",
        )

    return (
        "connect windsurf --replace overwrites only the memory-service entry and "
        "leaves all other mcpServers entries intact."
    )


def case_connect_codex_replace_preserves_others(ctx: "ValidationHarness") -> str:
    """P6-C13: connect codex --replace updates memory-service TOML section and preserves other sections."""
    with tempfile.TemporaryDirectory(prefix="p6-codex-c13-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        new_data_dir = str(tmp_path / "new-data")
        existing_toml = (
            "[other_tool]\n"
            'setting = "preserved-value"\n\n'
            "[mcp_servers.memory-service]\n"
            'enabled = true\n'
            'command = "node"\n'
            'args = ["/old/path.js"]\n\n'
            "[mcp_servers.memory-service.env]\n"
            'MEMORY_SERVICE_DATA_DIR = "/old/data"\n'
        )
        config_path.write_text(existing_toml, encoding="utf-8")

        result = _run_connect(
            CONNECT_CODEX,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", new_data_dir,
                "--replace",
            ],
        )
        _assert_success_result(result)
        ensure(config_path.is_file(), "connect codex --replace must write updated config.")
        updated_text = config_path.read_text(encoding="utf-8")
        ensure(
            "[other_tool]" in updated_text,
            "connect codex --replace must preserve other TOML sections.",
        )
        ensure(
            "preserved-value" in updated_text,
            "connect codex --replace must preserve values in other TOML sections.",
        )
        ensure(
            "/old/data" not in updated_text,
            "connect codex --replace must replace the old MEMORY_SERVICE_DATA_DIR.",
        )
        _assert_toml_has_mcp_servers_section(config_path)

    return (
        "connect codex --replace overwrites [mcp_servers.memory-service] with the new entry "
        "and preserves all other TOML sections."
    )


# ===========================================================================
# SG-2: copilot merge into existing config that has other servers but no memory-service entry
# ===========================================================================


def case_connect_copilot_merge_existing_with_others(ctx: "ValidationHarness") -> str:
    """P6-C14: connect copilot adds memory-service to existing config that already has other servers."""
    with tempfile.TemporaryDirectory(prefix="p6-copilot-c14-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp-config.json"
        existing_config = {
            "mcpServers": {
                "github-copilot": {
                    "type": "stdio",
                    "command": "copilot-mcp",
                    "args": ["--serve"],
                    "env": {},
                },
                "another-tool": {
                    "type": "stdio",
                    "command": "node",
                    "args": ["/usr/local/lib/another-tool.js"],
                    "env": {},
                },
            }
        }
        config_path.write_text(json.dumps(existing_config, indent=2), encoding="utf-8")
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_COPILOT,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
            ],
        )
        _assert_success_result(result)
        config = _read_json_config(config_path)
        _assert_mcp_server_entry(config)

        # Both pre-existing servers must remain
        ensure(
            "github-copilot" in config["mcpServers"],
            "connect copilot must preserve pre-existing github-copilot entry.",
        )
        ensure(
            "another-tool" in config["mcpServers"],
            "connect copilot must preserve pre-existing another-tool entry.",
        )
        ensure(
            len(config["mcpServers"]) == 3,
            f"Config must have exactly 3 servers after merge. Got: {list(config['mcpServers'].keys())}",
        )

    return (
        "connect copilot merges memory-service into an existing config that contains other "
        "servers, preserving all pre-existing entries (no --replace required)."
    )


# ===========================================================================
# SG-3: P1 tier-D entries have tier_target B
# ===========================================================================


def case_catalog_p1_entries_have_tier_target_b(ctx: "ValidationHarness") -> str:
    """P6-CAT3: integrations.yaml P1 tier-D entries carry tier_target B per Section 21.1."""
    ensure(
        INTEGRATIONS_YAML.is_file(),
        f"integrations.yaml not found at: {INTEGRATIONS_YAML}",
    )
    entries = _parse_integrations_yaml(INTEGRATIONS_YAML)
    by_id = {e["harness_id"]: e for e in entries}

    missing: list[str] = []
    wrong_tier: list[str] = []
    wrong_target: list[str] = []

    for hid in _P1_HARNESS_IDS:
        if hid not in by_id:
            missing.append(hid)
            continue
        entry = by_id[hid]
        if entry.get("tier") != "D":
            wrong_tier.append(f"{hid!r}: tier={entry.get('tier')!r} (expected D)")
        if entry.get("tier_target") != "B":
            wrong_target.append(f"{hid!r}: tier_target={entry.get('tier_target')!r} (expected B)")

    ensure(not missing, f"P1 harness IDs missing from catalog: {sorted(missing)}")
    ensure(not wrong_tier, "P1 entries must carry tier D:\n" + "\n".join(wrong_tier))
    ensure(
        not wrong_target,
        "P1 tier-D entries must carry tier_target B per Section 21.1:\n" + "\n".join(wrong_target),
    )

    return (
        f"All {len(_P1_HARNESS_IDS)} P1 harness IDs are present in integrations.yaml "
        "at tier D with tier_target B."
    )


# ===========================================================================
# S-1: --dry-run for cursor, windsurf, codex
# ===========================================================================


def case_connect_cursor_dry_run(ctx: "ValidationHarness") -> str:
    """P6-C15: connect cursor --dry-run prints merged JSON config to stdout without writing any file."""
    with tempfile.TemporaryDirectory(prefix="p6-cursor-c15-") as tmp:
        tmp_path = Path(tmp)
        global_config = tmp_path / "cursor-global.json"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_CURSOR,
            [
                "--global-config", str(global_config),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
                "--dry-run",
            ],
        )
        ensure(
            result.returncode == 0,
            f"connect cursor --dry-run must exit 0. Got: {result.returncode}. "
            f"stderr: {result.stderr[:400]}",
        )
        ensure(
            not global_config.exists(),
            "connect cursor --dry-run must not write the global config file.",
        )
        stdout = result.stdout.strip()
        try:
            printed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValidationFailure(
                f"connect cursor --dry-run must print valid JSON to stdout. Got: {stdout[:300]}"
            ) from exc
        _assert_mcp_server_entry(printed)

    return (
        "connect cursor --dry-run prints the merged mcpServers JSON to stdout and "
        "does not create or modify any config file on disk."
    )


def case_connect_windsurf_dry_run(ctx: "ValidationHarness") -> str:
    """P6-C16: connect windsurf --dry-run prints merged JSON config to stdout without writing the file."""
    with tempfile.TemporaryDirectory(prefix="p6-windsurf-c16-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp_config.json"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_WINDSURF,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
                "--dry-run",
            ],
        )
        ensure(
            result.returncode == 0,
            f"connect windsurf --dry-run must exit 0. Got: {result.returncode}. "
            f"stderr: {result.stderr[:400]}",
        )
        ensure(
            not config_path.exists(),
            "connect windsurf --dry-run must not write the config file.",
        )
        stdout = result.stdout.strip()
        try:
            printed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValidationFailure(
                f"connect windsurf --dry-run must print valid JSON to stdout. Got: {stdout[:300]}"
            ) from exc
        _assert_mcp_server_entry(printed)

    return (
        "connect windsurf --dry-run prints the merged mcpServers JSON to stdout and "
        "does not create or modify the config file on disk."
    )


def case_connect_codex_dry_run(ctx: "ValidationHarness") -> str:
    """P6-C17: connect codex --dry-run prints merged TOML to stdout without writing any file."""
    with tempfile.TemporaryDirectory(prefix="p6-codex-c17-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.toml"
        data_dir = str(tmp_path / "data")

        result = _run_connect(
            CONNECT_CODEX,
            [
                "--config", str(config_path),
                "--project-root", str(REPO_ROOT),
                "--data-dir", data_dir,
                "--dry-run",
            ],
        )
        ensure(
            result.returncode == 0,
            f"connect codex --dry-run must exit 0. Got: {result.returncode}. "
            f"stderr: {result.stderr[:400]}",
        )
        ensure(
            not config_path.exists(),
            "connect codex --dry-run must not write the config file.",
        )
        stdout = result.stdout
        ensure(
            "[mcp_servers." in stdout,
            f"connect codex --dry-run must print TOML with [mcp_servers.*] section. Got: {stdout[:300]}",
        )
        ensure(
            'command = "node"' in stdout or "command = 'node'" in stdout,
            "connect codex --dry-run TOML output must include command = \"node\".",
        )

    return (
        "connect codex --dry-run prints merged TOML with [mcp_servers.memory-service] to stdout "
        "and does not create or modify the config file on disk."
    )


# ===========================================================================
# S-2: Relative --data-dir is resolved to absolute path in written config
# ===========================================================================


def case_connect_relative_data_dir_resolved(ctx: "ValidationHarness") -> str:
    """P6-C18: connect copilot resolves a relative --data-dir to an absolute path in the written config."""
    with tempfile.TemporaryDirectory(prefix="p6-copilot-c18-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "mcp-config.json"
        project_root = tmp_path / "project"
        project_root.mkdir()
        relative_data_dir = ".ai-memory/data"  # relative path

        result = _run_connect(
            CONNECT_COPILOT,
            [
                "--config", str(config_path),
                "--project-root", str(project_root),
                "--data-dir", relative_data_dir,
            ],
        )
        _assert_success_result(result)
        config = _read_json_config(config_path)
        entry = _assert_mcp_server_entry(config)
        written_data_dir = entry["env"]["MEMORY_SERVICE_DATA_DIR"]
        ensure(
            Path(written_data_dir).is_absolute(),
            f"Written MEMORY_SERVICE_DATA_DIR must be absolute even when --data-dir was "
            f"relative. Got: {written_data_dir!r}",
        )
        expected_abs = str((project_root / relative_data_dir).resolve())
        ensure(
            written_data_dir == expected_abs,
            f"Written MEMORY_SERVICE_DATA_DIR must equal the relative path resolved against "
            f"--project-root. Expected: {expected_abs!r}, got: {written_data_dir!r}",
        )

    return (
        "connect copilot resolves a relative --data-dir to an absolute path "
        "against --project-root before writing into MEMORY_SERVICE_DATA_DIR."
    )


# ===========================================================================
# Exported case list for integration into the main ALL_CASES registry
# ===========================================================================

CONNECT_CASES = [
    # --- connect copilot ---
    (
        "connect copilot writes a valid mcpServers entry into an empty config with exit 0.",
        "Connect CLI",
        "21.4, 21.8",
        case_connect_copilot_empty_config,
    ),
    (
        "connect copilot exits 1 and refuses clobber when memory-service entry exists and --replace is omitted.",
        "Connect CLI",
        "21.4.2, 21.4.5, 21.8",
        case_connect_copilot_refuses_clobber,
    ),
    (
        "connect copilot --replace overwrites memory-service entry and preserves other mcpServers entries.",
        "Connect CLI",
        "21.4.2, 21.4.5, 21.8",
        case_connect_copilot_replace_flag_overwrites_preserves_others,
    ),
    (
        "connect copilot --dry-run prints merged JSON to stdout without writing the config file.",
        "Connect CLI",
        "21.4.5, 21.8",
        case_connect_copilot_dry_run,
    ),
    (
        "connect copilot merges memory-service into existing config that has other servers but no memory-service entry.",
        "Connect CLI",
        "21.4.2, 21.8",
        case_connect_copilot_merge_existing_with_others,
    ),
    (
        "connect copilot resolves a relative --data-dir to an absolute path against --project-root.",
        "Connect CLI",
        "21.4.3, 21.8",
        case_connect_relative_data_dir_resolved,
    ),
    # --- connect cursor ---
    (
        "connect cursor writes project .cursor/mcp.json with absolute MEMORY_SERVICE_DATA_DIR.",
        "Connect CLI",
        "21.4.1, 21.4.3, 21.8",
        case_connect_cursor_project_target,
    ),
    (
        "connect cursor writes global ~/.cursor/mcp.json with absolute MEMORY_SERVICE_DATA_DIR.",
        "Connect CLI",
        "21.4.1, 21.4.3, 21.8",
        case_connect_cursor_global_target,
    ),
    (
        "connect cursor --dry-run prints merged JSON config to stdout without writing any config file.",
        "Connect CLI",
        "21.4.5, 21.8",
        case_connect_cursor_dry_run,
    ),
    # --- connect windsurf ---
    (
        "connect windsurf writes global mcp_config.json with a valid mcpServers JSON object.",
        "Connect CLI",
        "21.4.1, 21.4.4, 21.8",
        case_connect_windsurf_global_mcp_config,
    ),
    (
        "connect windsurf exits 1 and refuses clobber when memory-service entry exists and --replace is omitted.",
        "Connect CLI",
        "21.4.2, 21.4.5, 21.8",
        case_connect_windsurf_refuses_clobber,
    ),
    (
        "connect windsurf --replace overwrites memory-service entry and preserves other mcpServers entries.",
        "Connect CLI",
        "21.4.2, 21.4.5, 21.8",
        case_connect_windsurf_replace_preserves_others,
    ),
    (
        "connect windsurf --dry-run prints merged JSON config to stdout without writing the config file.",
        "Connect CLI",
        "21.4.5, 21.8",
        case_connect_windsurf_dry_run,
    ),
    # --- connect codex ---
    (
        "connect codex writes TOML config with [mcp_servers.memory-service] section and absolute data-dir.",
        "Connect CLI",
        "21.4.1, 21.4.3, 21.8",
        case_connect_codex_toml_merge,
    ),
    (
        "connect codex exits 1 and refuses clobber when [mcp_servers.memory-service] entry exists and --replace is omitted.",
        "Connect CLI",
        "21.4.2, 21.4.5, 21.8",
        case_connect_codex_refuses_clobber,
    ),
    (
        "connect codex --replace updates [mcp_servers.memory-service] and preserves other TOML sections.",
        "Connect CLI",
        "21.4.2, 21.4.5, 21.8",
        case_connect_codex_replace_preserves_others,
    ),
    (
        "connect codex --dry-run prints merged TOML to stdout without writing any config file.",
        "Connect CLI",
        "21.4.5, 21.8",
        case_connect_codex_dry_run,
    ),
    # --- unsupported harness ---
    (
        "ai-memory connect with an unsupported harness name exits with code 2.",
        "Connect CLI",
        "21.4, 21.4.5, 21.8",
        case_connect_unknown_harness_exits_2,
    ),
    # --- catalog consistency ---
    (
        "integrations.yaml contains all P0 harness IDs at tier A or B per Section 21.2.",
        "Integration Catalog",
        "21.2, 21.3, 21.8",
        case_catalog_p0_entries_at_tier_a_or_b,
    ),
    (
        "integrations.yaml tier-B CLI-managed harnesses carry a non-null connect_command.",
        "Integration Catalog",
        "21.3, 21.4, 21.8",
        case_catalog_tier_b_cli_harnesses_have_connect_command,
    ),
    (
        "integrations.yaml P1 tier-D entries carry tier_target B per Section 21.1.",
        "Integration Catalog",
        "21.1, 21.3, 21.8",
        case_catalog_p1_entries_have_tier_target_b,
    ),
    # --- smoke scripts ---
    (
        "claude-code phase3 smoke script exits 0 (Hermes + OpenClaw hybrid search verify).",
        "Smoke Scripts",
        "21.5, 21.6, 21.8",
        case_smoke_claude_code,
    ),
    (
        "copilot-cli smoke script exits 0 (memory_status via MCP stdio + eleven-tool verify).",
        "Smoke Scripts",
        "21.5, 21.6, 21.8",
        case_smoke_copilot_cli,
    ),
    (
        "codex smoke script exits 0 (memory_status via MCP stdio + eleven-tool verify).",
        "Smoke Scripts",
        "21.5, 21.6, 21.8",
        case_smoke_codex,
    ),
    (
        "cursor smoke script exits 0 (memory_status via MCP stdio + eleven-tool verify).",
        "Smoke Scripts",
        "21.5, 21.6, 21.8",
        case_smoke_cursor,
    ),
    (
        "windsurf smoke script exits 0 (memory_status via MCP stdio + eleven-tool verify).",
        "Smoke Scripts",
        "21.5, 21.6, 21.8",
        case_smoke_windsurf,
    ),
    # --- MCP backward compatibility ---
    (
        "Phase 6 connect CLI does not add or remove tools from the eleven-tool MCP surface.",
        "Backward Compatibility",
        "21, 21.8",
        case_mcp_surface_unchanged_with_phase6,
    ),
]
