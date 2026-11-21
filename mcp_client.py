"""Minimal MCP client. Tool calls become approval-broker proposals; none auto-run.

Servers must be loopback or private IPv4 HTTP JSON-RPC endpoints. Stdio command
execution is not enabled in this build (untrusted command strings would be a
shell risk).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import urllib.error

from actions import ActionError
from webhook import validate_webhook_url

TIMEOUT = 8
MAX_RESPONSE = 40_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002
        raise ActionError(502, "redirect_blocked", "Redirects are not followed.")


@dataclass
class McpRegistry:
    servers: list[dict[str, Any]] = field(default_factory=list)
    transport: Any = None

    def list(self) -> list[dict[str, Any]]:
        return list(self.servers)

    def add(self, name: str, url: str) -> dict[str, Any]:
        if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}", name.strip()):
            raise ActionError(400, "invalid_name", "Invalid MCP server name.")
        if len(self.servers) >= 8:
            raise ActionError(429, "too_many", "Too many MCP servers.")
        origin = validate_webhook_url(url)
        item = {"id": uuid.uuid4().hex, "name": name.strip(), "url": origin, "tools": [], "ok": False}
        self.servers.append(item)
        return item

    def remove(self, server_id: str) -> None:
        before = len(self.servers)
        self.servers = [s for s in self.servers if s["id"] != server_id]
        if len(self.servers) == before:
            raise ActionError(404, "unknown_server", "Unknown MCP server.")

    def get(self, server_id: str) -> dict[str, Any]:
        for item in self.servers:
            if item["id"] == server_id:
                return item
        raise ActionError(404, "unknown_server", "Unknown MCP server.")

    def _rpc(self, server: dict[str, Any], method: str, params: dict[str, Any] | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        data = json.dumps(payload).encode()
        req = Request(
            server["url"] + "/mcp",
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "User-Agent": "JarvisCompanion/2.2"},
            method="POST",
        )
        # Also try the origin root if tests inject a transport that ignores path.
        opener = self.transport or build_opener(ProxyHandler({}), _NoRedirect)
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            # Fall back to POST on the origin itself (some MCP HTTP adapters).
            if exc.code == 404:
                req = Request(
                    server["url"] + "/",
                    data=data,
                    headers={"Accept": "application/json", "Content-Type": "application/json",
                             "User-Agent": "JarvisCompanion/2.2"},
                    method="POST",
                )
                try:
                    with opener.open(req, timeout=TIMEOUT) as resp:
                        raw = resp.read(MAX_RESPONSE + 1)
                except urllib.error.URLError as err:
                    raise ActionError(502, "mcp_unreachable", "MCP server was unreachable.") from err
            else:
                raise ActionError(502, "mcp_rejected", "MCP server rejected the request.") from exc
        except urllib.error.URLError as exc:
            raise ActionError(502, "mcp_unreachable", "MCP server was unreachable.") from exc
        if len(raw) > MAX_RESPONSE:
            raise ActionError(502, "mcp_rejected", "MCP response is too large.")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ActionError(502, "mcp_rejected", "MCP server returned invalid JSON.") from exc
        if type(body) is not dict:
            raise ActionError(502, "mcp_rejected", "MCP server returned invalid JSON.")
        if body.get("error"):
            raise ActionError(502, "mcp_rejected", "MCP server returned an error.")
        return body.get("result")

    def refresh(self, server_id: str) -> dict[str, Any]:
        server = self.get(server_id)
        self._rpc(server, "initialize", {"protocolVersion": "2024-11-05", "clientInfo": {"name": "jarvis-companion", "version": "2.2.0"}})
        result = self._rpc(server, "tools/list", {})
        tools = []
        if type(result) is dict and type(result.get("tools")) is list:
            for item in result["tools"][:32]:
                if type(item) is dict and type(item.get("name")) is str:
                    tools.append({
                        "name": item["name"][:80],
                        "description": item["description"][:200] if type(item.get("description")) is str else "",
                    })
        server["tools"] = tools
        server["ok"] = True
        return server

    def call(self, server_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Called only from the approval executor after the owner approved."""
        server = self.get(server_id)
        result = self._rpc(server, "tools/call", {"name": tool, "arguments": arguments or {}})
        preview = json.dumps(result, ensure_ascii=True, default=str)[:1000]
        return {"server": server["name"], "tool": tool, "result": preview}
