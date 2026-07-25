import unittest

from poc6 import (
    Article,
    BudgetExceeded,
    BudgetLedger,
    build_conflict_contexts,
    deduplicate_articles,
    detect_signals,
    parse_rss,
)


class Poc6Tests(unittest.TestCase):
    def test_parse_rss_and_normalize_utc(self) -> None:
        content = b"""<?xml version='1.0'?>
        <rss><channel><item><title>War starts</title><link>https://example/a</link>
        <pubDate>Mon, 13 Jul 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
        articles = parse_rss("TEST", content)
        self.assertEqual(1, len(articles))
        self.assertEqual("2026-07-13T12:00:00+00:00", articles[0].published_at)

    def test_exact_duplicate_title_is_removed(self) -> None:
        articles = [
            Article("A", "Same title", "https://a", "2026-07-13T12:00:00+00:00"),
            Article("B", "Same title", "https://b", "2026-07-13T12:05:00+00:00"),
        ]
        self.assertEqual(1, len(deduplicate_articles(articles)))

    def test_distinct_opposing_events_create_context(self) -> None:
        war = Article(
            "A", "War attack expands", "https://a", "2026-07-13T12:05:00+00:00"
        )
        rate = Article(
            "B", "Central bank rates raised", "https://b", "2026-07-13T12:20:00+00:00"
        )
        contexts = build_conflict_contexts(detect_signals(war) + detect_signals(rate))
        gold = [context for context in contexts if context["asset_id"] == "GOLD"]
        self.assertEqual(1, len(gold))
        self.assertEqual(2, len({signal["url"] for signal in gold[0]["signals"]}))

    def test_same_article_cannot_create_conflict_context(self) -> None:
        article = Article(
            "A",
            "War and rates raised",
            "https://a",
            "2026-07-13T12:05:00+00:00",
        )
        self.assertEqual([], build_conflict_contexts(detect_signals(article)))

    def test_budget_counts_retries_as_calls(self) -> None:
        ledger = BudgetLedger(max_calls=2, max_input_tokens=100, max_output_tokens=20)
        ledger.reserve(40, 5)
        ledger.reserve(40, 5)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve(1, 1)
        self.assertEqual(2, ledger.calls)

    def test_budget_is_atomic_when_reservation_fails(self) -> None:
        ledger = BudgetLedger(max_calls=2, max_input_tokens=50, max_output_tokens=20)
        ledger.reserve(40, 5)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve(20, 5)
        self.assertEqual(1, ledger.calls)
        self.assertEqual(40, ledger.input_tokens)


if __name__ == "__main__":
    unittest.main()

