#!/usr/bin/env python3
"""Automated dogfood probe — simulates a disciplined agent via MCP stdio."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
DEFAULT_PROBES = Path(__file__).resolve().parent / "probes.json"


@dataclass
class ProbeResult:
    probe_id: str
    mode: str
    prompt: str
    status: str
    detail: str
    needles_hit: list[str]
    anti_needles_hit: list[str]


class McpStdioClient:
    def __init__(self, command: list[str], *, env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=False,
        )

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        body = json.dumps(msg).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _recv(self) -> dict[str, Any]:
        assert self._proc.stdout is not None
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            ch = self._proc.stdout.read(1)
            if not ch:
                raise EOFError("MCP server closed stdout")
            header += ch
        length = int(
            [line for line in header.decode().splitlines() if line.startswith("Content-Length:")][0]
            .split(":", 1)[1]
            .strip()
        )
        payload = self._proc.stdout.read(length)
        return json.loads(payload)

    def initialize(self) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "dogfood-automated-probe", "version": "1.0"},
                },
            }
        )
        response = self._recv()
        if "result" not in response:
            raise RuntimeError(f"initialize failed: {response}")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        response = self._recv()
        if "error" in response:
            raise RuntimeError(f"{name} error: {response['error']}")
        result = response.get("result", {})
        content = result.get("content", [])
        if not content:
            return {}
        text = content[0].get("text", "{}")
        return json.loads(text)

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.terminate()
        self._proc.wait(timeout=5)


def _needle_hits(text: str, needles: list[str]) -> list[str]:
    lowered = text.lower()
    return [needle for needle in needles if needle.lower() in lowered]


def _collect_search_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("results", []):
        value = item.get("value")
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, dict):
            chunks.append(json.dumps(value, ensure_ascii=True))
    return "\n".join(chunks)


def run_mcp_probes(
    *,
    data_dir: Path,
    namespace: str,
    probes: list[dict[str, Any]],
    anti_needles: list[str],
    transport: str,
) -> list[ProbeResult]:
    env = {**os.environ, "MEMORY_SERVICE_DATA_DIR": str(data_dir)}
    if transport == "docker":
        image = os.environ.get("DOGFOOD_DOCKER_IMAGE", "palimem-mcp:local")
        container_data = "/data"
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "-e",
            f"MEMORY_SERVICE_DATA_DIR={container_data}",
            "-v",
            f"{data_dir}:{container_data}",
            image,
        ]
    elif transport == "node":
        command = ["node", str(APP / "scripts" / "memory-service-mcp.js")]
    else:
        command = [
            os.environ.get("MEMORY_SERVICE_PYTHON", "python3"),
            str(APP / "run_production_stdio_server.py"),
        ]

    client = McpStdioClient(command, env=env)
    results: list[ProbeResult] = []
    try:
        client.initialize()

        for probe in probes:
            scope = probe["scope"]
            status_payload = client.call_tool(
                "memory_status",
                {"scope": scope, "namespace": namespace},
            )
            if status_payload.get("ok") is not True:
                results.append(
                    ProbeResult(
                        probe_id=probe["id"],
                        mode="mcp",
                        prompt=probe["prompt"],
                        status="fail",
                        detail=f"memory_status failed: {status_payload}",
                        needles_hit=[],
                        anti_needles_hit=[],
                    )
                )
                continue

            search_payload = client.call_tool(
                "memory_search",
                {
                    "scope": scope,
                    "namespace": namespace,
                    "query": probe["search_query"],
                    "limit": 8,
                },
            )
            text = _collect_search_text(search_payload)
            hits = _needle_hits(text, probe["needles"])
            anti = _needle_hits(text, anti_needles)
            ok = bool(hits) and not anti
            detail = f"hits={hits or 'none'}; anti={anti or 'none'}; wal={status_payload.get('wal_high_water_seq')}"
            results.append(
                ProbeResult(
                    probe_id=probe["id"],
                    mode="mcp",
                    prompt=probe["prompt"],
                    status="pass" if ok else "fail",
                    detail=detail,
                    needles_hit=hits,
                    anti_needles_hit=anti,
                )
            )
    finally:
        client.close()
    return results


def run_static_probes(
    *,
    probes: list[dict[str, Any]],
    anti_needles: list[str],
    user_md: Path,
    memory_md: Path,
) -> list[ProbeResult]:
    corpus = {
        "user": user_md.read_text(encoding="utf-8"),
        "repository": memory_md.read_text(encoding="utf-8"),
    }
    results: list[ProbeResult] = []
    for probe in probes:
        text = corpus.get(probe["scope"], "")
        # naive static search: whole-file text only (no TF-IDF)
        hits = _needle_hits(text, probe["needles"])
        anti = _needle_hits(text, anti_needles)
        ok = bool(hits) and not anti
        results.append(
            ProbeResult(
                probe_id=probe["id"],
                mode="static",
                prompt=probe["prompt"],
                status="pass" if ok else "fail",
                detail=f"static markdown hits={hits or 'none'}; anti={anti or 'none'}",
                needles_hit=hits,
                anti_needles_hit=anti,
            )
        )
    return results


def run_none_probes(*, probes: list[dict[str, Any]]) -> list[ProbeResult]:
    return [
        ProbeResult(
            probe_id=probe["id"],
            mode="none",
            prompt=probe["prompt"],
            status="fail",
            detail="no memory surface (baseline)",
            needles_hit=[],
            anti_needles_hit=[],
        )
        for probe in probes
    ]


def load_spec(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    return spec["namespace"], spec["probes"], spec.get("anti_needles", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automated Palimem dogfood probe runner.")
    parser.add_argument(
        "--mode",
        choices=["mcp", "static", "none", "all"],
        default="all",
        help="Probe mode: mcp (stdio), static (markdown only), none (empty baseline), or all.",
    )
    parser.add_argument(
        "--transport",
        choices=["python", "node", "docker"],
        default="python",
        help="MCP transport for --mode mcp/all.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / ".ai-memory" / "data",
        help="MEMORY_SERVICE_DATA_DIR for MCP mode.",
    )
    parser.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    parser.add_argument("--output", type=Path, default=ROOT / "dogfood" / "artifacts" / "latest-probe-results.json")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any MCP probe fails.")
    args = parser.parse_args(argv)

    namespace, probes, anti_needles = load_spec(args.probes)
    user_md = ROOT / "dogfood" / "USER.md"
    memory_md = ROOT / "dogfood" / "MEMORY.md"

    all_results: list[ProbeResult] = []
    modes = ["mcp", "static", "none"] if args.mode == "all" else [args.mode]

    if "none" in modes:
        all_results.extend(run_none_probes(probes=probes))
    if "static" in modes:
        all_results.extend(run_static_probes(probes=probes, anti_needles=anti_needles, user_md=user_md, memory_md=memory_md))
    if "mcp" in modes:
        if not args.data_dir.is_dir():
            print(f"ERROR: data dir missing — run: bash dogfood/setup.sh ({args.data_dir})", file=sys.stderr)
            return 2
        all_results.extend(
            run_mcp_probes(
                data_dir=args.data_dir.resolve(),
                namespace=namespace,
                probes=probes,
                anti_needles=anti_needles,
                transport=args.transport,
            )
        )

    passed = sum(1 for item in all_results if item.status == "pass")
    payload = {
        "results": [asdict(item) for item in all_results],
        "summary": {
            "passed": passed,
            "failed": len(all_results) - passed,
            "total": len(all_results),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    for item in all_results:
        print(f"{item.status.upper():4} [{item.mode:6}] {item.probe_id}: {item.detail}")

    print(f"\nSummary: {passed}/{len(all_results)} passed — artifact: {args.output}")

    mcp_failures = [item for item in all_results if item.mode == "mcp" and item.status == "fail"]
    if args.strict and mcp_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
