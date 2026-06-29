#!/usr/bin/env python3
"""Corpus-size latency sweep for memory_search."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from _lib import BenchmarkCaseResult, open_service, percentile, remember, timed_search

DEFAULT_SIZES = [100, 500, 1000]
DEFAULT_QUERIES = [
    "python backend",
    "deployment region",
    "sqlite wal",
    "editor preference",
    "architecture decision",
    "testing approach",
    "timezone preference",
    "repository noise",
    "semantic units",
    "session episode",
]


def _parse_sizes() -> list[int]:
    raw = os.environ.get("BENCHMARK_CORPUS_SIZES", "").strip()
    if not raw:
        return DEFAULT_SIZES
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def run_latency_sweep() -> BenchmarkCaseResult:
    namespace = "latency-benchmark"
    scope = "repository"
    sizes = _parse_sizes()
    sweep: list[dict[str, float | int]] = []

    with tempfile.TemporaryDirectory(prefix="latency-benchmark-") as tmp:
        for size in sizes:
            service = open_service(Path(tmp) / str(size))
            for index in range(1, size + 1):
                remember(
                    service,
                    scope=scope,
                    namespace=namespace,
                    memory_type="fact",
                    topic="benchmark",
                    field=f"f{index}",
                    value=f"Benchmark fact {index} about python backend sqlite wal deployment region testing.",
                    request_id=f"seed-{size}-{index}",
                )

            samples = [timed_search(service, scope=scope, namespace=namespace, query=query) for query in DEFAULT_QUERIES]
            sweep.append(
                {
                    "corpus_size": size,
                    "queries": len(samples),
                    "mean_ms": round(sum(samples) / len(samples), 3),
                    "p50_ms": round(percentile(samples, 50), 3),
                    "p95_ms": round(percentile(samples, 95), 3),
                    "max_ms": round(max(samples), 3),
                }
            )

    # Informative thresholds for regression tracking; non-blocking in CI.
    max_p95 = max(item["p95_ms"] for item in sweep)
    exit_ok = max_p95 < 500.0
    status = "pass" if exit_ok else "warn"
    notes = (
        f"memory_search latency sweep complete; max p95={max_p95}ms across corpus sizes {sizes}."
        if exit_ok
        else f"memory_search p95 exceeded soft threshold (500ms): max p95={max_p95}ms."
    )
    return BenchmarkCaseResult(
        name="latency_sweep",
        status=status,
        notes=notes,
        metrics={"sizes": sweep, "soft_threshold_p95_ms": 500.0, "exit_criteria_met": exit_ok},
    )
