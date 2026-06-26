from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .domain import serialize_json


def _topic_entity_id(topic: str) -> str:
    return f"topic:{topic}"


def _field_entity_id(topic: str, field: str | None) -> str:
    suffix = field if field is not None else "_"
    return f"field:{topic}:{suffix}"


class GraphIndex:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, scope: str, namespace: str) -> Path:
        safe_namespace = namespace.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{scope}__{safe_namespace}.json"

    def rebuild(self, scope: str, namespace: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        snapshot = derive_graph_snapshot(records)
        self._path_for(scope, namespace).write_text(serialize_json(snapshot), encoding="utf-8")
        return snapshot


def derive_graph_snapshot(records: list[dict[str, Any]]) -> dict[str, Any]:
    topic_ranges: dict[str, dict[str, Any]] = {}
    topic_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    field_entities: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    for record in records:
        if record.get("memory_type") == "episode":
            continue
        topic = str(record["topic"])
        field = record.get("field")
        topic_id = _topic_entity_id(topic)
        source_subject = {
            "scope": record["scope"],
            "namespace": record["namespace"],
            "topic": topic,
            "field": field,
            "memory_type": record["memory_type"],
        }
        current_topic = topic_ranges.get(topic_id)
        if current_topic is None:
            topic_ranges[topic_id] = {
                "entity_id": topic_id,
                "entity_type": "subject_topic",
                "label": topic,
                "aliases": [],
                "valid_from_seq": record["valid_from_seq"],
                "valid_to_seq": record["valid_to_seq"],
            }
        else:
            current_topic["valid_from_seq"] = min(current_topic["valid_from_seq"], record["valid_from_seq"])
            if current_topic["valid_to_seq"] is None or record["valid_to_seq"] is None:
                current_topic["valid_to_seq"] = None
            else:
                current_topic["valid_to_seq"] = max(current_topic["valid_to_seq"], record["valid_to_seq"])
        topic_sources[topic_id].append(source_subject)

        field_id = _field_entity_id(topic, field)
        field_entity = field_entities.get(field_id)
        if field_entity is None:
            label = topic if field is None else f"{topic}.{field}"
            field_entities[field_id] = {
                "entity_id": field_id,
                "entity_type": "subject_field",
                "label": label,
                "aliases": [],
                "valid_from_seq": record["valid_from_seq"],
                "valid_to_seq": record["valid_to_seq"],
                "source_subjects": [source_subject],
            }
        else:
            field_entity["valid_from_seq"] = min(field_entity["valid_from_seq"], record["valid_from_seq"])
            if field_entity["valid_to_seq"] is None or record["valid_to_seq"] is None:
                field_entity["valid_to_seq"] = None
            else:
                field_entity["valid_to_seq"] = max(field_entity["valid_to_seq"], record["valid_to_seq"])
            field_entity["source_subjects"].append(source_subject)

        field_edge_type = field or "field"
        field_edge_id = f"field:{topic}:{field or '_'}:{record['memory_type']}:{record.get('persona_id') or '_'}"
        edges[field_edge_id] = {
            "edge_id": field_edge_id,
            "edge_type": field_edge_type,
            "from_entity_id": topic_id,
            "to_entity_id": field_id,
            "valid_from_seq": record["valid_from_seq"],
            "valid_to_seq": record["valid_to_seq"],
            "properties": {
                "memory_type": record["memory_type"],
                "value": record.get("value"),
                "field": field,
                "persona_id": record.get("persona_id"),
            },
            "source_event_id": record["event_id"],
        }

        extends = record.get("extends") or []
        for parent in extends:
            parent_topic = str(parent["topic"])
            parent_field = parent.get("field")
            parent_id = _topic_entity_id(parent_topic)
            if parent_id not in topic_ranges:
                topic_ranges[parent_id] = {
                    "entity_id": parent_id,
                    "entity_type": "subject_topic",
                    "label": parent_topic,
                    "aliases": [],
                    "valid_from_seq": record["valid_from_seq"],
                    "valid_to_seq": record["valid_to_seq"],
                }
                topic_sources[parent_id] = []
            depends_on_id = (
                f"depends_on:{topic}:{record['memory_type']}:{field or '_'}:{parent_topic}:{parent_field or '_'}"
            )
            edges[depends_on_id] = {
                "edge_id": depends_on_id,
                "edge_type": "depends_on",
                "from_entity_id": topic_id,
                "to_entity_id": parent_id,
                "valid_from_seq": record["valid_from_seq"],
                "valid_to_seq": record["valid_to_seq"],
                "properties": {
                    "field": field,
                    "memory_type": record["memory_type"],
                    "parent_field": parent_field,
                },
                "source_event_id": record["event_id"],
            }

    entities = list(topic_ranges.values()) + list(field_entities.values())
    for entity in entities:
        if entity["entity_id"] in topic_sources:
            entity["source_subjects"] = topic_sources[entity["entity_id"]]
    entities.sort(key=lambda item: item["entity_id"])
    sorted_edges = sorted(edges.values(), key=lambda item: item["edge_id"])
    return {"entities": entities, "edges": sorted_edges}
