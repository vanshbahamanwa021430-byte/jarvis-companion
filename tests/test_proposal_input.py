import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from broker import Broker, BrokerError
from proposal_input import MAX_INPUT_BYTES, Proposal, ProposalInputError, parse_proposal


DEMO_CATALOG = frozenset({("read_file", "demo-note"), ("read_calendar", "demo-event")})


def payload(**overrides):
    return json.dumps({
        "action": "read_file",
        "target": "demo-note",
        "reason": "Read the selected demo note.",
        **overrides,
    }, ensure_ascii=True).encode("utf-8")


class ProposalInputTests(unittest.TestCase):
    def assert_rejected(self, code, raw):
        with self.assertRaises(ProposalInputError) as rejected:
            parse_proposal(raw, catalog=DEMO_CATALOG)
        self.assertEqual(rejected.exception.code, code)
        self.assertEqual(str(rejected.exception), code)

    def test_one_valid_immutable_exact_scope(self):
        parsed = parse_proposal(payload(reason="  Étudier: अपना नोट पढ़ें  "), catalog=DEMO_CATALOG)
        self.assertEqual(parsed, Proposal("read_file", "demo-note", "Étudier: अपना नोट पढ़ें"))
        with self.assertRaises(FrozenInstanceError):
            parsed.target = "demo-event"

    def test_default_catalog_is_brokers_fixed_catalog(self):
        self.assertEqual(parse_proposal(body=payload()).target, "demo-note")
        with self.assertRaises(ProposalInputError):
            parse_proposal(payload(target="not-in-catalog"))

    def test_rejects_oversized_unbounded_or_nonbytes_input(self):
        for value in (b"", bytes(MAX_INPUT_BYTES + 1), "{}", bytearray(b"{}")):
            with self.subTest(value=type(value)):
                self.assert_rejected("invalid_size", value)

    def test_rejects_invalid_utf8_or_nonstandard_json(self):
        for value in (
            b"\xff{}", b"\xef\xbb\xbf{}", b'{"action":NaN}', b"{}`",
        ):
            with self.subTest(value=value[:16]):
                self.assert_rejected("invalid_json", value)

    def test_rejects_deep_nesting_without_leaking_parser_errors(self):
        value = b"[" * 1000 + b"]" * 1000
        with self.assertRaises(ProposalInputError):
            parse_proposal(value)

    def test_rejects_duplicate_names_after_unescaping(self):
        self.assert_rejected(
            "duplicate_field",
            b'{"action":"read_file","\\u0061ction":"read_calendar","target":"demo-note","reason":"Read."}',
        )

    def test_rejects_omitted_and_extra_fields_and_non_objects(self):
        for value in (
            b'{"action":"read_file","target":"demo-note"}',
            payload(approved=True),
            payload(command="not an authority"),
            b"[]",
            b"null",
        ):
            self.assert_rejected("invalid_fields", value)

    def test_matches_exact_action_and_target_pair(self):
        for value in (
            payload(action="read_calendar"),
            payload(action="READ_FILE"),
            payload(target="demo-event"),
        ):
            self.assert_rejected("unknown_action", value)

    def test_rejects_confusables_path_url_and_nested_target(self):
        for value in (
            payload(action="read_f\u0456le"), payload(target="../demo-note"),
            payload(target="https://example.invalid/note"),
            payload(target={"id": "demo-note"}),
        ):
            self.assert_rejected("invalid_identifier", value)

    def test_rejects_untrusted_controls_and_invalid_reason_types(self):
        for value in (
            payload(reason=None), payload(reason=" "), payload(reason=1), payload(reason="x" * 241),
            payload(reason="text\nnew line"), payload(reason="text\u202e"),
            payload(reason="text\u200b"), payload(reason="text\ud800"),
        ):
            self.assert_rejected("invalid_reason", value)

    def test_validated_proposal_cannot_execute_without_separate_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = Broker(Path(tmp) / "broker.db")
            catalog = frozenset(
                (item["action"], item["target"]) for item in broker.snapshot()["catalog"]
            )
            parsed = parse_proposal(payload(), catalog=catalog)
            pending = broker.propose(parsed.action, parsed.target, parsed.reason)
            self.assertEqual(pending["status"], "pending")
            self.assertIsNone(pending["result"])
            with self.assertRaises(BrokerError):
                broker.execute(pending["id"])


if __name__ == "__main__":
    unittest.main()
