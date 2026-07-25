import unittest
from datetime import datetime, timezone

from market_policy import resolve_sessions, session_completed_at


class MarketPolicyTests(unittest.TestCase):
    def test_completion_time_uses_provider_timezone(self) -> None:
        completed = session_completed_at("2026-07-13", "America/New_York")
        self.assertEqual("2026-07-13T21:00:00+00:00", completed.isoformat())

    def test_resolver_skips_weekend_from_returned_sessions(self) -> None:
        observations = [
            {"session": "2026-07-10", "close": 100.0},
            {"session": "2026-07-13", "close": 101.0},
            {"session": "2026-07-14", "close": 102.0},
        ]
        baseline, settlement = resolve_sessions(
            observations,
            datetime(2026, 7, 12, 12, tzinfo=timezone.utc),
            "America/New_York",
        )
        self.assertEqual("2026-07-10", baseline["session"])
        self.assertEqual("2026-07-13", settlement["session"])


if __name__ == "__main__":
    unittest.main()
