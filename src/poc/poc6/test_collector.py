import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collector import canonicalize_url, collect, load_corpus
from poc6 import RSS_SOURCES


RSS = b"""<?xml version='1.0'?>
<rss><channel><item><title>Market update</title><link>https://example/a?utm_source=x</link>
<pubDate>Mon, 13 Jul 2026 12:00:00 GMT</pubDate></item></channel></rss>"""


class CollectorTests(unittest.TestCase):
    def test_url_identity_removes_tracking_and_fragment(self) -> None:
        value = "HTTPS://Example.COM:443/a?b=2&utm_source=x&a=1#part"
        self.assertEqual("https://example.com/a?a=1&b=2", canonicalize_url(value))

    def test_restart_is_duplicate_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "articles.jsonl"
            acquired = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
            first = collect(output, fetcher=lambda _: RSS, acquired_at=acquired)
            second = collect(output, fetcher=lambda _: RSS, acquired_at=acquired)
            self.assertEqual(1, first["added_records"])
            self.assertEqual(0, second["added_records"])
            self.assertEqual(1, len(load_corpus(output)))

    def test_source_failure_does_not_abort_cycle(self) -> None:
        failing_url = next(iter(RSS_SOURCES.values()))

        def fetcher(url: str) -> bytes:
            if url == failing_url:
                raise TimeoutError()
            return RSS

        with tempfile.TemporaryDirectory() as directory:
            summary = collect(Path(directory) / "articles.jsonl", fetcher=fetcher)
            statuses = [item["status"] for item in summary["source_reports"]]
            self.assertIn("ERROR", statuses)
            self.assertIn("OK", statuses)
            self.assertEqual(1, summary["total_records"])


if __name__ == "__main__":
    unittest.main()
