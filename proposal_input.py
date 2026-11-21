"""Validate an untrusted agent's *request for permission*, never a grant.

Pass a trusted immutable set of concrete (action, target) pairs, preferably
from the broker's catalog. Never take that set from the agent or the client.
Store/authorize the parsed fields via the existing approval broker; a valid
proposal itself authorizes nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AbstractSet

from broker import proposal_catalog

MAX_INPUT_BYTES = 2048
MAX_IDENTIFIER_CHARS = 64
MAX_REASON_CHARS = 240
_REQUIRED_FIELDS = frozenset({"action", "target", "reason"})


class ProposalInputError(ValueError):
    """Fixed message codes; deliberately never echoes input into logs."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Proposal:
    action: str
    target: str
    reason: str


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProposalInputError("duplicate_field")
        result[key] = value
    return result


def _reject_constant(_constant):
    raise ProposalInputError("invalid_json")


def _identifier(value):
    # Only stable machine identifiers are acceptable. Labels/paths/URLs aren't
    # identifiers and must never be interpreted as executable locations.
    return (
        type(value) is str
        and 0 < len(value) <= MAX_IDENTIFIER_CHARS
        and value.isascii()
        and value[0].isalnum()
        and all(c.isalnum() or c in "_-" for c in value)
    )


def parse_proposal(
    body: bytes, catalog: AbstractSet[tuple[str, str]] | None = None
) -> Proposal:
    """Strict UTF-8 JSON in a bounded envelope, with exact action and target."""

    if type(body) is not bytes or not 0 < len(body) <= MAX_INPUT_BYTES:
        raise ProposalInputError("invalid_size")

    try:
        parsed = json.loads(
            body.decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ProposalInputError:
        raise
    except (ValueError, RecursionError):
        raise ProposalInputError("invalid_json") from None

    if type(parsed) is not dict or parsed.keys() != _REQUIRED_FIELDS:
        raise ProposalInputError("invalid_fields")

    action, target, reason = parsed["action"], parsed["target"], parsed["reason"]
    if not (_identifier(action) and _identifier(target)):
        raise ProposalInputError("invalid_identifier")
    if (action, target) not in (proposal_catalog() if catalog is None else catalog):
        raise ProposalInputError("unknown_action")
    if (
        type(reason) is not str
        or not 0 < len(reason) <= MAX_REASON_CHARS
        or any(not character.isprintable() for character in reason)
        or not reason.strip()
    ):
        raise ProposalInputError("invalid_reason")
    return Proposal(action=action, target=target, reason=reason.strip())
