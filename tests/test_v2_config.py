"""Config file + CLI flags, with loopback-only and no-secrets-in-file guarantees."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from config import Config, ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write(self, data):
        path = Path(self.temp.name) / "c.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return str(path)

    def test_defaults(self):
        c = load_config([])
        self.assertEqual((c.port, c.manual, c.ttl_seconds, c.log_format), (8765, False, 300, "json"))
        self.assertIsNotNone(c.memory_db)

    def test_file_then_cli_precedence(self):
        path = self.write({"port": 9000, "manual": True, "ttl_seconds": 120, "log_format": "text"})
        c = load_config(["--config", path, "--port", "9100"])
        self.assertEqual((c.port, c.manual, c.ttl_seconds, c.log_format), (9100, True, 120, "text"))

    def test_no_memory_flag_and_null_memory_db(self):
        self.assertIsNone(load_config(["--no-memory"]).memory_db)
        self.assertIsNone(load_config(["--config", self.write({"memory_db": None})]).memory_db)

    def test_bind_address_and_secrets_cannot_be_configured(self):
        for key in ("host", "bind", "address", "proposal_key", "agent_key", "owner_key"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config(["--config", self.write({key: "0.0.0.0"})])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            load_config(["--host", "0.0.0.0"])  # no such flag

    def test_invalid_values_rejected(self):
        for data in ({"port": 70000}, {"port": "80"}, {"manual": "yes"}, {"ttl_seconds": 5},
                     {"session_minutes": 0}, {"worker_interval": 0}, {"log_level": "TRACE"},
                     {"db": 5}, {"unknown": 1}, {"db": "same.db", "memory_db": "same.db"}, [1, 2]):
            with self.subTest(data=data), self.assertRaises(ConfigError):
                load_config(["--config", self.write(data)])
        with self.assertRaises(ConfigError):
            load_config(["--config", self.write("{not json")])
        with self.assertRaises(ConfigError):
            load_config(["--config", str(Path(self.temp.name) / "missing.json")])
        with self.assertRaises(ConfigError):
            load_config(["--worker-interval", "nan"])

    def test_example_config_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "config.example.json"
        self.assertIsInstance(load_config(["--config", str(example)]), Config)

    def test_public_view_has_no_secret_fields(self):
        self.assertFalse({"key", "agent_key", "proposal_key"} & set(Config().public()))


if __name__ == "__main__":
    unittest.main()
