"""Private-by-scope, file-backed memory for workspace-owned records only.

Callers must authenticate and authorize the workspace/project IDs before using
this store. IDs scope queries; they are not authentication secrets. This
module does not import, transmit, fetch links, or execute any recorded text.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

KINDS = frozenset({"task", "run", "evidence", "lesson", "decision", "artifact"})
RELATIONS = frozenset({
    "depends_on", "supports", "related_to", "derived_from",
    "version_of", "authored_by", "tested_by",
})
MAX_TASK_DEPTH = 24
_ID = re.compile(r"[0-9a-f]{32}")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS wm_workspaces (
    id TEXT NOT NULL PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wm_projects (
    workspace_id TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id) REFERENCES wm_workspaces(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS wm_records (
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('task','run','evidence','lesson','decision','artifact')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    parent_id TEXT,
    depth INTEGER NOT NULL CHECK(depth BETWEEN 0 AND 24),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, project_id, id),
    FOREIGN KEY (workspace_id, project_id)
        REFERENCES wm_projects(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, project_id, parent_id)
        REFERENCES wm_records(workspace_id, project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS wm_records_scope_parent
    ON wm_records(workspace_id, project_id, parent_id);
CREATE INDEX IF NOT EXISTS wm_records_scope_recent
    ON wm_records(workspace_id, project_id, updated_at DESC);
CREATE TRIGGER IF NOT EXISTS wm_immutable_record_scope
    BEFORE UPDATE OF workspace_id, project_id, id, kind, parent_id, depth, created_at
    ON wm_records
    BEGIN SELECT RAISE(ABORT, 'immutable record metadata'); END;
CREATE TABLE IF NOT EXISTS wm_links (
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN (
        'depends_on','supports','related_to','derived_from','version_of','authored_by','tested_by'
    )),
    created_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, project_id, source_id, target_id, relation),
    CHECK(source_id <> target_id),
    FOREIGN KEY (workspace_id, project_id, source_id)
        REFERENCES wm_records(workspace_id, project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, project_id, target_id)
        REFERENCES wm_records(workspace_id, project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS wm_links_scope_target
    ON wm_links(workspace_id, project_id, target_id);
"""
_PUBLIC_FIELDS = (
    "workspace_id, project_id, id, kind, title, body, parent_id, version, created_at, updated_at"
)


class WorkspaceMemoryError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise WorkspaceMemoryError("not_found")
    return value


def _text(value, cap, *, allow_empty=False, multiline=False):
    if type(value) is not str or len(value) > cap:
        raise WorkspaceMemoryError("invalid_text")
    allowed_control = "\n\r\t" if multiline else ""
    if any(not character.isprintable() and character not in allowed_control for character in value):
        raise WorkspaceMemoryError("invalid_text")
    value = value.strip()
    if not value and not allow_empty:
        raise WorkspaceMemoryError("invalid_text")
    return value


def _kind(value):
    if type(value) is not str or value not in KINDS:
        raise WorkspaceMemoryError("invalid_kind")
    return value


