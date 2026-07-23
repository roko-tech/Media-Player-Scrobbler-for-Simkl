"""Durable completion-delivery ledger.

The public ``BacklogCleaner`` name is retained for compatibility with existing
callers. Its storage is an SQLite/WAL ledger so each completion has a stable
identity and provider outcomes survive restarts.
"""

import copy
import json
import logging
import pathlib
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


class BacklogCleaner:
    """Store pending completion events and their delivery outcomes durably."""

    def __init__(self, app_data_dir: pathlib.Path, backlog_file="backlog.json"):
        self.app_data_dir = pathlib.Path(app_data_dir)
        self.backlog_file = self.app_data_dir / backlog_file
        self.backup_file = self.backlog_file.with_suffix(
            self.backlog_file.suffix + ".bak"
        )
        self.database_file = self.app_data_dir / "completion_ledger.sqlite3"
        self._lock = threading.RLock()
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._migrate_legacy_backlog_once()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _connect(self):
        connection = sqlite3.connect(self.database_file, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_database(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS completion_events (
                    event_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'delivered', 'failed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_timestamp REAL,
                    last_error TEXT,
                    claim_owner TEXT,
                    claim_expires_at REAL
                );

                CREATE TABLE IF NOT EXISTS provider_outcomes (
                    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retryable INTEGER NOT NULL,
                    status_code INTEGER,
                    detail_json TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES completion_events(event_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ledger_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_completion_events_state_created
                    ON completion_events(state, created_at);
                CREATE INDEX IF NOT EXISTS idx_provider_outcomes_event
                    ON provider_outcomes(event_id, outcome_id);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(completion_events)"
                ).fetchall()
            }
            if "claim_owner" not in columns:
                connection.execute(
                    "ALTER TABLE completion_events ADD COLUMN claim_owner TEXT"
                )
            if "claim_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE completion_events ADD COLUMN claim_expires_at REAL"
                )

    def _legacy_data(self):
        if not self.backlog_file.exists():
            return {}
        try:
            loaded = json.loads(self.backlog_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
            if isinstance(loaded, list):
                converted = {}
                for item in loaded:
                    if not isinstance(item, dict) or "simkl_id" not in item:
                        logger.warning("Skipping malformed legacy backlog item: %r", item)
                        continue
                    key = str(item["simkl_id"])
                    if key in converted:
                        key = str(uuid.uuid4())
                    converted[key] = item
                return converted
            raise TypeError(
                f"Backlog must be a JSON object or array, got {type(loaded).__name__}"
            )
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            logger.error("Could not read legacy backlog: %s", exc)
            return self._recover_legacy_backlog()

    def _recover_legacy_backlog(self):
        recovered = {}
        try:
            backup_data = json.loads(self.backup_file.read_text(encoding="utf-8"))
            if isinstance(backup_data, dict):
                recovered = backup_data
            else:
                logger.error("Legacy backlog backup has an unexpected data type.")
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            logger.error("Could not recover legacy backlog backup: %s", exc)

        if self.backlog_file.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            corrupt = self.backlog_file.with_name(
                f"{self.backlog_file.name}.corrupt-{stamp}"
            )
            try:
                self.backlog_file.replace(corrupt)
                logger.error("Preserved corrupt legacy backlog as %s", corrupt.name)
            except OSError as exc:
                logger.error("Could not preserve corrupt legacy backlog: %s", exc)
        return recovered

    def _migrate_legacy_backlog_once(self):
        with self._lock, self._connect() as connection:
            migrated = connection.execute(
                "SELECT value FROM ledger_metadata WHERE key = 'legacy_backlog_migrated'"
            ).fetchone()
            if migrated:
                return

            for legacy_key, item in self._legacy_data().items():
                if not isinstance(item, dict):
                    continue
                payload = copy.deepcopy(item)
                event_id = payload.get("event_id") or str(legacy_key)
                payload["event_id"] = event_id
                created_at = payload.get("timestamp") or self._now()
                connection.execute(
                    """
                    INSERT OR IGNORE INTO completion_events (
                        event_id, payload_json, state, created_at, updated_at,
                        attempt_count, last_attempt_timestamp, last_error
                    ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        json.dumps(payload, ensure_ascii=False),
                        created_at,
                        created_at,
                        int(payload.get("attempt_count") or 0),
                        payload.get("last_attempt_timestamp"),
                        payload.get("last_error"),
                    ),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO ledger_metadata (key, value)
                VALUES ('legacy_backlog_migrated', ?)
                """,
                (self._now(),),
            )

    @staticmethod
    def _row_payload(row):
        payload = json.loads(row["payload_json"])
        payload["event_id"] = row["event_id"]
        payload["attempt_count"] = row["attempt_count"]
        payload["last_attempt_timestamp"] = row["last_attempt_timestamp"]
        payload["last_error"] = row["last_error"]
        payload["delivery_state"] = row["state"]
        return payload


    @staticmethod
    def _provider_outcomes(connection, event_id):
        outcomes = connection.execute(
            """
            SELECT provider, status, retryable, status_code, detail_json, recorded_at
            FROM provider_outcomes
            WHERE event_id = ?
            ORDER BY outcome_id
            """,
            (str(event_id),),
        ).fetchall()
        return [
            {
                "provider": outcome["provider"],
                "status": outcome["status"],
                "retryable": bool(outcome["retryable"]),
                "status_code": outcome["status_code"],
                "detail": json.loads(outcome["detail_json"])
                if outcome["detail_json"]
                else None,
                "recorded_at": outcome["recorded_at"],
            }
            for outcome in outcomes
        ]

    def add(self, simkl_id, title, additional_data=None, unique_event=False):
        """Insert a durable event, returning its stable identifier."""
        with self._lock:
            event_id = str(uuid.uuid4()) if unique_event else str(simkl_id)
            now = self._now()
            payload = {
                "event_id": event_id,
                "simkl_id": simkl_id,
                "title": title,
                "timestamp": now,
            }
            if isinstance(additional_data, dict):
                payload.update(copy.deepcopy(additional_data))
            if unique_event:
                payload.setdefault("watched_at", now)
            payload["event_id"] = event_id

            try:
                with self._connect() as connection:
                    existing = connection.execute(
                        "SELECT payload_json, created_at FROM completion_events WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()
                    if existing:
                        merged = json.loads(existing["payload_json"])
                        merged.update(payload)
                        connection.execute(
                            """
                            UPDATE completion_events
                            SET payload_json = ?, state = 'pending', updated_at = ?
                            WHERE event_id = ?
                            """,
                            (json.dumps(merged, ensure_ascii=False), now, event_id),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO completion_events (
                                event_id, payload_json, state, created_at, updated_at
                            ) VALUES (?, ?, 'pending', ?, ?)
                            """,
                            (
                                event_id,
                                json.dumps(payload, ensure_ascii=False),
                                now,
                                now,
                            ),
                        )
                return event_id
            except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
                logger.error("Could not persist completion event %s: %s", event_id, exc)
                return None

    def get_pending(self) -> dict:
        """Return a snapshot of pending events keyed by stable event ID."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM completion_events
                WHERE state = 'pending'
                ORDER BY created_at, event_id
                """
            ).fetchall()
            pending = {}
            for row in rows:
                payload = self._row_payload(row)
                payload["provider_outcomes"] = self._provider_outcomes(
                    connection,
                    row["event_id"],
                )
                pending[row["event_id"]] = payload
        return pending

    def get_event(self, event_id):
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM completion_events WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()
            if not row:
                return None
            payload = self._row_payload(row)
            payload["provider_outcomes"] = self._provider_outcomes(
                connection,
                event_id,
            )
        return payload

    def recent_events(self, limit=50):
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM completion_events
                ORDER BY updated_at DESC, event_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self.get_event(row["event_id"]) for row in rows]


    def delivery_counts(self):
        """Return exact completion-event counts grouped by persisted state."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM completion_events GROUP BY state"
            ).fetchall()
        counts = {"pending": 0, "delivered": 0, "failed": 0}
        counts.update({row["state"]: int(row["count"]) for row in rows})
        return counts

    def update_item(self, event_id, updates: dict):
        event_id = str(event_id)
        if not isinstance(updates, dict):
            return False
        with self._lock:
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT payload_json FROM completion_events WHERE event_id = ?",
                        (event_id,),
                    ).fetchone()
                    if not row:
                        logger.warning(
                            "Attempted to update non-existent completion event: %s",
                            event_id,
                        )
                        return False
                    payload = json.loads(row["payload_json"])
                    payload.update(copy.deepcopy(updates))
                    state = updates.get("delivery_state", "pending")
                    if state not in {"pending", "delivered", "failed"}:
                        state = "pending"
                    connection.execute(
                        """
                        UPDATE completion_events
                        SET payload_json = ?, state = ?, updated_at = ?,
                            attempt_count = ?, last_attempt_timestamp = ?,
                            last_error = ?
                        WHERE event_id = ?
                        """,
                        (
                            json.dumps(payload, ensure_ascii=False),
                            state,
                            self._now(),
                            int(payload.get("attempt_count") or 0),
                            payload.get("last_attempt_timestamp"),
                            payload.get("last_error"),
                            event_id,
                        ),
                    )
                return True
            except (sqlite3.Error, TypeError, ValueError) as exc:
                logger.error("Could not update completion event %s: %s", event_id, exc)
                return False

    def claim_event(self, event_id, owner, lease_seconds=600):
        """Atomically lease one pending event to a delivery worker."""
        owner = str(owner)
        now = time.time()
        expires_at = now + max(1, float(lease_seconds))
        with self._lock:
            try:
                with self._connect() as connection:
                    cursor = connection.execute(
                        """
                        UPDATE completion_events
                        SET claim_owner = ?, claim_expires_at = ?
                        WHERE event_id = ?
                          AND state = 'pending'
                          AND (
                              claim_owner IS NULL
                              OR claim_expires_at IS NULL
                              OR claim_expires_at <= ?
                              OR claim_owner = ?
                          )
                        """,
                        (owner, expires_at, str(event_id), now, owner),
                    )
                    return cursor.rowcount == 1
            except (sqlite3.Error, TypeError, ValueError) as exc:
                logger.error("Could not claim completion event %s: %s", event_id, exc)
                return False

    def release_event_claim(self, event_id, owner):
        """Release a delivery lease only when it is still owned by this worker."""
        with self._lock:
            try:
                with self._connect() as connection:
                    cursor = connection.execute(
                        """
                        UPDATE completion_events
                        SET claim_owner = NULL, claim_expires_at = NULL
                        WHERE event_id = ? AND claim_owner = ?
                        """,
                        (str(event_id), str(owner)),
                    )
                    return cursor.rowcount == 1
            except sqlite3.Error as exc:
                logger.error(
                    "Could not release completion event %s: %s",
                    event_id,
                    exc,
                )
                return False

    def record_outcome(
        self,
        event_id,
        provider,
        status,
        retryable,
        status_code=None,
        detail=None,
    ):
        """Append a typed provider outcome to an event's audit trail."""
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO provider_outcomes (
                            event_id, provider, status, retryable, status_code,
                            detail_json, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(event_id),
                            str(provider),
                            str(status),
                            int(bool(retryable)),
                            status_code,
                            json.dumps(detail, ensure_ascii=False)
                            if detail is not None
                            else None,
                            self._now(),
                        ),
                    )
                return True
            except (sqlite3.Error, TypeError, ValueError) as exc:
                logger.error(
                    "Could not record %s outcome for event %s: %s",
                    provider,
                    event_id,
                    exc,
                )
                return False


    def remove(self, event_id):
        """Mark a pending event delivered while retaining its audit trail."""
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE completion_events
                SET state = 'delivered', updated_at = ?
                WHERE event_id = ? AND state = 'pending'
                """,
                (self._now(), str(event_id)),
            )
            return cursor.rowcount == 1

    def fail(self, event_id, error=None):
        updates = {"delivery_state": "failed"}
        if error is not None:
            updates["last_error"] = str(error)
        return self.update_item(event_id, updates)

    def requeue_unauthorized(self):
        """Requeue failed events blocked by the most recent Simkl authorization outcome."""
        with self._lock:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        """
                        SELECT event_id, payload_json
                        FROM completion_events
                        WHERE state = 'failed'
                          AND (
                              SELECT status
                              FROM provider_outcomes
                              WHERE provider_outcomes.event_id = completion_events.event_id
                                AND provider = 'simkl'
                              ORDER BY outcome_id DESC
                              LIMIT 1
                          ) = 'unauthorized'
                        ORDER BY created_at, event_id
                        """
                    ).fetchall()
                    now = self._now()
                    event_ids = []
                    for row in rows:
                        payload = json.loads(row["payload_json"])
                        payload.update(
                            {
                                "delivery_state": "pending",
                                "attempt_count": 0,
                                "last_attempt_timestamp": None,
                                "last_error": None,
                            }
                        )
                        connection.execute(
                            """
                            UPDATE completion_events
                            SET payload_json = ?, state = 'pending', updated_at = ?,
                                attempt_count = 0, last_attempt_timestamp = NULL,
                                last_error = NULL, claim_owner = NULL,
                                claim_expires_at = NULL
                            WHERE event_id = ?
                            """,
                            (
                                json.dumps(payload, ensure_ascii=False),
                                now,
                                row["event_id"],
                            ),
                        )
                        event_ids.append(row["event_id"])
                return event_ids
            except (sqlite3.Error, TypeError, ValueError) as exc:
                logger.error("Could not requeue unauthorized completion events: %s", exc)
                return []

    def clear(self):
        """Delete pending events only; delivered audit history is retained."""
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM completion_events WHERE state = 'pending'")
        return True

    def has_pending_items(self) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM completion_events WHERE state = 'pending' LIMIT 1"
            ).fetchone()
        return row is not None
