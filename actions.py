"""Typed v2.2 action proposals. Separate from the demo catalog in broker.py.

Every action is proposed, then approved by the owner, then executed at most
once. Phone actions are marked runs_on=device and are never executed here.
Desk-side actions (Google, webhooks, MCP) run only after approval. The kill
switch rejects new proposals and execution.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

STATUSES = frozenset({"pending", "approved", "denied", "expired", "revoked", "consumed"})
MAX_ACTIVE = 50
MAX_REASON = 240
MAX_PAYLOAD = 8000
MAX_STRING = 500
_ID_LENGTH = 32
_KIND = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")


def _int(name: str, value: Any, lo: int, hi: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not lo <= value <= hi:
        raise ActionError(400, "invalid_payload", f"Invalid {name}.")
    return value


def _str(name: str, value: Any, *, max_len: int = MAX_STRING, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value.strip() or len(value) > max_len:
        raise ActionError(400, "invalid_payload", f"Invalid {name}.")
    return value.strip()


def _enum(name: str, value: Any, choices: frozenset[str]) -> str:
    if type(value) is not str or value not in choices:
        raise ActionError(400, "invalid_payload", f"Invalid {name}.")
    return value


def _spec(label: str, family: str, runs_on: str, risk: str, required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"label": label, "family": family, "runs_on": runs_on, "risk": risk, "required": required}


# risk=high requires a spoken Confirm/Cancel as well as the on-screen card.
CATALOG: dict[str, dict[str, Any]] = {
    "phone.open_app": _spec("Open an app", "phone", "device", "low", ("app",)),
    "phone.tap": _spec("Tap the screen", "phone", "device", "low"),
    "phone.type": _spec("Type text", "phone", "device", "high", ("text",)),
    "phone.scroll": _spec("Scroll the screen", "phone", "device", "low", ("direction",)),
    "phone.back": _spec("Go back", "phone", "device", "low"),
    "phone.home": _spec("Go home", "phone", "device", "low"),
    "phone.read_screen": _spec("Read screen text", "phone", "device", "high"),
    "phone.call": _spec("Open the dialer", "phone", "device", "high", ("number",)),
    "phone.sms_draft": _spec("Open an SMS draft", "phone", "device", "high", ("number",)),
    "phone.set_alarm": _spec("Set an alarm", "phone", "device", "low"),
    "phone.open_settings": _spec("Open settings", "phone", "device", "low"),
    "phone.read_notifications": _spec("Read notifications", "phone", "device", "high"),
    "google.gmail.read": _spec("Read Gmail", "google", "desk", "low"),
    "google.gmail.send": _spec("Send Gmail", "google", "desk", "high", ("to", "subject", "body")),
    "google.calendar.read": _spec("Read Calendar", "google", "desk", "low"),
    "google.calendar.write": _spec("Write Calendar", "google", "desk", "high", ("title",)),
    "google.drive.read": _spec("Read Drive", "google", "desk", "low"),
    "google.drive.write": _spec("Write Drive", "google", "desk", "high", ("name", "content")),
    "google.contacts.read": _spec("Read Contacts", "google", "desk", "low"),
    "google.tasks.read": _spec("Read Tasks", "google", "desk", "low"),
    "google.tasks.write": _spec("Write Tasks", "google", "desk", "high", ("title",)),
    "google.youtube.open": _spec("Open YouTube", "google", "device", "low"),
    "google.maps.open": _spec("Open Maps", "google", "device", "low", ("query",)),
    "webhook.http": _spec("HTTP webhook", "webhook", "desk", "high", ("connector_id", "method")),
    "mcp.call": _spec("MCP tool call", "mcp", "desk", "high", ("server_id", "tool")),
    "plugin.run": _spec("Run a plugin action", "plugin", "desk", "low", ("plugin", "action")),
}


def catalog_public() -> list[dict[str, Any]]:
    return [{"kind": kind, **{k: v for k, v in spec.items() if k != "required"}} for kind, spec in CATALOG.items()]


def validate_payload(kind: str, payload: Any) -> dict[str, Any]:
    if kind not in CATALOG:
        raise ActionError(400, "unknown_action", "Unknown action kind.")
    if type(payload) is not dict:
        raise ActionError(400, "invalid_payload", "Payload must be an object.")
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if len(raw) > MAX_PAYLOAD:
        raise ActionError(400, "invalid_payload", "Payload is too large.")
    spec = CATALOG[kind]
    data = dict(payload)
    for key in spec["required"]:
        if key not in data:
            raise ActionError(400, "invalid_payload", f"Missing {key}.")
    # Kind-specific checks.
    if kind == "phone.open_app":
        app = _str("app", data.get("app"))
        package = _str("package", data.get("package"), optional=True)
        out = {"app": app}
        if package:
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+", package):
                raise ActionError(400, "invalid_payload", "Invalid package.")
            out["package"] = package
        return out
    if kind == "phone.tap":
        if "text" in data:
            return {"text": _str("text", data["text"], max_len=80)}
        return {"x": _int("x", data.get("x"), 0, 10000), "y": _int("y", data.get("y"), 0, 10000)}
    if kind == "phone.type":
        return {"text": _str("text", data["text"], max_len=200)}
    if kind == "phone.scroll":
        return {"direction": _enum("direction", data["direction"], frozenset({"up", "down", "left", "right"}))}
    if kind in {"phone.back", "phone.home", "phone.read_screen", "phone.read_notifications"}:
        return {}
    if kind == "phone.call":
        return {"number": _phone(data["number"])}
    if kind == "phone.sms_draft":
        body = _str("body", data.get("body"), max_len=240, optional=True) or ""
        return {"number": _phone(data["number"]), "body": body}
    if kind == "phone.set_alarm":
        hour = _int("hour", data.get("hour"), 0, 23)
        minute = _int("minute", data.get("minute"), 0, 59)
        message = _str("message", data.get("message"), max_len=80, optional=True) or ""
        return {"hour": hour, "minute": minute, "message": message}
    if kind == "phone.open_settings":
        panel = _str("panel", data.get("panel"), max_len=40, optional=True) or "main"
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", panel):
            raise ActionError(400, "invalid_payload", "Invalid panel.")
        return {"panel": panel}
    if kind == "google.gmail.read":
        return {"query": _str("query", data.get("query"), optional=True) or ""}
    if kind == "google.gmail.send":
        return {
            "to": _email(data["to"]),
            "subject": _str("subject", data["subject"], max_len=120),
            "body": _str("body", data["body"], max_len=2000),
        }
    if kind == "google.calendar.read":
        return {"when": _str("when", data.get("when"), optional=True) or ""}
    if kind == "google.calendar.write":
        return {
            "title": _str("title", data["title"], max_len=120),
            "start": _str("start", data.get("start"), optional=True) or "",
            "end": _str("end", data.get("end"), optional=True) or "",
        }
    if kind == "google.drive.read":
        return {"query": _str("query", data.get("query"), optional=True) or ""}
    if kind == "google.drive.write":
        return {"name": _str("name", data["name"], max_len=80), "content": _str("content", data["content"], max_len=4000)}
    if kind == "google.contacts.read":
        return {"query": _str("query", data.get("query"), optional=True) or ""}
    if kind in {"google.tasks.read"}:
        return {}
    if kind == "google.tasks.write":
        return {"title": _str("title", data["title"], max_len=120)}
    if kind == "google.youtube.open":
        return {"query": _str("query", data.get("query"), optional=True) or ""}
    if kind == "google.maps.open":
        return {"query": _str("query", data["query"], max_len=200)}
    if kind == "webhook.http":
        method = _enum("method", data["method"].upper() if type(data.get("method")) is str else data.get("method"),
                       frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"}))
        path = data.get("path") or "/"
        if type(path) is not str or not path.startswith("/") or ".." in path or len(path) > 200:
            raise ActionError(400, "invalid_payload", "Invalid path.")
        body = data.get("body")
        if body is not None and type(body) is not str:
            raise ActionError(400, "invalid_payload", "Invalid body.")
        if type(body) is str and len(body) > 4000:
            raise ActionError(400, "invalid_payload", "Invalid body.")
        connector_id = _id(data["connector_id"])
        return {"connector_id": connector_id, "method": method, "path": path, "body": body or ""}
    if kind == "mcp.call":
        tool = _str("tool", data["tool"], max_len=80)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", tool):
            raise ActionError(400, "invalid_payload", "Invalid tool.")
        arguments = data.get("arguments") or {}
        if type(arguments) is not dict:
            raise ActionError(400, "invalid_payload", "Invalid arguments.")
        dumped = json.dumps(arguments, ensure_ascii=True, separators=(",", ":"))
        if len(dumped) > 4000:
            raise ActionError(400, "invalid_payload", "Invalid arguments.")
        return {"server_id": _id(data["server_id"]), "tool": tool, "arguments": arguments}
    if kind == "plugin.run":
        plugin = _str("plugin", data["plugin"], max_len=32)
        action = _str("action", data["action"], max_len=32)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", plugin) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", action):
            raise ActionError(400, "invalid_payload", "Invalid plugin action.")
        params = data.get("params") or {}
        if type(params) is not dict:
            raise ActionError(400, "invalid_payload", "Invalid params.")
        return {"plugin": plugin, "action": action, "params": params}
    raise ActionError(400, "unknown_action", "Unknown action kind.")


def _phone(value: Any) -> str:
    if type(value) is not str:
        raise ActionError(400, "invalid_payload", "Invalid number.")
    compact = re.sub(r"[ \-()]", "", value.strip())
    if not re.fullmatch(r"\+?[0-9]{6,15}", compact):
        raise ActionError(400, "invalid_payload", "Invalid number.")
    return compact


def _email(value: Any) -> str:
    text = _str("to", value, max_len=120)
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}", text or ""):
        raise ActionError(400, "invalid_payload", "Invalid email.")
    return text.lower()  # type: ignore[union-attr]


def _id(value: Any) -> str:
    if type(value) is not str or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
        raise ActionError(400, "invalid_payload", "Invalid id.")
    return value.lower()


def describe(kind: str, payload: dict[str, Any]) -> str:
    label = CATALOG[kind]["label"]
    if kind == "phone.open_app":
        return f"Open app {payload.get('app')}"
    if kind == "phone.call":
        return f"Dial {payload.get('number')}"
    if kind == "phone.sms_draft":
        return f"SMS draft to {payload.get('number')}"
    if kind == "google.gmail.send":
        return f"Send Gmail to {payload.get('to')}"
    if kind == "google.maps.open":
        return f"Open Maps for {payload.get('query')}"
    if kind == "plugin.run":
        return f"Plugin {payload.get('plugin')}:{payload.get('action')}"
    if kind == "mcp.call":
        return f"MCP {payload.get('tool')}"
    if kind == "webhook.http":
        return f"Webhook {payload.get('method')} {payload.get('path')}"
    return label


class ActionError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status = status
        self.code = code


_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_state (
    slot INTEGER PRIMARY KEY CHECK(slot = 1),
    paused INTEGER NOT NULL CHECK(paused IN (0, 1)),
    killed INTEGER NOT NULL CHECK(killed IN (0, 1))
);
INSERT OR IGNORE INTO action_state(slot, paused, killed) VALUES(1, 0, 0);
CREATE TABLE IF NOT EXISTS action_requests (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL CHECK(status IN
        ('pending','approved','denied','expired','revoked','consumed')),
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    approved_at REAL,
    completed_at REAL,
    result TEXT
);
CREATE INDEX IF NOT EXISTS ix_action_status ON action_requests(status, expires_at);
CREATE TRIGGER IF NOT EXISTS immutable_action_details
    BEFORE UPDATE OF id, kind, payload, reason, source, created_at ON action_requests
    BEGIN SELECT RAISE(ABORT, 'action details are immutable'); END;
CREATE TABLE IF NOT EXISTS action_audit (
    id INTEGER PRIMARY KEY,
    event TEXT NOT NULL,
    request_id TEXT,
    at REAL NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS device_heartbeat (
    slot INTEGER PRIMARY KEY CHECK(slot = 1),
    at REAL NOT NULL,
    payload TEXT NOT NULL
);
"""


