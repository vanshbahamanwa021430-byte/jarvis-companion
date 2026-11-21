"""Single-owner demo approval broker. Call only from a trusted/authenticated API.

No arbitrary commands, paths, remote connections, or account credentials are
supported. Fixed fixtures make the approval state machine independently testable.
"""

from __future__ import annotations

import math
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

# Immutable target registry; requests do not contain live paths, URLs, or code.
# Value: (label, result title, synthetic result text).
_CATALOG: Mapping[tuple[str, str], tuple[str, str, str]] = MappingProxyType({
    ("read_file", "demo-note"): (
        "Read demo note",
        "Demo workspace note",
        "Synthetic content: plan a desk cleanup and review tomorrow's study list.",
    ),
    ("read_calendar", "demo-event"): (
        "Read demo calendar event",
        "Demo calendar event",
        "Synthetic event: review project notes, tomorrow at 10:00.",
    ),
})

STATUSES = frozenset({"pending", "approved", "denied", "expired", "revoked", "consumed"})
ACTIVE_STATUSES = ("pending", "approved")
AUDIT_EVENTS = frozenset(STATUSES - {"pending"} | {"proposed", "paused", "resumed"})
MAX_ACTIVE_REQUESTS = 50
MAX_REASON_CHARS = 240
MAX_PAGE_SIZE = 100
_ID_LENGTH = 32


def proposal_catalog() -> frozenset[tuple[str, str]]:
    """Return only allowed identifiers, not fixture content."""
    return frozenset(_CATALOG)


def catalog_actions() -> frozenset[str]:
    return frozenset(action for action, _ in _CATALOG)


