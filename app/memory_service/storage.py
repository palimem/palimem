from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import hashlib
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator

from .domain import (
    SubjectKey,
    deserialize_json,
    is_expired,
    parent_edge_key,
    parse_rfc3339_utc,
    record_visible_to_persona,
    serialize_json,
    utc_now_rfc3339,
)


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
                    provenance_json TEXT NOT NULL,
                    legal_hold INTEGER NOT NULL DEFAULT 0
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
                    legal_hold INTEGER NOT NULL DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS profile_engine_namespaces (
                    namespace TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pii_scan_settings (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    policy TEXT NOT NULL,
                    placeholder TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    enabled_memory_types_json TEXT,
                    government_id_patterns_json TEXT,
                    financial_account_patterns_json TEXT,
                    free_text_names_json TEXT,
                    PRIMARY KEY (scope, namespace)
                );

                CREATE TABLE IF NOT EXISTS audit_settings (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    fail_closed INTEGER NOT NULL,
                    PRIMARY KEY (scope, namespace)
                );

                CREATE TABLE IF NOT EXISTS retention_policies (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    PRIMARY KEY (scope, namespace, memory_type)
                );

                CREATE TABLE IF NOT EXISTS fleet_settings (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    backend_reachable INTEGER NOT NULL,
                    last_synced_seq INTEGER,
                    replica_lag_seq INTEGER,
                    serve_reads_from_replica INTEGER NOT NULL DEFAULT 0,
                    max_staleness_seq INTEGER,
                    PRIMARY KEY (scope, namespace)
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    audit_id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    actor_json TEXT NOT NULL,
                    subject_json TEXT,
                    wal_seq INTEGER,
                    wal_event_id TEXT,
                    outcome TEXT NOT NULL,
                    error_code TEXT
                );
                """
            )
            self._migrate_schema()

    def _migrate_schema(self) -> None:
        self._ensure_column("wal_events", "expires_at", "TEXT")
        self._ensure_column("wal_events", "blocks_actions_json", "TEXT")
        self._ensure_column("wal_events", "observation_json", "TEXT")
        self._ensure_column("wal_events", "persona_id", "TEXT")
        self._ensure_column("wal_events", "share_to_json", "TEXT")
        self._ensure_column("wal_events", "derived_from_json", "TEXT")
        self._ensure_column("wal_events", "legal_hold", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("semantic_versions", "expires_at", "TEXT")
        self._ensure_column("semantic_versions", "blocks_actions_json", "TEXT")
        self._ensure_column("semantic_versions", "persona_id", "TEXT")
        self._ensure_column("semantic_versions", "share_to_json", "TEXT")
        self._ensure_column("semantic_versions", "derived_from_json", "TEXT")
        self._ensure_column("semantic_versions", "legal_hold", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("namespace_status", "profile_engine_enabled", "INTEGER DEFAULT 0")
        self._ensure_column("namespace_status", "profile_engine_last_run_at", "TEXT")
        self._ensure_column("namespace_status", "session_summary_last_updated_seq", "INTEGER")

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
            """
            SELECT index_state, last_indexed_at, last_consolidation_at,
                   profile_engine_enabled, profile_engine_last_run_at, session_summary_last_updated_seq
            FROM namespace_status WHERE scope = ? AND namespace = ?
            """,
            (scope, namespace),
        )
        if row is None:
            return {
                "index_state": "current",
                "last_indexed_at": None,
                "last_consolidation_at": None,
                "profile_engine_enabled": False,
                "profile_engine_last_run_at": None,
                "session_summary_last_updated_seq": None,
            }
        return {
            "index_state": row["index_state"],
            "last_indexed_at": row["last_indexed_at"],
            "last_consolidation_at": row["last_consolidation_at"],
            "profile_engine_enabled": bool(row["profile_engine_enabled"]) if row["profile_engine_enabled"] is not None else False,
            "profile_engine_last_run_at": row["profile_engine_last_run_at"],
            "session_summary_last_updated_seq": row["session_summary_last_updated_seq"],
        }

    def set_profile_engine_enabled(self, scope: str, namespace: str, enabled: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO namespace_status (
                    scope, namespace, index_state, last_indexed_at, last_consolidation_at,
                    profile_engine_enabled, profile_engine_last_run_at, session_summary_last_updated_seq
                )
                VALUES (?, ?, 'current', NULL, NULL, ?, NULL, NULL)
                ON CONFLICT(scope, namespace) DO UPDATE SET profile_engine_enabled = excluded.profile_engine_enabled
                """,
                (scope, namespace, 1 if enabled else 0),
            )

    def set_profile_engine_enabled_namespace(self, namespace: str, enabled: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO profile_engine_namespaces (namespace, enabled)
                VALUES (?, ?)
                ON CONFLICT(namespace) DO UPDATE SET enabled = excluded.enabled
                """,
                (namespace, 1 if enabled else 0),
            )

    def get_profile_engine_enabled_namespaces(self) -> set[str]:
        rows = self.fetchall("SELECT namespace FROM profile_engine_namespaces WHERE enabled = 1")
        return {str(row["namespace"]) for row in rows}

    def set_profile_engine_last_run(self, scope: str, namespace: str, timestamp: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO namespace_status (
                    scope, namespace, index_state, last_indexed_at, last_consolidation_at,
                    profile_engine_enabled, profile_engine_last_run_at, session_summary_last_updated_seq
                )
                VALUES (?, ?, 'current', NULL, NULL, 0, ?, NULL)
                ON CONFLICT(scope, namespace) DO UPDATE SET profile_engine_last_run_at = excluded.profile_engine_last_run_at
                """,
                (scope, namespace, timestamp),
            )

    def set_session_summary_seq(self, scope: str, namespace: str, seq: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO namespace_status (
                    scope, namespace, index_state, last_indexed_at, last_consolidation_at,
                    profile_engine_enabled, profile_engine_last_run_at, session_summary_last_updated_seq
                )
                VALUES (?, ?, 'current', NULL, NULL, 0, NULL, ?)
                ON CONFLICT(scope, namespace) DO UPDATE SET session_summary_last_updated_seq = excluded.session_summary_last_updated_seq
                """,
                (scope, namespace, seq),
            )

    def all_namespaces(self) -> list[tuple[str, str]]:
        rows = self.fetchall("SELECT DISTINCT scope, namespace FROM wal_events ORDER BY scope, namespace")
        return [(str(row["scope"]), str(row["namespace"])) for row in rows]

    def insert_wal_event(self, cursor: sqlite3.Cursor, event: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO wal_events (
                event_id, seq, recorded_at, scope, namespace, kind, memory_type, topic, field,
                value_json, episode_id, extends_json, provenance_json, expires_at,
                blocks_actions_json, observation_json, persona_id, share_to_json, derived_from_json, legal_hold
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                event.get("persona_id"),
                event.get("share_to_json"),
                event.get("derived_from_json"),
                1 if event.get("legal_hold") else 0,
            ),
        )

    def close_open_versions(self, cursor: sqlite3.Cursor, subject: SubjectKey, closing_seq: int) -> None:
        cursor.execute(
            """
            UPDATE semantic_versions
            SET valid_to_seq = ?
            WHERE scope = ? AND namespace = ? AND topic = ?
              AND ((field IS NULL AND ? IS NULL) OR field = ?)
              AND memory_type = ?
              AND ((persona_id IS NULL AND ? IS NULL) OR persona_id = ?)
              AND valid_to_seq IS NULL
            """,
            (
                closing_seq,
                subject.scope,
                subject.namespace,
                subject.topic,
                subject.field,
                subject.field,
                subject.memory_type,
                subject.persona_id,
                subject.persona_id,
            ),
        )

    def insert_semantic_version(self, cursor: sqlite3.Cursor, version: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO semantic_versions (
                scope, namespace, topic, field, memory_type, value_json, seq, valid_from_seq,
                valid_to_seq, recorded_at, episode_id, event_id, provenance_json, salience,
                extends_json, bindings_json, layer, expires_at, blocks_actions_json,
                persona_id, share_to_json, derived_from_json, legal_hold
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                version.get("persona_id"),
                version.get("share_to_json"),
                version.get("derived_from_json"),
                1 if version.get("legal_hold") else 0,
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
              AND ((persona_id IS NULL AND ? IS NULL) OR persona_id = ?)
            ORDER BY seq DESC
            """,
            (
                subject.scope,
                subject.namespace,
                subject.topic,
                subject.field,
                subject.field,
                subject.memory_type,
                subject.persona_id,
                subject.persona_id,
            ),
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

    def get_open_subject(self, subject: SubjectKey) -> sqlite3.Row | None:
        rows = self.fetchall(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND topic = ?
              AND ((field IS NULL AND ? IS NULL) OR field = ?)
              AND memory_type = ?
              AND ((persona_id IS NULL AND ? IS NULL) OR persona_id = ?)
              AND valid_to_seq IS NULL
            ORDER BY seq DESC LIMIT 1
            """,
            (
                subject.scope,
                subject.namespace,
                subject.topic,
                subject.field,
                subject.field,
                subject.memory_type,
                subject.persona_id,
                subject.persona_id,
            ),
        )
        if not rows:
            return None
        row = rows[0]
        eval_time = utc_now_rfc3339()
        if self._row_is_expired(row, eval_time):
            return None
        return row

    def get_subject_at(
        self,
        subject: SubjectKey,
        evaluation_seq: int | None,
        evaluation_time: str | None = None,
        read_persona_id: str | None = None,
    ) -> sqlite3.Row | None:
        eval_time = evaluation_time or self.get_evaluation_time(evaluation_seq)
        if evaluation_seq is None:
            query = (
                "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND topic = ? "
                "AND ((field IS NULL AND ? IS NULL) OR field = ?) AND memory_type = ? AND valid_to_seq IS NULL "
                "ORDER BY seq DESC"
            )
            params: tuple[Any, ...] = (
                subject.scope,
                subject.namespace,
                subject.topic,
                subject.field,
                subject.field,
                subject.memory_type,
            )
            rows = self.fetchall(query, params)
            for row in rows:
                if subject.persona_id is not None:
                    stored = row["persona_id"] if "persona_id" in row.keys() else None
                    if stored != subject.persona_id:
                        continue
                if self._row_is_expired(row, eval_time):
                    continue
                share_to = self._row_share_to(row)
                stored_persona = row["persona_id"] if "persona_id" in row.keys() else None
                if not record_visible_to_persona(stored_persona, share_to, read_persona_id):
                    continue
                return row
            return None
        query = (
            "SELECT * FROM semantic_versions WHERE scope = ? AND namespace = ? AND topic = ? "
            "AND ((field IS NULL AND ? IS NULL) OR field = ?) AND memory_type = ? "
            "AND valid_from_seq <= ? AND (valid_to_seq IS NULL OR ? < valid_to_seq) "
            "ORDER BY seq DESC"
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
        rows = self.fetchall(query, params)
        for row in rows:
            if subject.persona_id is not None:
                stored = row["persona_id"] if "persona_id" in row.keys() else None
                if stored != subject.persona_id:
                    continue
            if self._row_is_expired(row, eval_time):
                continue
            share_to = self._row_share_to(row)
            stored_persona = row["persona_id"] if "persona_id" in row.keys() else None
            if not record_visible_to_persona(stored_persona, share_to, read_persona_id):
                continue
            return row
        return None

    def get_subject_at_seq(self, subject: SubjectKey, seq: int) -> sqlite3.Row | None:
        return self.fetchone(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND topic = ?
              AND ((field IS NULL AND ? IS NULL) OR field = ?)
              AND memory_type = ?
              AND ((persona_id IS NULL AND ? IS NULL) OR persona_id = ?)
              AND seq = ?
            LIMIT 1
            """,
            (
                subject.scope,
                subject.namespace,
                subject.topic,
                subject.field,
                subject.field,
                subject.memory_type,
                subject.persona_id,
                subject.persona_id,
                seq,
            ),
        )

    def get_rows_for_search(
        self,
        scope: str,
        namespace: str,
        evaluation_seq: int | None,
        include_episodes: bool,
        memory_types: list[str] | None,
        evaluation_time: str | None = None,
        read_persona_id: str | None = None,
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
            share_to = self._row_share_to(row)
            stored_persona = row["persona_id"] if "persona_id" in row.keys() else None
            if not record_visible_to_persona(stored_persona, share_to, read_persona_id):
                continue
            filtered.append(row)
        return filtered

    def list_episodes(
        self,
        scope: str,
        namespace: str,
        *,
        limit: int = 50,
        persona_id: str | None = None,
    ) -> list[sqlite3.Row]:
        rows = self.fetchall(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND memory_type = 'episode' AND valid_to_seq IS NULL
            ORDER BY seq DESC LIMIT ?
            """,
            (scope, namespace, limit),
        )
        result: list[sqlite3.Row] = []
        for row in rows:
            share_to = self._row_share_to(row)
            stored_persona = row["persona_id"] if "persona_id" in row.keys() else None
            if not record_visible_to_persona(stored_persona, share_to, persona_id):
                continue
            result.append(row)
        return result

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

    def get_wal_event_rows(
        self,
        scope: str,
        namespace: str,
        *,
        since_seq: int | None = None,
        until_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT event_id, seq, recorded_at, scope, namespace, kind, memory_type, topic, field, "
            "value_json, episode_id, extends_json, provenance_json, expires_at, blocks_actions_json, "
            "observation_json, persona_id, share_to_json, derived_from_json, legal_hold "
            "FROM wal_events WHERE scope = ? AND namespace = ?"
        )
        params: list[Any] = [scope, namespace]
        if since_seq is not None:
            query += " AND seq >= ?"
            params.append(since_seq)
        if until_seq is not None:
            query += " AND seq < ?"
            params.append(until_seq)
        query += " ORDER BY seq ASC"
        rows = self.fetchall(query, tuple(params))
        return [
            {
                "event_id": row["event_id"],
                "seq": row["seq"],
                "recorded_at": row["recorded_at"],
                "scope": row["scope"],
                "namespace": row["namespace"],
                "kind": row["kind"],
                "memory_type": row["memory_type"],
                "topic": row["topic"],
                "field": row["field"],
                "value_json": row["value_json"],
                "episode_id": row["episode_id"],
                "extends_json": row["extends_json"],
                "provenance_json": row["provenance_json"],
                "expires_at": row["expires_at"],
                "blocks_actions_json": row["blocks_actions_json"],
                "observation_json": row["observation_json"],
                "persona_id": row["persona_id"],
                "share_to_json": row["share_to_json"],
                "derived_from_json": row["derived_from_json"],
                "legal_hold": bool(row["legal_hold"]),
            }
            for row in rows
        ]

    def import_wal_event_rows(self, rows: list[dict[str, Any]]) -> int:
        inserted = 0
        with self.transaction() as cursor:
            for row in rows:
                result = cursor.execute(
                    """
                    INSERT OR IGNORE INTO wal_events (
                        event_id, seq, recorded_at, scope, namespace, kind, memory_type, topic, field,
                        value_json, episode_id, extends_json, provenance_json, expires_at,
                        blocks_actions_json, observation_json, persona_id, share_to_json, derived_from_json, legal_hold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["event_id"],
                        row["seq"],
                        row["recorded_at"],
                        row["scope"],
                        row["namespace"],
                        row["kind"],
                        row["memory_type"],
                        row["topic"],
                        row["field"],
                        row["value_json"],
                        row["episode_id"],
                        row["extends_json"],
                        row["provenance_json"],
                        row.get("expires_at"),
                        row.get("blocks_actions_json"),
                        row.get("observation_json"),
                        row.get("persona_id"),
                        row.get("share_to_json"),
                        row.get("derived_from_json"),
                        1 if row.get("legal_hold") else 0,
                    ),
                )
                inserted += int(result.rowcount or 0)
        return inserted

    def get_semantic_version_rows(self, scope: str, namespace: str) -> list[dict[str, Any]]:
        rows = self.fetchall(
            """
            SELECT scope, namespace, topic, field, memory_type, value_json, seq, valid_from_seq,
                   valid_to_seq, recorded_at, episode_id, event_id, provenance_json, salience,
                   extends_json, bindings_json, layer, expires_at, blocks_actions_json,
                   persona_id, share_to_json, derived_from_json, legal_hold
            FROM semantic_versions
            WHERE scope = ? AND namespace = ?
            ORDER BY seq ASC, id ASC
            """,
            (scope, namespace),
        )
        return [
            {
                "scope": row["scope"],
                "namespace": row["namespace"],
                "topic": row["topic"],
                "field": row["field"],
                "memory_type": row["memory_type"],
                "value_json": row["value_json"],
                "seq": row["seq"],
                "valid_from_seq": row["valid_from_seq"],
                "valid_to_seq": row["valid_to_seq"],
                "recorded_at": row["recorded_at"],
                "episode_id": row["episode_id"],
                "event_id": row["event_id"],
                "provenance_json": row["provenance_json"],
                "salience": row["salience"],
                "extends_json": row["extends_json"],
                "bindings_json": row["bindings_json"],
                "layer": row["layer"],
                "expires_at": row["expires_at"],
                "blocks_actions_json": row["blocks_actions_json"],
                "persona_id": row["persona_id"],
                "share_to_json": row["share_to_json"],
                "derived_from_json": row["derived_from_json"],
                "legal_hold": bool(row["legal_hold"]),
            }
            for row in rows
        ]

    def replace_semantic_version_rows(self, scope: str, namespace: str, rows: list[dict[str, Any]]) -> None:
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM semantic_versions WHERE scope = ? AND namespace = ?", (scope, namespace))
            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO semantic_versions (
                        scope, namespace, topic, field, memory_type, value_json, seq, valid_from_seq,
                        valid_to_seq, recorded_at, episode_id, event_id, provenance_json, salience,
                        extends_json, bindings_json, layer, expires_at, blocks_actions_json,
                        persona_id, share_to_json, derived_from_json, legal_hold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["scope"],
                        row["namespace"],
                        row["topic"],
                        row["field"],
                        row["memory_type"],
                        row["value_json"],
                        row["seq"],
                        row["valid_from_seq"],
                        row.get("valid_to_seq"),
                        row["recorded_at"],
                        row.get("episode_id"),
                        row["event_id"],
                        row["provenance_json"],
                        row.get("salience"),
                        row["extends_json"],
                        row["bindings_json"],
                        row["layer"],
                        row.get("expires_at"),
                        row.get("blocks_actions_json"),
                        row.get("persona_id"),
                        row.get("share_to_json"),
                        row.get("derived_from_json"),
                        1 if row.get("legal_hold") else 0,
                    ),
                )

    def get_namespace_status_snapshot(self, scope: str, namespace: str) -> dict[str, Any] | None:
        row = self.fetchone(
            """
            SELECT scope, namespace, index_state, last_indexed_at, last_consolidation_at,
                   profile_engine_enabled, profile_engine_last_run_at, session_summary_last_updated_seq
            FROM namespace_status
            WHERE scope = ? AND namespace = ?
            """,
            (scope, namespace),
        )
        if row is None:
            return None
        return {
            "scope": row["scope"],
            "namespace": row["namespace"],
            "index_state": row["index_state"],
            "last_indexed_at": row["last_indexed_at"],
            "last_consolidation_at": row["last_consolidation_at"],
            "profile_engine_enabled": bool(row["profile_engine_enabled"]) if row["profile_engine_enabled"] is not None else False,
            "profile_engine_last_run_at": row["profile_engine_last_run_at"],
            "session_summary_last_updated_seq": row["session_summary_last_updated_seq"],
        }

    def replace_namespace_status(self, snapshot: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO namespace_status (
                    scope, namespace, index_state, last_indexed_at, last_consolidation_at,
                    profile_engine_enabled, profile_engine_last_run_at, session_summary_last_updated_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, namespace) DO UPDATE SET
                    index_state = excluded.index_state,
                    last_indexed_at = excluded.last_indexed_at,
                    last_consolidation_at = excluded.last_consolidation_at,
                    profile_engine_enabled = excluded.profile_engine_enabled,
                    profile_engine_last_run_at = excluded.profile_engine_last_run_at,
                    session_summary_last_updated_seq = excluded.session_summary_last_updated_seq
                """,
                (
                    snapshot["scope"],
                    snapshot["namespace"],
                    snapshot["index_state"],
                    snapshot.get("last_indexed_at"),
                    snapshot.get("last_consolidation_at"),
                    1 if snapshot.get("profile_engine_enabled") else 0,
                    snapshot.get("profile_engine_last_run_at"),
                    snapshot.get("session_summary_last_updated_seq"),
                ),
            )

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
        persona_id = row["persona_id"] if "persona_id" in row.keys() else None
        derived_from_json = row["derived_from_json"] if "derived_from_json" in row.keys() else None
        legal_hold = bool(row["legal_hold"]) if "legal_hold" in row.keys() else False
        if expires_at:
            record["expires_at"] = expires_at
        if blocks_actions_json:
            record["blocks_actions"] = deserialize_json(blocks_actions_json)
        if persona_id:
            record["persona_id"] = persona_id
        share_to = self._row_share_to(row)
        if share_to:
            record["share_to"] = share_to
        derived_from = deserialize_json(derived_from_json) if derived_from_json else None
        if derived_from:
            record["derived_from"] = derived_from
        if legal_hold:
            record["legal_hold"] = True
        return record

    def _row_share_to(self, row: sqlite3.Row) -> list[str] | None:
        share_to_json = row["share_to_json"] if "share_to_json" in row.keys() else None
        if not share_to_json:
            return None
        return deserialize_json(share_to_json)

    def row_bindings(self, row: sqlite3.Row) -> dict[str, Any]:
        return deserialize_json(row["bindings_json"]) or {}

    def _row_is_expired(self, row: sqlite3.Row, evaluation_time: str) -> bool:
        if "legal_hold" in row.keys() and bool(row["legal_hold"]):
            return False
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

    def get_topic_partition_rows(
        self,
        scope: str,
        namespace: str,
        topic: str,
        field: str | None = None,
        memory_types: list[str] | None = None,
        persona_id: str | None = None,
    ) -> list[sqlite3.Row]:
        rows = self.fetchall(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND topic = ?
            ORDER BY seq ASC, memory_type ASC
            """,
            (scope, namespace, topic),
        )
        filtered: list[sqlite3.Row] = []
        for row in rows:
            if field is not None and row["field"] != field:
                continue
            if memory_types and row["memory_type"] not in memory_types:
                continue
            stored_persona = row["persona_id"] if "persona_id" in row.keys() else None
            if persona_id is not None and stored_persona != persona_id:
                continue
            filtered.append(row)
        return filtered

    def get_latest_subject_event(
        self,
        subject: SubjectKey,
        up_to_seq: int | None = None,
    ) -> sqlite3.Row | None:
        query = (
            "SELECT * FROM wal_events WHERE scope = ? AND namespace = ? AND topic = ? "
            "AND ((field IS NULL AND ? IS NULL) OR field = ?) AND memory_type = ? "
            "AND ((persona_id IS NULL AND ? IS NULL) OR persona_id = ?)"
        )
        params: list[Any] = [
            subject.scope,
            subject.namespace,
            subject.topic,
            subject.field,
            subject.field,
            subject.memory_type,
            subject.persona_id,
            subject.persona_id,
        ]
        if up_to_seq is not None:
            query += " AND seq <= ?"
            params.append(up_to_seq)
        query += " ORDER BY seq DESC LIMIT 1"
        return self.fetchone(query, tuple(params))

    def set_pii_scan_config(self, scope: str, namespace: str, config: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO pii_scan_settings (
                    scope, namespace, enabled, policy, placeholder, categories_json,
                    enabled_memory_types_json, government_id_patterns_json,
                    financial_account_patterns_json, free_text_names_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, namespace) DO UPDATE SET
                    enabled = excluded.enabled,
                    policy = excluded.policy,
                    placeholder = excluded.placeholder,
                    categories_json = excluded.categories_json,
                    enabled_memory_types_json = excluded.enabled_memory_types_json,
                    government_id_patterns_json = excluded.government_id_patterns_json,
                    financial_account_patterns_json = excluded.financial_account_patterns_json,
                    free_text_names_json = excluded.free_text_names_json
                """,
                (
                    scope,
                    namespace,
                    1 if config.get("enabled") else 0,
                    config.get("policy") or "block",
                    config.get("placeholder") or "[REDACTED_PII]",
                    serialize_json(config.get("categories") or []),
                    serialize_json(config.get("enabled_memory_types") or []),
                    serialize_json(config.get("government_id_patterns") or []),
                    serialize_json(config.get("financial_account_patterns") or []),
                    serialize_json(config.get("free_text_names") or []),
                ),
            )

    def get_pii_scan_config(self, scope: str, namespace: str) -> dict[str, Any]:
        row = self.fetchone("SELECT * FROM pii_scan_settings WHERE scope = ? AND namespace = ?", (scope, namespace))
        if row is None:
            return {
                "enabled": False,
                "policy": "block",
                "placeholder": "[REDACTED_PII]",
                "categories": [],
                "enabled_memory_types": [],
                "government_id_patterns": [],
                "financial_account_patterns": [],
                "free_text_names": [],
            }
        return {
            "enabled": bool(row["enabled"]),
            "policy": row["policy"],
            "placeholder": row["placeholder"],
            "categories": deserialize_json(row["categories_json"]) or [],
            "enabled_memory_types": deserialize_json(row["enabled_memory_types_json"]) or [],
            "government_id_patterns": deserialize_json(row["government_id_patterns_json"]) or [],
            "financial_account_patterns": deserialize_json(row["financial_account_patterns_json"]) or [],
            "free_text_names": deserialize_json(row["free_text_names_json"]) or [],
        }

    def set_audit_config(self, scope: str, namespace: str, enabled: bool, fail_closed: bool = False) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO audit_settings (scope, namespace, enabled, fail_closed)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, namespace) DO UPDATE SET
                    enabled = excluded.enabled,
                    fail_closed = excluded.fail_closed
                """,
                (scope, namespace, 1 if enabled else 0, 1 if fail_closed else 0),
            )

    def get_audit_config(self, scope: str, namespace: str) -> dict[str, Any]:
        row = self.fetchone("SELECT * FROM audit_settings WHERE scope = ? AND namespace = ?", (scope, namespace))
        if row is None:
            return {"enabled": False, "fail_closed": False}
        return {"enabled": bool(row["enabled"]), "fail_closed": bool(row["fail_closed"])}

    def set_retention_policy(self, scope: str, namespace: str, memory_type: str, ttl_seconds: int | None) -> None:
        with self._lock, self._conn:
            if ttl_seconds is None:
                self._conn.execute(
                    "DELETE FROM retention_policies WHERE scope = ? AND namespace = ? AND memory_type = ?",
                    (scope, namespace, memory_type),
                )
                return
            self._conn.execute(
                """
                INSERT INTO retention_policies (scope, namespace, memory_type, ttl_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, namespace, memory_type) DO UPDATE SET ttl_seconds = excluded.ttl_seconds
                """,
                (scope, namespace, memory_type, ttl_seconds),
            )

    def get_retention_policies(self, scope: str, namespace: str) -> list[dict[str, Any]]:
        rows = self.fetchall(
            """
            SELECT memory_type, ttl_seconds FROM retention_policies
            WHERE scope = ? AND namespace = ?
            ORDER BY memory_type ASC
            """,
            (scope, namespace),
        )
        return [{"memory_type": str(row["memory_type"]), "ttl_seconds": int(row["ttl_seconds"])} for row in rows]

    def get_rows_for_retention(self, scope: str, namespace: str) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT * FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND valid_to_seq IS NULL
            ORDER BY seq ASC
            """,
            (scope, namespace),
        )

    def count_legal_holds(self, scope: str, namespace: str) -> int:
        row = self.fetchone(
            """
            SELECT COUNT(*) AS hold_count
            FROM semantic_versions
            WHERE scope = ? AND namespace = ? AND valid_to_seq IS NULL AND legal_hold = 1
            """,
            (scope, namespace),
        )
        return int(row["hold_count"] if row else 0)

    def set_fleet_status(self, scope: str, namespace: str, payload: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO fleet_settings (
                    scope, namespace, mode, backend_reachable, last_synced_seq, replica_lag_seq,
                    serve_reads_from_replica, max_staleness_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, namespace) DO UPDATE SET
                    mode = excluded.mode,
                    backend_reachable = excluded.backend_reachable,
                    last_synced_seq = excluded.last_synced_seq,
                    replica_lag_seq = excluded.replica_lag_seq,
                    serve_reads_from_replica = excluded.serve_reads_from_replica,
                    max_staleness_seq = excluded.max_staleness_seq
                """,
                (
                    scope,
                    namespace,
                    payload.get("mode") or "local",
                    1 if payload.get("backend_reachable") else 0,
                    payload.get("last_synced_seq"),
                    payload.get("replica_lag_seq"),
                    1 if payload.get("serve_reads_from_replica") else 0,
                    payload.get("max_staleness_seq"),
                ),
            )

    def get_fleet_status(self, scope: str, namespace: str) -> dict[str, Any]:
        row = self.fetchone("SELECT * FROM fleet_settings WHERE scope = ? AND namespace = ?", (scope, namespace))
        if row is None:
            return {
                "mode": "local",
                "backend_reachable": False,
                "last_synced_seq": None,
                "replica_lag_seq": None,
                "serve_reads_from_replica": False,
                "max_staleness_seq": None,
            }
        return {
            "mode": row["mode"],
            "backend_reachable": bool(row["backend_reachable"]),
            "last_synced_seq": row["last_synced_seq"],
            "replica_lag_seq": row["replica_lag_seq"],
            "serve_reads_from_replica": bool(row["serve_reads_from_replica"]),
            "max_staleness_seq": row["max_staleness_seq"],
        }

    def next_audit_id(self, cursor: sqlite3.Cursor, scope: str, namespace: str) -> str:
        row = cursor.execute(
            """
            SELECT COUNT(*) + 1 AS next_id
            FROM audit_events
            WHERE scope = ? AND namespace = ?
            """,
            (scope, namespace),
        ).fetchone()
        namespace_key = hashlib.sha1(f"{scope}:{namespace}".encode("utf-8")).hexdigest()[:8]
        return f"aud_{namespace_key}_{int(row['next_id']):06d}"

    def insert_audit_event(self, cursor: sqlite3.Cursor, event: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO audit_events (
                audit_id, recorded_at, event_kind, scope, namespace, tool, actor_json,
                subject_json, wal_seq, wal_event_id, outcome, error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["audit_id"],
                event["recorded_at"],
                event["event_kind"],
                event["scope"],
                event["namespace"],
                event["tool"],
                serialize_json(event["actor"]),
                serialize_json(event["subject"]) if event.get("subject") is not None else None,
                event.get("wal_seq"),
                event.get("wal_event_id"),
                event["outcome"],
                event.get("error_code"),
            ),
        )

    def list_audit_events(
        self,
        scope: str,
        namespace: str,
        *,
        since_seq: int | None = None,
        until_seq: int | None = None,
        since_recorded_at: str | None = None,
        until_recorded_at: str | None = None,
        event_kinds: list[str] | None = None,
        limit: int = 1000,
    ) -> tuple[list[dict[str, Any]], bool]:
        rows = self.fetchall(
            """
            SELECT * FROM audit_events
            WHERE scope = ? AND namespace = ?
            ORDER BY recorded_at ASC, audit_id ASC
            """,
            (scope, namespace),
        )
        matched_records: list[dict[str, Any]] = []
        for row in rows:
            record = {
                "audit_id": row["audit_id"],
                "recorded_at": row["recorded_at"],
                "event_kind": row["event_kind"],
                "scope": row["scope"],
                "namespace": row["namespace"],
                "tool": row["tool"],
                "actor": deserialize_json(row["actor_json"]),
                "outcome": row["outcome"],
                "error_code": row["error_code"],
            }
            subject = deserialize_json(row["subject_json"]) if row["subject_json"] else None
            if subject is not None:
                record["subject"] = subject
            if row["wal_seq"] is not None:
                record["wal_seq"] = row["wal_seq"]
            if row["wal_event_id"] is not None:
                record["wal_event_id"] = row["wal_event_id"]
            if (since_seq is not None or until_seq is not None) and row["wal_seq"] is None:
                continue
            if since_seq is not None and row["wal_seq"] is not None and int(row["wal_seq"]) < since_seq:
                continue
            if until_seq is not None and row["wal_seq"] is not None and int(row["wal_seq"]) >= until_seq:
                continue
            if since_recorded_at is not None and str(row["recorded_at"]) < since_recorded_at:
                continue
            if until_recorded_at is not None and str(row["recorded_at"]) >= until_recorded_at:
                continue
            if event_kinds and row["event_kind"] not in event_kinds:
                continue
            matched_records.append(record)
        truncated = len(matched_records) > limit
        records = matched_records[:limit]
        return records, truncated

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
