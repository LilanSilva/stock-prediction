import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from historical_collector import GdeltClient, collect_historical, gdelt_datetime, iter_windows


class Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class HistoricalCollectorTests(unittest.TestCase):
    def test_gdelt_date_is_utc(self) -> None:
        parsed = gdelt_datetime("20260713T120000Z")
        self.assertEqual("2026-07-13T12:00:00+00:00", parsed.isoformat())

    def test_windows_do_not_overlap(self) -> None:
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 1, tzinfo=timezone.utc)
        windows = iter_windows(start, end, 14)
        self.assertEqual(start, windows[0][0])
        self.assertEqual(end, windows[-1][1])
        self.assertTrue(all(left[1] == right[0] for left, right in zip(windows, windows[1:])))

    def test_success_is_cached_and_replayed_without_network(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return Response({"articles": []})

        with tempfile.TemporaryDirectory() as directory:
            client = GdeltClient(Path(directory), opener=opener, min_interval_seconds=0)
            first = client.get_json("https://example.test/query")
            second = client.get_json("https://example.test/query")
            self.assertEqual(first, second)
            self.assertEqual(1, len(calls))

    def test_collection_deduplicates_canonical_urls(self) -> None:
        payload = {
            "articles": [
                {
                    "url": "https://example.test/a?utm_source=x",
                    "title": "War expands",
                    "seendate": "20260713T120000Z",
                    "domain": "example.test",
                    "language": "English",
                }
            ]
        }

        def opener(request, timeout):
            return Response(payload)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = GdeltClient(root / "cache", opener=opener, min_interval_seconds=0)
            report = collect_historical(
                root / "articles.jsonl",
                root / "cache",
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 2, tzinfo=timezone.utc),
                client=client,
            )
            self.assertEqual(1, report["total_records"])
            self.assertEqual(1, report["added_records"])


if __name__ == "__main__":
    unittest.main()
