"""Voice-first command parsing and spoken confirmation.

Transcripts are mapped to typed actions in actions.CATALOG. The first
utterance only proposes an action. Risky actions also require a spoken
Confirm or Cancel (or a tap on the on-screen card). Nothing is executed
from the wake word or the first command alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from actions import ActionError, ActionsBroker, CATALOG, describe

WAKE_PHRASES = ("hey jarvis", "ok jarvis", "okay jarvis")
CONFIRM_PHRASES = frozenset({
    "confirm", "yes", "yes please", "yeah", "yep", "do it", "proceed",
    "approve", "allow", "allow once", "go ahead", "ok", "okay",
})
CANCEL_PHRASES = frozenset({
    "cancel", "no", "nope", "stop", "deny", "don't", "dont", "abort",
    "never mind", "nevermind", "forget it",
})

_ORDINALS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}


def normalize(text: str) -> str:
    if type(text) is not str:
        return ""
    lowered = text.strip().lower()
    lowered = lowered.replace("jarviss", "jarvis")
    lowered = re.sub(r"[!?]", " ", lowered)
    lowered = re.sub(r"[,;]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def strip_wake(text: str) -> tuple[bool, str]:
    norm = normalize(text)
    for phrase in WAKE_PHRASES:
        if norm == phrase:
            return True, ""
        if norm.startswith(phrase + " "):
            return True, norm[len(phrase) + 1:].strip()
    return False, norm


def is_confirm(text: str) -> bool:
    return normalize(text) in CONFIRM_PHRASES


def is_cancel(text: str) -> bool:
    return normalize(text) in CANCEL_PHRASES


@dataclass(frozen=True)
class ParsedIntent:
    kind: str
    payload: dict[str, Any]
    reason: str
    risk: str

    @property
    def needs_spoken_confirm(self) -> bool:
        return self.risk == "high"

    @property
    def label(self) -> str:
        return describe(self.kind, self.payload)


def parse_intent(text: str) -> ParsedIntent | None:
    """Map a transcript to a typed action, or None if it is not understood."""
    _woke, rest = strip_wake(text)
    utterance = rest or (normalize(text) if not _woke else "")
    if not utterance or is_confirm(utterance) or is_cancel(utterance):
        return None

    alarm = _alarm(utterance)
    if alarm:
        return _make("phone.set_alarm", alarm, utterance)

    opened = _open_maps_or_youtube(utterance)
    if opened:
        return opened

    if re.search(r"\bsettings\b", utterance) or utterance in {"settings", "open setting"}:
        panel = "main"
        if "wifi" in utterance or "wi-fi" in utterance or "wi fi" in utterance:
            panel = "wifi"
        elif "bluetooth" in utterance:
            panel = "bluetooth"
        elif "battery" in utterance:
            panel = "battery"
        return _make("phone.open_settings", {"panel": panel}, utterance)

    app = _open_app(utterance)
    if app:
        return _make("phone.open_app", {"app": app}, utterance)

    if re.search(r"\b(go )?home\b", utterance) or utterance in {"home screen", "go to the home screen"}:
        return _make("phone.home", {}, utterance)
    if re.search(r"\b(go )?back\b", utterance) or utterance == "go back":
        return _make("phone.back", {}, utterance)

    scroll = re.search(r"\bscroll(?:\s+(up|down|left|right))?\b", utterance)
    if scroll:
        direction = scroll.group(1) or "down"
        return _make("phone.scroll", {"direction": direction}, utterance)

    if re.search(r"\b(read|what's on|whats on) (the )?screen\b", utterance) or utterance in {
        "read the screen", "what's on screen", "whats on the screen",
    }:
        return _make("phone.read_screen", {}, utterance)

    if re.search(r"\b(read|check|show) (my )?notifications\b", utterance):
        return _make("phone.read_notifications", {}, utterance)

    typed = re.search(r"^\s*(type|enter|write)\s+(.+)$", utterance)
    if typed:
        return _make("phone.type", {"text": typed.group(2).strip()}, utterance)

    tap = re.search(r"\btap(?:\s+(?:on|at))?\s+(.+)$", utterance)
    if tap:
        target = tap.group(1).strip()
        coords = re.fullmatch(r"(\d+)\s+(\d+)", target)
        if coords:
            return _make("phone.tap", {"x": int(coords.group(1)), "y": int(coords.group(2))}, utterance)
        return _make("phone.tap", {"text": target[:80]}, utterance)

    call = re.search(r"\b(call|dial)\s+(.+)$", utterance)
    if call:
        number = _as_number(call.group(2))
        if number:
            return _make("phone.call", {"number": number}, utterance)

    sms = re.search(r"\b(text|message|sms|send (an? )?sms to)\s+(\+?[0-9][0-9 \-]{5,18})\s*(?:saying|that)?\s*(.*)$", utterance)
    if sms:
        body = (sms.group(4) or "").strip()
        return _make("phone.sms_draft", {"number": _as_number(sms.group(3)) or sms.group(3), "body": body}, utterance)

    gmail_send = re.search(
        r"\b(send|write|compose) (an? )?(email|gmail) to\s+(\S+@\S+)(?:\s+(?:subject|about)\s+(.+?))?(?:\s+(?:body|saying|that)\s+(.+))?$",
        utterance,
    )
    if gmail_send:
        to = gmail_send.group(4).rstrip(".,")
        subject = (gmail_send.group(5) or "hello").strip() or "hello"
        body = (gmail_send.group(6) or subject).strip() or "hello"
        return _make("google.gmail.send", {"to": to, "subject": subject, "body": body}, utterance)

    if re.search(r"\b(read|check|what's on|whats on|show) (my )?(email|gmail|inbox)\b", utterance):
        return _make("google.gmail.read", {"query": ""}, utterance)
    if re.search(r"\b(what's|whats|show|read|check) (on )?(my )?calendar\b", utterance):
        return _make("google.calendar.read", {"when": ""}, utterance)
    cal_write = re.search(r"\b(add|create|schedule) (an? )?(event|meeting)\s+(?:called|titled)?\s*(.+)$", utterance)
    if cal_write:
        return _make("google.calendar.write", {"title": cal_write.group(4).strip()[:120]}, utterance)
    if re.search(r"\b(search|look in|read|show) (my )?drive\b", utterance):
        return _make("google.drive.read", {"query": ""}, utterance)
    drive_write = re.search(r"\b(save|create|write) (a )?drive file (?:called|named)\s+(\S+)\s*(?:with|saying)?\s*(.*)$", utterance)
    if drive_write:
        return _make("google.drive.write", {
            "name": drive_write.group(3)[:80],
            "content": (drive_write.group(4) or " ").strip() or "Note",
        }, utterance)
    if re.search(r"\b(read|show|search) (my )?contacts\b", utterance):
        return _make("google.contacts.read", {"query": ""}, utterance)
    if re.search(r"\b(read|show|check) (my )?tasks\b", utterance):
        return _make("google.tasks.read", {}, utterance)
    task = re.search(r"\b(add|create) (a )?task\s+(?:called|to)?\s*(.+)$", utterance)
    if task:
        return _make("google.tasks.write", {"title": task.group(4).strip()[:120]}, utterance)

    plugin = re.search(r"\b(run|use) plugin\s+([a-z0-9][a-z0-9-]{0,31})\s+([a-z0-9][a-z0-9-]{0,31})\b", utterance)
    if plugin:
        return _make("plugin.run", {"plugin": plugin.group(2), "action": plugin.group(3), "params": {}}, utterance)

    return None


def _make(kind: str, payload: dict[str, Any], reason: str) -> ParsedIntent:
    risk = CATALOG[kind]["risk"]
    return ParsedIntent(kind=kind, payload=payload, reason=reason[:240], risk=risk)


def _open_app(utterance: str) -> str | None:
    match = re.search(r"\b(open|launch|start)\s+(?:the\s+)?(?:app\s+)?(.+)$", utterance)
    if not match:
        return None
    name = match.group(2).strip()
    # Avoid stealing maps/youtube/settings which have more specific handlers,
    # but those are tried first.
    name = re.sub(r"\s+app$", "", name).strip()
    if not name or name in {"settings", "setting"}:
        return None
    if name.startswith("maps") or name.startswith("youtube"):
        return None
    if len(name) > 40:
        name = name[:40]
    return name


def _open_maps_or_youtube(utterance: str) -> ParsedIntent | None:
    maps = re.search(r"\b(open )?maps?( to| for)?\s+(.+)$", utterance)
    if "map" in utterance and maps and "open app" not in utterance:
        query = maps.group(3).strip()
        if query and query not in {"app", "please"}:
            return _make("google.maps.open", {"query": query[:200]}, utterance)
    if re.search(r"\bopen maps\b", utterance) and " to " not in utterance and " for " not in utterance:
        return _make("google.maps.open", {"query": "home"}, utterance)
    tube = re.search(r"\b(open )?youtube(?: and search(?: for)?| search(?: for)?| for)?\s*(.*)$", utterance)
    if "youtube" in utterance and tube:
        return _make("google.youtube.open", {"query": (tube.group(2) or "").strip()}, utterance)
    return None


def _as_number(text: str) -> str | None:
    raw = normalize(text)
    digits = re.sub(r"[ \-()]", "", raw)
    if re.fullmatch(r"\+?[0-9]{6,15}", digits):
        return digits
    # Very small spoken-digit helper: "one two three ..."
    parts = raw.split()
    if parts and all(p in _ORDINALS for p in parts) and 6 <= len(parts) <= 15:
        return "".join(str(_ORDINALS[p]) for p in parts)
    return None


def _alarm(utterance: str) -> dict[str, Any] | None:
    match = re.search(
        r"\b(set |create )?(an? )?alarm (for |at )?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        utterance,
    )
    if not match:
        return None
    hour = int(match.group(4))
    minute = int(match.group(5) or 0)
    ampm = match.group(6)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return {"hour": hour, "minute": minute, "message": "Jarvis alarm"}


@dataclass
class VoiceSession:
    """Tracks a waiting spoken confirmation for one pending risky action."""

    waiting_id: str | None = None
    waiting_label: str = ""
    expires_at: float = 0.0


@dataclass
class VoiceHub:
    actions: ActionsBroker
    clock: Callable[[], float]
    session: VoiceSession = field(default_factory=VoiceSession)
    muted: bool = False

    def status(self) -> dict[str, Any]:
        waiting = None
        if self.session.waiting_id and self.clock() < self.session.expires_at:
            waiting = {"id": self.session.waiting_id, "label": self.session.waiting_label}
        elif self.session.waiting_id:
            self.session = VoiceSession()
        return {
            "muted": self.muted,
            "wake": "Hey Jarvis",
            "waiting": waiting,
        }

    def set_muted(self, muted: bool) -> dict[str, Any]:
        self.muted = bool(muted)
        if self.muted:
            self.session = VoiceSession()
        return self.status()

    def handle(self, text: str, *, source: str = "voice") -> dict[str, Any]:
        if type(text) is not str or not text.strip() or len(text) > 500:
            raise ActionError(400, "invalid_text", "Invalid utterance.")
        woke, rest = strip_wake(text)
        utterance = rest or ("" if woke else normalize(text))

        flags = self.actions.flags()
        if flags["killed"]:
            return {"ok": False, "error": "killed", "speak": "The kill switch is on.", "woke": woke}

        if self.muted:
            return {"ok": False, "error": "muted", "speak": "Voice is muted.", "woke": woke}

        now = self.clock()
        if self.session.waiting_id and now >= self.session.expires_at:
            self.session = VoiceSession()

        # Spoken confirmation for a risky pending action.
        if self.session.waiting_id:
            if is_confirm(utterance) or (woke and is_confirm(rest)):
                record = self.actions.approve(self.session.waiting_id)
                label = self.session.waiting_label
                self.session = VoiceSession()
                return {
                    "ok": True, "event": "approved", "action": record,
                    "speak": f"Approved. {label}.", "woke": True,
                }
            if is_cancel(utterance) or (woke and is_cancel(rest)):
                record = self.actions.deny(self.session.waiting_id)
                label = self.session.waiting_label
                self.session = VoiceSession()
                return {
                    "ok": True, "event": "denied", "action": record,
                    "speak": f"Cancelled. {label}.", "woke": True,
                }
            # A new command while waiting replaces nothing; ask again.
            if not utterance:
                return {
                    "ok": True, "event": "awaiting_confirm",
                    "speak": f"Please say confirm or cancel. {self.session.waiting_label}.",
                    "woke": woke, "needs_spoken_confirm": True,
                    "action": {"id": self.session.waiting_id, "label": self.session.waiting_label},
                }

        if woke and not utterance:
            return {"ok": True, "event": "awake", "speak": "Yes?", "woke": True}

        intent = parse_intent(text if utterance else "")
        if intent is None:
            if not utterance:
                return {"ok": True, "event": "awake", "speak": "Yes?", "woke": woke}
            return {
                "ok": False, "error": "unrecognised",
                "speak": "I did not understand that command.", "woke": woke,
            }

        record = self.actions.propose(intent.kind, intent.payload, reason=intent.reason, source=source)
        if intent.needs_spoken_confirm:
            self.session = VoiceSession(
                waiting_id=record["id"],
                waiting_label=intent.label,
                expires_at=now + float(self.actions._ttl_seconds),
            )
            return {
                "ok": True, "event": "proposed", "action": record,
                "needs_spoken_confirm": True, "woke": woke or True,
                "speak": f"{intent.label}. Say confirm or cancel, or use the card on the screen.",
            }
        return {
            "ok": True, "event": "proposed", "action": record,
            "needs_spoken_confirm": False, "woke": woke or True,
            "speak": f"Queued {intent.label}. Approve it on the screen to run it.",
        }
