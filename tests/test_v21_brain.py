"""OpenJarvis brain: URL policy, proxy, and no auto-execution of tools."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from brain import (
    APPROVAL_TOOL, BrainError, apply_tool_calls, ask, check_health, validate_openjarvis_url,
)
from config import ConfigError, load_config
from v2_helpers import LiveServer


class UrlValidationTests(unittest.TestCase):
    def test_empty_disables(self):
        self.assertIsNone(validate_openjarvis_url(None))
        self.assertIsNone(validate_openjarvis_url(""))
        self.assertIsNone(validate_openjarvis_url("  "))

    def test_loopback_and_private_normalised(self):
        self.assertEqual(validate_openjarvis_url("http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(validate_openjarvis_url("127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertEqual(validate_openjarvis_url("localhost"), "http://localhost:8000")
        self.assertEqual(validate_openjarvis_url("http://localhost"), "http://localhost:8000")
        self.assertEqual(validate_openjarvis_url("http://192.168.1.10:9000/"), "http://192.168.1.10:9000")
        self.assertEqual(validate_openjarvis_url("https://10.0.0.5"), "https://10.0.0.5:443")

    def test_public_and_junk_rejected(self):
        for raw in ("http://example.com", "http://8.8.8.8", "ftp://127.0.0.1",
                    "http://user:pass@127.0.0.1:8000", "http://127.0.0.1:8000/secret",
                    "http://127.0.0.1:8000?x=1", "javascript:alert(1)", "http://evil.local",
                    8000, "http://0.0.0.0:8000"):
            with self.subTest(raw=raw), self.assertRaises(BrainError):
                validate_openjarvis_url(raw)

    def test_config_file_and_cli(self):
        self.assertIsNone(load_config([]).openjarvis_url)
        self.assertEqual(load_config(["--openjarvis-url", "http://127.0.0.1:8000"]).openjarvis_url,
                         "http://127.0.0.1:8000")
        with self.assertRaises(ConfigError):
            load_config(["--openjarvis-url", "http://example.com"])


class _MockOpenJarvis(ThreadingHTTPServer):
    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        super().__init__(("127.0.0.1", 0), _MockHandler)


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.server.requests.append((method, self.path, body, dict(self.headers)))
        status, payload = self.server.responses.get((method, self.path), (404, {"error": "missing"}))
        if callable(payload):
            status, payload = payload(body)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_mock(responses):
    server = _MockOpenJarvis(responses)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    return server, thread, url


class BrainClientTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "mock", None):
            self.mock.shutdown()
            self.mock.server_close()
            self.thread.join(timeout=3)

    def start(self, responses):
        self.mock, self.thread, self.url = _start_mock(responses)
        return self.url

    def test_health(self):
        self.start({("GET", "/health"): (200, {"status": "ok", "engine": "ollama"})})
        self.assertEqual(check_health(self.url), {"ok": True, "status": 200, "engine": "ollama"})

    def test_health_down(self):
        self.start({("GET", "/health"): (503, {"detail": "Engine unhealthy"})})
        self.assertFalse(check_health(self.url)["ok"])

    def test_ask_returns_text_and_sends_only_approval_tool(self):
        self.start({
            ("POST", "/v1/chat/completions"): (200, {
                "choices": [{"message": {"role": "assistant", "content": "Hello from the lab."}}],
            }),
        })
        result = ask(self.url, "Hello", lambda *a: self.fail("should not propose"))
        self.assertEqual(result["reply"], "Hello from the lab.")
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["blocked"], [])
        method, path, body, _ = self.mock.requests[-1]
        self.assertEqual((method, path), ("POST", "/v1/chat/completions"))
        names = [t["function"]["name"] for t in body["tools"]]
        self.assertEqual(names, [APPROVAL_TOOL])
        self.assertFalse(body["stream"])

    def test_approval_tool_is_queued_not_executed(self):
        queued = []

        def propose(action, target, reason):
            queued.append((action, target, reason))
            return {"id": "a" * 32, "status": "pending"}

        def payload(_body):
            return 200, {"choices": [{"message": {"role": "assistant", "content": "I need approval.",
                "tool_calls": [{"function": {"name": APPROVAL_TOOL,
                    "arguments": json.dumps({"action": "read_file", "target": "demo-note", "reason": "demo"})}}]}}]}

        self.start({("POST", "/v1/chat/completions"): (200, payload)})
        result = ask(self.url, "Read the sample note", propose)
        self.assertEqual(queued, [("read_file", "demo-note", "demo")])
        self.assertEqual(result["proposals"], [{"tool": APPROVAL_TOOL, "ok": True, "id": "a" * 32, "status": "pending"}])

    def test_other_tools_are_blocked(self):
        def propose(*_a):
            self.fail("must not propose unknown tools")

        def payload(_body):
            return 200, {"choices": [{"message": {"role": "assistant", "content": "",
                "tool_calls": [{"function": {"name": "file_read", "arguments": "{\"path\": \"/etc/passwd\"}"}}]}}]}

        self.start({("POST", "/v1/chat/completions"): (200, payload)})
        result = ask(self.url, "read a file", propose)
        self.assertEqual(result["blocked"], [{"tool": "file_read", "ok": False, "error": "tool_blocked"}])
        self.assertEqual(result["proposals"], [])

    def test_apply_tool_calls_refuses_unknown_without_calling_propose(self):
        called = []
        out = apply_tool_calls(
            [{"function": {"name": "shell", "arguments": "{\"cmd\": \"ls\"}"}}],
            lambda *a: called.append(a) or {"id": "x", "status": "pending"},
        )
        self.assertEqual(called, [])
        self.assertEqual(out[0]["error"], "tool_blocked")


class BrainHttpTests(unittest.TestCase):
    def setUp(self):
        self.s = LiveServer()
        self.mock = None

    def tearDown(self):
        if self.mock:
            self.mock.shutdown()
            self.mock.server_close()
            self.thread.join(timeout=3)
        self.s.close()

    def start_mock(self, responses):
        self.mock, self.thread, url = _start_mock(responses)
        return url

    def test_disabled_until_configured(self):
        self.s.login()
        status, body, _ = self.s.request("POST", "/api/brain/ask", {"message": "hi"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "brain_disabled")
        _, state, _ = self.s.request("GET", "/api/state")
        self.assertFalse(state["brain"])
        self.assertEqual(state["openjarvis_url"], "")

    def test_public_url_rejected_and_ask_queues_proposal(self):
        self.s.login()
        status, body, _ = self.s.request("POST", "/api/brain/config", {"url": "http://example.com"})
        self.assertEqual(status, 400)
        url = self.start_mock({
            ("GET", "/health"): (200, {"status": "ok", "engine": "mock"}),
            ("POST", "/v1/chat/completions"): (200, {
                "choices": [{"message": {"role": "assistant", "content": "Need a look.",
                    "tool_calls": [{"function": {"name": APPROVAL_TOOL,
                        "arguments": {"action": "read_calendar", "target": "demo-event", "reason": "check"}}}]}}]
            }),
        })
        status, body, _ = self.s.request("POST", "/api/brain/config", {"url": url})
        self.assertEqual(status, 200)
        self.assertTrue(body["enabled"])
        status, health, _ = self.s.request("GET", "/api/brain/status")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        status, result, _ = self.s.request("POST", "/api/brain/ask", {"message": "What's on the sample calendar?"})
        self.assertEqual(status, 200)
        self.assertEqual(result["reply"], "Need a look.")
        self.assertEqual(len(result["proposals"]), 1)
        request_id = result["proposals"][0]["id"]
        _, snapshot, _ = self.s.request("GET", "/api/state")
        matching = next(item for item in snapshot["requests"] if item["id"] == request_id)
        self.assertEqual(matching["status"], "pending")
        self.assertIsNone(matching.get("result"))
        # Auto-execution must not have happened; execute without approval must fail.
        self.assertNotEqual(self.s.request("POST", "/api/execute", {"id": request_id})[0], 200)

    def test_owner_required(self):
        self.assertEqual(self.s.request("POST", "/api/brain/config", {"url": "http://127.0.0.1:8000"})[0], 401)
        self.assertEqual(self.s.request("POST", "/api/brain/ask", {"message": "hi"})[0], 401)


if __name__ == "__main__":
    unittest.main()
