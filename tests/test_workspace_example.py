import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workspace_example import example
from workspace_memory import WorkspaceMemory


class WorkspaceExampleTests(unittest.TestCase):
    def test_verified_lesson_reuse_and_forget(self):
        with tempfile.TemporaryDirectory() as temp:
            result = example(Path(temp) / "workspace.sqlite3")
            for label in ("failed_before_fix", "passed_after_fix", "reused_after_reopen",
                          "sibling_excluded", "edited_and_deleted_lessons_removed"):
                self.assertIs(result[label], True)

    def test_retrieval_survives_new_python_process(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "workspace.sqlite3"
            m = WorkspaceMemory(path)
            w = m.create_workspace("Synthetic studio")["id"]
            p = m.create_project(w, "One")["id"]
            a = m.create_record(w, p, "lesson", "Persistent lesson", "Unique check")["id"]
            code = ("import json, sys; from workspace_memory import WorkspaceMemory; "
                    "m=WorkspaceMemory(sys.argv[1]); "
                    "print(json.dumps(m.search(sys.argv[2],sys.argv[3],'Unique check',kind='lesson')))")
            cmd = [sys.executable, "-c", code, str(path), w, p]
            self.assertEqual([r["id"] for r in json.loads(subprocess.check_output(cmd, text=True))], [a])
            m.delete_record(w, p, a)
            self.assertEqual(json.loads(subprocess.check_output(cmd, text=True)), [])
