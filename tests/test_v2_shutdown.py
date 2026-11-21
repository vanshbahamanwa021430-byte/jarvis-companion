"""Run the real server process and verify graceful shutdown on SIGTERM/SIGINT."""
import http.client
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipIf(os.name == "nt", "POSIX signals")
class ShutdownTests(unittest.TestCase):
    def start(self, *extra):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {k: v for k, v in os.environ.items() if k != "JARVIS_PROPOSAL_KEY"}
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), "--port", "0", "--db", str(Path(self.temp.name) / "b.db"),
             "--memory-db", str(Path(self.temp.name) / "m.db"), *extra],
            cwd=self.temp.name, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        url_line = proc.stdout.readline()
        key_line = proc.stdout.readline()
        self.assertIn("http://127.0.0.1:", url_line)
        self.assertIn("Owner unlock key", key_line)
        port = int(url_line.rsplit(":", 1)[1].strip(" /\n"))
        return proc, port, key_line.rsplit(" ", 1)[1].strip()

    def stop(self, proc, sig):
        proc.send_signal(sig)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("server did not shut down within 10 s")
        return proc.returncode, err

    def check(self, sig):
        proc, port, key = self.start()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
        conn.request("GET", "/")
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        code, err = self.stop(proc, sig)
        self.assertEqual(code, 0, err)
        events = [json.loads(line)["event"] for line in err.splitlines() if line.startswith("{")]
        self.assertIn("server_started", events)
        self.assertIn("shutdown_requested", events)
        self.assertEqual(events[-1], "shutdown_complete")
        self.assertNotIn(key, err)  # the owner key is printed to stdout only, never logged

    def test_sigterm_graceful(self):
        self.check(signal.SIGTERM)

    def test_sigint_graceful(self):
        self.check(signal.SIGINT)

    def test_config_error_exits_2_without_starting(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = Path(temp.name) / "c.json"
        cfg.write_text('{"host": "0.0.0.0"}')
        result = subprocess.run([sys.executable, str(ROOT / "server.py"), "--config", str(cfg)],
                                capture_output=True, text=True, timeout=10, cwd=temp.name)
        self.assertEqual(result.returncode, 2)
        self.assertIn("loopback", result.stderr)

    def test_port_in_use_fails_cleanly(self):
        proc, port, _ = self.start()
        result = subprocess.run([sys.executable, str(ROOT / "server.py"), "--port", str(port),
                                 "--db", str(Path(self.temp.name) / "c.db"), "--no-memory"],
                                capture_output=True, text=True, timeout=10, cwd=self.temp.name)
        self.assertEqual(result.returncode, 1)
        self.assertIn("startup_failed", result.stderr)
        self.stop(proc, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
