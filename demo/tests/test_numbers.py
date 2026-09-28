import unittest

from number_utils import summarize_numbers


class NumbersAcceptance(unittest.TestCase):
    def test_mixed_values(self):
        self.assertEqual(
            summarize_numbers([1, 2.5, -3, 7.5]),
            {"count": 4, "total": 8.0, "minimum": -3,
             "maximum": 7.5, "mean": 2.0},
        )

    def test_empty(self):
        self.assertEqual(
            summarize_numbers([]),
            {"count": 0, "total": 0, "minimum": None,
             "maximum": None, "mean": None},
        )

    def test_one_shot_generator(self):
        result = summarize_numbers(value for value in (2, 4, 6))
        self.assertEqual(result,
                         {"count": 3, "total": 12, "minimum": 2,
                          "maximum": 6, "mean": 4})

    def test_single_negative_value(self):
        self.assertEqual(
            summarize_numbers([-4]),
            {"count": 1, "total": -4, "minimum": -4,
             "maximum": -4, "mean": -4},
        )

    def test_input_not_mutated(self):
        values = [3, 1, 2]
        summarize_numbers(values)
        self.assertEqual(values, [3, 1, 2])

    def test_rejects_invalid_element_types(self):
        for value in (True, False, "2", None):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    summarize_numbers([1, value])

    def test_rejects_nonfinite_values(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    summarize_numbers([1, value])