class WorkspaceMemory:
    """Project-scoped store; project records are plain text, never instructions."""

    def __init__(self, db_path):
        if not db_path or str(db_path) == ":memory:":
            raise ValueError("A file-backed database is required")
        self._path = Path(db_path).resolve()
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.create_function("wm_casefold", 1, str.casefold, deterministic=True)
        return db

    @contextmanager
    def _transaction(self, *, write=False):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _workspace(db, workspace_id):
        row = db.execute(
            "SELECT * FROM wm_workspaces WHERE id = ?", (_id(workspace_id),)
        ).fetchone()
        if row is None:
            raise WorkspaceMemoryError("not_found")
        return row

    @staticmethod
    def _project(db, workspace_id, project_id):
        row = db.execute(
            "SELECT * FROM wm_projects WHERE workspace_id = ? AND id = ?",
            (_id(workspace_id), _id(project_id)),
        ).fetchone()
        if row is None:
            raise WorkspaceMemoryError("not_found")
        return row

    @staticmethod
    def _record(db, workspace_id, project_id, record_id):
        row = db.execute(
            f"SELECT {_PUBLIC_FIELDS}, depth FROM wm_records "
            "WHERE workspace_id = ? AND project_id = ? AND id = ?",
            (_id(workspace_id), _id(project_id), _id(record_id)),
        ).fetchone()
        if row is None:
            raise WorkspaceMemoryError("not_found")
        return row

    @staticmethod
    def _public_record(row):
        return {key: row[key] for key in row.keys() if key != "depth"}

    def create_workspace(self, title):
        title = _text(title, 160)
        new_id, now = uuid.uuid4().hex, time.time()
        with self._transaction(write=True) as db:
            db.execute(
                "INSERT INTO wm_workspaces(id, title, created_at) VALUES(?, ?, ?)",
                (new_id, title, now),
            )
            return dict(self._workspace(db, new_id))

    def list_workspaces(self, *, limit=50):
        """Newest-first workspaces. Callers must already be the authenticated owner."""
        limit = self._limit(limit)
        with self._transaction() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM wm_workspaces ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            )]

    def list_projects(self, workspace_id, *, limit=50):
        limit = self._limit(limit)
        with self._transaction() as db:
            self._workspace(db, workspace_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM wm_projects WHERE workspace_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (workspace_id, limit),
            )]

    def create_project(self, workspace_id, title):
        title = _text(title, 160)
        new_id, now = uuid.uuid4().hex, time.time()
        with self._transaction(write=True) as db:
            self._workspace(db, workspace_id)
            db.execute(
                "INSERT INTO wm_projects(workspace_id, id, title, created_at) VALUES(?, ?, ?, ?)",
                (workspace_id, new_id, title, now),
            )
            return dict(self._project(db, workspace_id, new_id))

    def create_record(self, workspace_id, project_id, kind, title, body="", parent_id=None):
        kind = _kind(kind)
        title, body = _text(title, 160), _text(body, 16000, allow_empty=True, multiline=True)
        new_id, now, depth = uuid.uuid4().hex, time.time(), 0
        with self._transaction(write=True) as db:
            self._project(db, workspace_id, project_id)
            if parent_id is not None:
                parent = self._record(db, workspace_id, project_id, parent_id)
                if parent["kind"] != "task":
                    raise WorkspaceMemoryError("parent_must_be_task")
                depth = parent["depth"] + 1
                if depth > MAX_TASK_DEPTH:
                    raise WorkspaceMemoryError("max_depth")
            db.execute(
                "INSERT INTO wm_records(workspace_id, project_id, id, kind, title, body, "
                "parent_id, depth, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (workspace_id, project_id, new_id, kind, title, body, parent_id, depth, now, now),
            )
            return self._public_record(self._record(db, workspace_id, project_id, new_id))

    def get_record(self, workspace_id, project_id, record_id):
        with self._transaction() as db:
            return self._public_record(self._record(db, workspace_id, project_id, record_id))

    def update_record(self, workspace_id, project_id, record_id, *, title=None, body=None):
        if title is None and body is None:
            raise WorkspaceMemoryError("no_change")
        if title is not None:
            title = _text(title, 160)
        if body is not None:
            body = _text(body, 16000, allow_empty=True, multiline=True)
        with self._transaction(write=True) as db:
            self._record(db, workspace_id, project_id, record_id)
            db.execute(
                "UPDATE wm_records SET title = coalesce(?, title), body = coalesce(?, body), "
                "updated_at = ?, version = version + 1 "
                "WHERE workspace_id = ? AND project_id = ? AND id = ?",
                (title, body, time.time(), workspace_id, project_id, record_id),
            )
            return self._public_record(self._record(db, workspace_id, project_id, record_id))

    def delete_record(self, workspace_id, project_id, record_id):
        """Delete the scoped record, its nested children, and associated links."""
        with self._transaction(write=True) as db:
            self._record(db, workspace_id, project_id, record_id)
            db.execute(
                "DELETE FROM wm_records WHERE workspace_id = ? AND project_id = ? AND id = ?",
                (workspace_id, project_id, record_id),
            )
        return True

    @staticmethod
    def _limit(value):
        if type(value) is not int or not 1 <= value <= 100:
            raise WorkspaceMemoryError("invalid_limit")
        return value

    def search(self, workspace_id, project_id, query, *, kind=None, limit=50):
        """Literal case-insensitive substring search, never an all-project query."""
        query = _text(query, 320, allow_empty=True).casefold()
        limit = self._limit(limit)
        if kind is not None:
            kind = _kind(kind)
        with self._transaction() as db:
            self._project(db, workspace_id, project_id)
            # Analyze only the chosen project; do not rank globally then filter.
            # Indexed scope keys live in the same WHERE clause as the predicate.
            return [dict(row) for row in db.execute(
                f"SELECT {_PUBLIC_FIELDS} FROM wm_records "
                "WHERE workspace_id = ? AND project_id = ? "
                "AND (? IS NULL OR kind = ?) "
                "AND (? = '' OR instr(wm_casefold(title), ?) > 0 OR instr(wm_casefold(body), ?) > 0) "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (workspace_id, project_id, kind, kind, query, query, query, limit),
            )]

    def link(self, workspace_id, project_id, source_id, target_id, relation):
        """Attach a typed, same-project association, not a signed assertion."""
        if type(relation) is not str or relation not in RELATIONS:
            raise WorkspaceMemoryError("invalid_relation")
        if source_id == target_id:
            raise WorkspaceMemoryError("invalid_relation")
        scope = (workspace_id, project_id, source_id, target_id, relation)
        with self._transaction(write=True) as db:
            self._record(db, workspace_id, project_id, source_id)
            self._record(db, workspace_id, project_id, target_id)
            db.execute(
                "INSERT OR IGNORE INTO wm_links("
                "workspace_id, project_id, source_id, target_id, relation, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?)",
                (*scope, time.time()),
            )
            return dict(db.execute(
                "SELECT * FROM wm_links WHERE workspace_id = ? AND project_id = ? "
                "AND source_id = ? AND target_id = ? AND relation = ?",
                scope,
            ).fetchone())

    def links(self, workspace_id, project_id, record_id, *, limit=50):
        limit = self._limit(limit)
        with self._transaction() as db:
            self._record(db, workspace_id, project_id, record_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM wm_links WHERE workspace_id = ? AND project_id = ? "
                "AND (source_id = ? OR target_id = ?) "
                "ORDER BY created_at DESC, source_id, target_id, relation LIMIT ?",
                (workspace_id, project_id, record_id, record_id, limit),
            )]
