import io
import json
import unittest
from urllib.request import OpenerDirector

from actions import ActionError, ActionsBroker
from mcp_client import McpRegistry


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()
        self.status = 200

    def read(self, n=-1):
        data = self._payload
        return data if n < 0 else data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self):
        self.calls = []

    def open(self, req, timeout=0):
        body = json.loads(req.data.decode())
        self.calls.append(body)
        method = body["method"]
        if method == "initialize":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
        if method == "tools/list":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {
                "tools": [{"name": "echo", "description": "Echo text"}]
            }})
        if method == "tools/call":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "hi"}]}})
        return FakeResponse({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unknown"}})


class McpTests(unittest.TestCase):
    def setUp(self):
        self.opener = FakeOpener()
        self.reg = McpRegistry(transport=self.opener)

    def test_add_refresh_and_list_tools(self):
        server = self.reg.add("Local MCP", "http://127.0.0.1:8750")
        refreshed = self.reg.refresh(server["id"])
        self.assertTrue(refreshed["ok"])
        self.assertEqual(refreshed["tools"][0]["name"], "echo")
        methods = [c["method"] for c in self.opener.calls]
        self.assertEqual(methods, ["initialize", "tools/list"])

    def test_public_url_rejected(self):
        with self.assertRaises(ActionError):
            self.reg.add("Remote", "https://example.com")

    def test_tool_call_is_only_via_executor_after_approval(self,):
        import tempfile
        from pathlib import Path
        server = self.reg.add("Local MCP", "http://127.0.0.1:8750")
        self.reg.refresh(server["id"])
        temp = tempfile.TemporaryDirectory()
        executed = []

        def executor(kind, payload):
            executed.append((kind, payload))
            return self.reg.call(payload["server_id"], payload["tool"], payload.get("arguments") or {})

        broker = ActionsBroker(Path(temp.name) / "a.db", executor=executor)
        rec = broker.propose("mcp.call", {"server_id": server["id"], "tool": "echo", "arguments": {"text": "hi"}})
        self.assertEqual(executed, [])
        with self.assertRaises(ActionError):
            broker.execute(rec["id"])
        broker.approve(rec["id"])
        done = broker.execute(rec["id"])
        self.assertEqual(done["status"], "consumed")
        self.assertEqual(executed[0][0], "mcp.call")
        temp.cleanup()


if __name__ == "__main__":
    unittest.main()
