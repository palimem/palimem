#!/usr/bin/env python3
"""Phase 4 exit-criteria benchmark: profile recall vs USER.md-only baseline."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "app"
HERMES_ADAPTER = ROOT / "adapters" / "hermes"
PROBES_PATH = ROOT / "examples" / "markdown" / "persona-benchmark-probes.json"
USER_MD_SAMPLE = ROOT / "examples" / "markdown" / "USER.md.sample"

sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERMES_ADAPTER))

from memory_service.portability import import_markdown_file  # noqa: E402
from memory_service.service import MemoryService  # noqa: E402


def _needle_hit(text: str, needle: str) -> bool:
    return needle.lower() in text.lower()


def _search_hits(service: MemoryService, namespace: str, query: str, needle: str) -> bool:
    payload = service.memory_search(
        {
            "scope": "user",
            "namespace": namespace,
            "query": query,
            "limit": 10,
        }
    )
    if payload.get("ok") is not True:
        return False
    for result in payload.get("results", []):
        value = result.get("value")
        if isinstance(value, str) and _needle_hit(value, needle):
            return True
        if isinstance(value, dict):
            blob = json.dumps(value, ensure_ascii=True)
            if _needle_hit(blob, needle):
                return True
    return False


def _profile_hit(service: MemoryService, namespace: str, needle: str) -> bool:
    payload = service.memory_profile(
        {
            "scope": "user",
            "namespace": namespace,
            "depth": "full",
            "budget_tokens": 4096,
        }
    )
    if payload.get("ok") is not True:
        return False
    manifest = str(payload.get("manifest", ""))
    if _needle_hit(manifest, needle):
        return True
    for section in payload.get("sections", []):
        if _needle_hit(str(section.get("text", "")), needle):
            return True
    return False


def _score_user_md_baseline(service: MemoryService, namespace: str, probes: list[dict[str, str]]) -> tuple[int, int]:
    hits = 0
    for probe in probes:
        if _search_hits(service, namespace, probe["query"], probe["needle"]):
            hits += 1
    return hits, len(probes)


def _score_profile_treatment(service: MemoryService, namespace: str, probes: list[dict[str, str]]) -> tuple[int, int]:
    hits = 0
    for probe in probes:
        query = probe["query"]
        needle = probe["needle"]
        if _search_hits(service, namespace, query, needle) or _profile_hit(service, namespace, needle):
            hits += 1
    return hits, len(probes)


def _run_profile_engine(service: MemoryService, namespace: str, episode_text: str) -> None:
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
            "episode_id": "phase4-benchmark-episode",
            "provenance": {
                "source": "phase4-benchmark",
                "tool": "phase4-profile-benchmark",
                "actor": "benchmark",
                "request_id": "episode-1",
            },
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


def _hermes_user_md_prefetch_hit(hermes_home: Path, workspace: Path, namespace: str, query: str, needle: str) -> bool:
    from ai_memory_hermes import AiMemoryProvider  # noqa: E402

    provider = AiMemoryProvider()
    config = {
        "data_dir": str(workspace / ".ai-memory" / "data"),
        "namespace": namespace,
        "recall_mode": "hybrid",
        "mirror_builtin_memory": True,
        "profile_engine_enabled": False,
        "prefetch_limit": 8,
        "sync_turn_enabled": False,
    }
    provider.save_config(config, str(hermes_home))
    shutil.copy2(USER_MD_SAMPLE, hermes_home / "USER.md")
    provider.initialize("phase4-benchmark", hermes_home=str(hermes_home), workspace_root=str(workspace))
    prefetch = provider.prefetch(query, session_id="phase4-benchmark")
    provider.shutdown()
    return bool(prefetch) and _needle_hit(prefetch, needle)


def main() -> int:
    probes = json.loads(PROBES_PATH.read_text(encoding="utf-8"))
    user_md_probes = probes["user_md_probes"]
    episode_probes = probes["episode_probes"]
    episode_text = probes["episode_transcript"]
    namespace = "phase4-benchmark"

    with tempfile.TemporaryDirectory(prefix="phase4-benchmark-") as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        service = MemoryService(data_dir, "fresh", None)
        import_markdown_file(
            service,
            path=USER_MD_SAMPLE,
            scope="user",
            namespace=namespace,
            default_topic="user_profile",
            default_memory_type="preference",
            source_kind="USER.md",
        )
        baseline_hits, baseline_total = _score_user_md_baseline(service, namespace, user_md_probes)
        baseline_episode_hits, episode_total = _score_user_md_baseline(service, namespace, episode_probes)

        _run_profile_engine(service, namespace, episode_text)
        treatment_user_hits, _ = _score_profile_treatment(service, namespace, user_md_probes)
        treatment_episode_hits, _ = _score_profile_treatment(service, namespace, episode_probes)
        treatment_total_hits = treatment_user_hits + treatment_episode_hits
        baseline_total_hits = baseline_hits + baseline_episode_hits

        hermes_home = tmp_path / "hermes-home"
        hermes_home.mkdir(parents=True)
        hermes_workspace = tmp_path / "hermes-workspace"
        hermes_workspace.mkdir(parents=True)
        hermes_hits = 0
        for probe in user_md_probes:
            if _hermes_user_md_prefetch_hit(
                hermes_home,
                hermes_workspace,
                namespace,
                probe["query"],
                probe["needle"],
            ):
                hermes_hits += 1

    print(json.dumps(
        {
            "baseline_user_md_search": {"hits": baseline_hits, "total": baseline_total},
            "baseline_episode_search": {"hits": baseline_episode_hits, "total": episode_total},
            "baseline_combined": {"hits": baseline_total_hits, "total": baseline_total + episode_total},
            "treatment_profile_plus_search": {
                "user_md_hits": treatment_user_hits,
                "episode_hits": treatment_episode_hits,
                "combined_hits": treatment_total_hits,
                "total": baseline_total + episode_total,
            },
            "hermes_user_md_prefetch": {"hits": hermes_hits, "total": baseline_total},
            "exit_criteria_met": treatment_total_hits >= baseline_total_hits,
        },
        indent=2,
        ensure_ascii=True,
    ))

    if treatment_total_hits < baseline_total_hits:
        print(
            f"FAIL: treatment recall {treatment_total_hits} < baseline {baseline_total_hits}",
            file=sys.stderr,
        )
        return 1

    print("PASS: profile recall meets or exceeds USER.md-only baseline on sample personas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
