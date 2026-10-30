"""Synthetic checks for the promotion paired bootstrap. No manuscripts."""

from __future__ import annotations

import unittest

from benchmarks.promotion.paired_bootstrap import (
    paired_difference,
    stratified_indexes,
    worse_beyond_interval,
)


class PromotionBootstrapTest(unittest.TestCase):
    def test_draws_keep_each_stratum_size(self) -> None:
        strata = ["b", "a", "b", "a", "a"]
        draws = stratified_indexes(strata, resamples=20, seed=20261029)
        self.assertEqual(len(draws), 20)
        for indexes in draws:
            self.assertEqual(len(indexes), 5)
            names = [strata[index] for index in indexes]
            self.assertEqual(names.count("a"), 3)
            self.assertEqual(names.count("b"), 2)

    def test_identical_arms_have_a_zero_interval(self) -> None:
        rows = [
            {"stratum": "low", "pre": 1.0, "main": 1.0},
            {"stratum": "low", "pre": 2.0, "main": 2.0},
            {"stratum": "high", "pre": 3.0, "main": 3.0},
            {"stratum": "high", "pre": 4.0, "main": 4.0},
        ]

        def diff(sample):
            return sum(row["pre"] for row in sample) / len(sample) - sum(row["main"] for row in sample) / len(sample)

        interval = paired_difference(rows, diff, resamples=50, seed=20261029)
        self.assertEqual(interval["point"], 0.0)
        self.assertEqual(interval["low"], 0.0)
        self.assertEqual(interval["high"], 0.0)
        self.assertFalse(worse_beyond_interval(interval, higher_is_better=True))

    def test_a_constant_drop_is_worse_for_a_higher_is_better_metric(self) -> None:
        rows = [
            {"stratum": "only", "pre": 0.0, "main": 1.0},
            {"stratum": "only", "pre": 0.0, "main": 1.0},
        ]

        def diff(sample):
            return sum(row["pre"] - row["main"] for row in sample) / len(sample)

        interval = paired_difference(rows, diff, resamples=30, seed=1)
        self.assertEqual(interval["point"], -1.0)
        self.assertLess(interval["high"], 0.0)
        self.assertTrue(worse_beyond_interval(interval, higher_is_better=True))
        self.assertFalse(worse_beyond_interval(interval, higher_is_better=False))

    def test_undefined_interval_counts_as_worse(self) -> None:
        self.assertTrue(worse_beyond_interval({"low": None, "high": None}, higher_is_better=True))


if __name__ == "__main__":
    unittest.main()