_REQUEST_COLUMNS = (
    "id, action, target, reason, status, created_at, expires_at, "
    "approved_at, completed_at"
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_state (
    slot INTEGER PRIMARY KEY CHECK(slot = 1),
    paused INTEGER NOT NULL CHECK(paused IN (0, 1))
);
INSERT OR IGNORE INTO broker_state(slot, paused) VALUES(1, 0);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL CHECK(status IN
        ('pending','approved','denied','expired','revoked','consumed')),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    approved_at REAL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS ix_request_status_expiry ON requests(status, expires_at);
CREATE TRIGGER IF NOT EXISTS immutable_request_details
    BEFORE UPDATE OF id, action, target, reason, created_at ON requests
    BEGIN SELECT RAISE(ABORT, 'request details are immutable'); END;
CREATE TABLE IF NOT EXISTS demo_results (
    request_id TEXT PRIMARY KEY NOT NULL REFERENCES requests(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    event TEXT NOT NULL,
    request_id TEXT REFERENCES requests(id),
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_event ON audit(event, id);
"""


class BrokerError(Exception):
    """A business rejection with an HTTP-style status and a fixed code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _page_size(limit: Any) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise BrokerError(400, "invalid_limit", f"Limit must be 1-{MAX_PAGE_SIZE}.")
    return limit


def _cursor(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise BrokerError(400, "invalid_cursor", "Cursor must be a positive integer.")
    return value


class Broker:
    """File-backed, fail-closed approvals with at-most-once synthetic execution."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int | float = 300,
    ):
        if (
            type(ttl_seconds) not in (int, float)
            or not 0 < ttl_seconds <= 3600
        ):
            raise ValueError("ttl_seconds must be greater than zero and at most 3600")
        if not db_path or str(db_path) == ":memory:":
            raise ValueError("A file-backed database is required")
        self._db_path = Path(db_path).resolve()
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        with closing(self._connect()) as connection:
            # WAL lets dashboard reads proceed while the worker commits.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)

    @property
    def ttl_seconds(self) -> int | float:
        return self._ttl_seconds

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, float]]:
        with closing(self._connect()) as connection:
            # Serialize transitions across threads and processes. Read the clock
            # only after claiming the lock so a waiting caller cannot use an old time.
            connection.execute("BEGIN IMMEDIATE")
            try:
                now = float(self._clock())
                if not math.isfinite(now):
                    raise RuntimeError("Approval clock is not finite")
                self._expire(connection, now)
                yield connection, now
                connection.commit()
            except BrokerError:
                # Business rejections happen before this action writes any
                # state. Commit expiry transitions already performed above.
                connection.commit()
                raise
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _audit(connection: sqlite3.Connection, event: str, now: float, request_id: str | None = None) -> None:
        connection.execute(
            "INSERT INTO audit(event, request_id, at) VALUES(?, ?, ?)",
            (event, request_id, now),
        )

    def _expire(self, connection: sqlite3.Connection, now: float) -> None:
        for row in connection.execute(
            "SELECT id FROM requests WHERE status IN ('pending','approved') "
            "AND expires_at <= ?",
            (now,),
        ).fetchall():
            self._transition(connection, row["id"], "expired", now)

    @staticmethod
    def _paused(connection: sqlite3.Connection) -> bool:
        return bool(connection.execute(
            "SELECT paused FROM broker_state WHERE slot = 1"
        ).fetchone()["paused"])

    def _running(self, connection: sqlite3.Connection) -> None:
        if self._paused(connection):
            raise BrokerError(423, "paused", "The approval broker is paused.")

    @staticmethod
    def _details(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        fixture = _CATALOG.get((result["action"], result["target"]))
        # v2: rows left by an older/newer catalog must not crash the dashboard
        # or the worker. They are shown but can never execute.
        result["label"] = fixture[0] if fixture else "Unavailable item"
        result["result"] = None
        if result["status"] == "consumed":
            record = connection.execute(
                "SELECT title, body FROM demo_results WHERE request_id = ?",
                (result["id"],),
            ).fetchone()
            if record is not None:
                result["result"] = {"title": record["title"], "text": record["body"]}
        return result

    @staticmethod
    def _get(connection: sqlite3.Connection, request_id: Any) -> sqlite3.Row:
        if type(request_id) is not str or len(request_id) != _ID_LENGTH:
            raise BrokerError(404, "not_found", "Request not found.")
        row = connection.execute(
            f"SELECT {_REQUEST_COLUMNS} FROM requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise BrokerError(404, "not_found", "Request not found.")
        return row

    def _transition(self, connection: sqlite3.Connection, request_id: str, state: str, now: float) -> None:
        connection.execute(
            "UPDATE requests SET status = ?, completed_at = ? WHERE id = ?",
            (state, now, request_id),
        )
        self._audit(connection, state, now, request_id)

    @staticmethod
    def _require(row: sqlite3.Row, *states: str) -> None:
        if row["status"] not in states:
            raise BrokerError(409, "invalid_state", f"Request is {row['status']}.")

    # ----------------------------------------------------------------- actions
    def propose(self, action: str, target: str, reason: str | None = None) -> dict[str, Any]:
        if (
            type(action) is not str or type(target) is not str
            or (action, target) not in _CATALOG
        ):
            raise BrokerError(400, "unknown_action", "Choose an action and target from the demo catalog.")
        if reason is not None and (
            type(reason) is not str or len(reason) > MAX_REASON_CHARS
            or any(not ch.isprintable() for ch in reason)
        ):
            raise BrokerError(400, "invalid_reason", "Reason must be plain text of at most 240 characters.")
        if reason is not None:
            reason = reason.strip() or None
        with self._transaction() as (connection, now):
            self._running(connection)
            active_count = connection.execute(
                "SELECT count(*) FROM requests WHERE status IN ('pending','approved')"
            ).fetchone()[0]
            if active_count >= MAX_ACTIVE_REQUESTS:
                raise BrokerError(429, "request_limit", "Resolve existing requests before proposing more.")
            request_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO requests(id, action, target, reason, status, created_at, expires_at) "
                "VALUES(?, ?, ?, ?, 'pending', ?, ?)",
                (request_id, action, target, reason, now, now + self._ttl_seconds),
            )
            self._audit(connection, "proposed", now, request_id)
            return self._details(connection, self._get(connection, request_id))

    def approve(self, request_id: str) -> dict[str, Any]:
        with self._transaction() as (connection, now):
            self._running(connection)
            row = self._get(connection, request_id)
            self._require(row, "pending")
            if (row["action"], row["target"]) not in _CATALOG:
                raise BrokerError(409, "unavailable_target", "This item is no longer in the catalog.")
            connection.execute(
                "UPDATE requests SET status = 'approved', approved_at = ?, expires_at = ? WHERE id = ?",
                (now, now + self._ttl_seconds, request_id),
            )
            self._audit(connection, "approved", now, request_id)
            return self._details(connection, self._get(connection, request_id))

    def deny(self, request_id: str) -> dict[str, Any]:
        with self._transaction() as (connection, now):
            row = self._get(connection, request_id)
            self._require(row, "pending", "approved")
            state = "denied" if row["status"] == "pending" else "revoked"
            self._transition(connection, request_id, state, now)
            return self._details(connection, self._get(connection, request_id))

    def execute(self, request_id: str) -> dict[str, Any]:
        with self._transaction() as (connection, now):
            self._running(connection)
            row = self._get(connection, request_id)
            self._require(row, "approved")
            fixture = _CATALOG.get((row["action"], row["target"]))
            if fixture is None:
                raise BrokerError(409, "unavailable_target", "This item is no longer in the catalog.")
            # Fetch only the immutable registered fixture. No caller-supplied
            # target or bytes can enter execution, and no I/O occurs here.
            _, title, body = fixture
            connection.execute(
                "INSERT INTO demo_results(request_id, title, body) VALUES(?, ?, ?)",
                (request_id, title, body),
            )
            self._transition(connection, request_id, "consumed", now)
            return {"id": request_id, "status": "consumed", "result": {"title": title, "text": body}}

    def set_paused(self, paused: bool) -> dict[str, bool]:
        if type(paused) is not bool:
            raise BrokerError(400, "invalid_pause", "Paused must be true or false.")
        with self._transaction() as (connection, now):
            if self._paused(connection) != paused:
                connection.execute("UPDATE broker_state SET paused = ? WHERE slot = 1", (int(paused),))
                self._audit(connection, "paused" if paused else "resumed", now)
            if paused:
                for row in connection.execute(
                    "SELECT id FROM requests WHERE status IN ('pending','approved')"
                ).fetchall():
                    self._transition(connection, row["id"], "revoked", now)
        return {"paused": paused}

    # ------------------------------------------------------------------- reads
    def approved_ids(self) -> list[str]:
        """Oldest-first approved, executable request IDs (for the worker).

        Much cheaper than a full snapshot; also applies expiry. Returns an
        empty list while paused.
        """
        with self._transaction() as (connection, _now):
            if self._paused(connection):
                return []
            return [row["id"] for row in connection.execute(
                "SELECT id FROM requests WHERE status = 'approved' ORDER BY rowid"
            )]

    def status(self) -> dict[str, Any]:
        """Small, cheap summary for live polling. `revision` changes on any event."""
        with self._transaction() as (connection, now):
            counts = dict(connection.execute(
                "SELECT status, count(*) FROM requests WHERE status IN ('pending','approved') "
                "GROUP BY status"
            ).fetchall())
            revision = connection.execute("SELECT coalesce(max(id), 0) FROM audit").fetchone()[0]
            return {
                "revision": revision,
                "paused": self._paused(connection),
                "pending": counts.get("pending", 0),
                "approved": counts.get("approved", 0),
                "server_time": now,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._transaction() as (connection, now):
            return {
                "paused": self._paused(connection),
                "server_time": now,
                "ttl_seconds": self._ttl_seconds,
                "revision": connection.execute("SELECT coalesce(max(id), 0) FROM audit").fetchone()[0],
                "catalog": [
                    {"action": action, "target": target, "label": fixture[0]}
                    for (action, target), fixture in _CATALOG.items()
                ],
                "requests": [
                    self._details(connection, row) for row in connection.execute(
                        f"SELECT {_REQUEST_COLUMNS} FROM requests "
                        "ORDER BY (status IN ('pending','approved')) DESC, rowid DESC LIMIT 100"
                    )
                ],
                "audit": [
                    dict(row) for row in connection.execute(
                        "SELECT id, event, request_id, at FROM audit ORDER BY id DESC LIMIT 200"
                    )
                ],
            }

    def history(
        self,
        *,
        status: str | None = None,
        action: str | None = None,
        limit: int = 20,
        before: int | None = None,
    ) -> dict[str, Any]:
        """Newest-first request history with filters and a keyset cursor."""
        if status is not None and status not in STATUSES:
            raise BrokerError(400, "invalid_filter", "Unknown status filter.")
        if action is not None and action not in catalog_actions():
            raise BrokerError(400, "invalid_filter", "Unknown action filter.")
        limit, before = _page_size(limit), _cursor(before)
        with self._transaction() as (connection, _now):
            rows = connection.execute(
                f"SELECT rowid AS seq, {_REQUEST_COLUMNS} FROM requests "
                "WHERE (? IS NULL OR status = ?) AND (? IS NULL OR action = ?) "
                "AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT ?",
                (status, status, action, action, before, before, limit + 1),
            ).fetchall()
            items = [self._details(connection, row) for row in rows[:limit]]
            total = connection.execute(
                "SELECT count(*) FROM requests WHERE (? IS NULL OR status = ?) "
                "AND (? IS NULL OR action = ?)",
                (status, status, action, action),
            ).fetchone()[0]
        return {
            "items": items,
            "next_cursor": items[-1]["seq"] if len(rows) > limit else None,
            "total": total,
        }

    def audit_log(
        self,
        *,
        event: str | None = None,
        request_id: str | None = None,
        limit: int = 50,
        before: int | None = None,
    ) -> dict[str, Any]:
        """Newest-first audit events with filters and a keyset cursor."""
        if event is not None and event not in AUDIT_EVENTS:
            raise BrokerError(400, "invalid_filter", "Unknown event filter.")
        if request_id is not None and (type(request_id) is not str or len(request_id) != _ID_LENGTH):
            raise BrokerError(400, "invalid_filter", "Invalid request filter.")
        limit, before = _page_size(limit), _cursor(before)
        with self._transaction() as (connection, _now):
            rows = [dict(row) for row in connection.execute(
                "SELECT a.id, a.event, a.request_id, a.at, r.action, r.target FROM audit a "
                "LEFT JOIN requests r ON r.id = a.request_id "
                "WHERE (? IS NULL OR a.event = ?) AND (? IS NULL OR a.request_id = ?) "
                "AND (? IS NULL OR a.id < ?) ORDER BY a.id DESC LIMIT ?",
                (event, event, request_id, request_id, before, before, limit + 1),
            )]
        items = rows[:limit]
        for item in items:
            fixture = _CATALOG.get((item.pop("action"), item.pop("target")))
            item["label"] = fixture[0] if fixture else None
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
