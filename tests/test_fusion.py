"""Declared fusion candidates and rank intervals. Synthetic numbers only."""

from __future__ import annotations

import unittest

from autopaperreview.fusion import SHIPPABLE, RankSignals, candidate_scores, select_variant, selection_report
from autopaperreview.model_exec import DEFAULT_MODEL_TIMEOUT_SECONDS
from autopaperreview.models import FatalClass
from autopaperreview.public_calibration import strip_venue_status_lines
from autopaperreview.rank_metrics import agreement, bootstrap_interval
from autopaperreview.rating import rate_venue
from autopaperreview.rubrics import FUSION_COEFFICIENT_SET
from autopaperreview.stages.builtin import ExecuteReviewParams
from tests.test_rating import _all, _fatal, _package


def _with_holistic(package, rating: float):
    return package.model_copy(update={"metadata": {"holistic_rating": {"rating": rating, "verified": True}}})


class FusionFormulaTests(unittest.TestCase):
    def test_saturated_soundness_does_not_flatten_the_holistic_rank(self) -> None:
        package = _with_holistic(
            _package("soft", {**_all(8), "soundness": 4, "claims": 2}),
            9,
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.value, 9)
        self.assertEqual(scored.metadata["venue_bin"], 8)
        self.assertEqual(scored.metadata["candidates"]["holistic"], 9)
        self.assertEqual(scored.metadata["candidates"]["holistic-capped"], 9)
        self.assertLess(scored.metadata["candidates"]["band-continuous"], 5)
        self.assertEqual(scored.coefficient_set, FUSION_COEFFICIENT_SET)

    def test_verified_cap_is_the_only_downward_adjustment(self) -> None:
        package = _with_holistic(
            _package("capped", _all(8), issues=[_fatal("I-leak", FatalClass.leakage)]),
            9,
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.value, 3)
        self.assertEqual(scored.metadata["venue_bin"], 3)
        self.assertEqual(scored.metadata["candidates"]["holistic"], 9)
        self.assertEqual(scored.metadata["candidates"]["holistic-capped"], 3)

    def test_missing_holistic_rating_fails_instead_of_using_bands(self) -> None:
        package = _package("missing", {**_all(8), "soundness": 4})
        with self.assertRaises(ValueError) as caught:
            rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertIn("holistic", str(caught.exception))

    def test_cheap_package_ranks_without_dimension_bands(self) -> None:
        package = _package("cheap", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {"holistic_rating": {"rating": 7.5, "verified": True, "samples": [7, 8, 7.5]}},
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.value, 7.5)
        self.assertNotIn("band-continuous", scored.metadata["candidates"])

    def test_cap_v2_value_stays_the_integer_bin(self) -> None:
        scored = rate_venue(_package("high", _all(8)))
        self.assertEqual(scored.value, 8)
        self.assertEqual(scored.metadata["venue_bin"], 8)

    def test_exact_tie_uses_declared_priority_not_list_order(self) -> None:
        self.assertEqual(SHIPPABLE[0], "holistic-capped")
        metrics = {
            "holistic": {"spearman": 0.50, "auc": 0.60},
            "holistic-capped": {"spearman": 0.50, "auc": 0.60},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "holistic")
        self.assertEqual(report["tied"], ["holistic", "holistic-capped"])
        self.assertEqual(select_variant(metrics), "holistic")

    def test_selector_takes_the_higher_spearman(self) -> None:
        metrics = {
            "holistic": {"spearman": 0.40, "auc": 0.90},
            "holistic-capped": {"spearman": 0.72, "auc": 0.70},
        }
        self.assertEqual(select_variant(metrics), "holistic-capped")
        self.assertEqual(selection_report(metrics)["tied"], ["holistic-capped"])


class RankMetricTests(unittest.TestCase):
    def test_perfect_order_and_a_defined_interval(self) -> None:
        scores = [1, 3, 5, 8]
        means = [2, 4, 6, 9]
        labels = [0, 0, 1, 1]
        point = agreement(scores, means, labels)
        self.assertAlmostEqual(point["spearman"], 1.0)
        self.assertAlmostEqual(point["auc"], 1.0)
        interval = bootstrap_interval(scores, means, labels, "spearman", resamples=50, seed=1)
        self.assertAlmostEqual(interval["point"], 1.0)
        self.assertLessEqual(interval["low"], interval["high"])
        self.assertGreater(interval["defined_resamples"], 0)

    def test_constant_scores_leave_spearman_undefined(self) -> None:
        interval = bootstrap_interval([3, 3, 3, 3], [1, 2, 3, 4], [0, 0, 1, 1], "spearman", resamples=20, seed=1)
        self.assertIsNone(interval["point"])
        self.assertEqual(interval["defined_resamples"], 0)


class HarnessConstraintTests(unittest.TestCase):
    def test_adapter_timeout_default_covers_a_long_reasoning_call(self) -> None:
        params = ExecuteReviewParams(adapter="autopaperreview.model_exec:OpenAICompatibleAdapter")
        self.assertGreaterEqual(params.timeout_seconds, 900)
        self.assertEqual(params.timeout_seconds, DEFAULT_MODEL_TIMEOUT_SECONDS)

    def test_status_banner_is_removed_and_body_text_stays(self) -> None:
        text = "Published as a conference paper at ICLR 2099\nSynthetic cohort result holds.\n"
        stripped = strip_venue_status_lines(text)
        self.assertNotIn("Published as", stripped)
        self.assertIn("Synthetic cohort result holds.", stripped)

    def test_candidate_map_keeps_the_holistic_rank_above_a_diagnostic_band(self) -> None:
        signals = RankSignals(
            weighted=6.0,
            gate=4.0,
            claims=4.0,
            soundness=4.0,
            band_mean=6.0,
            venue_cap=None,
            holistic=8.0,
        )
        scores = candidate_scores(signals)
        self.assertEqual(scores["holistic"], 8.0)
        self.assertEqual(scores["holistic-capped"], 8.0)
        self.assertLess(scores["band-continuous"], scores["holistic"])
        self.assertNotIn("blend-60-gated", scores)

    def test_missing_holistic_omits_shippable_scores(self) -> None:
        signals = RankSignals(
            weighted=6.0,
            gate=4.0,
            claims=4.0,
            soundness=4.0,
            band_mean=6.0,
            venue_cap=None,
            holistic=None,
        )
        scores = candidate_scores(signals)
        self.assertNotIn("holistic", scores)
        self.assertNotIn("holistic-capped", scores)
        self.assertIn("band-continuous", scores)

    def test_holistic_sample_count_defaults_to_three(self) -> None:
        params = ExecuteReviewParams(adapter="autopaperreview.model_exec:OpenAICompatibleAdapter")
        self.assertEqual(params.holistic_samples, 3)
        self.assertTrue(params.cheap is False)
