import tempfile
import unittest
from pathlib import Path

from actions import ActionError, ActionsBroker, validate_payload


class PayloadTests(unittest.TestCase):
    def test_known_kinds_validate(self):
        self.assertEqual(validate_payload("phone.home", {}), {})
        self.assertEqual(validate_payload("phone.open_app", {"app": "WhatsApp"}), {"app": "WhatsApp"})
        self.assertEqual(validate_payload("phone.scroll", {"direction": "down"}), {"direction": "down"})
        self.assertEqual(validate_payload("phone.call", {"number": "+91 98765 43210"})["number"], "+919876543210")

    def test_unknown_and_bad_payloads_are_rejected(self):
        with self.assertRaises(ActionError) as ctx:
            validate_payload("shell.exec", {"cmd": "ls"})
        self.assertEqual(ctx.exception.code, "unknown_action")
        with self.assertRaises(ActionError):
            validate_payload("phone.call", {"number": "abc"})
        with self.assertRaises(ActionError):
            validate_payload("webhook.http", {"connector_id": "nope", "method": "GET"})
        with self.assertRaises(ActionError):
            validate_payload("phone.open_app", {"app": "x", "package": "not-a-package"})


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.executed = []

        def executor(kind, payload):
            self.executed.append((kind, payload))
            return {"did": kind}

        self.b = ActionsBroker(Path(self.temp.name) / "a.db", executor=executor, ttl_seconds=60)

    def tearDown(self):
        self.temp.cleanup()

    def test_propose_approve_execute_desk_action(self):
        rec = self.b.propose("google.gmail.read", {"query": "in:inbox"}, reason="check mail")
        self.assertEqual(rec["status"], "pending")
        self.assertEqual(rec["risk"], "low")
        self.assertEqual(rec["runs_on"], "desk")
        self.b.approve(rec["id"])
        done = self.b.execute(rec["id"])
        self.assertEqual(done["status"], "consumed")
        self.assertEqual(self.executed, [("google.gmail.read", {"query": "in:inbox"})])

    def test_cannot_execute_without_approval(self):
        rec = self.b.propose("google.tasks.read", {})
        with self.assertRaises(ActionError) as ctx:
            self.b.execute(rec["id"])
        self.assertEqual(ctx.exception.code, "not_approved")
        self.assertEqual(self.executed, [])

    def test_device_actions_are_not_executed_on_the_desk(self):
        rec = self.b.propose("phone.home", {}, source="voice")
        self.b.approve(rec["id"])
        with self.assertRaises(ActionError) as ctx:
            self.b.execute(rec["id"])
        self.assertEqual(ctx.exception.code, "device_action")
        done = self.b.complete_device(rec["id"], True, {"opened": "home"})
        self.assertEqual(done["status"], "consumed")
        self.assertTrue(done["result"]["ok"])
        self.assertEqual(self.executed, [])

    def test_kill_switch_blocks_propose_and_execute(self):
        rec = self.b.propose("google.tasks.read", {})
        self.b.set_killed(True)
        snap = self.b.snapshot()
        self.assertTrue(snap["killed"])
        self.assertEqual(self.b.get(rec["id"])["status"], "revoked")
        with self.assertRaises(ActionError) as ctx:
            self.b.propose("phone.back", {})
        self.assertEqual(ctx.exception.code, "killed")
        self.b.set_killed(False)
        # Still paused after unkilling.
        with self.assertRaises(ActionError) as ctx:
            self.b.propose("phone.back", {})
        self.assertEqual(ctx.exception.code, "paused")
        self.b.set_paused(False)
        rec = self.b.propose("google.tasks.read", {})
        self.b.approve(rec["id"])
        self.b.execute(rec["id"])
        self.assertEqual(self.executed[-1][0], "google.tasks.read")

    def test_pause_revokes_active(self):
        rec = self.b.propose("phone.back", {})
        self.b.set_paused(True)
        self.assertEqual(self.b.get(rec["id"])["status"], "revoked")

    def test_immutable_details(self):
        rec = self.b.propose("phone.back", {}, reason="go back")
        # Re-reading keeps the original reason; there is no API to edit it.
        self.assertEqual(self.b.get(rec["id"])["reason"], "go back")
        self.assertEqual(self.b.get(rec["id"])["kind"], "phone.back")

    def test_heartbeat_and_pending(self):
        self.b.record_heartbeat({"accessibility": True, "voice_muted": False, "listening": True})
        rec = self.b.propose("phone.read_screen", {}, source="voice")
        pending = self.b.pending_for_device()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], rec["id"])
        self.assertTrue(self.b.heartbeat()["accessibility"])


if __name__ == "__main__":
    unittest.main()
