#!/usr/bin/env python3
"""Run Palimem research benchmarks and write JSON artifacts."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

from _lib import ARTIFACTS_DIR, ROOT, case_to_dict, write_artifact  # noqa: E402
from agent_tasks import run_agent_tasks  # noqa: E402
from latency_sweep import run_latency_sweep  # noqa: E402
from persona_recall import run_persona_recall  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Palimem research benchmarks.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when a benchmark fails its exit criteria.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ARTIFACTS_DIR / "latest-benchmark-results.json",
        help="Path for the combined JSON artifact.",
    )
    args = parser.parse_args(argv)

    cases = [run_persona_recall(), run_agent_tasks(), run_latency_sweep()]
    blocking_failures = [case for case in cases if case.status == "fail"]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(ROOT),
        "run_command": "python3 benchmarks/run_benchmarks.py",
        "results": [case_to_dict(case) for case in cases],
        "summary": {
            "passed": sum(1 for case in cases if case.status == "pass"),
            "warned": sum(1 for case in cases if case.status == "warn"),
            "failed": sum(1 for case in cases if case.status == "fail"),
            "total": len(cases),
        },
    }
    write_artifact(args.output, payload)

    for case in cases:
        print(f"{case.status.upper()}: {case.name} — {case.notes}")

    if blocking_failures:
        print(
            f"\n{len(blocking_failures)} benchmark(s) failed exit criteria.",
            file=sys.stderr,
        )
        if args.strict:
            return 1
        print("Non-strict mode: exiting 0 (use --strict to fail).", file=sys.stderr)
        return 0

    print("\nALL BENCHMARKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
