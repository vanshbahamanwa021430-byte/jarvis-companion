"""OpenJarvis brain proxy. The desk talks to a local OpenJarvis server.

Chat is forwarded to OpenJarvis's OpenAI-compatible /v1/chat/completions
endpoint with only the jarvis_request_approval tool advertised. OpenJarvis
returns raw tool_calls for client-supplied tools and does not auto-execute
them. This module queues allowed proposals on the companion broker and
refuses every other tool name. It never executes a sample read itself.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

import ipaddress

LOOPBACK = "127.0.0.1"

APPROVAL_TOOL = "jarvis_request_approval"
MAX_MESSAGE = 4000
MAX_RESPONSE = 80_000
HEALTH_TIMEOUT = 3
ASK_TIMEOUT = 20

# Matches openjarvis_tool.py / the current OpenJarvis ToolSpec parameters.
APPROVAL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": APPROVAL_TOOL,
        "description": (
            "Ask the owner's Jarvis Companion desk to approve a sample read. "
            "Never grants approval or returns the sample content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read_file", "read_calendar"]},
                "target": {"type": "string", "enum": ["demo-note", "demo-event"]},
                "reason": {"type": "string", "maxLength": 240},
            },
            "required": ["action", "target", "reason"],
            "additionalProperties": False,
        },
    },
}



def is_private_ipv4(host: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return False
    if host != ip.compressed:
        return False
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved:
        return False
    return bool(ip.is_private or ip.is_link_local)


class BrainError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def validate_openjarvis_url(raw: Any) -> str | None:
    """Return a normalised http(s) URL, or None to disable the brain.

    Only localhost, 127.0.0.1, or a private IPv4 host is accepted. Userinfo,
    query strings and public names are rejected so the desk cannot be pointed
    at an unexpected remote server.
    """
    if raw is None:
        return None
    if type(raw) is not str:
        raise BrainError("invalid_url")
    text = raw.strip()
    if text == "":
        return None
    if "://" not in text:
        text = "http://" + text
    if " " in text or "\\" in text or "@" in text:
        raise BrainError("invalid_url")
    parsed = urllib.parse.urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise BrainError("invalid_url")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BrainError("invalid_url")
    host = (parsed.hostname or "").lower()
    if host not in ("localhost", LOOPBACK) and not is_private_ipv4(host or ""):
        raise BrainError("invalid_url")
    path = parsed.path or ""
    if path not in ("", "/"):
        raise BrainError("invalid_url")
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 8000
    if not 1 <= port <= 65535:
        raise BrainError("invalid_url")
    # Keep IPv4 literals unbracketed; localhost stays a name.
    return f"{scheme}://{host}:{port}"


def _request(url: str, path: str, *, payload: dict[str, Any] | None = None, timeout: float) -> tuple[int, Any]:
    if validate_openjarvis_url(url) is None:
        raise BrainError("invalid_url")
    target = urllib.parse.urljoin(url.rstrip("/") + "/", path.lstrip("/"))
    parsed = urllib.parse.urlparse(target)
    origin = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    if validate_openjarvis_url(origin) != url.rstrip("/"):
        raise BrainError("invalid_url")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "JarvisCompanion/2.1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(target, data=data, headers=headers, method="GET" if data is None else "POST")
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise BrainError("brain_rejected", 502)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE + 1)
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE + 1)
        status = exc.code
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError):
        raise BrainError("brain_unreachable", 502) from None
    if len(raw) > MAX_RESPONSE:
        raise BrainError("brain_rejected", 502)
    if not raw:
        body: Any = {}
    else:
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BrainError("brain_rejected", 502) from None
    return status, body


def check_health(url: str) -> dict[str, Any]:
    status, body = _request(url, "/health", timeout=HEALTH_TIMEOUT)
    ok = 200 <= status < 300
    return {"ok": ok, "status": status, "engine": isinstance(body, dict) and body.get("engine") or None}


def _extract_message(body: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(body, dict):
        raise BrainError("brain_rejected", 502)
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise BrainError("brain_rejected", 502)
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise BrainError("brain_rejected", 502)
    content = message.get("content") or ""
    if content is not None and type(content) is not str:
        raise BrainError("brain_rejected", 502)
    tool_calls = message.get("tool_calls") or []
    if tool_calls is None:
        tool_calls = []
    if type(tool_calls) is not list:
        raise BrainError("brain_rejected", 502)
    return content or "", tool_calls


def _tool_name_and_args(call: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(call, dict):
        raise BrainError("brain_rejected", 502)
    fn = call.get("function") or {}
    if not isinstance(fn, dict):
        raise BrainError("brain_rejected", 502)
    name = fn.get("name") or ""
    if type(name) is not str:
        raise BrainError("brain_rejected", 502)
    raw_args = fn.get("arguments", "{}")
    if isinstance(raw_args, dict):
        args = raw_args
    elif type(raw_args) is str:
        try:
            args = json.loads(raw_args) if raw_args else {}
        except ValueError:
            raise BrainError("brain_rejected", 502) from None
    else:
        raise BrainError("brain_rejected", 502)
    if not isinstance(args, dict):
        raise BrainError("brain_rejected", 502)
    return name, args


def apply_tool_calls(
    tool_calls: list[dict[str, Any]],
    propose: Callable[[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Queue allowed proposals. Never executes a read. Unknown tools are refused."""
    results: list[dict[str, Any]] = []
    for call in tool_calls:
        name, args = _tool_name_and_args(call)
        if name != APPROVAL_TOOL:
            results.append({"tool": name, "ok": False, "error": "tool_blocked"})
            continue
        action, target, reason = args.get("action"), args.get("target"), args.get("reason", "")
        if not all(type(v) is str for v in (action, target, reason)):
            results.append({"tool": name, "ok": False, "error": "invalid_proposal"})
            continue
        try:
            receipt = propose(action, target, reason)
        except Exception:
            results.append({"tool": name, "ok": False, "error": "invalid_proposal"})
            continue
        results.append({"tool": name, "ok": True, "id": receipt.get("id"), "status": receipt.get("status", "pending")})
    return results


def ask(url: str, message: str, propose: Callable[[str, str, str], dict[str, Any]]) -> dict[str, Any]:
    if type(message) is not str:
        raise BrainError("invalid_text")
    text = message.strip()
    if not text or len(text) > MAX_MESSAGE:
        raise BrainError("invalid_text")
    status, body = _request(url, "/v1/chat/completions", payload={
        "model": "openjarvis",
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "tools": [APPROVAL_TOOL_SCHEMA],
    }, timeout=ASK_TIMEOUT)
    if not 200 <= status < 300:
        raise BrainError("brain_rejected", 502)
    content, tool_calls = _extract_message(body)
    proposals = apply_tool_calls(tool_calls, propose)
    blocked = [item for item in proposals if item.get("error") == "tool_blocked"]
    queued = [item for item in proposals if item.get("ok")]
    return {
        "reply": content,
        "proposals": queued,
        "blocked": blocked,
    }
