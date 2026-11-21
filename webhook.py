"""Generic HTTP/webhook connector. Every request is an approved desk action.

Default policy: only http(s) to localhost, 127.0.0.1, or RFC1918 private IPv4.
Link-local (including cloud metadata 169.254.169.254), public IPs, hostnames,
redirects, and userinfo are rejected.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import urllib.error

import ipaddress

from actions import ActionError

MAX_CONNECTORS = 16
MAX_BODY = 4000
MAX_RESPONSE = 16_000
TIMEOUT = 8


def is_private_ipv4(host: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return False
    if host != ip.compressed:
        return False
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_link_local:
        return False
    return bool(ip.is_loopback or ip.is_private)


def validate_webhook_url(raw: Any) -> str:
    if type(raw) is not str:
        raise ActionError(400, "invalid_url", "Invalid webhook URL.")
    text = raw.strip()
    if "://" not in text:
        text = "http://" + text
    if " " in text or "\\" in text or "@" in text:
        raise ActionError(400, "invalid_url", "Invalid webhook URL.")
    parsed = urlparse(text)
    if (parsed.scheme or "").lower() not in {"http", "https"}:
        raise ActionError(400, "invalid_url", "Invalid webhook URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ActionError(400, "invalid_url", "Invalid webhook URL.")
    host = (parsed.hostname or "").lower()
    if host == "localhost":
        host = "127.0.0.1"
    if not is_private_ipv4(host):
        raise ActionError(400, "invalid_url", "Webhook URLs must be loopback or private IPv4.")
    path = parsed.path or ""
    if ".." in path:
        raise ActionError(400, "invalid_url", "Invalid webhook URL.")
    if path not in {"", "/"}:
        raise ActionError(400, "invalid_url", "Store only the origin; the path is chosen per request.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{host}:{port}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002
        raise ActionError(502, "redirect_blocked", "Redirects are not followed.")


@dataclass
class WebhookRegistry:
    connectors: list[dict[str, Any]] = field(default_factory=list)
    transport: Any = None  # optional injectable opener factory for tests

    def list(self) -> list[dict[str, Any]]:
        return list(self.connectors)

    def add(self, name: str, url: str) -> dict[str, Any]:
        if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}", name.strip()):
            raise ActionError(400, "invalid_name", "Invalid connector name.")
        if len(self.connectors) >= MAX_CONNECTORS:
            raise ActionError(429, "too_many", "Too many webhook connectors.")
        origin = validate_webhook_url(url)
        item = {"id": uuid.uuid4().hex, "name": name.strip(), "url": origin}
        self.connectors.append(item)
        return item

    def remove(self, connector_id: str) -> None:
        before = len(self.connectors)
        self.connectors = [c for c in self.connectors if c["id"] != connector_id]
        if len(self.connectors) == before:
            raise ActionError(404, "unknown_connector", "Unknown webhook connector.")

    def get(self, connector_id: str) -> dict[str, Any]:
        for item in self.connectors:
            if item["id"] == connector_id:
                return item
        raise ActionError(404, "unknown_connector", "Unknown webhook connector.")

    def send(self, connector_id: str, method: str, path: str, body: str = "") -> dict[str, Any]:
        connector = self.get(connector_id)
        if type(path) is not str or not path.startswith("/") or ".." in path or len(path) > 200:
            raise ActionError(400, "invalid_payload", "Invalid path.")
        target = connector["url"].rstrip("/") + path
        parsed = urlparse(target)
        origin = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
        if validate_webhook_url(origin) != connector["url"]:
            raise ActionError(400, "invalid_url", "Webhook target drifted off the allow-listed origin.")
        data = body.encode("utf-8") if body else None
        if data and len(data) > MAX_BODY:
            raise ActionError(400, "invalid_payload", "Body is too large.")
        headers = {"Accept": "application/json, text/plain;q=0.9", "User-Agent": "JarvisCompanion/2.2"}
        if data is not None:
            headers["Content-Type"] = "application/json" if body[:1] in "{[" else "text/plain"
        req = Request(target, data=data, headers=headers, method=method)
        opener = self.transport or build_opener(ProxyHandler({}), _NoRedirect)
        try:
            with opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read(MAX_RESPONSE + 1)
                status = resp.status
        except ActionError:
            raise
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE) if exc.fp else b""
            status = exc.code
        except urllib.error.URLError as exc:
            raise ActionError(502, "webhook_unreachable", "Webhook endpoint was unreachable.") from exc
        if len(raw) > MAX_RESPONSE:
            raw = raw[:MAX_RESPONSE]
        preview = raw.decode("utf-8", "replace")[:1000]
        return {"status": status, "body": preview, "connector": connector["name"]}
