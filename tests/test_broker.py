"""Independent acceptance tests for the demo's approval boundary."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from broker import Broker, BrokerError


class BrokerBoundaryTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "demo.sqlite3"
        self.now = 1000.0
        self.broker = Broker(self.db, clock=lambda: self.now, ttl_seconds=60)

    def propose(self, action="read_file", target="demo-note"):
        return self.broker.propose(action, target, "Read the demo fixture")

    def record(self, request_id):
        return next(row for row in self.broker.snapshot()["requests"] if row["id"] == request_id)

    def assertBlocked(self, operation, code, *args):
        with self.assertRaises(BrokerError) as exc:
            operation(*args)
        self.assertEqual(exc.exception.code, code)

    def test_pending_cannot_be_executed_or_return_content(self):
        pending = self.propose()
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["result"])
        self.assertBlocked(self.broker.execute, "invalid_state", pending["id"])
        self.assertIsNone(self.record(pending["id"])["result"])

    def test_deny_is_terminal(self):
        request_id = self.propose()["id"]
        self.assertEqual(self.broker.deny(request_id)["status"], "denied")
        self.assertBlocked(self.broker.approve, "invalid_state", request_id)
        self.assertBlocked(self.broker.execute, "invalid_state", request_id)
        self.assertIsNone(self.record(request_id)["result"])

    def test_deny_can_revoke_approved_but_unconsumed_grant(self):
        request_id = self.propose()["id"]
        self.broker.approve(request_id)
        self.assertEqual(self.broker.deny(request_id)["status"], "revoked")
        self.assertBlocked(self.broker.execute, "invalid_state", request_id)

    def test_single_use_is_bound_to_one_request_and_one_result(self):
        request_id = self.propose()["id"]
        unrelated = self.propose()["id"]
        grant = self.broker.approve(request_id)
        self.assertIsNone(grant["result"])
        self.assertBlocked(self.broker.execute, "invalid_state", unrelated)
        done = self.broker.execute(request_id)
        self.assertEqual(done["id"], request_id)
        self.assertEqual(done["status"], "consumed")
        self.assertEqual(done["result"]["title"], "Demo workspace note")
        self.assertEqual(self.record(request_id)["result"], done["result"])
        self.assertBlocked(self.broker.execute, "invalid_state", request_id)

    def test_concurrent_execution_consumes_at_most_once(self):
        request_id = self.propose()["id"]
        self.broker.approve(request_id)
        barrier = threading.Barrier(8, timeout=5)

        def claim():
            barrier.wait()
            try:
                return self.broker.execute(request_id)["status"]
            except BrokerError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: claim(), range(8)))
        self.assertCountEqual(outcomes, ["consumed"] + ["invalid_state"] * 7)
        audit = self.broker.snapshot()["audit"]
        self.assertEqual(sum(entry["event"] == "consumed" and entry["request_id"] == request_id
                             for entry in audit), 1)

    def test_pending_grant_at_deadline_cannot_be_approved(self):
        request_id = self.propose()["id"]
        self.now += 60
        self.assertBlocked(self.broker.approve, "invalid_state", request_id)
        self.assertEqual(self.record(request_id)["status"], "expired")

    def test_approved_grant_at_deadline_cannot_be_executed(self):
        request_id = self.propose()["id"]
        self.now += 8
        expires_at = self.broker.approve(request_id)["expires_at"]
        self.now = expires_at
        self.assertBlocked(self.broker.execute, "invalid_state", request_id)
        self.assertEqual(self.record(request_id)["status"], "expired")
        self.assertIsNone(self.record(request_id)["result"])

    def test_pause_revokes_outstanding_grants_even_after_resume(self):
        pending = self.propose()["id"]
        approved = self.propose()["id"]
        self.broker.approve(approved)
        self.assertTrue(self.broker.set_paused(True)["paused"])
        self.assertBlocked(self.broker.execute, "paused", approved)
        self.assertBlocked(self.broker.approve, "paused", pending)
        self.assertBlocked(self.propose, "paused")
        self.assertFalse(self.broker.set_paused(False)["paused"])
        for request_id in (pending, approved):
            self.assertEqual(self.record(request_id)["status"], "revoked")
            self.assertBlocked(self.broker.execute, "invalid_state", request_id)

    def test_only_catalog_action_target_pairs_are_accepted(self):
        self.assertBlocked(self.propose, "unknown_action", "read_file", "another-note")
        self.assertBlocked(self.propose, "unknown_action", "read_file", "demo-event")
        self.assertBlocked(self.propose, "unknown_action", "send_message", "demo-event")
        valid = self.propose("read_calendar", "demo-event")
        self.broker.approve(valid["id"])
        done = self.broker.execute(valid["id"])
        self.assertEqual(done["result"]["title"], "Demo calendar event")
        self.assertTrue(done["result"]["text"].startswith("Synthetic event:"))

    def test_completed_receipts_and_pause_survive_reopen(self):
        request_id = self.propose()["id"]
        self.broker.approve(request_id)
        consumed = self.broker.execute(request_id)
        self.broker.set_paused(True)
        self.broker = Broker(self.db, clock=lambda: self.now, ttl_seconds=60)
        self.assertTrue(self.broker.snapshot()["paused"])
        self.assertEqual(self.record(request_id)["result"], consumed["result"])
        self.broker.set_paused(False)
        self.assertBlocked(self.broker.execute, "invalid_state", request_id)


if __name__ == "__main__":
    unittest.main()
