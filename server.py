"""Single-user approval desk. Binds IPv4 loopback by default.

Pass --allow-lan (or set allow_lan in the config file) to bind every interface
so a phone on the same trusted LAN can open the dashboard. That mode prints a
warning. Do not use it on an untrusted network, and never expose this server
to the internet.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from actions import ActionError, ActionsBroker
from brain import BrainError, ask as brain_ask, check_health, validate_openjarvis_url
from broker import Broker, BrokerError, proposal_catalog
from config import ConfigError, load_config
from google_oauth import GoogleConnector
from jlog import get_logger, setup_logging
from mcp_client import McpRegistry
from memory_api import MemoryRequestError, handle_get as memory_get, handle_post as memory_post, kinds
from plugins import PluginRegistry
from proposal_input import parse_proposal
from voice import VoiceHub
from webhook import WebhookRegistry
from workspace_memory import WorkspaceMemory

VERSION = "2.2.1"
STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/img/jarvis-theme.webp": ("img/jarvis-theme.webp", "image/webp"),
}
COOKIE = "jarvis_owner"
LOOPBACK = "127.0.0.1"
MAX_OWNER_BODY = 20_000  # memory record bodies may be up to 16,000 characters
MAX_AGENT_BODY = 2048
MAX_QUERY_FIELDS = 8
MAX_WORKER_FAILURES = 5
AGENT_RATE_LIMIT = 5  # proposals per rolling minute
_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
log = get_logger()

LAN_BIND = "0.0.0.0"
LAN_WARNING = (
    "WARNING: LAN binding is on. The approval desk is reachable on your local network. "
    "Use this only on a trusted home network. Anyone on that network who has the owner "
    "unlock key can approve actions. The default remains loopback-only."
)


def is_private_ipv4(host: str) -> bool:
    """True for RFC1918 and link-local IPv4 addresses, not loopback or 0.0.0.0."""
    try:
        ip = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return False
    if host != ip.compressed:
        return False
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_reserved:
        return False
    return bool(ip.is_private or ip.is_link_local)


def host_header_name(header: str, server_port: int) -> str | None:
    """Return the Host name if the header targets this server port, else None."""
    if type(header) is not str or not header:
        return None
    suffix = f":{server_port}"
    if header.endswith(suffix):
        name = header[: -len(suffix)]
        return name or None
    if server_port == 80 and ":" not in header:
        return header
    return None


def valid_host_header(header: str, server_port: int, allow_lan: bool) -> bool:
    name = host_header_name(header, server_port)
    if name in ("localhost", LOOPBACK):
        return True
    return bool(allow_lan and name is not None and is_private_ipv4(name))


def local_private_ipv4s() -> list[str]:
    found: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 1))
            ip = sock.getsockname()[0]
            if is_private_ipv4(ip):
                found.add(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if is_private_ipv4(ip):
                found.add(ip)
    except OSError:
        pass
    return sorted(found)




class RequestRejected(Exception):
    """A client error detected while parsing input (fixed code, no echo)."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RequestRejected(400, "invalid_json")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise RequestRejected(400, "invalid_json")


def parse_owner_json(raw: bytes) -> Any:
    """Strict JSON: UTF-8 only, no duplicate keys, no NaN/Infinity."""
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_strict_object,
                          parse_constant=_reject_constant)
    except RequestRejected:
        raise
    except (ValueError, RecursionError):
        raise RequestRejected(400, "invalid_json") from None


def parse_query(query: str) -> dict[str, str]:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query),
                          max_num_fields=MAX_QUERY_FIELDS)
    except ValueError:
        raise RequestRejected(400, "invalid_query") from None
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result or len(value) > 320:
            raise RequestRejected(400, "invalid_query")
        result[key] = value
    return result


def _int_param(params: Mapping[str, str], name: str, default: int | None) -> int | None:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    if not raw.isascii() or not raw.isdigit() or len(raw) > 12:
        raise RequestRejected(400, "invalid_query")
    return int(raw)


