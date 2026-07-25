import unittest

from metrics import actual_outcome, balanced_accuracy, macro_f1


class MetricsTests(unittest.TestCase):
    def test_deadband_is_neutral(self) -> None:
        self.assertEqual("NEUTRAL", actual_outcome(100, 100.2)["direction"])

    def test_direction_and_magnitude(self) -> None:
        outcome = actual_outcome(100, 102)
        self.assertEqual("UP", outcome["direction"])
        self.assertEqual("MEDIUM", outcome["magnitude"])

    def test_balanced_accuracy_and_macro_f1(self) -> None:
        actual = ["UP", "DOWN", "NEUTRAL"]
        predicted = ["UP", "DOWN", "NEUTRAL"]
        self.assertEqual(1.0, balanced_accuracy(actual, predicted))
        self.assertEqual(1.0, macro_f1(actual, predicted))


if __name__ == "__main__":
    unittest.main()
