import http.server
import json
import threading
import unittest
from urllib.request import OpenerDirector, HTTPHandler

from actions import ActionError
from webhook import WebhookRegistry, validate_webhook_url


class UrlPolicyTests(unittest.TestCase):
    def test_accepts_loopback_and_private(self):
        self.assertEqual(validate_webhook_url("127.0.0.1:9090"), "http://127.0.0.1:9090")
        self.assertEqual(validate_webhook_url("http://192.168.1.8"), "http://192.168.1.8:80")
        self.assertEqual(validate_webhook_url("https://10.0.0.2"), "https://10.0.0.2:443")

    def test_rejects_public_metadata_and_tricks(self):
        for raw in (
            "https://example.com", "8.8.8.8", "169.254.169.254",
            "http://127.0.0.1:80/secret", "http://user:pass@127.0.0.1",
            "file:///etc/passwd", "http://localhost?x=1",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ActionError):
                    validate_webhook_url(raw)


class LiveWebhookTests(unittest.TestCase):
    def setUp(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                self.server.hits.append((self.path, body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.hits = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.reg = WebhookRegistry()
        self.connector = self.reg.add("Local ping", f"http://127.0.0.1:{self.httpd.server_address[1]}")

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()

    def test_send_only_after_registry_lookup(self):
        result = self.reg.send(self.connector["id"], "POST", "/hook", '{"ping":true}')
        self.assertEqual(result["status"], 200)
        self.assertEqual(self.httpd.hits[0][0], "/hook")

    def test_unknown_connector_and_bad_path(self):
        with self.assertRaises(ActionError):
            self.reg.send("0" * 32, "GET", "/")
        with self.assertRaises(ActionError):
            self.reg.send(self.connector["id"], "GET", "/../secret")


if __name__ == "__main__":
    unittest.main()
