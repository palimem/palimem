from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import sqlite3
import threading
from typing import Any, Iterator

from .domain import SubjectKey, deserialize_json, is_expired, parent_edge_key, parse_rfc3339_utc, serialize_json, utc_now_rfc3339


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "memory_service.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self.initialize_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def initialize_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wal_events (
                    event_id TEXT PRIMARY KEY,
                    seq INTEGER NOT NULL UNIQUE,
                    recorded_at TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    field TEXT,
                    value_json TEXT,
                    episode_id TEXT,
                    extends_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS semantic_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    field TEXT,
                    memory_type TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    valid_from_seq INTEGER NOT NULL,
                    valid_to_seq INTEGER,
                    recorded_at TEXT NOT NULL,
                    episode_id TEXT,
                    event_id TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    salience REAL,
                    extends_json TEXT NOT NULL,
                    bindings_json TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES wal_events(event_id)
                );

                CREATE TABLE IF NOT EXISTS namespace_status (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    index_state TEXT NOT NULL,
                    last_indexed_at TEXT,
                    last_consolidation_at TEXT,
                    PRIMARY KEY (scope, namespace)
                );

                CREATE TABLE IF NOT EXISTS review_promotions (
                    review_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    proposed_memory_type TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    field TEXT,
                    value_json TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    source_seqs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    PRIMARY KEY (scope, namespace, review_id)
                );

                CREATE TABLE IF NOT EXISTS consolidation_hidden (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    field TEXT,
                    memory_type TEXT NOT NULL,
                    PRIMARY KEY (scope, namespace, topic, field, memory_type)
                );

                CREATE TABLE IF NOT EXISTS consolidation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    field TEXT,
                    memory_type TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    salience REAL,
                    UNIQUE(scope, namespace, topic, field, memory_type)
                );
                """
            )
            self._migrate_schema()

    def _migrate_schema(self) -> None:
        self._ensure_column("wal_events", "expires_at", "TEXT")
        self._ensure_column("wal_events", "blocks_actions_json", "TEXT")
        self._ensure_column("wal_events", "observation_json", "TEXT")
        self._ensure_column("semantic_versions", "expires_at", "TEXT")
        self._ensure_column("semantic_versions", "blocks_actions_json", "TEXT")

    def _ensure_column(self, table: str, column: str, column_type: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row[1]) for row in rows}
        if column in existing:
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(query, params).fetchall()

    def next_seq(self, cursor: sqlite3.Cursor) -> int:
        row = cursor.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM wal_events").fetchone()
        return int(row["next_seq"])

    def next_recorded_at(self, cursor: sqlite3.Cursor, requested_at: str) -> str:
        row = cursor.execute("SELECT recorded_at FROM wal_events ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return requested_at
        last_recorded_at = str(row["recorded_at"])
        if last_recorded_at < requested_at:
            return requested_at
        return (parse_rfc3339_utc(last_recorded_at) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")

    def upsert_namespace_status(self, scope: str, namespace: str, index_state: str, last_indexed_at: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO namespace_status (scope, namespace, index_state, last_indexed_at, last_consolidation_at)
                VALUES (?, ?, ?, ?, COALESCE((SELECT last_consolidation_at FROM namespace_status WHERE scope = ? AND namespace = ?), NULL))
                ON CONFLICT(scope, namespace) DO UPDATE SET
                    index_state = excluded.index_state,
                    last_indexed_at = excluded.last_indexed_at
                """,
                (scope, namespace, index_state, last_indexed_at, scope, namespace),
            )

    def set_last_consolidation(self, scope: str, namespace: str, timestamp: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO namespace_status (scope, namespace, index_state, last_indexed_at, last_consolidation_at)
                VALUES (?, ?, 'current', NULL, ?)
                ON CONFLICT(scope, namespace) DO UPDATE SET
                    last_consolidation_at = excluded.last_consolidation_at
                """,
                (scope, namespace, timestamp),
            )

    def get_namespace_status(self, scope: str, namespace: str) -> dict[str, Any]:
        row = self.fetchone(
            "SELECT index_state, last_indexed_at, last_consolidation_at FROM namespace_status WHERE scope = ? AND namespace = ?",
            (scope, namespace),
        )
        if row is None:
            return {"index_state": "current", "last_indexed_at": None, "last_consolidation_at": None}
        return dict(row)

    def all_namespaces(self) -> list[tuple[str, str]]:
        rows = self.fetchall("SELECT DISTINCT scope, namespace FROM wal_events ORDER BY scope, namespace")
        return [(str(row["scope"]), str(row["namespace"])) for row in rows]

    def insert_wal_event(self, cursor: sqlite3.Cursor, event: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO wal_events (
                event_id, seq, recorded_at, scope, namespace, kind, memory_type, topic, field,
                value_json, episode_id, extends_json, provenance_json, expires_at,
                blocks_actions_json, observation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["seq"],
                event["recorded_at"],
                event["scope"],
                event["namespace"],
                event["kind"],
                event["memory_type"],
                event["topic"],
                event["field"],
                event["value_json"],
                event["episode_id"],
                event["extends_json"],
                event["provenance_json"],
                event.get("expires_at"),
                event.get("blocks_actions_json"),
                event.get("observation_json"),
            ),
        )

    def close_open_versions(self, cursor: sqlite3.Cursor, subject: SubjectKey, closing_seq: int) -> None:
        cursor.execute(
            """
            UPDATE semantic_versions
            SET valid_to_seq = ?
            WHERE scope = ? AND namespace = ? AND topic = ?
              AND ((field IS NULL AND ? IS NULL) OR field = ?)
              AND memory_type = ? AND valid_to_seq IS NULL
            """,
            (
                closing_seq,
                subject.scope,
                subject.namespace,
                subject.topic,
                subject.field,
                subject.field,
                subject.memory_type,
            ),
        )

    def insert_semantic_version(self, cursor: sqlite3.Cursor, version: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO semantic_versions (
                scope, namespace, topic, field, memory_type, value_json, seq, valid_from_seq,
                valid_to_seq, recorded_at, episode_id, event_id, provenance_json, salience,
                extends_json, bindings_json, layer, expires_at, blocks_actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version["scope"],
                version["namespace"],
                version["topic"],
                version["field"],
                version["memory_type"],
                version["value_json"],
                version["seq"],
                version["valid_from_seq"],
                version["valid_to_seq"],
                version["recorded_at"],
                version["episode_id"],
                version["event_id"],
                version["provenance_json"],
                version["salience"],
                version["extends_json"],
                version["bindings_json"],
                version["layer"],
                version.get("expires_at"),
                version.get("blocks_actions_json"),
            ),
        )

    def get_current_parent_rows(self, scope: str, namespace: str, edges: list[dict[str, str | None]]) -> dict[str, sqlite3.Row]:
        result: dict[str, sqlite3.Row] = {}
        evaluation_time = utc_now_rfc3339()
        with self._lock:
            for edge in edges:
                row = self._conn.execute(
                    """
                    SELECT * FROM semantic_versions
                    WHERE scope = ? AND namespace = ? AND topic = ?
                      AND ((field IS NULL AND ? IS NULL) OR field = ?)
                      AND memory_type <> 'episode' AND valid_to_seq IS NULL
                    ORDER BY seq DESC LIMIT 1
                    """,
                    (scope, namespace, edge["topic"], edge.get("field"), edge.get("field")),
                ).fetchone()
                if row is not None and not self._row_is_expired(row, evaluation_time):
                    result[parent_edge_key(edge["topic"], edge.get("field"))] = row
        return result

    def get_subject_versions(self, subject: SubjectKey) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND topic = ?
              AND ((field IS NULL AND ? IS NULL) OR field = ?)
              AND memory_type = ?
            ORDER BY seq DESC
            """,
            (subject.scope, subject.namespace, subject.topic, subject.field, subject.field, subject.memory_type),
        )

    def get_evaluation_time(self, evaluation_seq: int | None, as_of_recorded_at: str | None = None) -> str:
        if evaluation_seq is None:
            return utc_now_rfc3339()
        row = self.fetchone("SELECT recorded_at FROM wal_events WHERE seq = ?", (evaluation_seq,))
        if row is not None:
            return str(row["recorded_at"])
        if as_of_recorded_at is not None:
            return as_of_recorded_at
        return utc_now_rfc3339()

    def get_subject_at(self, subject: SubjectKey, evaluation_seq: int | None, evaluation_time: str | None = None) -> sqlite3.Row | None:
        eval_time = evaluation_time or self.get_evaluation_time(evaluation_seq)
        if evaluation_seq is None:
            query = (
                "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND topic = ? "
                "AND ((field IS NULL AND ? IS NULL) OR field = ?) AND memory_type = ? AND valid_to_seq IS NULL "
                "ORDER BY seq DESC LIMIT 1"
            )
            params = (subject.scope, subject.namespace, subject.topic, subject.field, subject.field, subject.memory_type)
            row = self.fetchone(query, params)
            if row is None:
                return None
            if self._row_is_expired(row, eval_time):
                return None
            return row
        query = (
            "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND topic = ? "
            "AND ((field IS NULL AND ? IS NULL) OR field = ?) AND memory_type = ? "
            "AND valid_from_seq <= ? AND (valid_to_seq IS NULL OR ? < valid_to_seq) "
            "ORDER BY seq DESC LIMIT 1"
        )
        params = (
            subject.scope,
            subject.namespace,
            subject.topic,
            subject.field,
            subject.field,
            subject.memory_type,
            evaluation_seq,
            evaluation_seq,
        )
        row = self.fetchone(query, params)
        if row is None:
            return None
        if self._row_is_expired(row, eval_time):
            return None
        return row

    def get_subject_at_seq(self, subject: SubjectKey, seq: int) -> sqlite3.Row | None:
        return self.fetchone(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND topic = ?
              AND ((field IS NULL AND ? IS NULL) OR field = ?)
              AND memory_type = ? AND seq = ?
            LIMIT 1
            """,
            (subject.scope, subject.namespace, subject.topic, subject.field, subject.field, subject.memory_type, seq),
        )

    def get_rows_for_search(
        self,
        scope: str,
        namespace: str,
        evaluation_seq: int | None,
        include_episodes: bool,
        memory_types: list[str] | None,
        evaluation_time: str | None = None,
    ) -> list[sqlite3.Row]:
        eval_time = evaluation_time or self.get_evaluation_time(evaluation_seq)
        rows = self.fetchall(
            "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? ORDER BY seq DESC",
            (scope, namespace),
        )
        filtered: list[sqlite3.Row] = []
        for row in rows:
            if not include_episodes and row["memory_type"] == "episode":
                continue
            if memory_types and row["memory_type"] not in memory_types:
                continue
            if evaluation_seq is None:
                if row["valid_to_seq"] is not None:
                    continue
                if self._row_is_expired(row, eval_time):
                    continue
            else:
                if row["valid_from_seq"] > evaluation_seq:
                    continue
                if row["valid_to_seq"] is not None and evaluation_seq >= row["valid_to_seq"]:
                    continue
                if self._row_is_expired(row, eval_time):
                    continue
            filtered.append(row)
        return filtered

    def get_dependents_for_parent(self, scope: str, namespace: str, topic: str, field: str | None) -> list[sqlite3.Row]:
        candidates = self.fetchall(
            "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND valid_to_seq IS NULL ORDER BY seq ASC",
            (scope, namespace),
        )
        evaluation_time = utc_now_rfc3339()
        target_key = parent_edge_key(topic, field)
        result = []
        for row in candidates:
            if self._row_is_expired(row, evaluation_time):
                continue
            extends = deserialize_json(row["extends_json"]) or []
            keys = {parent_edge_key(item["topic"], item.get("field")) for item in extends}
            if target_key in keys:
                result.append(row)
        return result

    def get_high_water_seq(self, scope: str, namespace: str) -> int:
        row = self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS high_water FROM wal_events WHERE scope = ? AND namespace = ?",
            (scope, namespace),
        )
        return int(row["high_water"] if row else 0)

    def resolve_recorded_at_seq(self, recorded_at: str) -> int:
        row = self.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS seq FROM wal_events WHERE recorded_at <= ?",
            (recorded_at,),
        )
        return int(row["seq"] if row else 0)

    def namespace_records_for_index(self, scope: str, namespace: str) -> list[dict[str, Any]]:
        evaluation_time = utc_now_rfc3339()
        rows = self.fetchall(
            "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND valid_to_seq IS NULL AND memory_type <> 'episode' ORDER BY seq DESC",
            (scope, namespace),
        )
        hidden = self.get_consolidation_hidden(scope, namespace)
        records = []
        for row in rows:
            if self._row_is_expired(row, evaluation_time):
                continue
            key = (row["topic"], row["field"], row["memory_type"])
            if key in hidden:
                continue
            records.append(self.row_to_record(row, status="current"))
        records.extend(self.get_consolidation_summary_records(scope, namespace))
        return records

    def row_to_record(self, row: sqlite3.Row, status: str) -> dict[str, Any]:
        record = {
            "scope": row["scope"],
            "namespace": row["namespace"],
            "topic": row["topic"],
            "field": row["field"],
            "memory_type": row["memory_type"],
            "value": deserialize_json(row["value_json"]),
            "event_id": row["event_id"],
            "seq": row["seq"],
            "valid_from_seq": row["valid_from_seq"],
            "valid_to_seq": row["valid_to_seq"],
            "recorded_at": row["recorded_at"],
            "provenance": deserialize_json(row["provenance_json"]),
            "salience": row["salience"],
            "layer": row["layer"],
            "status": status,
            "extends": deserialize_json(row["extends_json"]),
        }
        expires_at = row["expires_at"] if "expires_at" in row.keys() else None
        blocks_actions_json = row["blocks_actions_json"] if "blocks_actions_json" in row.keys() else None
        if expires_at:
            record["expires_at"] = expires_at
        if blocks_actions_json:
            record["blocks_actions"] = deserialize_json(blocks_actions_json)
        return record

    def row_bindings(self, row: sqlite3.Row) -> dict[str, Any]:
        return deserialize_json(row["bindings_json"]) or {}

    def _row_is_expired(self, row: sqlite3.Row, evaluation_time: str) -> bool:
        expires_at = row["expires_at"] if "expires_at" in row.keys() else None
        return is_expired(expires_at, evaluation_time)

    def count_pending_reviews(self, scope: str, namespace: str) -> int:
        row = self.fetchone(
            "SELECT COUNT(*) AS pending_count FROM review_promotions WHERE scope = ? AND namespace = ? AND status = 'pending'",
            (scope, namespace),
        )
        return int(row["pending_count"] if row else 0)

    def list_pending_reviews(self, scope: str, namespace: str, limit: int) -> list[dict[str, Any]]:
        rows = self.fetchall(
            """
            SELECT * FROM review_promotions
            WHERE scope = ? AND namespace = ? AND status = 'pending'
            ORDER BY created_at ASC, review_id ASC
            LIMIT ?
            """,
            (scope, namespace, limit),
        )
        return [self._promotion_from_row(row) for row in rows]

    def get_pending_review(self, scope: str, namespace: str, review_id: str) -> dict[str, Any] | None:
        row = self.fetchone(
            "SELECT * FROM review_promotions WHERE scope = ? AND namespace = ? AND review_id = ? AND status = 'pending'",
            (scope, namespace, review_id),
        )
        if row is None:
            return None
        return self._promotion_from_row(row)

    def insert_review_promotion(self, cursor: sqlite3.Cursor, scope: str, namespace: str, promotion: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT OR REPLACE INTO review_promotions (
                review_id, scope, namespace, status, proposed_memory_type, topic, field,
                value_json, rationale, source_seqs_json, created_at, resolved_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                promotion["review_id"],
                scope,
                namespace,
                promotion["proposed_memory_type"],
                promotion["topic"],
                promotion.get("field"),
                serialize_json(promotion["value"]),
                promotion["rationale"],
                serialize_json(promotion["source_seqs"]),
                utc_now_rfc3339(),
            ),
        )

    def resolve_review(self, scope: str, namespace: str, review_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE review_promotions
                SET status = ?, resolved_at = ?
                WHERE scope = ? AND namespace = ? AND review_id = ? AND status = 'pending'
                """,
                (status, utc_now_rfc3339(), scope, namespace, review_id),
            )

    def clear_pending_reviews(self, scope: str, namespace: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM review_promotions WHERE scope = ? AND namespace = ? AND status = 'pending'",
                (scope, namespace),
            )

    def get_consolidation_hidden(self, scope: str, namespace: str) -> set[tuple[str, str | None, str]]:
        rows = self.fetchall(
            "SELECT topic, field, memory_type FROM consolidation_hidden WHERE scope = ? AND namespace = ?",
            (scope, namespace),
        )
        return {(str(row["topic"]), row["field"], str(row["memory_type"])) for row in rows}

    def replace_consolidation_state(
        self,
        scope: str,
        namespace: str,
        hidden_units: list[tuple[str, str | None, str]],
        summary_units: list[dict[str, Any]],
        promotions: list[dict[str, Any]],
    ) -> None:
        with self._lock, self._conn:
            cursor = self._conn.cursor()
            cursor.execute("DELETE FROM consolidation_hidden WHERE scope = ? AND namespace = ?", (scope, namespace))
            cursor.execute("DELETE FROM consolidation_summaries WHERE scope = ? AND namespace = ?", (scope, namespace))
            cursor.execute(
                "DELETE FROM review_promotions WHERE scope = ? AND namespace = ? AND status = 'pending'",
                (scope, namespace),
            )
            for topic, field, memory_type in hidden_units:
                cursor.execute(
                    """
                    INSERT INTO consolidation_hidden (scope, namespace, topic, field, memory_type)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (scope, namespace, topic, field, memory_type),
                )
            for summary in summary_units:
                cursor.execute(
                    """
                    INSERT INTO consolidation_summaries (
                        scope, namespace, topic, field, memory_type, value_json, seq, recorded_at,
                        provenance_json, salience
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope,
                        namespace,
                        summary["topic"],
                        summary.get("field"),
                        summary["memory_type"],
                        serialize_json(summary["value"]),
                        summary["seq"],
                        summary["recorded_at"],
                        serialize_json(summary["provenance"]),
                        summary.get("salience"),
                    ),
                )
            for promotion in promotions:
                self.insert_review_promotion(cursor, scope, namespace, promotion)

    def get_consolidation_summary_records(self, scope: str, namespace: str) -> list[dict[str, Any]]:
        rows = self.fetchall(
            "SELECT * FROM consolidation_summaries WHERE scope = ? AND namespace = ? ORDER BY seq DESC",
            (scope, namespace),
        )
        records = []
        for row in rows:
            records.append(
                {
                    "scope": row["scope"],
                    "namespace": row["namespace"],
                    "topic": row["topic"],
                    "field": row["field"],
                    "memory_type": row["memory_type"],
                    "value": deserialize_json(row["value_json"]),
                    "event_id": f"consolidation_{row['id']}",
                    "seq": row["seq"],
                    "valid_from_seq": row["seq"],
                    "valid_to_seq": None,
                    "recorded_at": row["recorded_at"],
                    "provenance": deserialize_json(row["provenance_json"]),
                    "salience": row["salience"],
                    "layer": "semantic_unit",
                    "status": "current",
                    "extends": [],
                }
            )
        return records

    def current_semantic_records(self, scope: str, namespace: str) -> list[dict[str, Any]]:
        evaluation_time = utc_now_rfc3339()
        rows = self.fetchall(
            "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND valid_to_seq IS NULL AND memory_type <> 'episode' ORDER BY seq DESC",
            (scope, namespace),
        )
        records = []
        for row in rows:
            if self._row_is_expired(row, evaluation_time):
                continue
            records.append(self.row_to_record(row, status="current"))
        return records

    def _promotion_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "review_id": row["review_id"],
            "proposed_memory_type": row["proposed_memory_type"],
            "topic": row["topic"],
            "field": row["field"],
            "value": deserialize_json(row["value_json"]),
            "rationale": row["rationale"],
            "source_seqs": deserialize_json(row["source_seqs_json"]) or [],
        }
