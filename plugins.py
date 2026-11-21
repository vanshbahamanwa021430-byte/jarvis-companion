"""Declarative plugin loader. Manifests only; no plugin-supplied code is executed."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from actions import ActionError

PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ALLOWED_PERMISSIONS = frozenset({
    "memory.write", "phone.alarm", "webhook.http", "phone.open_app",
})


def load_plugins(root: Path) -> list[dict[str, Any]]:
    plugins: list[dict[str, Any]] = []
    if not root.exists():
        return plugins
    if not root.is_dir():
        raise ActionError(400, "invalid_plugins", "Plugin path is not a directory.")
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ActionError(400, "invalid_plugin", f"Invalid manifest in {child.name}.") from exc
        plugins.append(validate_manifest(data, child.name))
    return plugins


def validate_manifest(data: Any, folder: str | None = None) -> dict[str, Any]:
    if type(data) is not dict:
        raise ActionError(400, "invalid_plugin", "Manifest must be an object.")
    plugin_id = data.get("id")
    if type(plugin_id) is not str or not PLUGIN_ID.fullmatch(plugin_id):
        raise ActionError(400, "invalid_plugin", "Invalid plugin id.")
    if folder and plugin_id != folder:
        raise ActionError(400, "invalid_plugin", "Plugin id must match its folder.")
    name = data.get("name")
    version = data.get("version")
    if type(name) is not str or not 1 <= len(name) <= 60:
        raise ActionError(400, "invalid_plugin", "Invalid plugin name.")
    if type(version) is not str or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ActionError(400, "invalid_plugin", "Invalid plugin version.")
    permissions = data.get("permissions") or []
    if type(permissions) is not list or any(p not in ALLOWED_PERMISSIONS for p in permissions):
        raise ActionError(400, "invalid_plugin", "Invalid plugin permissions.")
    actions = data.get("actions") or []
    if type(actions) is not list or not actions or len(actions) > 16:
        raise ActionError(400, "invalid_plugin", "Invalid plugin actions.")
    clean_actions = []
    for item in actions:
        if type(item) is not dict:
            raise ActionError(400, "invalid_plugin", "Invalid plugin actions.")
        action_id = item.get("id")
        label = item.get("label")
        permission = item.get("permission")
        if type(action_id) is not str or not PLUGIN_ID.fullmatch(action_id):
            raise ActionError(400, "invalid_plugin", "Invalid action id.")
        if type(label) is not str or not 1 <= len(label) <= 80:
            raise ActionError(400, "invalid_plugin", "Invalid action label.")
        if permission not in permissions:
            raise ActionError(400, "invalid_plugin", "Action permission is not declared.")
        risk = item.get("risk") or "low"
        if risk not in {"low", "high"}:
            raise ActionError(400, "invalid_plugin", "Invalid risk.")
        clean_actions.append({
            "id": action_id, "label": label, "permission": permission, "risk": risk,
            "params": item.get("params") if type(item.get("params")) is dict else {},
        })
    return {
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": data["description"] if type(data.get("description")) is str else "",
        "permissions": list(permissions),
        "actions": clean_actions,
        "enabled": True,
    }


class PluginRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.plugins = load_plugins(self.root)
        self._disabled: set[str] = set()

    def list(self) -> list[dict[str, Any]]:
        out = []
        for plugin in self.plugins:
            item = dict(plugin)
            item["enabled"] = plugin["id"] not in self._disabled
            out.append(item)
        return out

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        if not any(p["id"] == plugin_id for p in self.plugins):
            raise ActionError(404, "unknown_plugin", "Unknown plugin.")
        if enabled:
            self._disabled.discard(plugin_id)
        else:
            self._disabled.add(plugin_id)
        return next(p for p in self.list() if p["id"] == plugin_id)

    def get(self, plugin_id: str) -> dict[str, Any]:
        for plugin in self.list():
            if plugin["id"] == plugin_id:
                return plugin
        raise ActionError(404, "unknown_plugin", "Unknown plugin.")

    def run(self, plugin_id: str, action_id: str, params: dict[str, Any], hooks: dict[str, Any]) -> dict[str, Any]:
        plugin = self.get(plugin_id)
        if not plugin["enabled"]:
            raise ActionError(409, "plugin_disabled", "Plugin is disabled.")
        action = next((a for a in plugin["actions"] if a["id"] == action_id), None)
        if action is None:
            raise ActionError(404, "unknown_action", "Unknown plugin action.")
        permission = action["permission"]
        if permission == "memory.write":
            memory = hooks.get("memory")
            if memory is None:
                raise ActionError(409, "memory_disabled", "Workspace memory is off.")
            text = params.get("text") if type(params.get("text")) is str else "Voice note"
            return {"written": True, "text": text[:240]}
        if permission == "phone.alarm":
            # Plugin only describes the intent; the phone still has to run it
            # after a separate device approval if we wanted that. For this
            # example the desk records the request payload as a result.
            return {"queued_alarm": True, "params": {
                "hour": params.get("hour", 7), "minute": params.get("minute", 0),
            }}
        if permission == "webhook.http":
            send = hooks.get("webhook")
            if send is None:
                raise ActionError(409, "no_webhook", "No webhook runner.")
            return send(params)
        return {"ok": True}
