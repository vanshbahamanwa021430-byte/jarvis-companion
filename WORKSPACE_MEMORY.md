# Working memory first increment

This is a persistent project-memory module and executable synthetic job, not a finished Jarvis memory feature. v2 wires it into the loopback owner dashboard (Memory tab, owner session only); it is still not wired into a live model, and the agent/proposal key cannot reach it. Keep all existing one-time approval requirements. Do not import personal or account data.

## What the example proves

Run `python workspace_example.py` from this directory. It creates a temporary SQLite database, records a sample project, task, failed run, passing regression, evidence, and a scoped lesson. It reopens the database, checks that an equally named sibling project lesson is absent, retrieves the relevant verified lesson, and uses it to rerun the check on a related task. It edits and deletes the lesson, reopens the database again, and verifies the old text cannot be retrieved. This tests a synthetic 3D scene contract, **not a rendered website**.

Run `python -m unittest discover -s tests -v` for the test suite. The store takes explicit workspace and project IDs from trusted code. Those IDs alone are not authorization. A future authenticated API must supply the approved scope, never infer it from an AI-provided tag; private data remains out of scope until that integration is verified. SQLite logical deletion in this demo means a record vanishes from supported retrieval, not guaranteed physical erasure from files or backups.

The upstream [OpenJarvis SQLite wrapper](https://github.com/open-jarvis/OpenJarvis/blob/main/src/openjarvis/tools/storage/sqlite.py) persists metadata, but its documented retrieval path does not apply custom project metadata as a scope. Companion filters its own rows by workspace/project before returning or ranking records. Disable upstream automatic memory for the first live engine check. Keep branch and private access authority outside the model.

## Still needed before the real feature

Wire the authenticated session and actual OpenJarvis task lifecycle into the store. Add immutable version lineage where needed, revision views, retention, secure deletion guarantees as appropriate, and owner-kept policy. Verify persistence across actual service restarts and on the same Android session, and show real run evidence. No runtime, connector, account, personal data, or Android device was tested for this milestone.
