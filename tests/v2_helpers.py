"""Shared helpers for v2 HTTP tests (real loopback server, real sockets)."""
import http.client
import json
import tempfile
import threading
from pathlib import Path

from broker import Broker
from server import App
from workspace_memory import WorkspaceMemory

OWNER_KEY = "v2-owner-test-key"
AGENT_KEY = "v2-agent-test-key-with-at-least-32-chars"


class LiveServer:
    def __init__(self, *, auto=False, memory=True, agent=False, broker=None, **kwargs):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.broker = broker or Broker(root / "broker.db")
        self.memory = WorkspaceMemory(root / "memory.db") if memory else None
        self.app = App(("127.0.0.1", 0), self.broker, OWNER_KEY, auto=auto, memory=self.memory,
                       agent_key=AGENT_KEY if agent else None, **kwargs)
        self.thread = threading.Thread(target=self.app.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.host = f"127.0.0.1:{self.app.server_port}"
        self.cookie = ""

    def close(self):
        self.app.shutdown()
        self.thread.join(timeout=3)
        self.app.server_close()
        self.temp.cleanup()

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server_port, timeout=6)
        try:
            conn.request(method, path, body, headers or {})
            resp = conn.getresponse()
            data = resp.read()
            ctype = resp.getheader("Content-Type", "")
            if "application/json" in ctype and data:
                data = json.loads(data)
            return resp.status, data, dict(resp.getheaders())
        finally:
            conn.close()

    def request(self, method, path, value=None, **extra):
        headers = {"Host": self.host, "Origin": "http://" + self.host}
        if self.cookie:
            headers["Cookie"] = self.cookie
        body = None
        if value is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(value).encode()
        headers.update(extra)
        headers = {k: v for k, v in headers.items() if v is not None}
        return self.raw(method, path, body, headers)

    def login(self):
        status, body, headers = self.request("POST", "/api/login", {"key": OWNER_KEY})
        assert status == 200, body
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        return headers

    def propose(self, action="read_file", target="demo-note", reason="test"):
        status, body, _ = self.request("POST", "/api/propose", {"action": action, "target": target, "reason": reason})
        assert status == 201, body
        return body["id"]
