import json
import tempfile
import unittest
from pathlib import Path

from freeze_corpus import build_candidates, freeze_reviewed


def record(identity: str, title: str, time: str) -> dict:
    return {
        "article_id": identity,
        "source_id": identity,
        "canonical_url": f"https://example.test/{identity}",
        "title": title,
        "published_at_utc": time,
        "published_at_original": time,
    }


class FreezeCorpusTests(unittest.TestCase):
    def test_strict_rules_keep_labor_strike_out(self) -> None:
        records = {
            "a": record("a", "Workers call strike at factory", "2026-07-13T12:05:00+00:00"),
            "b": record("b", "Fed raises interest rates", "2026-07-13T12:10:00+00:00"),
        }
        self.assertEqual([], build_candidates(records))

    def test_utility_rate_hike_is_not_monetary_policy(self) -> None:
        records = {
            "a": record("a", "Iran war expands after military attack", "2026-07-13T12:05:00+00:00"),
            "b": record("b", "Utility seeks water and sewer rate hike", "2026-07-13T12:10:00+00:00"),
        }
        self.assertEqual([], build_candidates(records))

    def test_distinct_strict_forces_form_candidate(self) -> None:
        records = {
            "a": record("a", "Iran war expands after military attack", "2026-07-13T12:05:00+00:00"),
            "b": record("b", "Fed raises interest rates", "2026-07-13T12:10:00+00:00"),
        }
        candidates = build_candidates(records)
        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0]["outcome_joined"])

    def test_fewer_than_thirty_reviews_cannot_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            review = Path(directory) / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "reviewed_by": "test",
                        "reviewed_at": "2026-07-13T00:00:00+00:00",
                        "accepted_context_ids": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                freeze_reviewed([], review, Path(directory) / "frozen.jsonl")


if __name__ == "__main__":
    unittest.main()
