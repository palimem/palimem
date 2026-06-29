#!/usr/bin/env python3
"""Expanded persona recall benchmark: USER.md baseline vs profile+search treatment."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from _lib import (
    DATA_DIR,
    ROOT,
    BenchmarkCaseResult,
    open_service,
    profile_hits,
    provenance,
    remember,
    search_hits,
)

USER_MD_SAMPLE = ROOT / "examples" / "markdown" / "USER.md.sample"


def _run_profile_engine(service, namespace: str, episode_text: str) -> None:
    service.storage.set_profile_engine_enabled_namespace(namespace, True)
    for scope in ("user", "session", "repository"):
        service.storage.set_profile_engine_enabled(scope, namespace, True)
    service.memory_remember(
        {
            "scope": "session",
            "namespace": namespace,
            "memory_type": "episode",
            "topic": "session_turn",
            "field": "turn",
            "value": episode_text,
            "episode_id": "persona-benchmark-episode",
            "provenance": provenance("episode-1"),
        }
    )
    service.run_profile_engine(
        scope="session",
        namespace=namespace,
        require_enabled=True,
        async_run=False,
        source_scope="session",
        source_namespace=namespace,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        status = service.memory_status({"scope": "user", "namespace": namespace})
        if status.get("profile_engine", {}).get("last_run_at"):
            break
        time.sleep(0.1)


def _score_probes(
    service,
    *,
    scope: str,
    namespace: str,
    probes: list[dict[str, str]],
    use_profile: bool,
) -> tuple[int, int]:
    hits = 0
    for probe in probes:
        query = probe["query"]
        needle = probe["needle"]
        if search_hits(service, scope=scope, namespace=namespace, query=query, needle=needle):
            hits += 1
        elif use_profile and profile_hits(service, scope=scope, namespace=namespace, needle=needle):
            hits += 1
    return hits, len(probes)


def run_persona_recall() -> BenchmarkCaseResult:
    from memory_service.portability import import_markdown_file  # noqa: E402

    spec = json.loads((DATA_DIR / "persona-probes.json").read_text(encoding="utf-8"))
    namespace = "persona-benchmark"
    scope = "user"

    with tempfile.TemporaryDirectory(prefix="persona-benchmark-") as tmp:
        service = open_service(Path(tmp))
        import_markdown_file(
            service,
            path=USER_MD_SAMPLE,
            scope=scope,
            namespace=namespace,
            default_topic="user_profile",
            default_memory_type="preference",
            source_kind="USER.md",
        )

        for item in spec.get("noise_seed", []):
            remember(
                service,
                scope=scope,
                namespace=namespace,
                memory_type=item["memory_type"],
                topic=item["topic"],
                field=item["field"],
                value=item["value"],
                request_id=f"noise-{item['field']}",
            )

        baseline_user_hits, baseline_user_total = _score_probes(
            service,
            scope=scope,
            namespace=namespace,
            probes=spec["user_md_probes"],
            use_profile=False,
        )
        baseline_episode_hits, baseline_episode_total = _score_probes(
            service,
            scope=scope,
            namespace=namespace,
            probes=spec["episode_probes"],
            use_profile=False,
        )
        baseline_noise_hits, baseline_noise_total = _score_probes(
            service,
            scope=scope,
            namespace=namespace,
            probes=spec.get("noise_probes", []),
            use_profile=False,
        )

        supersession_results: list[dict[str, Any]] = []
        for probe in spec.get("supersession_probes", []):
            probe_scope = probe.get("scope", "repository")
            for index, write in enumerate(probe["writes"]):
                remember(
                    service,
                    scope=probe_scope,
                    namespace=namespace,
                    memory_type=write["memory_type"],
                    topic=write["topic"],
                    field=write["field"],
                    value=write["value"],
                    request_id=f"supersede-{probe['id']}-{index}",
                )
            ok = search_hits(
                service,
                scope=probe_scope,
                namespace=namespace,
                query=probe["query"],
                needle=probe["needle"],
                exclude=probe.get("exclude"),
            )
            supersession_results.append({"id": probe["id"], "hit": ok})

        _run_profile_engine(service, namespace, spec["episode_transcript"])
        treatment_user_hits, _ = _score_probes(
            service,
            scope=scope,
            namespace=namespace,
            probes=spec["user_md_probes"],
            use_profile=True,
        )
        treatment_episode_hits, _ = _score_probes(
            service,
            scope=scope,
            namespace=namespace,
            probes=spec["episode_probes"],
            use_profile=True,
        )
        treatment_noise_hits, _ = _score_probes(
            service,
            scope=scope,
            namespace=namespace,
            probes=spec.get("noise_probes", []),
            use_profile=True,
        )

        baseline_combined = baseline_user_hits + baseline_episode_hits + baseline_noise_hits
        baseline_total = baseline_user_total + baseline_episode_total + baseline_noise_total
        treatment_combined = treatment_user_hits + treatment_episode_hits + treatment_noise_hits
        supersession_passed = all(item["hit"] for item in supersession_results)
        exit_ok = treatment_combined >= baseline_combined and supersession_passed

        metrics = {
            "baseline_user_md_search": {"hits": baseline_user_hits, "total": baseline_user_total},
            "baseline_episode_search": {"hits": baseline_episode_hits, "total": baseline_episode_total},
            "baseline_noise_search": {"hits": baseline_noise_hits, "total": baseline_noise_total},
            "baseline_combined": {"hits": baseline_combined, "total": baseline_total},
            "treatment_profile_plus_search": {
                "user_md_hits": treatment_user_hits,
                "episode_hits": treatment_episode_hits,
                "noise_hits": treatment_noise_hits,
                "combined_hits": treatment_combined,
                "total": baseline_total,
            },
            "supersession_probes": supersession_results,
            "exit_criteria_met": exit_ok,
        }

    status = "pass" if exit_ok else "fail"
    notes = (
        "Expanded persona recall meets or exceeds USER.md-only baseline and preserves supersession correctness."
        if exit_ok
        else "Persona recall regressed versus baseline or failed a supersession probe."
    )
    return BenchmarkCaseResult(name="persona_recall", status=status, notes=notes, metrics=metrics)