class ActionsBroker:
    """File-backed approvals for typed v2.2 actions."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: int | float = 300,
        executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        if type(ttl_seconds) not in (int, float) or not 0 < ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be greater than zero and at most 3600")
        if not db_path or str(db_path) == ":memory:":
            raise ValueError("A file-backed database is required")
        self._db_path = Path(db_path).resolve()
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self.executor = executor
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _expire(self, connection: sqlite3.Connection, now: float) -> None:
        rows = list(connection.execute(
            "SELECT id FROM action_requests WHERE status IN ('pending','approved') AND expires_at <= ?",
            (now,),
        ))
        for row in rows:
            connection.execute(
                "UPDATE action_requests SET status = 'expired' WHERE id = ?", (row["id"],))
            self._audit(connection, "expired", now, row["id"])

    def _flags(self, connection: sqlite3.Connection) -> tuple[bool, bool]:
        row = connection.execute("SELECT paused, killed FROM action_state WHERE slot = 1").fetchone()
        return bool(row["paused"]), bool(row["killed"])

    def _audit(self, connection: sqlite3.Connection, event: str, at: float,
               request_id: str | None = None, detail: str | None = None) -> None:
        connection.execute(
            "INSERT INTO action_audit(event, request_id, at, detail) VALUES (?, ?, ?, ?)",
            (event, request_id, at, detail),
        )

    def flags(self) -> dict[str, bool]:
        with closing(self._connect()) as connection:
            paused, killed = self._flags(connection)
            return {"paused": paused, "killed": killed}

    def set_paused(self, paused: bool) -> dict[str, Any]:
        now = self._clock()
        with self._tx() as connection:
            self._expire(connection, now)
            connection.execute("UPDATE action_state SET paused = ? WHERE slot = 1", (int(bool(paused)),))
            if paused:
                ids = [row["id"] for row in connection.execute(
                    "SELECT id FROM action_requests WHERE status IN ('pending','approved')")]
                for request_id in ids:
                    connection.execute(
                        "UPDATE action_requests SET status = 'revoked' WHERE id = ?", (request_id,))
                    self._audit(connection, "revoked", now, request_id)
                self._audit(connection, "paused", now)
            else:
                self._audit(connection, "resumed", now)
        return self.snapshot()

    def set_killed(self, killed: bool) -> dict[str, Any]:
        now = self._clock()
        with self._tx() as connection:
            self._expire(connection, now)
            connection.execute(
                "UPDATE action_state SET killed = ?, paused = ? WHERE slot = 1",
                (int(bool(killed)), 1 if killed else 0),
            )
            if killed:
                ids = [row["id"] for row in connection.execute(
                    "SELECT id FROM action_requests WHERE status IN ('pending','approved')")]
                for request_id in ids:
                    connection.execute(
                        "UPDATE action_requests SET status = 'revoked' WHERE id = ?", (request_id,))
                    self._audit(connection, "revoked", now, request_id)
                self._audit(connection, "killed", now)
            else:
                # Re-enable does not unpause automatically.
                connection.execute("UPDATE action_state SET paused = 1 WHERE slot = 1")
                self._audit(connection, "unkilled", now)
        return self.snapshot()

    def propose(self, kind: str, payload: dict[str, Any] | None = None,
                reason: str | None = None, source: str = "owner") -> dict[str, Any]:
        if type(kind) is not str or not _KIND.fullmatch(kind):
            raise ActionError(400, "unknown_action", "Unknown action kind.")
        clean = validate_payload(kind, payload or {})
        if reason is not None:
            if type(reason) is not str or len(reason) > MAX_REASON:
                raise ActionError(400, "invalid_reason", "Invalid reason.")
            reason = reason.strip() or None
        if type(source) is not str or source not in {"owner", "voice", "brain", "plugin", "mcp", "agent", "device"}:
            raise ActionError(400, "invalid_payload", "Invalid source.")
        now = self._clock()
        request_id = uuid.uuid4().hex
        with self._tx() as connection:
            self._expire(connection, now)
            paused, killed = self._flags(connection)
            if killed:
                raise ActionError(403, "killed", "The kill switch is on.")
            if paused:
                raise ActionError(409, "paused", "Access is paused.")
            active = connection.execute(
                "SELECT count(*) FROM action_requests WHERE status IN ('pending','approved')"
            ).fetchone()[0]
            if active >= MAX_ACTIVE:
                raise ActionError(429, "too_many", "Too many active requests.")
            connection.execute(
                "INSERT INTO action_requests(id, kind, payload, reason, status, source, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
                (request_id, kind, json.dumps(clean, separators=(",", ":")), reason, source,
                 now, now + float(self._ttl_seconds)),
            )
            self._audit(connection, "proposed", now, request_id, kind)
        return self.get(request_id)

    def _row(self, connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
        if type(request_id) is not str or len(request_id) != _ID_LENGTH:
            raise ActionError(400, "unknown_request", "Unknown request.")
        row = connection.execute("SELECT * FROM action_requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            raise ActionError(404, "unknown_request", "Unknown request.")
        return row

    def get(self, request_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            self._expire(connection, self._clock())
            return self._present(self._row(connection, request_id))

    def approve(self, request_id: str) -> dict[str, Any]:
        now = self._clock()
        with self._tx() as connection:
            self._expire(connection, now)
            paused, killed = self._flags(connection)
            if killed:
                raise ActionError(403, "killed", "The kill switch is on.")
            if paused:
                raise ActionError(409, "paused", "Access is paused.")
            row = self._row(connection, request_id)
            if row["status"] != "pending":
                raise ActionError(409, "not_pending", "Request is not pending.")
            if row["expires_at"] <= now:
                raise ActionError(409, "expired", "Request expired.")
            connection.execute(
                "UPDATE action_requests SET status = 'approved', approved_at = ?, expires_at = ? WHERE id = ?",
                (now, now + float(self._ttl_seconds), request_id),
            )
            self._audit(connection, "approved", now, request_id)
        return self.get(request_id)

    def deny(self, request_id: str) -> dict[str, Any]:
        now = self._clock()
        with self._tx() as connection:
            self._expire(connection, now)
            row = self._row(connection, request_id)
            if row["status"] != "pending":
                raise ActionError(409, "not_pending", "Request is not pending.")
            connection.execute(
                "UPDATE action_requests SET status = 'denied' WHERE id = ?", (request_id,))
            self._audit(connection, "denied", now, request_id)
        return self.get(request_id)

    def execute(self, request_id: str) -> dict[str, Any]:
        """Execute a desk-side action, or mark a device action as handed to the phone."""
        now = self._clock()
        with self._tx() as connection:
            self._expire(connection, now)
            paused, killed = self._flags(connection)
            if killed:
                raise ActionError(403, "killed", "The kill switch is on.")
            if paused:
                raise ActionError(409, "paused", "Access is paused.")
            row = self._row(connection, request_id)
            if row["status"] != "approved":
                raise ActionError(409, "not_approved", "Request is not approved.")
            kind = row["kind"]
            payload = json.loads(row["payload"])
            spec = CATALOG.get(kind)
            if spec is None:
                raise ActionError(409, "unknown_action", "Unknown action kind.")
            if spec["runs_on"] == "device":
                raise ActionError(409, "device_action", "This action runs on the phone.")
            result: dict[str, Any]
            try:
                if self.executor is None:
                    raise ActionError(500, "no_executor", "No executor is configured.")
                result = self.executor(kind, payload)
            except ActionError as exc:
                connection.execute(
                    "UPDATE action_requests SET status = 'consumed', completed_at = ?, result = ? WHERE id = ?",
                    (now, json.dumps({"ok": False, "error": exc.code}), request_id),
                )
                self._audit(connection, "failed", now, request_id, exc.code)
                raise
            connection.execute(
                "UPDATE action_requests SET status = 'consumed', completed_at = ?, result = ? WHERE id = ?",
                (now, json.dumps({"ok": True, **(result or {})}, ensure_ascii=True, default=str)[:8000],
                 request_id),
            )
            self._audit(connection, "consumed", now, request_id)
        return self.get(request_id)

    def complete_device(self, request_id: str, ok: bool, result: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self._clock()
        with self._tx() as connection:
            self._expire(connection, now)
            row = self._row(connection, request_id)
            if row["status"] != "approved":
                raise ActionError(409, "not_approved", "Request is not approved.")
            kind = row["kind"]
            if CATALOG.get(kind, {}).get("runs_on") != "device":
                raise ActionError(409, "not_device", "This action does not run on the phone.")
            payload = {"ok": bool(ok)}
            if result and type(result) is dict:
                payload.update({k: result[k] for k in list(result)[:8]})
            connection.execute(
                "UPDATE action_requests SET status = 'consumed', completed_at = ?, result = ? WHERE id = ?",
                (now, json.dumps(payload, ensure_ascii=True, default=str)[:8000], request_id),
            )
            self._audit(connection, "consumed" if ok else "failed", now, request_id)
        return self.get(request_id)

    def approved_desk_ids(self) -> list[str]:
        now = self._clock()
        with closing(self._connect()) as connection:
            self._expire(connection, now)
            paused, killed = self._flags(connection)
            if paused or killed:
                return []
            rows = connection.execute(
                "SELECT id, kind FROM action_requests WHERE status = 'approved' ORDER BY rowid"
            ).fetchall()
            return [row["id"] for row in rows if CATALOG.get(row["kind"], {}).get("runs_on") == "desk"]

    def pending_for_device(self) -> list[dict[str, Any]]:
        now = self._clock()
        with closing(self._connect()) as connection:
            self._expire(connection, now)
            paused, killed = self._flags(connection)
            if killed:
                return []
            rows = connection.execute(
                "SELECT * FROM action_requests WHERE status IN ('pending','approved') ORDER BY rowid"
            ).fetchall()
            return [self._present(row) for row in rows]

    def record_heartbeat(self, payload: dict[str, Any]) -> None:
        now = self._clock()
        clean = {k: bool(payload[k]) for k in (
            "accessibility", "notifications", "battery_exempt", "boot_enabled",
            "voice_muted", "listening", "kill_switch",
        ) if k in payload}
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO device_heartbeat(slot, at, payload) VALUES (1, ?, ?) "
                "ON CONFLICT(slot) DO UPDATE SET at = excluded.at, payload = excluded.payload",
                (now, json.dumps(clean)),
            )

    def heartbeat(self) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT at, payload FROM device_heartbeat WHERE slot = 1").fetchone()
            if row is None:
                return None
            data = json.loads(row["payload"])
            data["at"] = row["at"]
            return data

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        with closing(self._connect()) as connection:
            self._expire(connection, now)
            paused, killed = self._flags(connection)
            rows = [self._present(row) for row in connection.execute(
                "SELECT * FROM action_requests ORDER BY rowid DESC LIMIT 100"
            )]
            log = [dict(row) for row in connection.execute(
                "SELECT id, event, request_id, at, detail FROM action_audit ORDER BY id DESC LIMIT 50"
            )]
            counts = {"pending": 0, "approved": 0}
            for row in rows:
                if row["status"] in counts:
                    counts[row["status"]] += 1
            return {
                "paused": paused,
                "killed": killed,
                "pending": counts["pending"],
                "approved": counts["approved"],
                "actions": rows,
                "log": log,
                "device": self._heartbeat_unlocked(connection),
                "catalog": catalog_public(),
            }

    def _heartbeat_unlocked(self, connection: sqlite3.Connection) -> dict[str, Any] | None:
        row = connection.execute("SELECT at, payload FROM device_heartbeat WHERE slot = 1").fetchone()
        if row is None:
            return None
        data = json.loads(row["payload"])
        data["at"] = row["at"]
        return data

    def _present(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        kind = row["kind"]
        spec = CATALOG.get(kind, {})
        result = json.loads(row["result"]) if row["result"] else None
        return {
            "id": row["id"],
            "kind": kind,
            "payload": payload,
            "reason": row["reason"],
            "status": row["status"],
            "source": row["source"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "approved_at": row["approved_at"],
            "completed_at": row["completed_at"],
            "result": result,
            "label": describe(kind, payload),
            "family": spec.get("family", "other"),
            "runs_on": spec.get("runs_on", "desk"),
            "risk": spec.get("risk", "high"),
        }
