"""Owner-only HTTP adapter for WorkspaceMemory.

Only the authenticated owner session reaches these handlers (the server
checks the session, Host, Origin and Sec-Fetch-Site first). The proposal/agent
key never reaches them. Records are synthetic project notes, rendered as
plain text by the dashboard and never executed or fetched.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from workspace_memory import KINDS, WorkspaceMemory, WorkspaceMemoryError

Reply = tuple[int, dict[str, Any]]


class MemoryRequestError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _limit(params: Mapping[str, str], default: int) -> int:
    raw = params.get("limit")
    if raw is None:
        return default
    if not raw.isdigit() or not 1 <= int(raw) <= 100:
        raise MemoryRequestError(400, "invalid_limit")
    return int(raw)


def _require(body: Mapping[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    keys = set(body)
    if not required <= keys <= required | optional:
        raise MemoryRequestError(400, "invalid_fields")


def _param(params: Mapping[str, str], name: str) -> str:
    value = params.get(name)
    if value is None:
        raise MemoryRequestError(400, "invalid_fields")
    return value


def handle_get(memory: WorkspaceMemory, route: str, params: Mapping[str, str]) -> Reply:
    allowed = {
        "workspaces": {"limit"},
        "projects": {"workspace", "limit"},
        "records": {"workspace", "project", "q", "kind", "limit"},
        "links": {"workspace", "project", "id", "limit"},
    }
    if route not in allowed:
        raise MemoryRequestError(404, "not_found")
    if not set(params) <= allowed[route]:
        raise MemoryRequestError(400, "invalid_query")
    try:
        if route == "workspaces":
            return 200, {"items": memory.list_workspaces(limit=_limit(params, 50))}
        if route == "projects":
            return 200, {"items": memory.list_projects(_param(params, "workspace"), limit=_limit(params, 50))}
        if route == "records":
            kind = params.get("kind") or None
            return 200, {"items": memory.search(
                _param(params, "workspace"), _param(params, "project"), params.get("q", ""),
                kind=kind, limit=_limit(params, 50),
            )}
        return 200, {"items": memory.links(
            _param(params, "workspace"), _param(params, "project"), _param(params, "id"),
            limit=_limit(params, 50),
        )}
    except WorkspaceMemoryError as err:
        raise MemoryRequestError(404 if err.code == "not_found" else 400, err.code) from None


def handle_post(memory: WorkspaceMemory, route: str, body: Mapping[str, Any]) -> Reply:
    def create_workspace() -> Reply:
        _require(body, {"title"})
        return 201, memory.create_workspace(body["title"])

    def create_project() -> Reply:
        _require(body, {"workspace", "title"})
        return 201, memory.create_project(body["workspace"], body["title"])

    def create_record() -> Reply:
        _require(body, {"workspace", "project", "kind", "title"}, {"body", "parent"})
        return 201, memory.create_record(
            body["workspace"], body["project"], body["kind"], body["title"],
            body.get("body", ""), parent_id=body.get("parent"),
        )

    def update_record() -> Reply:
        _require(body, {"workspace", "project", "id"}, {"title", "body"})
        return 200, memory.update_record(
            body["workspace"], body["project"], body["id"],
            title=body.get("title"), body=body.get("body"),
        )

    def delete_record() -> Reply:
        _require(body, {"workspace", "project", "id"})
        memory.delete_record(body["workspace"], body["project"], body["id"])
        return 200, {"ok": True}

    handlers: dict[str, Callable[[], Reply]] = {
        "workspaces": create_workspace,
        "projects": create_project,
        "records": create_record,
        "records/update": update_record,
        "records/delete": delete_record,
    }
    if route not in handlers:
        raise MemoryRequestError(404, "not_found")
    try:
        return handlers[route]()
    except WorkspaceMemoryError as err:
        raise MemoryRequestError(404 if err.code == "not_found" else 400, err.code) from None


_KIND_ORDER = ("task", "lesson", "evidence", "decision", "run", "artifact")


def kinds() -> list[str]:
    """Record kinds in a human-friendly order (most common first)."""
    return [k for k in _KIND_ORDER if k in KINDS] + sorted(KINDS - set(_KIND_ORDER))