class App(ThreadingHTTPServer):
    # Wait for in-flight requests on close (each is bounded by a socket timeout).
    daemon_threads = False
    block_on_close = True

    def __init__(
        self,
        address: tuple[str, int],
        broker: Broker,
        key: str,
        *,
        auto: bool = False,
        agent_key: str | None = None,
        memory: WorkspaceMemory | None = None,
        session_seconds: int = 3600,
        worker_interval: float = .35,
        allow_lan: bool = False,
        openjarvis_url: str | None = None,
        actions: ActionsBroker | None = None,
        plugins: PluginRegistry | None = None,
        webhooks: WebhookRegistry | None = None,
        mcp: McpRegistry | None = None,
        google: GoogleConnector | None = None,
        voice: VoiceHub | None = None,
    ):
        if allow_lan:
            if address[0] != LAN_BIND:
                raise ValueError("LAN mode binds to 0.0.0.0 only.")
        elif address[0] != LOOPBACK:
            raise ValueError("This demo accepts IPv4 loopback only.")
        if type(key) is not str or len(key) < 8:
            raise ValueError("Owner key must be a string of at least 8 characters.")
        if agent_key is not None and (
            type(agent_key) is not str or len(agent_key) < 32
            or not _KEY_PATTERN.fullmatch(agent_key) or agent_key == key
        ):
            raise ValueError("Agent key must be independent and have at least 32 URL-safe characters.")
        if type(session_seconds) is not int or not 60 <= session_seconds <= 86400:
            raise ValueError("session_seconds must be between 60 and 86400.")
        # Set before binding: TCPServer calls server_close() if bind fails
        # (milestone 3 then crashed with AttributeError on a busy port).
        self.stop_worker = threading.Event()
        self.worker: threading.Thread | None = None
        self.worker_state = "off"
        super().__init__(address, Handler)
        self.broker, self.key, self.memory = broker, key, memory
        self.allow_lan = bool(allow_lan)
        self.openjarvis_url = openjarvis_url
        demo_dir = Path(broker._db_path).parent
        self.actions = actions or ActionsBroker(demo_dir / "actions.sqlite3",
                                                ttl_seconds=broker.ttl_seconds)
        self.plugins = plugins or PluginRegistry(Path(__file__).resolve().parent / "plugins")
        self.webhooks = webhooks or WebhookRegistry()
        self.mcp = mcp or McpRegistry()
        self.google = google or GoogleConnector(demo_dir / "google-tokens.json")
        self.voice = voice or VoiceHub(self.actions, clock=time.time)
        self.actions.executor = self._execute_desk
        self.lock = threading.Lock()
        self.agent_key = agent_key
        self.auto = bool(auto)
        self.session_seconds = session_seconds
        self.worker_interval = float(worker_interval)
        self.last_agent_proposals: list[float] = []
        self.session = ""
        self.session_expires = 0.0
        self.failures = 0
        self.retry_after = 0.0
        self.started = time.monotonic()
        if auto:
            self.worker_state = "running"
            self.worker = threading.Thread(target=self.run_worker, name="jarvis-worker", daemon=True)
            self.worker.start()

    def run_worker(self) -> None:
        """Execute approved demo jobs. Each execute re-checks approval atomically."""
        failures = 0
        delay = self.worker_interval
        while not self.stop_worker.wait(delay):
            try:
                for request_id in self.broker.approved_ids():
                    if self.stop_worker.is_set():
                        break
                    try:
                        self.broker.execute(request_id)
                        log.info("job_completed", extra={"request_id": request_id})
                    except BrokerError:
                        pass  # Expired, already completed, revoked, or paused meanwhile.
                for request_id in self.actions.approved_desk_ids():
                    if self.stop_worker.is_set():
                        break
                    try:
                        self.actions.execute(request_id)
                        log.info("action_completed", extra={"request_id": request_id})
                    except ActionError:
                        pass
                failures, delay = 0, self.worker_interval
            except Exception as exc:  # noqa: BLE001 - must never kill silently
                failures += 1
                # Do not log payloads, results, tokens, or untrusted data.
                log.warning("worker_error", extra={"error": type(exc).__name__, "consecutive": failures})
                if failures >= MAX_WORKER_FAILURES:
                    self.worker_state = "stopped"
                    log.error("worker_stopped", extra={"reason": "repeated_errors"})
                    return  # Fail closed; manual execution and restart remain available.
                delay = min(self.worker_interval * 2 ** failures, 5.0)
        if self.worker_state == "running":
            self.worker_state = "stopped"

    def _execute_desk(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind.startswith("google."):
            return self.google.execute(kind, payload)
        if kind == "webhook.http":
            return self.webhooks.send(payload["connector_id"], payload["method"],
                                      payload.get("path") or "/", payload.get("body") or "")
        if kind == "mcp.call":
            return self.mcp.call(payload["server_id"], payload["tool"], payload.get("arguments") or {})
        if kind == "plugin.run":
            return self.plugins.run(payload["plugin"], payload["action"], payload.get("params") or {}, {
                "memory": self.memory,
                "webhook": lambda params: self.webhooks.send(
                    params.get("connector_id"), params.get("method") or "GET",
                    params.get("path") or "/", params.get("body") or ""),
            })
        raise ActionError(400, "unknown_action", "No desk executor for this kind.")

    def runtime_info(self) -> dict[str, Any]:
        with self.lock:
            remaining = max(0, int(self.session_expires - time.monotonic())) if self.session else 0
        flags = self.actions.flags()
        return {
            "auto": self.auto,
            "worker": self.worker_state,
            "memory": self.memory is not None,
            "session_expires_in": remaining,
            "version": VERSION,
            "brain": bool(self.openjarvis_url),
            "openjarvis_url": self.openjarvis_url or "",
            "killed": flags["killed"],
            "voice": self.voice.status(),
            "google": self.google.status(),
        }

    def server_close(self) -> None:
        self.stop_worker.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=5)
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: App
    server_version = "JarvisCompanionDemo"
    sys_version = ""

    def setup(self) -> None:
        self.request.settimeout(4)
        super().setup()
        self._started = time.monotonic()
        self._replied = False

    def log_message(self, *_: Any) -> None:
        return  # Replaced by structured access logging in send_payload.

    # ----------------------------------------------------------------- output
    def send_payload(self, code: int, body: bytes, mime: str = "application/json",
                     cookie: str | None = None) -> None:
        self._replied = True
        self.send_response(code)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                         "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        log.info("http_request", extra={
            "method": self.command, "path": urlsplit(self.path).path[:80], "status": code,
            "ms": round((time.monotonic() - self._started) * 1000, 1),
        })

    def reply(self, code: int, value: Any, cookie: str | None = None) -> None:
        self.send_payload(code, json.dumps(value, ensure_ascii=True).encode(), cookie=cookie)

    def _dispatch(self, handler: Callable[[], None]) -> None:
        try:
            handler()
        except (BrokerError, MemoryRequestError, RequestRejected, ActionError) as err:
            self.reply(err.status, {"error": err.code})
        except Exception as exc:  # noqa: BLE001 - answer instead of dropping the socket
            log.error("request_failed", extra={"error": type(exc).__name__,
                                               "path": urlsplit(self.path).path[:80]})
            if not self._replied:
                self.reply(500, {"error": "internal_error"})

    # ------------------------------------------------------------------ guards
    @property
    def allowed_host(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        return len(hosts) == 1 and valid_host_header(
            hosts[0], self.server.server_port, self.server.allow_lan)

    def owner(self) -> bool:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
            token = cookie.value if cookie else ""
        except CookieError:
            return False
        with self.server.lock:
            return bool(token and self.server.session and
                        time.monotonic() < self.server.session_expires and
                        hmac.compare_digest(token.encode(), self.server.session.encode()))

    def read_body(self, limit: int) -> bytes:
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            raise RequestRejected(415, "json_required")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0]):
            raise RequestRejected(400, "invalid_length")
        length = int(lengths[0])
        if not 1 <= length <= limit:
            raise RequestRejected(413, "invalid_length")
        try:
            raw = self.rfile.read(length)
        except OSError:  # includes socket timeouts on truncated bodies
            raise RequestRejected(400, "invalid_length") from None
        if len(raw) != length:
            raise RequestRejected(400, "invalid_length")
        return raw

    # --------------------------------------------------------------------- GET
    def do_GET(self) -> None:
        self._dispatch(self._get)

    def do_HEAD(self) -> None:
        self._dispatch(self._get)

    def _get(self) -> None:
        if not self.allowed_host:
            return self.reply(403, {"error": "invalid_host"})
        split = urlsplit(self.path)
        path = split.path
        if path in ASSETS:
            name, mime = ASSETS[path]
            try:
                data = (STATIC / name).read_bytes()
            except OSError:
                return self.reply(404, {"error": "not_found"})
            return self.send_payload(200, data, mime)
        if path == "/api/google/callback":
            return self.google_callback(parse_query(split.query))
        if not path.startswith("/api/"):
            return self.reply(404, {"error": "not_found"})
        if not self.owner():
            return self.reply(401, {"error": "owner_required"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply(403, {"error": "invalid_origin"})
        params = parse_query(split.query)
        broker = self.server.broker
        if path == "/api/state":
            action_snap = self.server.actions.snapshot()
            return self.reply(200, {**broker.snapshot(), **self.server.runtime_info(),
                                    "memory_kinds": kinds(),
                                    "action_requests": action_snap["actions"],
                                    "action_log": action_snap["log"],
                                    "action_catalog": action_snap["catalog"],
                                    "device": action_snap["device"],
                                    "plugins": self.server.plugins.list(),
                                    "webhooks": self.server.webhooks.list(),
                                    "mcp": self.server.mcp.list()})
        if path == "/api/status":
            return self.reply(200, {**broker.status(), **self.server.runtime_info()})
        if path == "/api/brain/status":
            url = self.server.openjarvis_url
            if not url:
                return self.reply(200, {"enabled": False, "url": "", "ok": False})
            try:
                health = check_health(url)
            except BrainError as err:
                return self.reply(err.status, {"enabled": True, "url": url, "ok": False, "error": err.code})
            return self.reply(200, {"enabled": True, "url": url, **health})
        if path == "/api/history":
            if not set(params) <= {"status", "action", "limit", "before"}:
                raise RequestRejected(400, "invalid_query")
            return self.reply(200, broker.history(
                status=params.get("status") or None, action=params.get("action") or None,
                limit=_int_param(params, "limit", 20), before=_int_param(params, "before", None),
            ))
        if path == "/api/audit":
            if not set(params) <= {"event", "request", "limit", "before"}:
                raise RequestRejected(400, "invalid_query")
            return self.reply(200, broker.audit_log(
                event=params.get("event") or None, request_id=params.get("request") or None,
                limit=_int_param(params, "limit", 50), before=_int_param(params, "before", None),
            ))
        if path.startswith("/api/memory/"):
            if self.server.memory is None:
                return self.reply(404, {"error": "memory_disabled"})
            status, payload = memory_get(self.server.memory, path[len("/api/memory/"):], params)
            return self.reply(status, payload)
        if path == "/api/actions":
            return self.reply(200, self.server.actions.snapshot())
        if path == "/api/device/pending":
            return self.reply(200, {"killed": self.server.actions.flags()["killed"],
                                    "actions": self.server.actions.pending_for_device(),
                                    "voice": self.server.voice.status()})
        if path == "/api/voice/status":
            return self.reply(200, self.server.voice.status())
        if path == "/api/google/status":
            return self.reply(200, self.server.google.status())
        if path == "/api/plugins":
            return self.reply(200, {"plugins": self.server.plugins.list()})
        if path == "/api/webhooks":
            return self.reply(200, {"connectors": self.server.webhooks.list()})
        if path == "/api/mcp":
            return self.reply(200, {"servers": self.server.mcp.list()})
        return self.reply(404, {"error": "not_found"})

    # -------------------------------------------------------------- agent role
    def agent_proposal(self) -> None:
        if self.server.agent_key is None:
            return self.reply(404, {"error": "agent_disabled"})
        if self.headers.get_all("Origin"):
            return self.reply(403, {"error": "browser_request_rejected"})
        authorization = self.headers.get_all("Authorization", [])
        if len(authorization) != 1 or not hmac.compare_digest(
            authorization[0].encode("latin1", "replace"), ("Bearer " + self.server.agent_key).encode("ascii")
        ):
            return self.reply(401, {"error": "agent_required"})
        raw = self.read_body(MAX_AGENT_BODY)
        try:
            item = parse_proposal(raw, catalog=proposal_catalog())
        except ValueError:
            return self.reply(400, {"error": "invalid_proposal"})
        with self.server.lock:
            now = time.monotonic()
            proposals = self.server.last_agent_proposals
            proposals[:] = [t for t in proposals if t > now - 60]
            limited = len(proposals) >= AGENT_RATE_LIMIT
            if not limited:
                proposals.append(now)
        if limited:
            log.warning("agent_rate_limited")
            return self.reply(429, {"error": "proposal_limit"})
        request = self.server.broker.propose(item.action, item.target, item.reason)
        log.info("agent_proposed", extra={"request_id": request["id"]})
        return self.reply(202, {"id": request["id"], "status": "pending"})

    # -------------------------------------------------------------------- POST
    def do_POST(self) -> None:
        self._dispatch(self._post)

    def _post(self) -> None:
        if not self.allowed_host:
            return self.reply(403, {"error": "invalid_host"})
        path = urlsplit(self.path).path
        if path == "/api/agent/propose":
            return self.agent_proposal()
        # Require an exact same-origin browser request; never grant CORS.
        origin = "http://" + self.headers["Host"]
        if self.headers.get_all("Origin") != [origin]:
            return self.reply(403, {"error": "invalid_origin"})
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
            return self.reply(403, {"error": "invalid_origin"})
        try:
            body = parse_owner_json(self.read_body(MAX_OWNER_BODY))
        except RequestRejected as err:
            # Keep milestone-3 error codes for malformed JSON bodies.
            if err.code == "invalid_json":
                return self.reply(400, {"error": "invalid_json"})
            raise
        if not isinstance(body, dict):
            return self.reply(400, {"error": "expected_object"})
        if path == "/api/login":
            return self.login(body)
        if not self.owner():
            return self.reply(401, {"error": "owner_required"})
        broker = self.server.broker
        if path == "/api/logout" and not body:
            with self.server.lock:
                self.server.session = ""
                self.server.session_expires = 0.0
            log.info("owner_logout")
            return self.reply(200, {"ok": True}, f"{COOKIE}=; Max-Age=0" + self.cookie_attrs)
        if path == "/api/propose" and {"action", "target"} <= body.keys() <= {"action", "target", "reason"}:
            fields = [body.get("action"), body.get("target"), body.get("reason", "")]
            if not all(isinstance(value, str) for value in fields):
                return self.reply(400, {"error": "invalid_action"})
            return self.reply(201, broker.propose(*fields))
        if path == "/api/pause" and set(body) == {"paused"} and type(body["paused"]) is bool:
            result = broker.set_paused(body["paused"])
            self.server.actions.set_paused(body["paused"])
            if body["paused"]:
                self.server.voice.session = type(self.server.voice.session)()
            log.info("pause_changed", extra={"paused": body["paused"]})
            return self.reply(200, result)
        actions = {"/api/approve": broker.approve, "/api/deny": broker.deny, "/api/execute": broker.execute}
        if path in actions and set(body) == {"id"} and isinstance(body["id"], str):
            result = actions[path](body["id"])
            log.info("owner_decision", extra={"action": path.rsplit("/", 1)[1], "request_id": body["id"]})
            return self.reply(200, result)
        if path in actions or path in {"/api/pause", "/api/propose", "/api/logout"}:
            return self.reply(400, {"error": "invalid_fields"})
        if path == "/api/brain/config" and set(body) == {"url"}:
            try:
                normalised = validate_openjarvis_url(body.get("url"))
            except BrainError as err:
                return self.reply(err.status, {"error": err.code})
            with self.server.lock:
                self.server.openjarvis_url = normalised
            log.info("brain_url_changed", extra={"enabled": normalised is not None})
            return self.reply(200, {"enabled": normalised is not None, "url": normalised or ""})
        if path == "/api/brain/ask" and set(body) == {"message"}:
            url = self.server.openjarvis_url
            if not url:
                return self.reply(409, {"error": "brain_disabled"})
            message = body.get("message")
            if type(message) is not str:
                return self.reply(400, {"error": "invalid_text"})

            def _propose(action: str, target: str, reason: str):
                return broker.propose(action, target, reason)

            try:
                result = brain_ask(url, message, _propose)
            except BrainError as err:
                return self.reply(err.status, {"error": err.code})
            log.info("brain_ask", extra={
                "proposals": len(result.get("proposals") or []),
                "blocked": len(result.get("blocked") or []),
            })
            return self.reply(200, result)
        if path.startswith("/api/memory/"):
            if self.server.memory is None:
                return self.reply(404, {"error": "memory_disabled"})
            status, payload = memory_post(self.server.memory, path[len("/api/memory/"):], body)
            return self.reply(status, payload)
        return self._post_v22(path, body)

    def google_callback(self, params: Mapping[str, str]) -> None:
        # OAuth redirect is a top-level GET from the browser; the state is the CSRF token.
        try:
            result = self.server.google.finish({k: params.get(k, "") for k in ("code", "state", "error")})
            message = "Connected. You can close this window and return to the desk."
            ok = True
        except ActionError as err:
            result = {"error": err.code}
            message = "Google sign-in did not finish. Return to the desk and try again."
            ok = False
            log.info("google_oauth_failed", extra={"error": err.code})
        else:
            log.info("google_oauth_connected", extra={"service": result.get("connected")})
        page = (
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Google sign-in</title><link rel='stylesheet' href='/app.css'></head>"
            f"<body class='{"ok" if ok else "err"}'><main class='center-card'>"
            f"<h1>Google sign-in</h1><p>{message}</p></main></body></html>"
        )
        return self.send_payload(200 if ok else 400, page.encode(), mime="text/html")

    def _post_v22(self, path: str, body: dict[str, Any]) -> None:
        actions = self.server.actions
        if path == "/api/kill" and set(body) == {"killed"} and type(body["killed"]) is bool:
            snapshot = actions.set_killed(body["killed"])
            if body["killed"]:
                self.server.broker.set_paused(True)
                self.server.voice.set_muted(True)
                self.server.voice.session = type(self.server.voice.session)()
            log.info("kill_changed", extra={"killed": body["killed"]})
            return self.reply(200, snapshot)
        if path == "/api/actions/propose" and "kind" in body:
            rec = actions.propose(
                body.get("kind"), body.get("payload") or {},
                reason=body.get("reason"), source=body.get("source") or "owner")
            return self.reply(201, rec)
        if path == "/api/actions/approve" and set(body) == {"id"} and type(body["id"]) is str:
            rec = actions.approve(body["id"])
            # If this was the voice waiting card, clear it.
            if self.server.voice.session.waiting_id == body["id"]:
                self.server.voice.session = type(self.server.voice.session)()
            return self.reply(200, rec)
        if path == "/api/actions/deny" and set(body) == {"id"} and type(body["id"]) is str:
            rec = actions.deny(body["id"])
            if self.server.voice.session.waiting_id == body["id"]:
                self.server.voice.session = type(self.server.voice.session)()
            return self.reply(200, rec)
        if path == "/api/device/heartbeat" and type(body) is dict:
            actions.record_heartbeat(body)
            return self.reply(200, {"ok": True, "killed": actions.flags()["killed"]})
        if path == "/api/device/complete" and {"id", "ok"} <= body.keys() and type(body["id"]) is str:
            rec = actions.complete_device(body["id"], bool(body["ok"]), body.get("result") if type(body.get("result")) is dict else None)
            return self.reply(200, rec)
        if path == "/api/voice/utterance" and set(body) <= {"text", "source"} and "text" in body:
            result = self.server.voice.handle(body["text"], source=body.get("source") or "voice")
            return self.reply(200, result)
        if path == "/api/voice/mute" and set(body) == {"muted"} and type(body["muted"]) is bool:
            return self.reply(200, self.server.voice.set_muted(body["muted"]))
        if path == "/api/google/config" and set(body) == {"client_id"}:
            self.server.google.set_client_id(body.get("client_id") or None)
            return self.reply(200, self.server.google.status())
        if path == "/api/google/start" and {"service"} <= body.keys() <= {"service", "access"}:
            redirect = f"http://127.0.0.1:{self.server.server_port}/api/google/callback"
            return self.reply(200, self.server.google.start(
                body.get("service"), redirect, access=body.get("access") or "read"))
        if path == "/api/google/disconnect" and set(body) == {"service"}:
            return self.reply(200, self.server.google.disconnect(body["service"]))
        if path == "/api/plugins/enable" and set(body) == {"id", "enabled"} and type(body["enabled"]) is bool:
            return self.reply(200, self.server.plugins.set_enabled(body["id"], body["enabled"]))
        if path == "/api/webhooks/add" and set(body) == {"name", "url"}:
            return self.reply(201, self.server.webhooks.add(body["name"], body["url"]))
        if path == "/api/webhooks/remove" and set(body) == {"id"}:
            self.server.webhooks.remove(body["id"])
            return self.reply(200, {"ok": True})
        if path == "/api/mcp/add" and set(body) == {"name", "url"}:
            return self.reply(201, self.server.mcp.add(body["name"], body["url"]))
        if path == "/api/mcp/remove" and set(body) == {"id"}:
            self.server.mcp.remove(body["id"])
            return self.reply(200, {"ok": True})
        if path == "/api/mcp/refresh" and set(body) == {"id"}:
            return self.reply(200, self.server.mcp.refresh(body["id"]))
        return self.reply(404, {"error": "not_found"})

    @property
    def cookie_attrs(self) -> str:
        return "; Path=/; HttpOnly; SameSite=Strict"

    def login(self, body: dict[str, Any]) -> None:
        key = body.get("key")
        if set(body) != {"key"} or not isinstance(key, str):
            return self.reply(400, {"error": "invalid_key"})
        server = self.server
        with server.lock:
            now = time.monotonic()
            cookie = None
            if now < server.retry_after:
                outcome, value = 429, {"error": "try_again_later"}
            elif not hmac.compare_digest(key.encode("utf-8", "surrogatepass"), server.key.encode()):
                server.failures = min(server.failures + 1, 6)
                if server.failures >= 3:
                    server.retry_after = now + 2 ** (server.failures - 2)
                outcome, value = 401, {"error": "invalid_key"}
            else:
                session = secrets.token_urlsafe(32)
                server.session, server.session_expires = session, now + server.session_seconds
                server.failures, server.retry_after = 0, 0.0
                outcome, value = 200, {"ok": True}
                cookie = f"{COOKIE}={session}; Max-Age={server.session_seconds}" + self.cookie_attrs
        log.info("owner_login", extra={"outcome": outcome})
        if outcome == 429:
            retry = max(1, int(server.retry_after - time.monotonic()) + 1)
            return self.reply(outcome, {**value, "retry_after": retry}, cookie)
        return self.reply(outcome, value, cookie)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        config = load_config(argv)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    logger = setup_logging(config.log_level, config.log_format)
    os.umask(0o077)
    agent_key = os.environ.get("JARVIS_PROPOSAL_KEY") or None
    try:
        config.db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        broker = Broker(config.db, ttl_seconds=config.ttl_seconds)
        memory = None
        if config.memory_db is not None:
            config.memory_db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            memory = WorkspaceMemory(config.memory_db)
        key = secrets.token_urlsafe(18)
        bind_host = LAN_BIND if config.allow_lan else LOOPBACK
        plugins_dir = config.plugins_dir or (Path(__file__).resolve().parent / "plugins")
        actions = ActionsBroker(config.db.parent / "actions.sqlite3", ttl_seconds=config.ttl_seconds)
        google = GoogleConnector(config.db.parent / "google-tokens.json",
                                 client_id=config.google_client_id)
        app = App((bind_host, config.port), broker, key, auto=not config.manual, agent_key=agent_key,
                  memory=memory, session_seconds=config.session_minutes * 60,
                  worker_interval=config.worker_interval, allow_lan=config.allow_lan,
                  openjarvis_url=config.openjarvis_url, actions=actions,
                  plugins=PluginRegistry(plugins_dir), google=google)
    except (ValueError, OSError, sqlite3.Error) as exc:
        # ValueError messages here never contain key material.
        logger.error("startup_failed", extra={"error": type(exc).__name__, "detail": str(exc)[:200]})
        return 1

    stopping = threading.Event()

    def request_shutdown(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        logger.info("shutdown_requested", extra={"signal": signal.Signals(signum).name})
        threading.Thread(target=app.shutdown, name="jarvis-shutdown", daemon=True).start()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_shutdown)
    with app:
        logger.info("server_started", extra={"port": app.server_port, "agent_endpoint": agent_key is not None,
                                             **config.public()})
        print(f"Local demo: http://127.0.0.1:{app.server_port}/", flush=True)
        print(f"Owner unlock key (keep private): {key}", flush=True)
        if config.allow_lan:
            print(LAN_WARNING, flush=True)
            ips = local_private_ipv4s()
            if ips:
                for ip in ips:
                    print(f"LAN demo: http://{ip}:{app.server_port}/", flush=True)
            else:
                print("LAN demo: no private IPv4 address was detected; "
                      "enter this computer's LAN IP in the Android app.", flush=True)
        app.serve_forever(poll_interval=0.2)
    logger.info("shutdown_complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
