"""Unprivileged proposal client. No owner credentials and no account access."""
from __future__ import annotations

import http.client
import json
import re

MAX_PAYLOAD = 2048
MAX_RECEIPT = 512
_KEY = re.compile(r"[A-Za-z0-9_-]{32,}")


class ProposalError(RuntimeError):
    """Raised for any transport, protocol, or rejection failure (never echoes the key)."""


def propose(action: str, target: str, reason: str, *, port: int, key: str, timeout: float = 4) -> dict:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid local proposal port.")
    if type(key) is not str or not _KEY.fullmatch(key):
        raise ValueError("Independent proposal key is required.")
    if not all(type(value) is str for value in (action, target, reason)):
        raise ValueError("Proposal fields must be text.")
    payload = json.dumps({"action": action, "target": target, "reason": reason},
                         ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Proposal is too large.")
    # Deliberately no redirects, remote destination, or environment proxy.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", "/api/agent/propose", payload, {
            "Content-Type": "application/json", "Authorization": "Bearer " + key,
        })
        response = conn.getresponse()
        if response.status != 202:
            raise ProposalError(f"Proposal rejected ({response.status}).")
        raw = response.read(MAX_RECEIPT + 1)
        if len(raw) > MAX_RECEIPT:
            raise ProposalError("Unexpected proposal receipt.")
        try:
            body = json.loads(raw)
        except ValueError:
            raise ProposalError("Unexpected proposal receipt.") from None
        if (not isinstance(body, dict) or set(body) != {"id", "status"}
                or body.get("status") != "pending" or not isinstance(body.get("id"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", body["id"])):
            raise ProposalError("Unexpected proposal receipt.")
        return body
    except http.client.HTTPException as exc:
        # e.g. BadStatusLine / IncompleteRead are not OSError subclasses.
        raise ProposalError(f"Proposal transport failed ({type(exc).__name__}).") from None
    finally:
        conn.close()
