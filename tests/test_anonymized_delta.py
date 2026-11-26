"""Paired raw-versus-anonymized deltas stay zero when the scores match."""

from __future__ import annotations

import unittest

from benchmarks.promotion.anonymized_delta import paired_deltas

TIERS = ("clear_reject", "borderline_reject", "borderline_accept", "clear_accept")


def _rows() -> list[dict]:
    rows = []
    for index, tier in enumerate(TIERS):
        for copy in (0, 1):
            score = 3.0 + index + 0.1 * copy
            rows.append(
                {
                    "tier": tier,
                    "stratum": tier,
                    "human_mean": 3.0 + index,
                    "decision": "accept" if index > 1 else "reject",
                    "ratings": [3, 5, 6] if index < 2 else [6, 8, 8],
                    "rank_raw": score,
                    "rank_anon": score,
                    "bin_raw": 3 if index < 2 else 8,
                    "bin_anon": 3 if index < 2 else 8,
                }
            )
    return rows


class AnonymizedDeltaTests(unittest.TestCase):
    def test_identical_scores_have_a_zero_delta(self) -> None:
        population = {tier: 0.25 for tier in TIERS}
        result = paired_deltas(_rows(), population=population, resamples=30, seed=1)
        self.assertEqual(result["n"], 8)
        self.assertAlmostEqual(result["delta"]["spearman"]["point"], 0.0)
        self.assertAlmostEqual(result["delta"]["mae"]["point"], 0.0)
        self.assertAlmostEqual(result["delta"]["auc"]["point"], 0.0)
        self.assertIsNotNone(result["delta"]["spearman"]["low"])
        self.assertAlmostEqual(result["delta"]["spearman"]["low"], 0.0)
        self.assertAlmostEqual(result["delta"]["spearman"]["high"], 0.0)

    def test_a_one_point_score_shift_moves_mae_by_one(self) -> None:
        rows = _rows()
        for row in rows:
            row["rank_raw"] = row["human_mean"]
            row["rank_anon"] = row["human_mean"] + 1.0
            row["bin_raw"] = 5
            row["bin_anon"] = 6
        population = {tier: 0.25 for tier in TIERS}
        result = paired_deltas(rows, population=population, resamples=20, seed=1)
        self.assertAlmostEqual(result["raw"]["mae"], 0.0)
        self.assertAlmostEqual(result["anonymized"]["mae"], 1.0)
        self.assertAlmostEqual(result["delta"]["mae"]["point"], 1.0)
        self.assertAlmostEqual(result["delta"]["mae"]["low"], 1.0)
        self.assertAlmostEqual(result["delta"]["mae"]["high"], 1.0)
