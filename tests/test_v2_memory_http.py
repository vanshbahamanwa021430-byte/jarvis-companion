"""Workspace memory wired into the owner dashboard API (owner-only)."""
import unittest

from v2_helpers import AGENT_KEY, LiveServer


class MemoryHttpTests(unittest.TestCase):
    def setUp(self):
        self.s = LiveServer(agent=True)
        self.addCleanup(self.s.close)
        self.s.login()

    def post(self, path, value):
        return self.s.request("POST", "/api/memory/" + path, value)

    def seed(self):
        ws = self.post("workspaces", {"title": "Study"})[1]
        project = self.post("projects", {"workspace": ws["id"], "title": "Exam week"})[1]
        return ws["id"], project["id"]

    def test_create_list_search_update_delete_round_trip(self):
        ws, project = self.seed()
        self.assertEqual([w["id"] for w in self.s.request("GET", "/api/memory/workspaces")[1]["items"]], [ws])
        self.assertEqual([p["id"] for p in self.s.request("GET", f"/api/memory/projects?workspace={ws}")[1]["items"]], [project])
        status, task, _ = self.post("records", {"workspace": ws, "project": project, "kind": "task", "title": "Revise algebra"})
        self.assertEqual(status, 201, task)
        status, note, _ = self.post("records", {"workspace": ws, "project": project, "kind": "lesson",
                                                "title": "Flashcards help", "body": "<script>alert(1)</script>",
                                                "parent": task["id"]})
        self.assertEqual(status, 201, note)
        found = self.s.request("GET", f"/api/memory/records?workspace={ws}&project={project}&q=flashcards")[1]["items"]
        self.assertEqual([r["id"] for r in found], [note["id"]])
        self.assertEqual(found[0]["body"], "<script>alert(1)</script>")  # stored as inert text
        lessons = self.s.request("GET", f"/api/memory/records?workspace={ws}&project={project}&kind=lesson")[1]["items"]
        self.assertEqual(len(lessons), 1)
        status, updated, _ = self.post("records/update", {"workspace": ws, "project": project, "id": note["id"], "title": "Spaced repetition"})
        self.assertEqual((status, updated["version"]), (200, 2))
        status, _, _ = self.post("records/delete", {"workspace": ws, "project": project, "id": task["id"]})
        self.assertEqual(status, 200)
        remaining = self.s.request("GET", f"/api/memory/records?workspace={ws}&project={project}")[1]["items"]
        self.assertEqual(remaining, [])  # child cascaded with its task

    def test_scope_is_enforced_and_errors_are_mapped(self):
        ws, project = self.seed()
        other_ws, other_project = self.seed()
        rec = self.post("records", {"workspace": ws, "project": project, "kind": "evidence", "title": "A"})[1]
        cross = self.s.request("GET", f"/api/memory/records?workspace={other_ws}&project={project}")
        self.assertEqual(cross[0], 404)
        status, body, _ = self.post("records/delete", {"workspace": other_ws, "project": other_project, "id": rec["id"]})
        self.assertEqual((status, body["error"]), (404, "not_found"))
        for path, value, code in (
            ("records", {"workspace": ws, "project": project, "kind": "shell", "title": "x"}, "invalid_kind"),
            ("records", {"workspace": ws, "project": project, "kind": "task", "title": ""}, "invalid_text"),
            ("records", {"workspace": ws, "project": project, "kind": "task", "title": "x", "extra": 1}, "invalid_fields"),
            ("workspaces", {"title": "a\u0000b"}, "invalid_text"),
            ("records", {"workspace": ws, "project": project, "kind": "task", "title": "x", "parent": rec["id"]}, "parent_must_be_task"),
        ):
            with self.subTest(code=code):
                status, body, _ = self.post(path, value)
                self.assertEqual((status, body["error"]), (400, code))
        self.assertEqual(self.s.request("GET", "/api/memory/records?workspace=x")[0], 400)
        self.assertEqual(self.s.request("GET", "/api/memory/nope")[0], 404)
        self.assertEqual(self.post("nope", {"a": 1})[0], 404)

    def test_agent_key_and_cross_origin_cannot_touch_memory(self):
        ws, project = self.seed()
        status, _, _ = self.s.raw("GET", "/api/memory/workspaces", headers={"Authorization": "Bearer " + AGENT_KEY})
        self.assertEqual(status, 401)
        status, _, _ = self.s.raw("POST", "/api/memory/workspaces", b'{"title":"x"}', {
            "Authorization": "Bearer " + AGENT_KEY, "Content-Type": "application/json",
            "Origin": "http://" + self.s.host})
        self.assertEqual(status, 401)
        status, _, _ = self.s.request("POST", "/api/memory/workspaces", {"title": "x"}, Origin="http://evil.invalid")
        self.assertEqual(status, 403)
        status, _, _ = self.s.request("GET", "/api/memory/workspaces", **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(len(self.s.request("GET", "/api/memory/workspaces")[1]["items"]), 1)

    def test_memory_can_be_disabled(self):
        s = LiveServer(memory=False)
        self.addCleanup(s.close)
        s.login()
        self.assertEqual(s.request("GET", "/api/memory/workspaces")[1]["error"], "memory_disabled")
        self.assertEqual(s.request("POST", "/api/memory/workspaces", {"title": "x"})[0], 404)
        self.assertFalse(s.request("GET", "/api/state")[1]["memory"])

    def test_large_record_body_fits_owner_body_limit(self):
        ws, project = self.seed()
        status, rec, _ = self.post("records", {"workspace": ws, "project": project, "kind": "artifact",
                                               "title": "Long", "body": "x" * 16000})
        self.assertEqual(status, 201, rec)


class ListWorkspacesTests(unittest.TestCase):
    def test_list_workspaces_newest_first_and_limited(self):
        import tempfile
        from pathlib import Path
        from workspace_memory import WorkspaceMemory, WorkspaceMemoryError
        with tempfile.TemporaryDirectory() as d:
            m = WorkspaceMemory(Path(d) / "m.db")
            ids = [m.create_workspace(f"W{i}")["id"] for i in range(3)]
            self.assertEqual({w["id"] for w in m.list_workspaces()}, set(ids))
            self.assertEqual(len(m.list_workspaces(limit=2)), 2)
            with self.assertRaises(WorkspaceMemoryError):
                m.list_workspaces(limit=0)


if __name__ == "__main__":
    unittest.main()
