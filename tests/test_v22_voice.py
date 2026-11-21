import tempfile
import time
import unittest
from pathlib import Path

from actions import ActionsBroker
from voice import (
    VoiceHub, is_cancel, is_confirm, normalize, parse_intent, strip_wake,
)


class ParseTests(unittest.TestCase):
    def test_wake_word_is_stripped(self):
        woke, rest = strip_wake("Hey Jarvis, open WhatsApp")
        self.assertTrue(woke)
        self.assertEqual(rest, "open whatsapp")
        woke, rest = strip_wake("okay jarvis")
        self.assertTrue(woke)
        self.assertEqual(rest, "")
        woke, rest = strip_wake("set an alarm for 7 am")
        self.assertFalse(woke)

    def test_phone_commands(self):
        cases = {
            "open WhatsApp": ("phone.open_app", {"app": "whatsapp"}),
            "Hey Jarvis open chrome": ("phone.open_app", {"app": "chrome"}),
            "go home": ("phone.home", {}),
            "go back": ("phone.back", {}),
            "scroll up": ("phone.scroll", {"direction": "up"}),
            "read the screen": ("phone.read_screen", {}),
            "read my notifications": ("phone.read_notifications", {}),
            "type hello world": ("phone.type", {"text": "hello world"}),
            "tap Submit": ("phone.tap", {"text": "submit"}),
            "tap 40 80": ("phone.tap", {"x": 40, "y": 80}),
            "call 9876543210": ("phone.call", {"number": "9876543210"}),
            "text 9876543210 saying I am late": ("phone.sms_draft", {"number": "9876543210", "body": "i am late"}),
            "set an alarm for 7:30 pm": ("phone.set_alarm", {"hour": 19, "minute": 30, "message": "Jarvis alarm"}),
            "open settings": ("phone.open_settings", {"panel": "main"}),
            "open wifi settings": ("phone.open_settings", {"panel": "wifi"}),
        }
        for text, (kind, payload) in cases.items():
            parsed = parse_intent(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(parsed.kind, kind, text)
            self.assertEqual(parsed.payload, payload, text)

    def test_google_and_plugin_commands(self):
        self.assertEqual(parse_intent("what's on my calendar").kind, "google.calendar.read")
        self.assertEqual(parse_intent("check my gmail").kind, "google.gmail.read")
        send = parse_intent("send an email to ada@example.com subject Hello body Hi there")
        self.assertEqual(send.kind, "google.gmail.send")
        self.assertEqual(send.payload["to"], "ada@example.com")
        self.assertTrue(send.needs_spoken_confirm)
        self.assertEqual(parse_intent("add an event called Dentist").payload["title"], "dentist")
        self.assertEqual(parse_intent("open maps to the airport").kind, "google.maps.open")
        self.assertEqual(parse_intent("open youtube for lo-fi beats").kind, "google.youtube.open")
        plugin = parse_intent("run plugin local-note add")
        self.assertEqual(plugin.kind, "plugin.run")
        self.assertEqual(plugin.payload, {"plugin": "local-note", "action": "add", "params": {}})

    def test_unrecognised_and_confirm_words_are_not_intents(self):
        self.assertIsNone(parse_intent("confirm"))
        self.assertIsNone(parse_intent("cancel"))
        self.assertIsNone(parse_intent("hey jarvis"))
        self.assertIsNone(parse_intent("sing a song"))
        self.assertTrue(is_confirm("Allow once"))
        self.assertTrue(is_cancel("Never mind"))
        self.assertEqual(normalize("Hey JARVIS!"), "hey jarvis")


class ConfirmationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1_000.0]
        self.actions = ActionsBroker(
            Path(self.temp.name) / "a.db",
            clock=lambda: self.now[0],
            ttl_seconds=60,
            executor=lambda kind, payload: {"ok": True},
        )
        self.voice = VoiceHub(self.actions, clock=lambda: self.now[0])

    def tearDown(self):
        self.temp.cleanup()

    def test_low_risk_is_queued_not_executed(self):
        result = self.voice.handle("Hey Jarvis go home")
        self.assertEqual(result["event"], "proposed")
        self.assertFalse(result["needs_spoken_confirm"])
        self.assertEqual(result["action"]["status"], "pending")
        self.assertIn("Approve it on the screen", result["speak"])
        # Still not approved or executed.
        rec = self.actions.get(result["action"]["id"])
        self.assertEqual(rec["status"], "pending")

    def test_risky_command_needs_spoken_confirm_and_stays_pending(self):
        result = self.voice.handle("call 9876543210")
        self.assertTrue(result["needs_spoken_confirm"])
        self.assertEqual(result["action"]["status"], "pending")
        self.assertIn("confirm or cancel", result["speak"].lower())
        approved = self.voice.handle("Confirm")
        self.assertEqual(approved["event"], "approved")
        self.assertEqual(approved["action"]["status"], "approved")
        # Desk still does not execute a device action.
        rec = self.actions.get(approved["action"]["id"])
        self.assertEqual(rec["runs_on"], "device")

    def test_spoken_cancel_denies_the_card(self):
        result = self.voice.handle("read the screen")
        self.assertTrue(result["needs_spoken_confirm"])
        denied = self.voice.handle("cancel")
        self.assertEqual(denied["event"], "denied")
        self.assertEqual(self.actions.get(result["action"]["id"])["status"], "denied")

    def test_card_tap_also_approves_risky_action(self):
        result = self.voice.handle("type secret")
        rec = self.actions.approve(result["action"]["id"])
        self.assertEqual(rec["status"], "approved")

    def test_mute_and_kill_stop_voice(self):
        self.voice.set_muted(True)
        result = self.voice.handle("Hey Jarvis go home")
        self.assertEqual(result["error"], "muted")
        self.voice.set_muted(False)
        self.actions.set_killed(True)
        result = self.voice.handle("Hey Jarvis go home")
        self.assertEqual(result["error"], "killed")

    def test_wake_only(self):
        result = self.voice.handle("Hey Jarvis")
        self.assertEqual(result["event"], "awake")
        self.assertEqual(result["speak"], "Yes?")


if __name__ == "__main__":
    unittest.main()
