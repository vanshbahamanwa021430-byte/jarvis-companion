"""Structured logs are machine-readable and never contain secrets or payloads."""
import io
import json
import logging
import unittest

from jlog import JsonFormatter, setup_logging
from v2_helpers import OWNER_KEY, LiveServer


class LoggingTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("jarvis")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())

    def test_json_formatter_includes_extras(self):
        record = logging.makeLogRecord({"msg": "job_completed", "levelname": "INFO", "request_id": "abc"})
        entry = json.loads(JsonFormatter().format(record))
        self.assertEqual((entry["event"], entry["level"], entry["request_id"]), ("job_completed", "info", "abc"))
        self.assertTrue(entry["ts"].endswith("Z"))

    def test_text_format(self):
        stream = io.StringIO()
        setup_logging("INFO", "text", stream).info("hello", extra={"port": 1})
        self.assertIn("hello port=1", stream.getvalue())

    def test_http_access_log_has_no_secrets_bodies_or_queries(self):
        stream = io.StringIO()
        setup_logging("INFO", "json", stream)
        s = LiveServer()
        self.addCleanup(s.close)
        s.login()
        s.request("POST", "/api/memory/workspaces", {"title": "PRIVATE-TITLE-123"})
        s.request("GET", "/api/history?status=SECRETQUERY")
        s.propose(reason="PRIVATE-REASON-456")
        text = stream.getvalue()
        entries = [json.loads(line) for line in text.splitlines()]
        self.assertTrue(any(e["event"] == "http_request" and e["path"] == "/api/login" for e in entries))
        self.assertTrue(any(e["event"] == "owner_login" and e["outcome"] == 200 for e in entries))
        for secret in (OWNER_KEY, s.cookie.split("=", 1)[1], "PRIVATE-TITLE-123", "SECRETQUERY", "PRIVATE-REASON-456"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
