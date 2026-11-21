"""Opt-in LAN binding: default stays loopback, Host/Origin checks stay tight."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from config import ConfigError, load_config
from broker import Broker
from server import (
    App, LAN_BIND, LAN_WARNING, LOOPBACK, is_private_ipv4, local_private_ipv4s,
    valid_host_header,
)

ROOT = Path(__file__).resolve().parent.parent


class PrivateIPv4Tests(unittest.TestCase):
    def test_rfc1918_and_link_local_accepted(self):
        for host in ("10.0.0.1", "10.0.2.2", "172.16.0.1", "172.31.255.255",
                     "192.168.0.1", "192.168.1.50", "169.254.1.1"):
            with self.subTest(host=host):
                self.assertTrue(is_private_ipv4(host))

    def test_public_loopback_and_junk_rejected(self):
        for host in ("8.8.8.8", "1.1.1.1", "127.0.0.1", "127.0.0.2", "0.0.0.0",
                     "172.15.0.1", "172.32.0.1", "192.169.1.1", "11.0.0.1",
                     "192.168.01.1", "192.168.1", "localhost", "", "192.168.1.1/24",
                     "::1", "2001:db8::1"):
            with self.subTest(host=host):
                self.assertFalse(is_private_ipv4(host))


class HostHeaderTests(unittest.TestCase):
    def test_loopback_names_always_allowed(self):
        for header in ("127.0.0.1:8765", "localhost:8765"):
            self.assertTrue(valid_host_header(header, 8765, False))
            self.assertTrue(valid_host_header(header, 8765, True))

    def test_port_must_match_and_bare_host_only_on_port_80(self):
        self.assertFalse(valid_host_header("127.0.0.1:80", 8765, False))
        self.assertFalse(valid_host_header("127.0.0.1", 8765, False))
        self.assertTrue(valid_host_header("127.0.0.1", 80, False))
        self.assertTrue(valid_host_header("127.0.0.1:80", 80, False))
        self.assertFalse(valid_host_header("127.0.0.1:8080", 80, False))

    def test_private_hosts_only_when_lan_enabled(self):
        header = "192.168.1.20:8765"
        self.assertFalse(valid_host_header(header, 8765, False))
        self.assertTrue(valid_host_header(header, 8765, True))
        self.assertTrue(valid_host_header("10.0.2.2:8765", 8765, True))
        self.assertFalse(valid_host_header("8.8.8.8:8765", 8765, True))
        self.assertFalse(valid_host_header("evil.example:8765", 8765, True))
        self.assertFalse(valid_host_header("0.0.0.0:8765", 8765, True))


class AllowLanConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write(self, data):
        path = Path(self.temp.name) / "c.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return str(path)

    def test_default_is_loopback(self):
        self.assertFalse(load_config([]).allow_lan)

    def test_file_and_cli(self):
        self.assertTrue(load_config(["--config", self.write({"allow_lan": True})]).allow_lan)
        self.assertTrue(load_config(["--allow-lan"]).allow_lan)
        # CLI can turn it on over a false file value
        self.assertTrue(load_config(["--config", self.write({"allow_lan": False}), "--allow-lan"]).allow_lan)

    def test_non_bool_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(["--config", self.write({"allow_lan": "yes"})])

    def test_host_bind_address_still_forbidden(self):
        for key in ("host", "bind", "address"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config(["--config", self.write({key: "0.0.0.0"})])


class AllowLanHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        broker = Broker(Path(self.temp.name) / "demo.db")
        self.app = App((LAN_BIND, 0), broker, "test-login-key", auto=False, allow_lan=True)
        self.thread = threading.Thread(target=self.app.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.port = self.app.server_port

    def _stop(self):
        self.app.shutdown()
        self.app.server_close()
        self.thread.join(timeout=4)

    def request(self, method, path, body=None, **headers):
        import http.client
        headers = {"Host": f"127.0.0.1:{self.port}", **headers}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
            headers.setdefault("Content-Length", str(len(payload)))
            headers.setdefault("Origin", "http://" + headers["Host"])
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        try:
            conn.request(method, path, payload, headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, raw
        finally:
            conn.close()

    def test_app_still_rejects_lan_bind_without_the_flag(self):
        broker = Broker(Path(self.temp.name) / "other.db")
        with self.assertRaises(ValueError) as ctx:
            App((LAN_BIND, 0), broker, "valid-owner-key").server_close()
        self.assertIn("loopback", str(ctx.exception).lower())
        with self.assertRaises(ValueError):
            App((LOOPBACK, 0), broker, "valid-owner-key", allow_lan=True).server_close()

    def test_loopback_and_private_hosts_accepted(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/", Host=f"localhost:{self.port}")[0], 200)
        self.assertEqual(self.request("GET", "/", Host=f"192.168.1.20:{self.port}")[0], 200)
        self.assertEqual(self.request("GET", "/", Host=f"10.0.2.2:{self.port}")[0], 200)

    def test_public_and_unknown_hosts_rejected(self):
        self.assertEqual(self.request("GET", "/", Host=f"8.8.8.8:{self.port}")[0], 403)
        self.assertEqual(self.request("GET", "/", Host="demo.invalid")[0], 403)
        self.assertEqual(self.request("GET", "/", Host=f"evil.example:{self.port}")[0], 403)

    def test_origin_must_still_match_allowed_host_exactly(self):
        lan_host = f"192.168.1.20:{self.port}"
        # Matching Origin is accepted (login will fail on the key check only after origin).
        status, raw = self.request("POST", "/api/login", {"key": "test-login-key"},
                                   Host=lan_host, Origin=f"http://{lan_host}")
        self.assertEqual(status, 200)
        # Cross-origin is still rejected, including a different allowed host.
        status, _ = self.request("POST", "/api/login", {"key": "test-login-key"},
                                 Host=lan_host, Origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/login", {"key": "test-login-key"},
                                 Host=lan_host, Origin="http://evil.example")
        self.assertEqual(status, 403)

    def test_no_cors_headers(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{self.port}"})
        response = conn.getresponse()
        response.read()
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        csp = response.getheader("Content-Security-Policy")
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        conn.close()


@unittest.skipIf(os.name == "nt", "POSIX process startup")
class AllowLanProcessTests(unittest.TestCase):
    def test_allow_lan_prints_warning_after_standard_lines(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = {k: v for k, v in os.environ.items() if k != "JARVIS_PROPOSAL_KEY"}
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--allow-lan", "--port", "0",
             "--db", str(Path(temp.name) / "b.db"), "--no-memory"],
            cwd=temp.name, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        url_line = proc.stdout.readline()
        key_line = proc.stdout.readline()
        warn_line = proc.stdout.readline()
        self.assertIn("http://127.0.0.1:", url_line)
        self.assertIn("Owner unlock key", key_line)
        self.assertIn("WARNING: LAN binding is on", warn_line)
        self.assertIn("trusted home network", warn_line)
        proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("server did not shut down")
        self.assertEqual(proc.returncode, 0, err)
        self.assertIn("server_started", err)
        # The first two stdout lines stay in the same order as loopback mode.

    def test_detected_addresses_are_private_or_empty(self):
        for ip in local_private_ipv4s():
            self.assertTrue(is_private_ipv4(ip), ip)


if __name__ == "__main__":
    unittest.main()
