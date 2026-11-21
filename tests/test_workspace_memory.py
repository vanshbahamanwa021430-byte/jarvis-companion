"""Independent acceptance tests for scoped local workspace memory."""
import tempfile
import unittest
from pathlib import Path

from workspace_memory import MAX_TASK_DEPTH, WorkspaceMemory, WorkspaceMemoryError


class WorkspaceMemoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "workspace.sqlite3"
        self.memory = WorkspaceMemory(self.db)
        self.owner = self.memory.create_workspace("Owner")["id"]
        self.other = self.memory.create_workspace("Another")["id"]
        self.scope = (self.owner, self.memory.create_project(self.owner, "Current")["id"])
        self.sibling = (self.owner, self.memory.create_project(self.owner, "Sibling")["id"])
        self.foreign = (self.other, self.memory.create_project(self.other, "Foreign")["id"])

    def add(self, scope, kind, title, body="", parent_id=None):
        return self.memory.create_record(*scope, kind, title, body, parent_id)["id"]

    def assertCode(self, code, call, *args, **kwargs):
        with self.assertRaises(WorkspaceMemoryError) as result:
            call(*args, **kwargs)
        self.assertEqual(result.exception.code, code)

    def test_project_list_respects_workspace_before_limit(self):
        latest = self.memory.create_project(self.owner, "Latest")["id"]
        self.memory.create_project(self.other, "Newer unrelated project")
        self.assertEqual([row["id"] for row in self.memory.list_projects(self.owner, limit=1)], [latest])
        self.assertEqual(
            {row["id"] for row in self.memory.list_projects(self.owner)},
            {self.scope[1], self.sibling[1], latest},
        )
        self.assertCode("invalid_limit", self.memory.list_projects, self.owner, limit=0)

    def test_search_scope_precedes_limits_and_matches_text_literally(self):
        first = self.add(self.scope, "lesson", "First", "Progress 50%: Straße")
        self.add(self.scope, "task", "Second", "Progress 50%: Straße")
        self.add(self.scope, "lesson", "Not a percent match", "Progress 50X")
        self.add(self.sibling, "lesson", "Unrelated", "Progress 50%: Straße")
        self.add(self.foreign, "lesson", "Foreign", "Progress 50%: Straße")
        results = self.memory.search(*self.scope, "50%: STRASSE", kind="lesson", limit=1)
        self.assertEqual([record["id"] for record in results], [first])
        self.assertTrue(all(record["project_id"] == self.scope[1] for record in self.memory.search(*self.scope, "")))
        self.assertCode("invalid_limit", self.memory.search, *self.scope, "", limit=101)
        self.assertCode("invalid_limit", self.memory.search, *self.scope, "", limit=True)
        self.assertCode("not_found", self.memory.search, self.other, self.scope[1], "")

    def test_get_update_delete_and_link_query_do_not_cross_scope(self):
        original = self.add(self.scope, "lesson", "Original", "Original body")
        for wrong in (self.sibling, self.foreign, (self.other, self.scope[1])):
            with self.subTest(wrong=wrong):
                self.assertCode("not_found", self.memory.get_record, *wrong, original)
                self.assertCode("not_found", self.memory.update_record, *wrong, original, title="Changed")
                self.assertCode("not_found", self.memory.delete_record, *wrong, original)
                self.assertCode("not_found", self.memory.links, *wrong, original)
        self.assertEqual(self.memory.get_record(*self.scope, original)["title"], "Original")

    def test_provenance_is_typed_same_project_and_directional(self):
        lesson = self.add(self.scope, "lesson", "Verified lesson")
        evidence = self.add(self.scope, "evidence", "Test receipt")
        run = self.add(self.scope, "run", "Run 12")
        wrong = self.add(self.sibling, "evidence", "Wrong project test receipt")
        other = self.add(self.foreign, "run", "Other workspace run")
        first = self.memory.link(*self.scope, lesson, evidence, "derived_from")
        second = self.memory.link(*self.scope, lesson, run, "tested_by")
        self.assertEqual(self.memory.link(*self.scope, lesson, evidence, "derived_from"), first)
        self.assertEqual(self.memory.links(*self.scope, lesson, limit=1), [second])
        self.assertEqual(self.memory.links(*self.scope, evidence), [first])
        self.assertEqual((first["source_id"], first["target_id"], first["relation"]), (lesson, evidence, "derived_from"))
        self.assertCode("invalid_relation", self.memory.link, *self.scope, lesson, evidence, "unknown")
        self.assertCode("invalid_relation", self.memory.link, *self.scope, lesson, lesson, "derived_from")
        self.assertCode("not_found", self.memory.link, *self.scope, lesson, wrong, "derived_from")
        self.assertCode("not_found", self.memory.link, *self.scope, other, evidence, "tested_by")
        self.assertEqual(len(self.memory.links(*self.scope, lesson)), 2)
        self.assertEqual(self.memory.links(*self.sibling, wrong), [])
        self.assertEqual(self.memory.links(*self.foreign, other), [])

    def test_nested_tree_validates_parent_caps_depth_and_cascades(self):
        task = self.add(self.scope, "task", "Root task")
        subtask = self.add(self.scope, "task", "Subtask", parent_id=task)
        evidence = self.add(self.scope, "evidence", "Subtask evidence", parent_id=subtask)
        self.assertEqual(self.memory.get_record(*self.scope, evidence)["parent_id"], subtask)
        self.assertCode(
            "parent_must_be_task", self.add, self.scope, "task", "Wrong parent", parent_id=evidence,
        )
        outside = self.add(self.sibling, "task", "Other project parent")
        self.assertCode("not_found", self.add, self.scope, "task", "Wrong scope", parent_id=outside)
        parent = task
        for depth in range(1, MAX_TASK_DEPTH + 1):
            parent = self.add(self.scope, "task", f"Level {depth}", parent_id=parent)
        self.assertCode("max_depth", self.add, self.scope, "task", "Excess depth", parent_id=parent)
        lesson = self.add(self.scope, "lesson", "Independent")
        self.memory.link(*self.scope, lesson, evidence, "derived_from")
        self.assertTrue(self.memory.delete_record(*self.scope, task))
        self.assertEqual(self.memory.links(*self.scope, lesson), [])
        self.assertEqual([item["id"] for item in self.memory.search(*self.scope, "")], [lesson])
        self.assertEqual(self.memory.get_record(*self.sibling, outside)["title"], "Other project parent")

    def test_durable_reopen_update_and_delete(self):
        child = self.add(self.scope, "run", "Original name", "Original body")
        evidence = self.add(self.scope, "evidence", "CI report")
        self.memory.link(*self.scope, child, evidence, "supports")
        self.memory = WorkspaceMemory(self.db)
        first = self.memory.get_record(*self.scope, child)
        self.assertEqual(first["version"], 1)
        updated = self.memory.update_record(*self.scope, child, body="New body")
        self.assertEqual(updated["title"], first["title"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertEqual(updated["version"], 2)
        self.assertGreaterEqual(updated["updated_at"], first["updated_at"])
        self.memory = WorkspaceMemory(self.db)
        self.assertEqual(self.memory.get_record(*self.scope, child), updated)
        self.assertEqual(len(self.memory.links(*self.scope, evidence)), 1)
        self.assertTrue(self.memory.delete_record(*self.scope, child))
        self.memory = WorkspaceMemory(self.db)
        self.assertCode("not_found", self.memory.get_record, *self.scope, child)
        self.assertEqual(self.memory.links(*self.scope, evidence), [])
        self.assertEqual(self.memory.get_record(*self.scope, evidence)["title"], "CI report")


if __name__ == "__main__":
    unittest.main()
