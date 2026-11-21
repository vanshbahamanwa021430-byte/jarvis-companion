import sqlite3
from contextlib import closing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from broker import Broker, BrokerError


class BrokerTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / "broker.db"
        self.now = 1000.0
        self.store = Broker(self.path, clock=lambda: self.now, ttl_seconds=10)

    def create(self, *, reason="Review the selected note"):
        return self.store.propose("read_file", "demo-note", reason)["id"]

    def assert_broker_error(self, code, function, *args):
        with self.assertRaises(BrokerError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)

    def test_cannot_execute_before_approval_and_results_persist(self):
        request_id = self.create()
        self.assert_broker_error("invalid_state", self.store.execute, request_id)
        pending = self.store.snapshot()["requests"][0]
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["result"])
        self.store.approve(request_id)
        result = self.store.execute(request_id)
        reopened = Broker(self.path, clock=lambda: self.now)
        self.assert_broker_error("invalid_state", reopened.execute, request_id)
        record = reopened.snapshot()["requests"][0]
        self.assertEqual(record["result"], result["result"])
        self.assertEqual(record["reason"], "Review the selected note")

    def test_invalid_targets_are_rejected(self):
        self.assert_broker_error("unknown_action", self.store.propose, "read_file", "/unapproved")
        self.assert_broker_error("unknown_action", self.store.propose, "read_calendar", "demo-note")
        self.assert_broker_error("unknown_action", self.store.propose, ["read_file"], "demo-note")
        self.assertEqual(self.store.snapshot()["audit"], [])

    def test_invalid_reason_is_rejected(self):
        self.assert_broker_error("invalid_reason", self.store.propose, "read_file", "demo-note", "a" * 241)
        self.assert_broker_error("invalid_reason", self.store.propose, "read_file", "demo-note", object())
        self.assert_broker_error("invalid_reason", self.store.propose, "read_file", "demo-note", "text\u202ebidi")

    def test_denial_and_revocation_cannot_be_reapproved(self):
        first = self.create()
        self.assertEqual(self.store.deny(first)["status"], "denied")
        self.assert_broker_error("invalid_state", self.store.approve, first)
        second = self.create()
        self.store.approve(second)
        self.assertEqual(self.store.deny(second)["status"], "revoked")
        self.assert_broker_error("invalid_state", self.store.execute, second)
        self.assert_broker_error("invalid_state", self.store.approve, second)

    def test_pending_and_approved_expire_durably(self):
        pending = self.create()
        approved = self.create()
        self.now += 2
        self.store.approve(approved)
        self.now += 8
        self.assert_broker_error("invalid_state", self.store.approve, pending)
        self.now += 2
        self.assert_broker_error("invalid_state", self.store.execute, approved)
        self.now = 1001  # An observed expiry must not become valid if the clock moves back.
        records = {item["id"]: item["status"] for item in self.store.snapshot()["requests"]}
        self.assertEqual(records, {pending: "expired", approved: "expired"})

    def test_pause_revokes_pending_and_approved_and_survives_reopen(self):
        first, second = self.create(), self.create()
        self.store.approve(first)
        self.store.set_paused(True)
        reopened = Broker(self.path, clock=lambda: self.now)
        self.assertTrue(reopened.snapshot()["paused"])
        self.assert_broker_error("paused", reopened.execute, first)
        self.assert_broker_error("paused", reopened.propose, "read_file", "demo-note")
        reopened.set_paused(False)
        self.assert_broker_error("invalid_state", reopened.execute, first)
        self.assert_broker_error("invalid_state", reopened.approve, second)
        self.assertEqual(
            [event["event"] for event in reversed(reopened.snapshot()["audit"])],
            ["proposed", "proposed", "approved", "paused", "revoked", "revoked", "resumed"],
        )

    def test_concurrent_execution_consumes_only_once(self):
        request_id = self.create()
        self.store.approve(request_id)

        def attempt(_):
            try:
                return self.store.execute(request_id)["status"]
            except BrokerError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(attempt, range(16)))
        self.assertEqual(outcomes.count("consumed"), 1)
        self.assertEqual(outcomes.count("invalid_state"), 15)
        self.assertEqual(
            sum(event["event"] == "consumed" for event in self.store.snapshot()["audit"]), 1
        )

    def test_request_target_cannot_be_updated_indirectly(self):
        request_id = self.create()
        self.store.approve(request_id)
        with closing(sqlite3.connect(self.path)) as database:
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute("UPDATE requests SET target = ? WHERE id = ?", ("demo-event", request_id))
        self.assertEqual(self.store.execute(request_id)["result"]["title"], "Demo workspace note")


if __name__ == "__main__":
    unittest.main()
