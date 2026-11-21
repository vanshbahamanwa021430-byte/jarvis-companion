"""Acceptance checks of the owner-facing local demo."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from broker import Broker
from server import App


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(("127.0.0.1", 0), Broker(Path(self.temp.name) / "demo.db"), "test-login-key", auto=True)
        self.thread = threading.Thread(target=self.app.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.address = f"127.0.0.1:{self.app.server_port}"
        self.cookie = ""

    def tearDown(self):
        self.app.shutdown()
        self.thread.join(timeout=2)
        self.app.server_close()
        self.temp.cleanup()

    def request(self, method, path, value=None, **overrides):
        headers = {"Host": self.address, "Origin": "http://" + self.address}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if value is not None:
            headers["Content-Type"] = "application/json"
        headers.update(overrides)
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server_port, timeout=4)
        try:
            conn.request(method, path, json.dumps(value).encode() if value is not None else None, headers)
            result = conn.getresponse()
            data = result.read()
            if "application/json" in result.getheader("Content-Type", ""):
                data = json.loads(data)
            return result.status, data, dict(result.getheaders())
        finally:
            conn.close()

    def login(self):
        status, _, headers = self.request("POST", "/api/login", {"key": "test-login-key"})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def proposal(self):
        status, obj, _ = self.request("POST", "/api/propose", {
            "action": "read_file", "target": "demo-note", "reason": "Summarize the demo note."
        })
        self.assertEqual(status, 201, obj)
        return obj["id"]

    def assert_post_status(self, path, data, status):
        actual, body, _ = self.request("POST", path, data)
        self.assertEqual(actual, status, body)

    def test_owner_session_and_logout(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.assertEqual(self.request("POST", "/api/approve", {"id": "test"})[0], 401)
        self.login()
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assert_post_status("/api/logout", {}, 200)
        self.assertEqual(self.request("GET", "/api/state")[0], 401)

    def test_exact_origin_and_local_host_required(self):
        self.assertEqual(self.request("POST", "/api/login", {"key": "test-login-key"}, Origin="null")[0], 403)
        self.assertEqual(self.request("GET", "/", Host="demo.invalid")[0], 403)

    def test_owner_approval_triggers_worker_without_client_actions(self):
        self.login()
        request = self.proposal()
        code, snapshot, _ = self.request("GET", "/api/state")
        self.assertEqual(code, 200)
        matching = next(item for item in snapshot["requests"] if item["id"] == request)
        self.assertEqual(matching["status"], "pending")
        self.assertIsNone(matching.get("result"))
        self.assertNotEqual(self.request("POST", "/api/execute", {"id":request})[0], 200)
        self.assert_post_status("/api/approve", {"id": request}, 200)
        time.sleep(.8)  # Client is idle; only the background worker may execute.
        _, snapshot, _ = self.request("GET", "/api/state")
        matching = next(item for item in snapshot["requests"] if item["id"] == request)
        self.assertEqual(matching["status"], "consumed")
        self.assertTrue(matching["result"]["text"])
        self.assertNotEqual(self.request("POST", "/api/execute", {"id":request})[0], 200)

    def test_pause_and_resume_does_not_restore_approval(self):
        self.login()
        request = self.proposal()
        self.assert_post_status("/api/pause", {"paused": True}, 200)
        self.assertNotEqual(self.request("POST", "/api/approve", {"id":request})[0], 200)
        self.assert_post_status("/api/pause", {"paused": False}, 200)
        self.assertNotEqual(self.request("POST", "/api/approve", {"id":request})[0], 200)
        _, snapshot, _ = self.request("GET", "/api/state")
        self.assertEqual(next(item for item in snapshot["requests"] if item["id"] == request)["status"], "revoked")

    def test_no_arbitrary_targets_or_string_pause(self):
        self.login()
        self.assertNotEqual(self.request("POST", "/api/propose", {"action": "read_file", "target": "other-note"})[0], 201)
        self.assert_post_status("/api/pause", {"paused":"yes"}, 400)

    def test_static_security_headers(self):
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Jarvis", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
