"""Single-user, loopback-only demonstration. Never expose this server to a network."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from broker import Broker, BrokerError, proposal_catalog
from config import ConfigError, load_config
from jlog import get_logger, setup_logging
from memory_api import MemoryRequestError, handle_get as memory_get, handle_post as memory_post, kinds
from proposal_input import parse_proposal
from workspace_memory import WorkspaceMemory

VERSION = "2.1.0"
STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
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
    ):
        if address[0] != LOOPBACK:
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

    def runtime_info(self) -> dict[str, Any]:
        with self.lock:
            remaining = max(0, int(self.session_expires - time.monotonic())) if self.session else 0
        return {
            "auto": self.auto,
            "worker": self.worker_state,
            "memory": self.memory is not None,
            "session_expires_in": remaining,
            "version": VERSION,
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
        except (BrokerError, MemoryRequestError, RequestRejected) as err:
            self.reply(err.status, {"error": err.code})
        except Exception as exc:  # noqa: BLE001 - answer instead of dropping the socket
            log.error("request_failed", extra={"error": type(exc).__name__,
                                               "path": urlsplit(self.path).path[:80]})
            if not self._replied:
                self.reply(500, {"error": "internal_error"})

    # ------------------------------------------------------------------ guards
    @property
    def allowed_host(self) -> bool:
        return self.headers.get("Host", "") in {
            f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"
        } and len(self.headers.get_all("Host", [])) == 1

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
        if not path.startswith("/api/"):
            return self.reply(404, {"error": "not_found"})
        if not self.owner():
            return self.reply(401, {"error": "owner_required"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply(403, {"error": "invalid_origin"})
        params = parse_query(split.query)
        broker = self.server.broker
        if path == "/api/state":
            return self.reply(200, {**broker.snapshot(), **self.server.runtime_info(),
                                    "memory_kinds": kinds()})
        if path == "/api/status":
            return self.reply(200, {**broker.status(), **self.server.runtime_info()})
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
            log.info("pause_changed", extra={"paused": body["paused"]})
            return self.reply(200, result)
        actions = {"/api/approve": broker.approve, "/api/deny": broker.deny, "/api/execute": broker.execute}
        if path in actions and set(body) == {"id"} and isinstance(body["id"], str):
            result = actions[path](body["id"])
            log.info("owner_decision", extra={"action": path.rsplit("/", 1)[1], "request_id": body["id"]})
            return self.reply(200, result)
        if path in actions or path in {"/api/pause", "/api/propose", "/api/logout"}:
            return self.reply(400, {"error": "invalid_fields"})
        if path.startswith("/api/memory/"):
            if self.server.memory is None:
                return self.reply(404, {"error": "memory_disabled"})
            status, payload = memory_post(self.server.memory, path[len("/api/memory/"):], body)
            return self.reply(status, payload)
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
        app = App((LOOPBACK, config.port), broker, key, auto=not config.manual, agent_key=agent_key,
                  memory=memory, session_seconds=config.session_minutes * 60,
                  worker_interval=config.worker_interval)
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
        app.serve_forever(poll_interval=0.2)
    logger.info("shutdown_complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
