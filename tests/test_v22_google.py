import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from actions import ActionError, ActionsBroker
from google_oauth import GoogleConnector, SCOPES


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode()
        self.status = status

    def read(self, n=-1):
        data = self._payload
        return data if n < 0 else data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=0):
        self.requests.append(req)
        url = req.full_url
        if "oauth2.googleapis.com/token" in url:
            return FakeResponse({"access_token": "ya29.access", "refresh_token": "1//refresh", "expires_in": 3600})
        return FakeResponse({"id": "ok", "messages": []})


class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.opener = FakeOpener()
        self.g = GoogleConnector(
            Path(self.temp.name) / "tokens.json",
            client_id="1234567890-abc.apps.googleusercontent.com",
            transport=self.opener,
            clock=lambda: 1000,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_start_uses_least_privilege_scopes_and_pkce(self):
        started = self.g.start("gmail", "http://127.0.0.1:8765/api/google/callback", access="read")
        parts = urlparse(started["auth_url"])
        self.assertEqual(parts.netloc, "accounts.google.com")
        query = parse_qs(parts.query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertIn("code_challenge", query)
        scopes = query["scope"][0].split()
        self.assertEqual(scopes, list(SCOPES["gmail"]["read"]))
        self.assertNotIn("https://www.googleapis.com/auth/gmail.send", scopes)
        self.assertNotIn("https://mail.google.com/", scopes)

    def test_finish_stores_tokens_and_execute_needs_approval(self):
        started = self.g.start("gmail", "http://127.0.0.1:8765/api/google/callback", access="read")
        self.g.finish({"state": started["state"], "code": "4/abc"})
        self.assertTrue(self.g.status()["services"]["gmail"]["connected"])
        # Execute without going through the broker is what the executor does
        # after approval; missing send scope is rejected.
        with self.assertRaises(ActionError) as ctx:
            self.g.execute("google.gmail.send", {"to": "a@b.co", "subject": "Hi", "body": "Hello"})
        self.assertEqual(ctx.exception.code, "google_not_connected")
        # Read works when connected.
        result = self.g.execute("google.gmail.read", {"query": ""})
        self.assertEqual(result["service"], "gmail")

    def test_unconfigured_client_and_unknown_service(self):
        bare = GoogleConnector(Path(self.temp.name) / "empty.json")
        with self.assertRaises(ActionError) as ctx:
            bare.start("gmail", "http://127.0.0.1/callback")
        self.assertEqual(ctx.exception.code, "google_not_configured")
        with self.assertRaises(ActionError):
            self.g.start("photos", "http://127.0.0.1/callback")

    def test_write_goes_through_broker(self):
        started = self.g.start("gmail", "http://127.0.0.1:8765/api/google/callback", access="send")
        self.g.finish({"state": started["state"], "code": "4/abc"})
        broker = ActionsBroker(Path(self.temp.name) / "a.db", executor=self.g.execute)
        rec = broker.propose("google.gmail.send", {"to": "ada@example.com", "subject": "Hi", "body": "Hello"})
        self.assertEqual(rec["risk"], "high")
        with self.assertRaises(ActionError):
            broker.execute(rec["id"])
        broker.approve(rec["id"])
        done = broker.execute(rec["id"])
        self.assertTrue(done["result"]["ok"])


if __name__ == "__main__":
    unittest.main()
