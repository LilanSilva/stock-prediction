import json
import unittest

from evaluation import EvaluationBudget, build_baselines, dry_run, parse_model_json


def context(index: int) -> dict:
    return {
        "context_id": f"context-{index}",
        "context_hash": f"hash-{index}",
        "asset_id": "GOLD",
        "window_start": "2026-07-13T12:00:00+00:00",
        "signals": [
            {
                "event_id": f"up-{index}",
                "rule": "military_conflict",
                "event_type": "MILITARY_CONFLICT",
                "direction": "UP",
                "title": "Iran war expands after military attack",
            },
            {
                "event_id": f"down-{index}",
                "rule": "rate_hike",
                "event_type": "RATE_DECISION",
                "direction": "DOWN",
                "title": "Central bank raises interest rates",
            },
        ],
    }


class EvaluationTests(unittest.TestCase):
    def test_baselines_are_byte_stable(self) -> None:
        first = json.dumps(build_baselines([context(1)]), sort_keys=True)
        second = json.dumps(build_baselines([context(1)]), sort_keys=True)
        self.assertEqual(first, second)

    def test_dry_run_reserves_exactly_forty_attempts_without_calls(self) -> None:
        report = dry_run([context(index) for index in range(30)])
        self.assertEqual(40, report["budget"]["calls"])
        self.assertEqual(0, report["llm_calls_made"])
        self.assertLessEqual(report["budget"]["reserved_input_tokens"], 80_000)
        self.assertLessEqual(report["budget"]["reserved_output_tokens"], 8_000)

    def test_simulated_retry_consumes_second_reservation(self) -> None:
        budget = EvaluationBudget(max_calls=2)
        budget.reserve(100, 20)
        budget.reserve(100, 20)
        self.assertEqual(2, budget.calls)
        with self.assertRaises(RuntimeError):
            budget.reserve(1, 1)

    def test_structured_response_validation(self) -> None:
        value = parse_model_json(
            '{"direction":"UP","magnitude":"SMALL","confidence":0.6,"rationale":"x"}'
        )
        self.assertEqual("UP", value["direction"])


if __name__ == "__main__":
    unittest.main()
