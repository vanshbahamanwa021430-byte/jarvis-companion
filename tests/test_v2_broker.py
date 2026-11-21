"""v2 broker features: history/audit pagination, status, worker query, catalog drift."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from broker import Broker, BrokerError


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class V2BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "b.db"
        self.clock = Clock()
        self.broker = Broker(self.path, clock=self.clock, ttl_seconds=60)

    def make(self, n, action="read_file", target="demo-note"):
        ids = []
        for _ in range(n):
            ids.append(self.broker.propose(action, target, "r")["id"])
            self.clock.now += 1
        return ids

    def test_history_paginates_newest_first_without_gaps_or_duplicates(self):
        ids = self.make(7)
        seen, cursor = [], None
        while True:
            page = self.broker.history(limit=3, before=cursor)
            self.assertEqual(page["total"], 7)
            seen += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, list(reversed(ids)))

    def test_history_filters_by_status_and_action(self):
        file_ids = self.make(3)
        cal_ids = self.make(2, "read_calendar", "demo-event")
        self.broker.deny(file_ids[0])
        self.broker.approve(cal_ids[0])
        self.broker.execute(cal_ids[0])
        self.assertEqual([i["id"] for i in self.broker.history(status="denied")["items"]], [file_ids[0]])
        self.assertEqual({i["id"] for i in self.broker.history(action="read_calendar")["items"]}, set(cal_ids))
        consumed = self.broker.history(status="consumed", action="read_calendar")
        self.assertEqual(consumed["total"], 1)
        self.assertEqual(consumed["items"][0]["result"]["title"], "Demo calendar event")
        self.assertEqual(self.broker.history(status="consumed", action="read_file")["items"], [])

    def test_history_and_audit_reject_bad_inputs(self):
        for kwargs in ({"status": "bogus"}, {"action": "rm"}, {"limit": 0}, {"limit": 101},
                       {"limit": True}, {"before": 0}, {"before": "5"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BrokerError) as ctx:
                self.broker.history(**kwargs)
            self.assertEqual(ctx.exception.status, 400)
        for kwargs in ({"event": "drop"}, {"request_id": "short"}, {"limit": 500}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BrokerError):
                self.broker.audit_log(**kwargs)

    def test_audit_log_paginates_and_filters(self):
        ids = self.make(3)
        self.broker.approve(ids[1])
        self.broker.set_paused(True)
        events = self.broker.audit_log(limit=100)["items"]
        self.assertEqual(events[0]["event"], "revoked")
        self.assertIn("paused", [e["event"] for e in events])
        page1 = self.broker.audit_log(limit=2)
        page2 = self.broker.audit_log(limit=2, before=page1["next_cursor"])
        self.assertTrue(page1["items"][-1]["id"] > page2["items"][0]["id"])
        approved = self.broker.audit_log(event="approved")["items"]
        self.assertEqual([e["request_id"] for e in approved], [ids[1]])
        self.assertEqual(approved[0]["label"], "Read demo note")
        only = self.broker.audit_log(request_id=ids[1])["items"]
        self.assertEqual({e["request_id"] for e in only}, {ids[1]})
        self.assertIsNone(next(e for e in events if e["event"] == "paused")["label"])

    def test_status_revision_changes_on_every_event_including_expiry(self):
        first = self.broker.status()
        self.assertEqual((first["pending"], first["approved"], first["paused"]), (0, 0, False))
        request_id = self.make(1)[0]
        second = self.broker.status()
        self.assertGreater(second["revision"], first["revision"])
        self.assertEqual(second["pending"], 1)
        self.clock.now += 120  # past TTL
        third = self.broker.status()
        self.assertGreater(third["revision"], second["revision"])
        self.assertEqual(third["pending"], 0)
        self.assertEqual(self.broker.history()["items"][0]["status"], "expired")
        self.assertEqual(self.broker.history()["items"][0]["id"], request_id)

    def test_approved_ids_is_oldest_first_and_empty_while_paused(self):
        a, b, c = self.make(3)
        self.broker.approve(b)
        self.broker.approve(a)
        self.assertEqual(self.broker.approved_ids(), [a, b])
        self.broker.set_paused(True)
        self.assertEqual(self.broker.approved_ids(), [])
        self.broker.set_paused(False)
        self.assertEqual(self.broker.approved_ids(), [])  # resume never restores approvals

    def test_approved_ids_applies_expiry(self):
        a = self.make(1)[0]
        self.broker.approve(a)
        self.clock.now += 61
        self.assertEqual(self.broker.approved_ids(), [])
        with self.assertRaises(BrokerError):
            self.broker.execute(a)

    def test_rows_outside_catalog_do_not_crash_and_never_execute(self):
        good = self.make(1)[0]
        with closing(sqlite3.connect(self.path)) as db, db:
            for rid, status in (("a" * 32, "pending"), ("b" * 32, "approved")):
                db.execute("INSERT INTO requests(id, action, target, reason, status, created_at, expires_at)"
                           " VALUES(?, 'read_file', 'retired-note', NULL, ?, ?, ?)",
                           (rid, status, self.clock.now, self.clock.now + 50))
        snapshot = self.broker.snapshot()  # milestone 3 raised KeyError here
        labels = {r["id"]: r["label"] for r in snapshot["requests"]}
        self.assertEqual(labels["a" * 32], "Unavailable item")
        self.assertEqual(labels[good], "Read demo note")
        for call, rid in ((self.broker.approve, "a" * 32), (self.broker.execute, "b" * 32)):
            with self.subTest(call=call.__name__), self.assertRaises(BrokerError) as ctx:
                call(rid)
            self.assertEqual(ctx.exception.code, "unavailable_target")
        self.assertIsNotNone(self.broker.history(limit=10))
        self.assertEqual(self.broker.deny("b" * 32)["status"], "revoked")

    def test_snapshot_reports_server_time_and_ttl(self):
        snap = self.broker.snapshot()
        self.assertEqual(snap["server_time"], self.clock.now)
        self.assertEqual(snap["ttl_seconds"], 60)
        self.assertIn("revision", snap)


if __name__ == "__main__":
    unittest.main()
