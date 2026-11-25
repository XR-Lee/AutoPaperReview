"""Numeric checks for the dev-set reanalysis. No manuscripts."""

from __future__ import annotations

import unittest

from benchmarks.promotion.reanalysis_v2 import (
    arm_metrics,
    cohen_kappa,
    corp_decomposition,
    effective_sample_size,
    ipw_weights,
    mixture_tier_probabilities,
    quadratic_weighted_kappa,
    thorndike_case_ii,
    weighted_spearman,
)
from autopaperreview.public_calibration import spearman


class ReanalysisTests(unittest.TestCase):
    def test_uniform_population_leaves_spearman_and_sample_size_unchanged(self) -> None:
        population = {name: 0.25 for name in ("clear_reject", "borderline_reject", "borderline_accept", "clear_accept")}
        tiers = ["clear_reject", "borderline_reject", "borderline_accept", "clear_accept"]
        weights = ipw_weights(tiers, population)
        self.assertEqual(weights, [1.0, 1.0, 1.0, 1.0])
        self.assertAlmostEqual(effective_sample_size(weights), 4.0)
        left = [1.0, 2.0, 3.0, 4.0]
        right = [1.0, 3.0, 2.0, 4.0]
        self.assertAlmostEqual(weighted_spearman(left, right, weights), spearman(left, right))

    def test_mixture_shares_sum_to_one(self) -> None:
        statuses = [
            {"count": 10, "mean": 6.5, "std": 0.7},
            {"count": 20, "mean": 4.5, "std": 0.9},
        ]
        mixture = mixture_tier_probabilities(statuses)
        self.assertAlmostEqual(sum(mixture["tiers"].values()), 1.0)
        self.assertGreater(mixture["tiers"]["borderline_reject"], mixture["tiers"]["clear_accept"])

    def test_kappa_and_corp_have_the_expected_endpoints(self) -> None:
        self.assertAlmostEqual(cohen_kappa([1, 3, 5], [1, 3, 5]), 1.0)
        self.assertLess(cohen_kappa([1, 3, 5, 6], [3, 5, 6, 8]), quadratic_weighted_kappa([1, 3, 5, 6], [3, 5, 6, 8]))
        perfect = corp_decomposition([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(perfect["miscalibration"], 0.0)
        self.assertAlmostEqual(perfect["identity_gap"], 0.0, places=9)
        pooled = corp_decomposition([1.0, 1.2, 3.0, 3.1], [1.0, 2.0, 2.5, 4.0])
        self.assertAlmostEqual(pooled["identity_gap"], 0.0, places=9)
        self.assertAlmostEqual(thorndike_case_ii(0.5, 1.0, 1.0), 0.5)

    def test_arm_metrics_bootstrap_is_defined_on_a_tiny_stratified_set(self) -> None:
        population = {name: 0.25 for name in ("clear_reject", "borderline_reject", "borderline_accept", "clear_accept")}
        rows = []
        for index, tier in enumerate(population):
            rows.append(
                {
                    "tier": tier,
                    "stratum": tier,
                    "human_mean": 3.0 + index,
                    "decision": "accept" if index > 1 else "reject",
                    "ratings": [3, 3, 5] if index < 2 else [6, 8, 8],
                    "title_chars": 20 + index,
                    "rank_score": 3.2 + index,
                    "our_bin": 3 if index < 2 else 8,
                }
            )
        result = arm_metrics(rows, population=population, resamples=20, seed=1)
        self.assertEqual(result["point"]["n_scored"], 4)
        self.assertIsNotNone(result["intervals"]["spearman"]["low"])
        self.assertGreaterEqual(result["intervals"]["mae"]["defined"], 1)
