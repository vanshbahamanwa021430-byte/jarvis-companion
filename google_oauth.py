"""Google OAuth connector with least-privilege scopes and a placeholder client id.

Tokens are stored in a mode-0600 file on the desk. This build does not encrypt
that file at rest (stdlib only); protect the computer and the demo directory.
Write/send actions always need a fresh owner approval. No client secret is
shipped; paste a Desktop OAuth client id in config or the Connect tab.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from actions import ActionError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Least-privilege scopes per service. YouTube and Maps use on-device intents
# and do not need OAuth.
SCOPES = {
    "gmail": {
        "read": ("https://www.googleapis.com/auth/gmail.readonly",),
        "send": ("https://www.googleapis.com/auth/gmail.send",),
    },
    "calendar": {
        "read": ("https://www.googleapis.com/auth/calendar.readonly",),
        "write": ("https://www.googleapis.com/auth/calendar.events",),
    },
    "drive": {
        "read": ("https://www.googleapis.com/auth/drive.readonly",),
        "write": ("https://www.googleapis.com/auth/drive.file",),
    },
    "contacts": {
        "read": ("https://www.googleapis.com/auth/contacts.readonly",),
    },
    "tasks": {
        "read": ("https://www.googleapis.com/auth/tasks.readonly",),
        "write": ("https://www.googleapis.com/auth/tasks",),
    },
}

SERVICE_OF_KIND = {
    "google.gmail.read": ("gmail", "read"),
    "google.gmail.send": ("gmail", "send"),
    "google.calendar.read": ("calendar", "read"),
    "google.calendar.write": ("calendar", "write"),
    "google.drive.read": ("drive", "read"),
    "google.drive.write": ("drive", "write"),
    "google.contacts.read": ("contacts", "read"),
    "google.tasks.read": ("tasks", "read"),
    "google.tasks.write": ("tasks", "write"),
}

API_TEMPLATES = {
    "google.gmail.read": ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=5"),
    "google.calendar.read": ("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList"),
    "google.drive.read": ("GET", "https://www.googleapis.com/drive/v3/files?pageSize=5"),
    "google.contacts.read": ("GET", "https://people.googleapis.com/v1/people/me/connections?pageSize=5&personFields=names,emailAddresses"),
    "google.tasks.read": ("GET", "https://tasks.googleapis.com/tasks/v1/users/@me/lists"),
}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class GoogleConnector:
    def __init__(self, store_path: Path, *, client_id: str | None = None, transport: Any = None,
                 clock: Any = time.time):
        self.store_path = Path(store_path)
        self.client_id = (client_id or "").strip() or None
        self.transport = transport
        self.clock = clock
        self._pending: dict[str, Any] | None = None
        self._tokens: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {"services": {}}
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"services": {}}
        if type(data) is not dict or type(data.get("services")) is not dict:
            return {"services": {}}
        return data

    def _save(self) -> None:
        self.store_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tokens, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.store_path)
        os.chmod(self.store_path, 0o600)

    def set_client_id(self, client_id: str | None) -> None:
        if client_id is None or client_id == "":
            self.client_id = None
            return
        if type(client_id) is not str or not re_client(client_id):
            raise ActionError(400, "invalid_client_id", "Invalid Google client id.")
        self.client_id = client_id.strip()

    def status(self) -> dict[str, Any]:
        services = {}
        for name in SCOPES:
            record = self._tokens.get("services", {}).get(name) or {}
            services[name] = {
                "connected": bool(record.get("refresh_token") or record.get("access_token")),
                "scopes": list(record.get("scopes") or []),
            }
        return {"configured": bool(self.client_id), "client_id_set": bool(self.client_id), "services": services}

    def start(self, service: str, redirect_uri: str, access: str = "read") -> dict[str, Any]:
        if not self.client_id:
            raise ActionError(409, "google_not_configured", "Set a Google OAuth client id first.")
        if service not in SCOPES:
            raise ActionError(400, "unknown_service", "Unknown Google service.")
        if access not in SCOPES[service]:
            raise ActionError(400, "unknown_service", "That access level is not available.")
        # Incremental: request only the scopes for this access, plus any already granted.
        wanted = set(SCOPES[service][access])
        existing = self._tokens.get("services", {}).get(service, {}).get("scopes") or []
        wanted.update(existing)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(18)
        self._pending = {
            "state": state, "verifier": verifier, "service": service,
            "scopes": sorted(wanted), "redirect_uri": redirect_uri, "at": self.clock(),
        }
        query = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(sorted(wanted)),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        })
        return {"auth_url": f"{AUTH_URL}?{query}", "state": state, "service": service}

    def finish(self, params: dict[str, str]) -> dict[str, Any]:
        pending = self._pending
        self._pending = None
        if not pending:
            raise ActionError(400, "invalid_oauth", "No OAuth attempt is in progress.")
        if params.get("state") != pending["state"]:
            raise ActionError(400, "invalid_oauth", "OAuth state mismatch.")
        if params.get("error"):
            raise ActionError(400, "oauth_denied", "Google returned an error.")
        code = params.get("code")
        if not code:
            raise ActionError(400, "invalid_oauth", "Missing authorization code.")
        body = urllib.parse.urlencode({
            "client_id": self.client_id,
            "code": code,
            "code_verifier": pending["verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": pending["redirect_uri"],
        }).encode()
        data = self._post_token(body)
        services = self._tokens.setdefault("services", {})
        services[pending["service"]] = {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token") or services.get(pending["service"], {}).get("refresh_token"),
            "expiry": self.clock() + int(data.get("expires_in") or 0),
            "scopes": pending["scopes"],
        }
        self._save()
        return {"connected": pending["service"]}

    def disconnect(self, service: str) -> dict[str, Any]:
        if service not in SCOPES:
            raise ActionError(400, "unknown_service", "Unknown Google service.")
        self._tokens.setdefault("services", {}).pop(service, None)
        self._save()
        return self.status()

    def _post_token(self, body: bytes) -> dict[str, Any]:
        req = urllib.request.Request(
            TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "JarvisCompanion/2.2"},
            method="POST",
        )
        opener = self.transport or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=12) as resp:
                raw = resp.read(16_000)
        except urllib.error.URLError as exc:
            raise ActionError(502, "google_unreachable", "Google token endpoint was unreachable.") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ActionError(502, "google_rejected", "Invalid token response.") from exc
        if type(data) is not dict or not data.get("access_token"):
            raise ActionError(502, "google_rejected", "Invalid token response.")
        return data

    def access_token(self, service: str) -> str:
        record = self._tokens.get("services", {}).get(service) or {}
        token = record.get("access_token")
        if not token:
            raise ActionError(409, "google_not_connected", f"{service} is not connected.")
        return token

    def execute(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        pair = SERVICE_OF_KIND.get(kind)
        if pair is None:
            raise ActionError(400, "unknown_action", "Not a Google API action.")
        service, access = pair
        record = self._tokens.get("services", {}).get(service) or {}
        granted = set(record.get("scopes") or [])
        needed = set(SCOPES[service][access])
        if not needed <= granted:
            raise ActionError(409, "google_not_connected", f"{service} is missing the {access} scope.")
        token = self.access_token(service)
        if kind in API_TEMPLATES and access == "read":
            method, url = API_TEMPLATES[kind]
            if payload.get("query") and kind == "google.gmail.read":
                url += "&q=" + urllib.parse.quote(payload["query"])
            return {"service": service, "result": self._api(method, url, token)}
        # Writes are issued only after approval; still least-privilege endpoints.
        if kind == "google.gmail.send":
            # Do not actually send in tests without a transport; _api posts a tiny payload.
            raw = _rfc822(payload["to"], payload["subject"], payload["body"])
            return {"service": service, "result": self._api(
                "POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                token, {"raw": raw})}
        if kind == "google.calendar.write":
            return {"service": service, "result": self._api(
                "POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                token, {"summary": payload["title"], "start": {"dateTime": payload.get("start") or None},
                        "end": {"dateTime": payload.get("end") or None}})}
        if kind == "google.drive.write":
            return {"service": service, "result": self._api(
                "POST", "https://www.googleapis.com/drive/v3/files",
                token, {"name": payload["name"], "mimeType": "text/plain"})}
        if kind == "google.tasks.write":
            return {"service": service, "result": self._api(
                "POST", "https://tasks.googleapis.com/tasks/v1/users/@me/lists",
                token, {"title": payload["title"]})}
        raise ActionError(400, "unknown_action", "Not a Google API action.")

    def _api(self, method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                     "User-Agent": "JarvisCompanion/2.2",
                     **({"Content-Type": "application/json"} if data else {})},
            method=method,
        )
        opener = self.transport or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=12) as resp:
                raw = resp.read(16_000)
        except urllib.error.HTTPError as exc:
            raise ActionError(502, "google_rejected", "Google API rejected the request.") from exc
        except urllib.error.URLError as exc:
            raise ActionError(502, "google_unreachable", "Google API was unreachable.") from exc
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"raw": raw.decode("utf-8", "replace")[:500]}


def _rfc822(to: str, subject: str, body: str) -> str:
    # Gmail send API expects base64url raw messages. Import locally to keep module import cheap.
    import base64
    message = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    return base64.urlsafe_b64encode(message.encode("utf-8")).decode("ascii").rstrip("=")


def re_client(value: str) -> bool:
    # Google client ids look like 123456789-xxxxx.apps.googleusercontent.com
    text = value.strip()
    if len(text) < 20 or len(text) > 120 or " " in text:
        return False
    return text.endswith(".apps.googleusercontent.com") or text.replace("-", "").replace(".", "").isalnum()
