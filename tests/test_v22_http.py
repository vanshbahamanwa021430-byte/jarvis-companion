import unittest

from v2_helpers import LiveServer


class V22HttpTests(unittest.TestCase):
    def setUp(self):
        self.s = LiveServer()
        self.addCleanup(self.s.close)

    def test_new_routes_require_owner(self):
        for path in ("/api/actions", "/api/device/pending", "/api/voice/status",
                     "/api/google/status", "/api/plugins"):
            self.assertEqual(self.s.request("GET", path)[0], 401, path)
        self.assertEqual(self.s.request("POST", "/api/kill", {"killed": True})[0], 401)
        self.assertEqual(self.s.request("POST", "/api/voice/utterance", {"text": "go home"})[0], 401)

    def test_voice_propose_and_confirm_via_http(self):
        self.s.login()
        status, body, _ = self.s.request("POST", "/api/voice/utterance", {"text": "Hey Jarvis call 9876543210"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["needs_spoken_confirm"])
        self.assertEqual(body["action"]["status"], "pending")
        request_id = body["action"]["id"]
        status, body, _ = self.s.request("POST", "/api/voice/utterance", {"text": "confirm"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["event"], "approved")
        self.assertEqual(body["action"]["id"], request_id)
        self.assertEqual(body["action"]["status"], "approved")
        # Still not executed on the desk.
        rec = self.s.app.actions.get(request_id)
        self.assertEqual(rec["runs_on"], "device")
        self.assertEqual(rec["status"], "approved")

    def test_kill_switch_and_state_keys(self):
        self.s.login()
        self.s.request("POST", "/api/voice/utterance", {"text": "go home"})
        status, body, _ = self.s.request("POST", "/api/kill", {"killed": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["killed"])
        status, body, _ = self.s.request("POST", "/api/voice/utterance", {"text": "go back"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["error"], "killed")
        state = self.s.request("GET", "/api/state")[1]
        self.assertTrue(state["killed"])
        self.assertIn("requests", state)  # demo catalog snapshot is intact
        self.assertIn("action_requests", state)
        self.assertIn("plugins", state)
        plugin_ids = {p["id"] for p in state["plugins"]}
        self.assertEqual(plugin_ids, {"local-note", "clock-alarm", "webhook-ping"})
        # Unkill leaves access paused.
        self.s.request("POST", "/api/kill", {"killed": False})
        status, body, _ = self.s.request("POST", "/api/actions/propose", {"kind": "phone.back"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "paused")

    def test_webhook_connector_rejects_public_url(self):
        self.s.login()
        status, body, _ = self.s.request("POST", "/api/webhooks/add",
                                         {"name": "Remote", "url": "https://example.com/hook"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_url")

    def test_google_not_configured_until_client_id_set(self):
        self.s.login()
        status, body, _ = self.s.request("GET", "/api/google/status")
        self.assertEqual(status, 200)
        self.assertFalse(body["configured"])
        status, body, _ = self.s.request("POST", "/api/google/start", {"service": "gmail"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "google_not_configured")

    def test_plugin_enable_roundtrip(self):
        self.s.login()
        status, body, _ = self.s.request("POST", "/api/plugins/enable", {"id": "local-note", "enabled": False})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["enabled"])


if __name__ == "__main__":
    unittest.main()
