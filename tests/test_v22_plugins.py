import tempfile
import unittest
from pathlib import Path

from actions import ActionError
from plugins import PluginRegistry, load_plugins, validate_manifest

ROOT = Path(__file__).resolve().parents[1] / "plugins"


class ManifestTests(unittest.TestCase):
    def test_ships_three_example_plugins(self):
        plugins = load_plugins(ROOT)
        ids = {p["id"] for p in plugins}
        self.assertEqual(ids, {"local-note", "clock-alarm", "webhook-ping"})
        webhook = next(p for p in plugins if p["id"] == "webhook-ping")
        self.assertEqual(webhook["actions"][0]["risk"], "high")
        self.assertIn("webhook.http", webhook["permissions"])

    def test_rejects_path_traversal_and_unknown_permissions(self):
        with self.assertRaises(ActionError):
            validate_manifest({"id": "../evil", "name": "x", "version": "1.0.0",
                               "permissions": ["memory.write"],
                               "actions": [{"id": "a", "label": "A", "permission": "memory.write"}]})
        with self.assertRaises(ActionError):
            validate_manifest({"id": "ok", "name": "x", "version": "1.0.0",
                               "permissions": ["shell.exec"],
                               "actions": [{"id": "a", "label": "A", "permission": "shell.exec"}]})
        with self.assertRaises(ActionError):
            validate_manifest({"id": "ok", "name": "x", "version": "1.0.0",
                               "permissions": ["memory.write"],
                               "actions": [{"id": "a", "label": "A", "permission": "webhook.http"}]})

    def test_id_must_match_folder(self):
        temp = tempfile.TemporaryDirectory()
        folder = Path(temp.name) / "local-note"
        folder.mkdir()
        (folder / "manifest.json").write_text('{"id": "other", "name": "X", "version": "1.0.0",'
                                              '"permissions": ["memory.write"],'
                                              '"actions": [{"id": "add", "label": "Add",'
                                              '"permission": "memory.write"}]}')
        with self.assertRaises(ActionError):
            load_plugins(Path(temp.name))
        temp.cleanup()

    def test_enable_disable_and_run(self):
        registry = PluginRegistry(ROOT)
        registry.set_enabled("local-note", False)
        with self.assertRaises(ActionError) as ctx:
            registry.run("local-note", "add", {"text": "hi"}, {"memory": object()})
        self.assertEqual(ctx.exception.code, "plugin_disabled")
        registry.set_enabled("local-note", True)
        result = registry.run("local-note", "add", {"text": "buy milk"}, {"memory": object()})
        self.assertTrue(result["written"])
        self.assertEqual(result["text"], "buy milk")


if __name__ == "__main__":
    unittest.main()
