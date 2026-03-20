from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

from infostream.core.logging import get_logger
from infostream.models.schemas import DigestRecord, ItemRecord, RunRecord, RunSourceResult


class Database:
    def __init__(self, path: Path) -> None:
        self._logger = get_logger("db.database")
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        self._logger.info(
            "db_initialized", message="database connection initialized", path=str(path)
        )

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()
        self._logger.info("db_closed", message="database connection closed")

    def init_schema(self) -> None:
        # Create tables and indexes
        # items, runs, run_source_results, run_items, digests, source_state
        # Triple quotes might have to be changed? Test and see
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    item_ref TEXT PRIMARY KEY,
                    source_uid TEXT NOT NULL,
                    external_id TEXT,
                    url TEXT,
                    title TEXT,
                    created_at TEXT,
                    ingested_at TEXT NOT NULL,
                    content_text TEXT,
                    content_hash TEXT NOT NULL,
                    media_refs_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_external
                ON items(source_uid, external_id)
                WHERE external_id IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_url
                ON items(source_uid, url)
                WHERE url IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_content_hash
                ON items(source_uid, content_hash)
                WHERE content_hash IS NOT NULL;

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    digest_id TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    raw_llm_output TEXT
                );

                CREATE TABLE IF NOT EXISTS run_source_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source_uid TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    items_ingested_count INTEGER NOT NULL,
                    stderr_tail TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_items (
                    run_id TEXT NOT NULL,
                    item_ref TEXT NOT NULL,
                    PRIMARY KEY(run_id, item_ref),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
                    FOREIGN KEY(item_ref) REFERENCES items(item_ref) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS digests (
                    digest_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS source_state (
                    source_uid TEXT PRIMARY KEY,
                    next_cursor TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
        self._logger.info("db_schema_initialized", message="database schema initialized")

    def _row_to_item(self, row: sqlite3.Row) -> ItemRecord:
        # Return the item in the DB at the given row
        return ItemRecord(
            item_ref=row["item_ref"],
            source_uid=row["source_uid"],
            external_id=row["external_id"],
            url=row["url"],
            title=row["title"],
            created_at=row["created_at"],
            ingested_at=row["ingested_at"],
            content_text=row["content_text"],
            content_hash=row["content_hash"],
            media_refs=json.loads(row["media_refs_json"]),
            tags=json.loads(row["tags_json"]),
        )

    def _lookup_duplicate(self, item: ItemRecord) -> sqlite3.Row | None:
        # Determine whether item already exists in the database
        # based on its ItemRecord. Test in order: external_id, url, content_hash
        cur = self._conn.cursor()
        if item.external_id:
            row = cur.execute(
                "SELECT * FROM items WHERE source_uid = ? AND external_id = ?",
                (item.source_uid, item.external_id),
            ).fetchone()
            if row is not None:
                return cast(sqlite3.Row, row)

        if item.url:
            row = cur.execute(
                "SELECT * FROM items WHERE source_uid = ? AND url = ?",
                (item.source_uid, item.url),
            ).fetchone()
            if row is not None:
                return cast(sqlite3.Row, row)

        row = cur.execute(
            "SELECT * FROM items WHERE source_uid = ? AND content_hash = ?",
            (item.source_uid, item.content_hash),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def upsert_item(self, item: ItemRecord, seen_at: str) -> tuple[bool, ItemRecord]:
        # Insert new item if new, or update last_seen_at if duplicate
        with self._lock, self._conn:
            existing = self._lookup_duplicate(item)
            if existing is not None:  # If duplicate, update metadata
                self._conn.execute(
                    """
                    UPDATE items
                    SET last_seen_at = ?,
                        title = COALESCE(title, ?),
                        url = COALESCE(url, ?),
                        created_at = COALESCE(created_at, ?)
                    WHERE item_ref = ?
                    """,
                    (
                        seen_at,
                        item.title,
                        item.url,
                        item.created_at,
                        existing["item_ref"],
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM items WHERE item_ref = ?", (existing["item_ref"],)
                ).fetchone()
                assert row is not None
                return False, self._row_to_item(row)

            self._conn.execute(  # If not in DB then insert the item into the DB anew
                """
                INSERT INTO items (
                    item_ref, source_uid, external_id, url, title, created_at,
                    ingested_at, content_text, content_hash,
                    media_refs_json, tags_json, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_ref,
                    item.source_uid,
                    item.external_id,
                    item.url,
                    item.title,
                    item.created_at,
                    item.ingested_at,
                    item.content_text,
                    item.content_hash,
                    json.dumps(item.media_refs),
                    json.dumps(item.tags),
                    seen_at,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM items WHERE item_ref = ?", (item.item_ref,)
            ).fetchone()
            assert row is not None
            return True, self._row_to_item(row)

    # Link item to run
    def add_run_item(self, run_id: str, item_ref: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO run_items (run_id, item_ref) VALUES (?, ?)",
                (run_id, item_ref),
            )

    # Get the items that were produced during a given run
    def get_items_for_run(self, run_id: str) -> list[ItemRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT i.* FROM items i
                INNER JOIN run_items ri ON ri.item_ref = i.item_ref
                WHERE ri.run_id = ?
                ORDER BY i.ingested_at DESC
                """,
                (run_id,),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    # Create a record of a run
    def create_run(self, run: RunRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO runs (
                    run_id, trigger, status, started_at, finished_at,
                    digest_id, error_code, error_message, raw_llm_output
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.trigger,
                    run.status,
                    run.started_at,
                    run.finished_at,
                    run.digest_id,
                    run.error_code,
                    run.error_message,
                    run.raw_llm_output,
                ),
            )
        self._logger.debug(
            "db_run_created",
            message="created run record",
            run_id=run.run_id,
            trigger=run.trigger,
            status=run.status,
        )

    # Update run status and fields if a run is already in the DB
    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
        digest_id: str | None,
        error_code: str | None,
        error_message: str | None,
        raw_llm_output: str | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, digest_id = ?, error_code = ?,
                    error_message = ?, raw_llm_output = ?
                WHERE run_id = ?
                """,
                (status, finished_at, digest_id, error_code, error_message, raw_llm_output, run_id),
            )
        self._logger.debug(
            "db_run_updated",
            message="updated run record",
            run_id=run_id,
            status=status,
            error_code=error_code,
            digest_id=digest_id,
        )

    # Get the run which corresponds to a given run_id
    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            trigger=row["trigger"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            digest_id=row["digest_id"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            raw_llm_output=row["raw_llm_output"],
        )

    # Fetch the latest run
    def get_latest_run(self) -> RunRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            trigger=row["trigger"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            digest_id=row["digest_id"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            raw_llm_output=row["raw_llm_output"],
        )

    # Store the post-run result FROM A GIVEN SOURCE into the DB
    def add_run_source_result(self, run_id: str, result: RunSourceResult) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO run_source_results (
                    run_id, source_uid, status, duration_ms, items_ingested_count, stderr_tail
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.source_uid,
                    result.status,
                    result.duration_ms,
                    result.items_ingested_count,
                    result.stderr_tail,
                ),
            )
        self._logger.debug(
            "db_run_source_result_added",
            message="added run source result",
            run_id=run_id,
            source_uid=result.source_uid,
            status=result.status,
            duration_ms=result.duration_ms,
            items_ingested_count=result.items_ingested_count,
        )

    # Fetch the results from a given run and source
    def list_run_source_results(self, run_id: str) -> list[RunSourceResult]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT source_uid, status, duration_ms, items_ingested_count, stderr_tail
                FROM run_source_results
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            RunSourceResult(
                source_uid=row["source_uid"],
                status=row["status"],
                duration_ms=row["duration_ms"],
                items_ingested_count=row["items_ingested_count"],
                stderr_tail=row["stderr_tail"],
            )
            for row in rows
        ]

    # Store the output JSON from a digest into DB
    def create_digest(self, digest: DigestRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO digests (digest_id, run_id, version, created_at, output_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    digest.digest_id,
                    digest.run_id,
                    digest.version,
                    digest.created_at,
                    json.dumps(digest.output_json),
                ),
            )
        self._logger.debug(
            "db_digest_created",
            message="created digest record",
            digest_id=digest.digest_id,
            run_id=digest.run_id,
            version=digest.version,
        )

    # Fetch the latest digest
    def get_current_digest(self) -> DigestRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM digests ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return DigestRecord(
            digest_id=row["digest_id"],
            run_id=row["run_id"],
            version=row["version"],
            created_at=row["created_at"],
            output_json=json.loads(row["output_json"]),
        )

    # List all the digests, from newest to oldest
    def list_digest_history(self) -> list[DigestRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM digests ORDER BY created_at DESC").fetchall()
        return [
            DigestRecord(
                digest_id=row["digest_id"],
                run_id=row["run_id"],
                version=row["version"],
                created_at=row["created_at"],
                output_json=json.loads(row["output_json"]),
            )
            for row in rows
        ]

    # Update or insert a source cursor - ensure that a source knows where it left off
    def set_source_cursor(self, source_uid: str, next_cursor: str | None, updated_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO source_state (source_uid, next_cursor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_uid)
                DO UPDATE SET next_cursor = excluded.next_cursor, updated_at = excluded.updated_at
                """,
                (source_uid, next_cursor, updated_at),
            )

    # Read the source cursor
    def get_source_cursor(self, source_uid: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT next_cursor FROM source_state WHERE source_uid = ?",
                (source_uid,),
            ).fetchone()
        if row is None:
            return None
        return cast(str | None, row["next_cursor"])

    # Look up an item by its citation metadata
    def get_items_by_refs(self, refs: list[str]) -> list[dict[str, Any]]:
        if not refs:
            return []
        placeholders = ",".join(["?"] * len(refs))
        with self._lock:
            rows = self._conn.execute(
                (
                    "SELECT item_ref, source_uid, title, url "
                    f"FROM items WHERE item_ref IN ({placeholders})"
                ),
                tuple(refs),
            ).fetchall()
        ordered: list[dict[str, Any]] = []
        rows_by_ref = {row["item_ref"]: row for row in rows}
        for ref in refs:
            row = rows_by_ref.get(ref)
            if row is None:
                continue
            ordered.append(
                {
                    "item_ref": row["item_ref"],
                    "source_uid": row["source_uid"],
                    "title": row["title"],
                    "url": row["url"],
                }
            )
        return ordered

    # Check whether a source is participating in a queued or running run
    def run_is_active_for_source(self, source_uid: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM run_source_results rs
                JOIN runs r ON r.run_id = rs.run_id
                WHERE rs.source_uid = ? AND r.status IN ('queued', 'running')
                LIMIT 1
                """,
                (source_uid,),
            ).fetchone()
        return row is not None
