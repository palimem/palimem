from __future__ import annotations

from typing import Any

from .domain import build_search_text, serialize_json, utc_now_rfc3339

NOISE_SALIENCE_THRESHOLD = 1.0
SUMMARY_FIELD = "_consolidated_notes"
SUMMARY_TOPIC_SUFFIX = "_consolidated"


def compute_corpus_stats(records: list[dict[str, Any]]) -> dict[str, int]:
    units = len(records)
    total_bytes = 0
    for record in records:
        total_bytes += len(build_search_text(record).encode("utf-8"))
    return {"units_before": units, "units_after": units, "bytes_before": total_bytes, "bytes_after": total_bytes}


def plan_safe_merge(
    records: list[dict[str, Any]],
    existing_hidden: set[tuple[str, str | None, str]],
    existing_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Plan a non-destructive safe_merge pass over the active search corpus."""
    visible = [
        record
        for record in records
        if (record["topic"], record["field"], record["memory_type"]) not in existing_hidden
        and not str(record.get("topic", "")).endswith(SUMMARY_TOPIC_SUFFIX)
    ]

    before_stats = compute_corpus_stats(visible + existing_summaries)

    noise_by_topic: dict[str, list[dict[str, Any]]] = {}
    protected: list[dict[str, Any]] = []
    for record in visible:
        if _is_protected_from_merge(record):
            protected.append(record)
            continue
        if _is_mergeable_belief(record):
            noise_by_topic.setdefault(record["topic"], []).append(record)
            continue
        protected.append(record)

    new_hidden: list[tuple[str, str | None, str]] = []
    new_summaries: list[dict[str, Any]] = []
    promotions: list[dict[str, Any]] = []
    review_counter = 0

    for topic, cluster in noise_by_topic.items():
        if len(cluster) < 2:
            protected.extend(cluster)
            continue
        source_seqs = sorted({int(item["seq"]) for item in cluster})
        for item in cluster:
            new_hidden.append((item["topic"], item["field"], item["memory_type"]))
        summary_topic = f"{topic}{SUMMARY_TOPIC_SUFFIX}"
        summary_value = f"{len(cluster)} archived low-salience notes"
        summary_record = {
            "scope": cluster[0]["scope"],
            "namespace": cluster[0]["namespace"],
            "topic": summary_topic,
            "field": SUMMARY_FIELD,
            "memory_type": "belief",
            "value": summary_value,
            "event_id": f"consolidation_{topic}",
            "seq": max(source_seqs),
            "valid_from_seq": max(source_seqs),
            "valid_to_seq": None,
            "recorded_at": utc_now_rfc3339(),
            "provenance": {
                "source": "consolidation",
                "tool": "memory_consolidate",
                "actor": "system",
                "request_id": f"safe_merge_{topic}",
            },
            "salience": 0.5,
            "layer": "semantic_unit",
            "status": "current",
            "extends": [],
        }
        new_summaries.append(summary_record)
        review_counter += 1
        promotions.append(
            {
                "review_id": f"rev_{topic}_{review_counter}",
                "proposed_memory_type": "belief",
                "topic": summary_topic,
                "field": SUMMARY_FIELD,
                "value": summary_value,
                "rationale": (
                    f"Consolidation merged {len(cluster)} low-salience belief units on topic '{topic}' "
                    "into a bounded summary without mutating WAL-held facts."
                ),
                "source_seqs": source_seqs,
            }
        )
        dominant = max(cluster, key=lambda item: float(item.get("salience") or 0.0))
        if dominant.get("memory_type") == "belief":
            review_counter += 1
            promotions.append(
                {
                    "review_id": f"rev_{topic}_{review_counter}_fact",
                    "proposed_memory_type": "fact",
                    "topic": topic,
                    "field": dominant.get("field"),
                    "value": dominant.get("value"),
                    "rationale": (
                        f"Consolidation proposes promoting the strongest belief on '{topic}' "
                        "after deduplicating redundant low-salience noise."
                    ),
                    "source_seqs": [int(dominant["seq"])],
                }
            )

    after_records = protected + existing_summaries + new_summaries
    after_stats = compute_corpus_stats(after_records)
    after_stats["units_before"] = before_stats["units_before"]
    after_stats["bytes_before"] = before_stats["bytes_before"]

    return {
        "stats": after_stats,
        "promotions": promotions,
        "hidden_units": new_hidden,
        "summary_units": new_summaries,
    }


def _is_protected_from_merge(record: dict[str, Any]) -> bool:
    memory_type = record.get("memory_type")
    if memory_type == "fact":
        return True
    if memory_type in {"preference", "constraint", "procedure"}:
        return True
    return False


def _is_mergeable_belief(record: dict[str, Any]) -> bool:
    """Beliefs are safe_merge noise candidates; held facts stay protected separately."""
    if record.get("memory_type") != "belief":
        return False
    salience = record.get("salience")
    if salience is None:
        return True
    return float(salience) <= NOISE_SALIENCE_THRESHOLD
