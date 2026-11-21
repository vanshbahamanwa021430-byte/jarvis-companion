"""Configuration: defaults < JSON config file < CLI flags.

The bind address is IPv4 loopback (127.0.0.1) by default. `host`, `bind` and
`address` cannot be set. To listen on the LAN, set `allow_lan` to true or pass
`--allow-lan`; the server then binds 0.0.0.0 and prints a warning. Secrets
(the proposal key) come only from the environment, never from the config file,
so the file can be shared or committed safely.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Sequence

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMATS = ("json", "text")
_FORBIDDEN_KEYS = {
    "google_client_secret": "Do not put a Google client secret in the config file. Use a Desktop OAuth client with PKCE, or set GOOGLE_CLIENT_SECRET in the environment.",
    "google_tokens": "Token files are not configured here.",
    "host": "The bind address is fixed to 127.0.0.1 (loopback only) unless you set allow_lan or pass --allow-lan.",
    "bind": "The bind address is fixed to 127.0.0.1 (loopback only) unless you set allow_lan or pass --allow-lan.",
    "address": "The bind address is fixed to 127.0.0.1 (loopback only) unless you set allow_lan or pass --allow-lan.",
    "proposal_key": "Set JARVIS_PROPOSAL_KEY in the environment; never store keys in config files.",
    "agent_key": "Set JARVIS_PROPOSAL_KEY in the environment; never store keys in config files.",
    "owner_key": "The owner key is generated fresh at every start and never configured.",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    port: int = 8765
    db: Path = Path(".local-demo/broker.sqlite3")
    memory_db: Path | None = Path(".local-demo/memory.sqlite3")
    manual: bool = False
    ttl_seconds: int = 300
    session_minutes: int = 60
    worker_interval: float = 0.35
    log_level: str = "INFO"
    log_format: str = "json"
    allow_lan: bool = False
    openjarvis_url: str | None = None
    google_client_id: str | None = None
    plugins_dir: Path | None = None

    def validate(self) -> "Config":
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ConfigError("port must be an integer between 0 and 65535")
        if type(self.manual) is not bool:
            raise ConfigError("manual must be true or false")
        if type(self.ttl_seconds) is not int or not 10 <= self.ttl_seconds <= 3600:
            raise ConfigError("ttl_seconds must be an integer between 10 and 3600")
        if type(self.session_minutes) is not int or not 5 <= self.session_minutes <= 1440:
            raise ConfigError("session_minutes must be an integer between 5 and 1440")
        if (type(self.worker_interval) not in (int, float) or not math.isfinite(self.worker_interval)
                or not 0.05 <= self.worker_interval <= 10):
            raise ConfigError("worker_interval must be between 0.05 and 10 seconds")
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(f"log_level must be one of {', '.join(LOG_LEVELS)}")
        if self.log_format not in LOG_FORMATS:
            raise ConfigError(f"log_format must be one of {', '.join(LOG_FORMATS)}")
        if type(self.allow_lan) is not bool:
            raise ConfigError("allow_lan must be true or false")
        if self.openjarvis_url is not None:
            from brain import BrainError, validate_openjarvis_url
            try:
                self = replace(self, openjarvis_url=validate_openjarvis_url(self.openjarvis_url))
            except BrainError:
                raise ConfigError("openjarvis_url must be an http(s) loopback or private LAN address") from None
        if self.google_client_id is not None:
            from google_oauth import re_client
            if type(self.google_client_id) is not str or not re_client(self.google_client_id):
                raise ConfigError("google_client_id must be a Google OAuth client id or null")
        if self.plugins_dir is not None:
            if not isinstance(self.plugins_dir, (str, Path)):
                raise ConfigError("plugins_dir must be a path or null")
            self = replace(self, plugins_dir=Path(self.plugins_dir))
        if not str(self.db) or str(self.db) == ":memory:":
            raise ConfigError("db must be a file path")
        if self.memory_db is not None and (not str(self.memory_db) or str(self.memory_db) == ":memory:"):
            raise ConfigError("memory_db must be a file path or null")
        if self.memory_db is not None and Path(self.memory_db).resolve() == Path(self.db).resolve():
            raise ConfigError("memory_db must be a different file from db")
        return self

    def public(self) -> dict[str, Any]:
        """Loggable view (contains no secrets)."""
        data = asdict(self)
        data["db"] = str(self.db)
        data["memory_db"] = None if self.memory_db is None else str(self.memory_db)
        data["plugins_dir"] = None if self.plugins_dir is None else str(self.plugins_dir)
        return data


def _from_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {exc.strerror or exc}") from None
    except ValueError:
        raise ConfigError("config file is not valid JSON") from None
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a JSON object")
    known = {f.name for f in fields(Config)}
    for key in raw:
        if key in _FORBIDDEN_KEYS:
            raise ConfigError(f"'{key}' is not allowed: {_FORBIDDEN_KEYS[key]}")
        if key not in known:
            raise ConfigError(f"unknown config key '{key}'")
    values = dict(raw)
    if "db" in values:
        if not isinstance(values["db"], str):
            raise ConfigError("db must be a string path")
        values["db"] = Path(values["db"])
    if "memory_db" in values:
        if values["memory_db"] is not None and not isinstance(values["memory_db"], str):
            raise ConfigError("memory_db must be a string path or null")
        values["memory_db"] = None if values["memory_db"] is None else Path(values["memory_db"])
    if "openjarvis_url" in values and values["openjarvis_url"] is not None:
        if not isinstance(values["openjarvis_url"], str):
            raise ConfigError("openjarvis_url must be a string or null")
    if "google_client_id" in values and values["google_client_id"] is not None:
        if not isinstance(values["google_client_id"], str):
            raise ConfigError("google_client_id must be a string or null")
    if "plugins_dir" in values and values["plugins_dir"] is not None:
        if not isinstance(values["plugins_dir"], str):
            raise ConfigError("plugins_dir must be a string path or null")
        values["plugins_dir"] = Path(values["plugins_dir"])
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Jarvis Companion: single-user approval desk (demo data only). Binds loopback unless --allow-lan is set.",
    )
    parser.add_argument("--config", type=Path, help="JSON config file (see config.example.json)")
    parser.add_argument("--port", type=int, help="loopback port (0 = pick a free port; default 8765)")
    parser.add_argument("--db", type=Path, help="broker SQLite file")
    parser.add_argument("--memory-db", type=Path, help="workspace memory SQLite file")
    parser.add_argument("--no-memory", action="store_true", help="disable the workspace memory panel")
    parser.add_argument("--manual", action="store_true", default=None,
                        help="don't run approved demo jobs automatically")
    parser.add_argument("--ttl-seconds", type=int, help="request/approval lifetime (10-3600)")
    parser.add_argument("--session-minutes", type=int, help="owner session lifetime (5-1440)")
    parser.add_argument("--worker-interval", type=float, help="worker poll interval in seconds")
    parser.add_argument("--log-level", choices=LOG_LEVELS)
    parser.add_argument("--log-format", choices=LOG_FORMATS)
    parser.add_argument("--openjarvis-url", dest="openjarvis_url",
                        help="OpenJarvis API base URL (http://127.0.0.1:8000). Loopback or private LAN only.")
    parser.add_argument("--google-client-id", dest="google_client_id",
                        help="Google OAuth Desktop client id (not a secret). See docs/GOOGLE_OAUTH.md.")
    parser.add_argument("--plugins-dir", dest="plugins_dir", type=Path,
                        help="Directory of declarative plugin manifests")
    parser.add_argument(
        "--allow-lan", dest="allow_lan", action="store_true", default=None,
        help="bind on all interfaces so a phone on the same LAN can open the dashboard. "
             "Default is loopback-only. Prints a warning. Do not use on an untrusted network.",
    )
    return parser


def load_config(argv: Sequence[str] | None = None) -> Config:
    args = build_parser().parse_args(argv)
    config = Config()
    if args.config is not None:
        config = replace(config, **_from_file(args.config))
    overrides = {
        "port": args.port, "db": args.db, "memory_db": args.memory_db, "manual": args.manual,
        "ttl_seconds": args.ttl_seconds, "session_minutes": args.session_minutes,
        "worker_interval": args.worker_interval, "log_level": args.log_level,
        "log_format": args.log_format, "allow_lan": args.allow_lan, "openjarvis_url": args.openjarvis_url,
        "google_client_id": args.google_client_id, "plugins_dir": args.plugins_dir,
    }
    config = replace(config, **{k: v for k, v in overrides.items() if v is not None})
    if args.no_memory:
        config = replace(config, memory_db=None)
    return config.validate()
