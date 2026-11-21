"""Verify the optional proposer role against synthetic data on loopback."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from broker import Broker
from server import App


OWNER_KEY = "test-owner-key-not-agent-credential"
AGENT_KEY = "test-agent-key-has-no-owner-privilege"
PROPOSAL = {"action": "read_calendar", "target": "demo-event", "reason": "Review the synthetic event."}


class AgentRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = App(
            ("127.0.0.1", 0), Broker(Path(self.temp.name) / "roles.sqlite3"),
            OWNER_KEY, auto=False, agent_key=AGENT_KEY,
        )
        self.thread = threading.Thread(
            target=self.app.serve_forever, kwargs={"poll_interval": .02}, daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = f"http://127.0.0.1:{self.app.server_port}"

    def close_server(self):
        self.app.shutdown()
        self.thread.join(timeout=2)
        self.app.server_close()

    def request(self, method, path, value=None, *, bearer=None, origin=None, cookie=None):
        headers = {}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        if cookie is not None:
            headers["Cookie"] = cookie
        if origin is not None:
            headers["Origin"] = origin
        if value is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.app.server_port, timeout=4)
        try:
            conn.request(method, path, None if value is None else json.dumps(value), headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read()), response.getheader("Set-Cookie")
        finally:
            conn.close()

    def post_owner(self, path, value, cookie=None, **kwargs):
        return self.request("POST", path, value, cookie=cookie, origin=self.origin, **kwargs)

    def login(self):
        status, body, set_cookie = self.post_owner("/api/login", {"key": OWNER_KEY})
        self.assertEqual(status, 200, body)
        return set_cookie.split(";", 1)[0]

    def proposal(self, **kwargs):
        return self.request("POST", "/api/agent/propose", PROPOSAL, **kwargs)

    def state(self, cookie):
        status, state, _ = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(status, 200, state)
        return state

    def test_dummy_proposal_is_sanitized_and_owner_must_allow(self):
        status, response, set_cookie = self.proposal(bearer=AGENT_KEY)
        self.assertEqual(status, 202, response)
        self.assertEqual(set(response), {"id", "status"})
        self.assertEqual(response["status"], "pending")
        self.assertIsNone(set_cookie)
        request_id = response["id"]
        owner = self.login()
        self.assertEqual(self.state(owner)["requests"][0]["reason"], PROPOSAL["reason"])
        self.assertIsNone(self.state(owner)["requests"][0]["result"])
        status, body, _ = self.post_owner("/api/execute", {"id": request_id}, owner)
        self.assertEqual(status, 409, body)
        status, body, _ = self.post_owner("/api/approve", {"id": request_id}, owner)
        self.assertEqual(status, 200, body)
        status, body, _ = self.post_owner("/api/execute", {"id": request_id}, owner)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["result"]["title"], "Demo calendar event")

    def test_agent_bearer_cannot_read_state_or_mutate_owner_requests(self):
        owner = self.login()
        request_id = self.proposal(bearer=AGENT_KEY)[1]["id"]
        status, body, _ = self.request("GET", "/api/state", bearer=AGENT_KEY)
        self.assertEqual(status, 401, body)
        for endpoint, params in [
            ("/api/approve", {"id": request_id}),
            ("/api/deny", {"id": request_id}),
            ("/api/execute", {"id": request_id}),
            ("/api/logout", {}),
            ("/api/pause", {"paused": True}),
            ("/api/propose", PROPOSAL),
        ]:
            with self.subTest(endpoint=endpoint):
                status, body, _ = self.post_owner(endpoint, params, bearer=AGENT_KEY)
                self.assertEqual(status, 401, body)
        state = self.state(owner)
        self.assertFalse(state["paused"])
        self.assertEqual(len(state["requests"]), 1)
        self.assertEqual(state["requests"][0]["status"], "pending")
        self.assertIsNone(state["requests"][0]["result"])

    def test_owner_and_missing_credentials_cannot_impersonate_agent(self):
        owner = self.login()
        for headers in ({}, {"bearer": OWNER_KEY}, {"bearer": "invalid"}, {"cookie": owner}):
            with self.subTest(headers_present=tuple(headers)):
                status, body, _ = self.proposal(**headers)
                self.assertEqual(status, 401, body)
        self.assertEqual(self.state(owner)["requests"], [])

    def test_agent_endpoint_rejects_origin_even_with_bearer(self):
        for origin in (self.origin, "https://example.invalid", "null"):
            with self.subTest(origin=origin):
                status, body, _ = self.proposal(bearer=AGENT_KEY, origin=origin)
                self.assertEqual(status, 403, body)
        self.assertEqual(self.state(self.login())["requests"], [])

    def test_proposals_are_rate_limited_without_affecting_owner_decisions(self):
        owner = self.login()
        request_ids = []
        for _ in range(5):
            status, body, _ = self.proposal(bearer=AGENT_KEY)
            self.assertEqual(status, 202, body)
            request_ids.append(body["id"])
        status, body, _ = self.proposal(bearer=AGENT_KEY)
        self.assertEqual(status, 429, body)
        self.assertEqual(len(set(request_ids)), 5)
        self.assertEqual(len(self.state(owner)["requests"]), 5)
        status, body, _ = self.post_owner("/api/deny", {"id": request_ids[0]}, owner)
        self.assertEqual(status, 200, body)

    def test_agent_can_only_propose_supported_synthetic_action_target(self):
        for proposal in (
            {**PROPOSAL, "target": "non-demo-event"},
            {**PROPOSAL, "action": "read_google_calendar"},
            {**PROPOSAL, "approval": True},
            {**PROPOSAL, "target": 42},
        ):
            with self.subTest(proposal=proposal):
                status, body, _ = self.request("POST", "/api/agent/propose", proposal, bearer=AGENT_KEY)
                self.assertEqual(status, 400, body)
        self.assertEqual(self.state(self.login())["requests"], [])


if __name__ == "__main__":
    unittest.main()
