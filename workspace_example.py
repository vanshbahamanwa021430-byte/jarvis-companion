"""A repo-owned synthetic work sample; never connects to personal resources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from workspace_memory import WorkspaceMemory


def validate_walkthrough(spec: dict) -> list[str]:
    """Validate a synthetic scene contract, not an actual browser render."""
    issues = []
    if spec.get("renderer") != "3d":
        issues.append("renderer must use 3d")
    positions = spec.get("camera_z", [])
    if len(positions) < 3 or not any(x < 0 for x in positions) or not any(x > 0 for x in positions):
        issues.append("camera path must cross doorway")
    return issues


def example(path: Path) -> dict:
    memory = WorkspaceMemory(path)
    ws = memory.create_workspace("Synthetic studio")["id"]
    one = memory.create_project(ws, "Example one")["id"]
    other = memory.create_project(ws, "Example two")["id"]
    task = memory.create_record(ws, one, "task", "Preview the walkthrough")["id"]

    first_errors = validate_walkthrough({"renderer": "2d", "camera_z": [0]})
    first = memory.create_record(ws, one, "run", "First walkthrough check", json.dumps(first_errors), parent_id=task)["id"]
    checked = validate_walkthrough({"renderer": "3d", "camera_z": [-5, 0, 3]})
    if not first_errors or checked:
        raise RuntimeError("The sample must fail before the fix and pass afterward.")
    passed = memory.create_record(ws, one, "run", "Regression check passed", "Fixed synthetic specification.", parent_id=task)["id"]
    evidence = memory.create_record(ws, one, "evidence", "Regression result", json.dumps({"passed": not checked}), parent_id=task)["id"]
    memory.link(ws, one, evidence, passed, "supports")
    lesson = memory.create_record(ws, one, "lesson", "Walkthrough renderer lesson",
        json.dumps({"check": "doorway_contract_v1", "status": "verified",
                    "note": "Do not use a flat preview; check renderer mode and doorway-crossing path."}),
        parent_id=task)["id"]
    memory.link(ws, one, lesson, evidence, "derived_from")
    other_task = memory.create_record(ws, other, "task", "Other walkthrough")["id"]
    sibling = memory.create_record(ws, other, "lesson", "Walkthrough renderer other lesson",
        "Different project fixture.", parent_id=other_task)["id"]

    reopened = WorkspaceMemory(path)
    matching = reopened.search(ws, one, "Walkthrough renderer", kind="lesson")
    if [record["id"] for record in matching] != [lesson] or not reopened.links(ws, one, lesson):
        raise RuntimeError("Scope, persistence or evidence failure.")
    followup = reopened.create_record(ws, one, "task", "Related walkthrough follow-up")["id"]
    followup_spec = {"renderer": "3d", "camera_z": [-2, -1, 1]}
    payload = json.loads(matching[0]["body"])
    validators = {"doorway_contract_v1": validate_walkthrough}
    validator = validators.get(payload.get("check")) if payload.get("status") == "verified" else None
    reused = validator(followup_spec) if validator else ["No verified allowlisted check"]
    if reused:
        raise RuntimeError("Follow-up regression failed.")
    actual = reopened.create_record(ws, one, "run", "Follow-up check passed", "Reused scene contract check.",
                                  parent_id=followup)["id"]
    reopened.link(ws, one, actual, lesson, "derived_from")
    if not reopened.links(ws, one, actual):
        raise RuntimeError("Missing follow-up provenance.")
    reopened.update_record(ws, one, lesson, title="Geometry prerequisite", body="Current guidance")
    updated = WorkspaceMemory(path)
    if updated.search(ws, one, "flat preview", kind="lesson") or updated.search(ws, one, "Walkthrough renderer", kind="lesson"):
        raise RuntimeError("Outdated lesson remained searchable.")
    updated.delete_record(ws, one, lesson)
    final = WorkspaceMemory(path)
    if final.search(ws, one, "Geometry prerequisite", kind="lesson"):
        raise RuntimeError("Deleted lesson remained searchable.")
    if [r["id"] for r in final.search(ws, other, "Walkthrough renderer", kind="lesson")] != [sibling]:
        raise RuntimeError("Sibling evidence was unexpectedly modified.")
    return {"first_run": first, "regression_run": passed, "followup_run": actual,
            "failed_before_fix": True, "passed_after_fix": True,
            "reused_after_reopen": True, "sibling_excluded": True,
            "edited_and_deleted_lessons_removed": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    with TemporaryDirectory(prefix="jarvis-sample-") as temp:
        print(json.dumps(example(Path(temp) / "workspace.sqlite3"), indent=2))
