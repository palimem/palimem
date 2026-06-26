from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
from typing import Any

from .domain import build_search_text, search_terms, serialize_json


class TfidfRetriever:
    def rank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        terms = search_terms(query)
        if not candidates:
            return []
        if not terms:
            for index, candidate in enumerate(candidates):
                candidate["_score"] = float(len(candidates) - index)
                candidate["match_reason"] = "returned by deterministic fallback ordering"
            return sorted(candidates, key=lambda item: (-item["_score"], -int(item["seq"])))

        document_terms = [search_terms(candidate["_search_text"]) for candidate in candidates]
        doc_count = len(candidates)
        doc_freq = Counter()
        for terms_for_doc in document_terms:
            for term in set(terms_for_doc):
                doc_freq[term] += 1

        ranked: list[dict[str, Any]] = []
        for candidate, terms_for_doc in zip(candidates, document_terms):
            tf = Counter(terms_for_doc)
            score = 0.0
            matched: list[str] = []
            for term in terms:
                if tf[term] == 0:
                    continue
                matched.append(term)
                idf = math.log((1 + doc_count) / (1 + doc_freq[term])) + 1.0
                score += tf[term] * idf
            if score <= 0:
                continue
            candidate["_score"] = score
            candidate["match_reason"] = "matched query terms: " + ", ".join(sorted(set(matched)))
            ranked.append(candidate)

        return sorted(ranked, key=lambda item: (-item["_score"], -int(item["seq"])))


class IndexManager:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, scope: str, namespace: str) -> Path:
        safe_namespace = namespace.replace("/", "_").replace("\\", "_")
        return self.base_dir / f"{scope}__{safe_namespace}.json"

    def rebuild(self, scope: str, namespace: str, records: list[dict[str, Any]]) -> None:
        docs = []
        for record in records:
            doc = dict(record)
            doc["_search_text"] = build_search_text(record)
            docs.append(doc)
        self._path_for(scope, namespace).write_text(serialize_json({"documents": docs}), encoding="utf-8")

    def load(self, scope: str, namespace: str) -> list[dict[str, Any]]:
        path = self._path_for(scope, namespace)
        if not path.exists():
            return []
        payload = path.read_text(encoding="utf-8")
        import json

        return json.loads(payload).get("documents", [])
