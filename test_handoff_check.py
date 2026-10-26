import json
import unittest
from datetime import datetime, timedelta, timezone

from handoff_check import (
    check_handoff, summarize, shannon_entropy, parse_ts, main,
)

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def good_doc():
    return {
        "sender": "research-agent",
        "receiver": "writer-agent",
        "goal": "Write a 200-word summary of the Q3 revenue numbers for the blog draft.",
        "created_at": "2026-10-26T11:00:00Z",
        "status": "ready",
        "context": [
            {"source": "user", "ts": "2026-10-26T10:30:00Z",
             "text": "User asked for a casual tone and US spelling."},
            {"source": "tool", "ts": "2026-10-26T10:45:00Z",
             "text": "Q3 revenue: $1.2M, up 8% quarter over quarter."},
            {"source": "external", "untrusted": True, "ts": "2026-10-26T10:50:00Z",
             "text": "Industry blog claims the sector average is 5% growth."},
        ],
        "constraints": {"tone": "casual", "length": "200 words"},
        "open_questions": [],
        "artifacts": [{"id": "sheet-q3", "uri": "https://example.com/q3.csv"}],
    }


def rules(findings):
    return [f["rule"] for f in findings]


def errors(findings):
    return [f for f in findings if f["severity"] == "error"]


class TestGoodHandoff(unittest.TestCase):
    def test_good_doc_has_no_errors(self):
        findings = check_handoff(good_doc(), now=NOW)
        self.assertEqual(errors(findings), [], findings)

    def test_good_doc_ok_summary(self):
        summary = summarize(check_handoff(good_doc(), now=NOW))
        self.assertTrue(summary["ok"])

    def test_non_object_document(self):
        findings = check_handoff([1, 2, 3], now=NOW)
        self.assertIn("invalid-document", rules(findings))


class TestCompleteness(unittest.TestCase):
    def test_missing_goal(self):
        doc = good_doc(); doc["goal"] = ""
        self.assertIn("missing-goal", rules(check_handoff(doc, now=NOW)))

    def test_missing_sender_and_receiver(self):
        doc = good_doc(); del doc["sender"]; del doc["receiver"]
        found = rules(check_handoff(doc, now=NOW))
        self.assertIn("missing-sender", found)
        self.assertIn("missing-receiver", found)

    def test_empty_context_warns(self):
        doc = good_doc(); doc["context"] = []
        findings = check_handoff(doc, now=NOW)
        self.assertIn("missing-context", rules(findings))
        self.assertEqual(errors(findings), [])


class TestProvenance(unittest.TestCase):
    def test_unlabeled_source(self):
        doc = good_doc(); del doc["context"][0]["source"]
        findings = check_handoff(doc, now=NOW)
        self.assertIn("unlabeled-source", rules(findings))
        self.assertTrue(errors(findings))

    def test_unknown_source_label(self):
        doc = good_doc(); doc["context"][0]["source"] = "boss"
        self.assertIn("unlabeled-source", rules(check_handoff(doc, now=NOW)))

    def test_external_must_be_marked_untrusted(self):
        doc = good_doc(); del doc["context"][2]["untrusted"]
        findings = check_handoff(doc, now=NOW)
        self.assertIn("unmarked-external", rules(findings))
        self.assertTrue(errors(findings))

    def test_internal_sources_need_no_untrusted_flag(self):
        findings = check_handoff(good_doc(), now=NOW)
        self.assertNotIn("unmarked-external", rules(findings))


class TestMinimality(unittest.TestCase):
    def test_inline_payload_over_limit(self):
        doc = good_doc()
        doc["context"][0]["text"] = "word " * 2000  # ~10k chars, low entropy
        findings = check_handoff(doc, max_inline=4000, now=NOW)
        self.assertIn("inline-payload", rules(findings))

    def test_high_entropy_blob(self):
        doc = good_doc()
        import random, string
        alphabet = string.ascii_letters + string.digits + "+/="
        blob = "".join(random.Random(42).choices(alphabet, k=300))
        self.assertGreater(shannon_entropy(blob), 4.5)
        doc["context"][0]["text"] = blob
        self.assertIn("high-entropy-blob", rules(check_handoff(doc, now=NOW)))

    def test_prose_is_not_flagged_as_blob(self):
        doc = good_doc()
        doc["context"][0]["text"] = "The quarterly report says revenue grew. " * 10
        findings = check_handoff(doc, now=NOW)
        self.assertNotIn("high-entropy-blob", rules(findings))

    def test_artifact_without_reference(self):
        doc = good_doc(); doc["artifacts"] = [{"name": "results"}]
        self.assertIn("artifact-without-reference", rules(check_handoff(doc, now=NOW)))


class TestFreshnessAndConstraints(unittest.TestCase):
    def test_stale_context(self):
        doc = good_doc()
        old = (NOW - timedelta(hours=48)).isoformat()
        doc["context"][0]["ts"] = old
        findings = check_handoff(doc, stale_hours=24, now=NOW)
        self.assertIn("stale-context", rules(findings))

    def test_fresh_context_not_stale(self):
        findings = check_handoff(good_doc(), stale_hours=24, now=NOW)
        self.assertNotIn("stale-context", rules(findings))

    def test_constraint_conflict(self):
        doc = good_doc()
        doc["constraints"] = {"Tone": "casual", " tone ": "formal"}
        self.assertIn("constraint-conflict", rules(check_handoff(doc, now=NOW)))

    def test_duplicate_constraint_info(self):
        doc = good_doc()
        doc["constraints"] = ["no markdown", "No markdown"]
        self.assertIn("constraint-duplicate", rules(check_handoff(doc, now=NOW)))

    def test_blocked_without_open_questions(self):
        doc = good_doc(); doc["status"] = "blocked"; doc["open_questions"] = []
        self.assertIn("missing-open-questions", rules(check_handoff(doc, now=NOW)))


class TestHelpers(unittest.TestCase):
    def test_entropy_empty(self):
        self.assertEqual(shannon_entropy(""), 0.0)

    def test_parse_ts_zulu(self):
        self.assertEqual(parse_ts("2026-10-26T12:00:00Z"), NOW)

    def test_parse_ts_naive_assumed_utc(self):
        self.assertEqual(parse_ts("2026-10-26T12:00:00"), NOW)

    def test_parse_ts_garbage(self):
        self.assertIsNone(parse_ts("not a date"))
        self.assertIsNone(parse_ts(None))


class TestCLI(unittest.TestCase):
    def test_cli_exit_codes(self, tmp_path=None):
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            good = os.path.join(td, "good.json")
            bad = os.path.join(td, "bad.json")
            with open(good, "w") as fh:
                json.dump(good_doc(), fh)
            with open(bad, "w") as fh:
                json.dump({"goal": ""}, fh)
            self.assertEqual(main([good, "--json"]), 0)
            self.assertEqual(main([bad]), 1)
            self.assertEqual(main([os.path.join(td, "nope.json")]), 2)


if __name__ == "__main__":
    unittest.main()
