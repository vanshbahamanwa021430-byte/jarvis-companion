"""v2 HTTP API: live status, history/audit endpoints, strict input, error handling."""
import json
import time
import unittest

from broker import BrokerError
from v2_helpers import AGENT_KEY, LiveServer


class V2HttpTests(unittest.TestCase):
    def setUp(self):
        self.s = LiveServer(agent=True)
        self.addCleanup(self.s.close)

    def test_new_read_endpoints_require_owner(self):
        for path in ("/api/status", "/api/history", "/api/audit", "/api/memory/workspaces"):
            with self.subTest(path=path):
                self.assertEqual(self.s.request("GET", path)[0], 401)
                status, _, _ = self.s.raw("GET", path, headers={"Authorization": "Bearer " + AGENT_KEY})
                self.assertEqual(status, 401)

    def test_status_endpoint_reports_revision_and_runtime(self):
        self.s.login()
        status, body, _ = self.s.request("GET", "/api/status")
        self.assertEqual(status, 200)
        for key in ("revision", "paused", "pending", "approved", "server_time", "worker", "auto",
                    "memory", "session_expires_in", "version"):
            self.assertIn(key, body)
        self.assertEqual(body["worker"], "off")
        self.assertTrue(body["memory"])
        self.assertGreater(body["session_expires_in"], 3500)
        before = body["revision"]
        self.s.propose()
        after = self.s.request("GET", "/api/status")[1]
        self.assertGreater(after["revision"], before)
        self.assertEqual(after["pending"], 1)

    def test_history_and_audit_endpoints_paginate_and_filter(self):
        self.s.login()
        ids = [self.s.propose() for _ in range(5)]
        self.s.request("POST", "/api/deny", {"id": ids[0]})
        status, page, _ = self.s.request("GET", "/api/history?limit=2")
        self.assertEqual(status, 200, page)
        self.assertEqual([i["id"] for i in page["items"]], [ids[4], ids[3]])
        self.assertEqual(page["total"], 5)
        page2 = self.s.request("GET", f"/api/history?limit=2&before={page['next_cursor']}")[1]
        self.assertEqual([i["id"] for i in page2["items"]], [ids[2], ids[1]])
        denied = self.s.request("GET", "/api/history?status=denied&action=read_file")[1]
        self.assertEqual([i["id"] for i in denied["items"]], [ids[0]])
        audit = self.s.request("GET", "/api/audit?event=denied")[1]
        self.assertEqual([e["request_id"] for e in audit["items"]], [ids[0]])
        empty_filter = self.s.request("GET", "/api/history?status=&action=")[1]
        self.assertEqual(empty_filter["total"], 5)

    def test_bad_queries_are_rejected(self):
        self.s.login()
        for path in ("/api/history?status=hacked", "/api/history?limit=abc", "/api/history?limit=0",
                     "/api/history?limit=1&limit=2", "/api/history?sql=1", "/api/audit?before=-1",
                     "/api/audit?event=x", "/api/history?limit=%EF%BC%91", "/api/history?a=1&b=2&c=3&d=4&e=5&f=6&g=7&h=8&i=9"):
            with self.subTest(path=path):
                status, body, _ = self.s.request("GET", path)
                self.assertEqual(status, 400, body)

    def test_strict_json_rejects_duplicates_and_constants(self):
        self.s.login()
        request_id = self.s.propose()
        headers = {"Host": self.s.host, "Origin": "http://" + self.s.host, "Cookie": self.s.cookie,
                   "Content-Type": "application/json"}
        for raw in (b'{"paused": true, "paused": false}', b'{"id": NaN}', b'{"id": "' + request_id.encode() + b'", "id": "x"}',
                    b'\xff\xfe{}'):
            with self.subTest(raw=raw):
                status, body, _ = self.s.raw("POST", "/api/pause", raw, headers)
                self.assertEqual(status, 400, body)
                self.assertEqual(body["error"], "invalid_json")
        self.assertFalse(self.s.request("GET", "/api/state")[1]["paused"])

    def test_cross_site_fetch_metadata_is_rejected_on_post(self):
        self.s.login()
        status, body, _ = self.s.request("POST", "/api/pause", {"paused": True}, **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403, body)
        status, _, _ = self.s.request("POST", "/api/pause", {"paused": True}, **{"Sec-Fetch-Site": "same-site"})
        self.assertEqual(status, 403)
        self.assertFalse(self.s.request("GET", "/api/state")[1]["paused"])
        status, _, _ = self.s.request("POST", "/api/pause", {"paused": True}, **{"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)

    def test_oversized_and_mismatched_lengths(self):
        self.s.login()
        headers = {"Host": self.s.host, "Origin": "http://" + self.s.host, "Cookie": self.s.cookie,
                   "Content-Type": "application/json", "Content-Length": "999999"}
        status, body, _ = self.s.raw("POST", "/api/pause", None, headers)
        self.assertEqual(status, 413, body)

    def test_unexpected_errors_return_json_500_instead_of_dropping_connection(self):
        self.s.login()

        def boom():
            raise RuntimeError("simulated database failure")

        self.s.broker.snapshot = boom
        status, body, _ = self.s.request("GET", "/api/state")
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "internal_error"})
        self.assertNotIn("simulated", json.dumps(body))
        # Server keeps serving afterwards.
        self.assertEqual(self.s.request("GET", "/api/status")[0], 200)

    def test_static_assets_with_query_strings_head_and_icon(self):
        for path in ("/", "/?v=2", "/app.js?cache=1", "/app.css", "/icon.svg"):
            with self.subTest(path=path):
                status, body, headers = self.s.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, body, headers = self.s.request("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertEqual(self.s.request("GET", "/server.py")[0], 404)
        self.assertEqual(self.s.request("GET", "/static/../server.py")[0], 404)

    def test_login_lockout_reports_retry_after(self):
        for _ in range(3):
            self.assertEqual(self.s.request("POST", "/api/login", {"key": "wrong-key-x"})[0], 401)
        status, body, _ = self.s.request("POST", "/api/login", {"key": "wrong-key-x"})
        self.assertEqual(status, 429)
        self.assertGreaterEqual(body["retry_after"], 1)

    def test_logout_clears_session_expiry(self):
        self.s.login()
        self.s.request("POST", "/api/logout", {})
        self.assertEqual(self.s.app.session_expires, 0.0)
        self.assertEqual(self.s.request("GET", "/api/status")[0], 401)


class V2AppConfigTests(unittest.TestCase):
    def test_session_lifetime_is_configurable(self):
        s = LiveServer(session_seconds=600)
        self.addCleanup(s.close)
        headers = s.login()
        self.assertIn("Max-Age=600", headers["Set-Cookie"])
        self.assertLessEqual(s.request("GET", "/api/status")[1]["session_expires_in"], 600)

    def test_app_constructor_validation(self):
        from broker import Broker
        from server import App
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            broker = Broker(Path(d) / "x.db")
            for kwargs in ({"address": ("0.0.0.0", 0)}, {"address": ("localhost", 0)},
                           {"key": "short"}, {"key": None}, {"session_seconds": 5},
                           {"agent_key": "x" * 31}, {"agent_key": 12345678901234567890123456789012345}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    params = {"address": ("127.0.0.1", 0), "key": "valid-owner-key"}
                    params.update(kwargs)
                    App(params.pop("address"), broker, params.pop("key"), **params).server_close()


class FlakyBroker:
    """Wraps a real broker; approved_ids fails `failures` times first."""

    def __init__(self, broker, failures):
        self._broker, self.failures = broker, failures

    def approved_ids(self):
        if self.failures:
            self.failures -= 1
            raise OSError("transient")
        return self._broker.approved_ids()

    def __getattr__(self, name):
        return getattr(self._broker, name)


class V2WorkerTests(unittest.TestCase):
    def _server(self, failures):
        import tempfile
        from pathlib import Path
        from broker import Broker
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        real = Broker(Path(self.temp.name) / "w.db")
        s = LiveServer(auto=True, broker=FlakyBroker(real, failures), worker_interval=.05)
        self.addCleanup(s.close)
        return s, real

    def test_worker_recovers_from_transient_errors(self):
        s, real = self._server(failures=2)  # milestone 3 stopped forever on the first error
        s.login()
        request_id = s.propose()
        s.request("POST", "/api/approve", {"id": request_id})
        deadline = time.time() + 5
        while time.time() < deadline and real.history()["items"][0]["status"] != "consumed":
            time.sleep(.05)
        self.assertEqual(real.history()["items"][0]["status"], "consumed")
        self.assertEqual(s.request("GET", "/api/status")[1]["worker"], "running")

    def test_worker_stops_after_repeated_errors_and_reports_it(self):
        s, real = self._server(failures=10**6)
        deadline = time.time() + 8
        while time.time() < deadline and s.app.worker_state != "stopped":
            time.sleep(.05)
        self.assertEqual(s.app.worker_state, "stopped")
        s.login()
        state = s.request("GET", "/api/state")[1]
        self.assertEqual(state["worker"], "stopped")
        # Manual execution still requires approval and still works.
        request_id = s.propose()
        self.assertEqual(s.request("POST", "/api/execute", {"id": request_id})[0], 409)
        s.request("POST", "/api/approve", {"id": request_id})
        self.assertEqual(s.request("POST", "/api/execute", {"id": request_id})[0], 200)

    def test_worker_never_executes_without_approval(self):
        s, real = self._server(failures=0)
        s.login()
        request_id = s.propose()
        time.sleep(.4)
        self.assertEqual(real.history()["items"][0]["status"], "pending")
        with self.assertRaises(BrokerError):
            real.execute(request_id)


if __name__ == "__main__":
    unittest.main()
