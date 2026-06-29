#!/usr/bin/env python3
"""Agent-task harness: can governed memory answer task-critical recall probes?"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from _lib import (
    DATA_DIR,
    ROOT,
    BenchmarkCaseResult,
    open_service,
    remember,
)

USER_MD_SAMPLE = ROOT / "examples" / "markdown" / "USER.md.sample"


def _task_passes(service, task: dict[str, Any], namespace: str) -> tuple[bool, str]:
    scope = task["scope"]
    query = task["query"]
    needle = str(task["needle"]).lower()
    exclude = task.get("exclude")
    include_episodes = bool(task.get("include_episodes", False))

    payload = {
        "scope": scope,
        "namespace": namespace,
        "query": query,
        "limit": 10,
    }
    if include_episodes:
        payload["include_episodes"] = True
    response = service.memory_search(payload)
    if response.get("ok") is not True:
        return False, f"memory_search failed: {response}"

    texts: list[str] = []
    for result in response.get("results", []):
        value = result.get("value")
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            texts.append(json.dumps(value, ensure_ascii=True))

    if not texts:
        return False, "no search results"
    joined = "\n".join(texts).lower()
    if needle not in joined:
        return False, f"needle {task['needle']!r} not found"
    if exclude and str(exclude).lower() in joined:
        return False, f"excluded value {exclude!r} still present"
    return True, "recall ok"


def run_agent_tasks() -> BenchmarkCaseResult:
    from memory_service.portability import import_markdown_file  # noqa: E402

    spec = json.loads((DATA_DIR / "agent-tasks.json").read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="agent-task-benchmark-") as tmp:
        for task in spec["tasks"]:
            namespace = f"agent-task-{task['id']}"
            service = open_service(Path(tmp) / task["id"])

            if task.get("import_user_md"):
                import_markdown_file(
                    service,
                    path=USER_MD_SAMPLE,
                    scope="user",
                    namespace=namespace,
                    default_topic="user_profile",
                    default_memory_type="preference",
                    source_kind="USER.md",
                )

            for index, write in enumerate(task.get("writes", [])):
                remember(
                    service,
                    scope=task["scope"],
                    namespace=namespace,
                    memory_type=write["memory_type"],
                    topic=write["topic"],
                    field=write["field"],
                    value=write["value"],
                    request_id=f"{task['id']}-write-{index}",
                )

            for index, write in enumerate(task.get("noise_writes", [])):
                remember(
                    service,
                    scope=task["scope"],
                    namespace=namespace,
                    memory_type=write["memory_type"],
                    topic=write["topic"],
                    field=write["field"],
                    value=write["value"],
                    request_id=f"{task['id']}-noise-{index}",
                )

            ok, detail = _task_passes(service, task, namespace)
            results.append(
                {
                    "id": task["id"],
                    "description": task["description"],
                    "status": "pass" if ok else "fail",
                    "detail": detail,
                }
            )

    passed = sum(1 for item in results if item["status"] == "pass")
    total = len(results)
    exit_ok = passed == total
    status = "pass" if exit_ok else "fail"
    notes = f"Agent-task recall {passed}/{total} tasks passed."
    return BenchmarkCaseResult(
        name="agent_tasks",
        status=status,
        notes=notes,
        metrics={"passed": passed, "total": total, "tasks": results, "exit_criteria_met": exit_ok},
    )
