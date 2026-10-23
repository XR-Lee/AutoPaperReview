"""Declared fusion candidates and rank intervals. Synthetic numbers only."""

from __future__ import annotations

import unittest

from autopaperreview.fusion import RankSignals, candidate_scores, select_variant
from autopaperreview.model_exec import DEFAULT_MODEL_TIMEOUT_SECONDS
from autopaperreview.models import FatalClass
from autopaperreview.public_calibration import strip_venue_status_lines
from autopaperreview.rank_metrics import agreement, bootstrap_interval
from autopaperreview.rating import rate_venue
from autopaperreview.rubrics import FUSION_COEFFICIENT_SET
from autopaperreview.stages.builtin import ExecuteReviewParams
from tests.test_rating import _all, _fatal, _package


class FusionFormulaTests(unittest.TestCase):
    def test_one_low_band_does_not_flatten_the_holistic_signal(self) -> None:
        package = _package("soft", _all(8))
        package = package.model_copy(
            update={
                "dimension_scores": _package(
                    "soft",
                    {**_all(8), "soundness": 4},
                ).dimension_scores,
                "metadata": {"holistic_rating": {"rating": 9, "verified": True}},
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertGreater(scored.value, 5)
        self.assertLess(scored.value, 9)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        self.assertEqual(scored.coefficient_set, FUSION_COEFFICIENT_SET)

    def test_both_critical_bands_low_restore_the_weakest_link(self) -> None:
        package = _package("both", {**_all(8), "claims": 4, "soundness": 4})
        package = package.model_copy(
            update={"metadata": {"holistic_rating": {"rating": 9, "verified": True}}}
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertLessEqual(scored.value, scored.metadata["gate"] + 1)

    def test_verified_cap_ceilings_the_blend(self) -> None:
        package = _package("capped", _all(8), issues=[_fatal("I-leak", FatalClass.leakage)])
        package = package.model_copy(
            update={"metadata": {"holistic_rating": {"rating": 9, "verified": True}}}
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertLessEqual(scored.value, 3)
        self.assertEqual(scored.metadata["venue_bin"], 3)

    def test_cap_v2_value_stays_the_integer_bin(self) -> None:
        scored = rate_venue(_package("high", _all(8)))
        self.assertEqual(scored.value, 8)
        self.assertEqual(scored.metadata["venue_bin"], 8)

    def test_selector_keeps_the_dimension_term_inside_a_small_gap(self) -> None:
        metrics = {
            "band-continuous": {"spearman": 0.70, "auc": 0.80},
            "blend-60-gated": {"spearman": 0.71, "auc": 0.81},
            "band-bin": {"spearman": 0.20, "auc": 0.55},
            "mean-gated": {"spearman": 0.40, "auc": 0.60},
            "holistic-gated": {"spearman": 0.50, "auc": 0.60},
            "blend-50-gated": {"spearman": 0.60, "auc": 0.70},
            "blend-70-gated": {"spearman": 0.69, "auc": 0.79},
            "blend-60-hardgate": {"spearman": 0.30, "auc": 0.50},
        }
        self.assertEqual(select_variant(metrics), "band-continuous")

    def test_selector_takes_a_real_gain(self) -> None:
        metrics = {
            "band-continuous": {"spearman": 0.40, "auc": 0.70},
            "blend-60-gated": {"spearman": 0.72, "auc": 0.78},
            "band-bin": {"spearman": 0.10, "auc": 0.50},
            "mean-gated": {"spearman": 0.40, "auc": 0.60},
            "holistic-gated": {"spearman": 0.55, "auc": 0.62},
            "blend-50-gated": {"spearman": 0.60, "auc": 0.74},
            "blend-70-gated": {"spearman": 0.68, "auc": 0.76},
            "blend-60-hardgate": {"spearman": 0.42, "auc": 0.71},
        }
        self.assertEqual(select_variant(metrics), "blend-60-gated")


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

    def test_candidate_map_names_the_shipped_blend(self) -> None:
        signals = RankSignals(
            weighted=6.0,
            gate=6.0,
            claims=6.0,
            soundness=6.0,
            band_mean=6.0,
            venue_cap=None,
            holistic=8.0,
        )
        scores = candidate_scores(signals)
        self.assertIn("blend-60-gated", scores)
        self.assertGreater(scores["blend-60-gated"], scores["band-continuous"])
