import tempfile
import threading
import unittest
from pathlib import Path

from agent_client import propose
from broker import Broker
from server import App


class AgentClientTests(unittest.TestCase):
    def test_propose_from_worker_without_owner_session(self):
        with tempfile.TemporaryDirectory() as d:
            broker = Broker(Path(d)/"test.db")
            key = "mock-test-key-" * 3
            with App(("127.0.0.1", 0), broker, "unused-owner-test-key", agent_key=key) as app:
                worker = threading.Thread(target=app.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
                worker.start()
                try:
                    receipt = propose("read_calendar", "demo-event", "Check the example",
                                      port=app.server_port, key=key)
                    self.assertEqual(receipt["status"], "pending")
                    item, = broker.snapshot()["requests"]
                    self.assertEqual(item["id"], receipt["id"])
                    self.assertEqual(item["status"], "pending")
                    self.assertIsNone(item["result"])
                finally:
                    app.shutdown()
                    worker.join(timeout=2)
