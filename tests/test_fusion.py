"""Declared fusion candidates and rank intervals. Synthetic numbers only."""

from __future__ import annotations

import unittest

from autopaperreview.fusion import (
    FUSION_VARIANT,
    LOCKED_CANDIDATE,
    SHIPPABLE,
    RankSignals,
    candidate_scores,
    decision_bin,
    select_variant,
    selection_report,
)
from autopaperreview.model_exec import DEFAULT_MODEL_TIMEOUT_SECONDS
from autopaperreview.models import FatalClass
from autopaperreview.public_calibration import strip_venue_status_lines
from autopaperreview.rank_metrics import agreement, bootstrap_interval
from autopaperreview.rating import rate_venue, snap_down
from autopaperreview.rubrics import FUSION_COEFFICIENT_SET
from autopaperreview.stages.builtin import ExecuteReviewParams
from tests.test_rating import _all, _fatal, _package


def _with_holistic(package, rating: float):
    return package.model_copy(update={"metadata": {"holistic_rating": {"rating": rating, "verified": True}}})


class FusionFormulaTests(unittest.TestCase):
    def test_saturated_soundness_does_not_flatten_the_decision_rank(self) -> None:
        package = _with_holistic(
            _package("soft", {**_all(8), "soundness": 4, "claims": 2}),
            5,
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.value, 5)
        self.assertEqual(scored.metadata["venue_bin"], 8)
        self.assertEqual(scored.metadata["candidates"]["decision"], 5)
        self.assertEqual(scored.metadata["candidates"]["decision-k1"], 5)
        self.assertEqual(scored.metadata["candidates"]["decision-k1-capped"], 5)
        self.assertLess(scored.metadata["candidates"]["band-continuous"], 5)
        self.assertEqual(scored.coefficient_set, FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 6)

    def test_verified_cap_stays_off_the_shipped_rank(self) -> None:
        package = _with_holistic(
            _package("capped", _all(8), issues=[_fatal("I-leak", FatalClass.leakage)]),
            5,
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.value, 5)
        self.assertEqual(scored.metadata["venue_bin"], 8)
        self.assertEqual(scored.metadata["candidates"]["decision"], 5)
        capped = scored.metadata["candidates"]["decision-k1-capped"]
        self.assertLess(capped, 2.5)
        self.assertEqual(decision_bin(capped), 3)

    def test_levels_one_through_four_land_on_different_bins(self) -> None:
        self.assertEqual([decision_bin(value) for value in (1.0, 2.0, 3.0, 4.0)], [1, 3, 5, 6])
        self.assertEqual(snap_down(3.0), snap_down(4.0))
        self.assertNotEqual(decision_bin(3.0), decision_bin(4.0))

    def test_missing_holistic_rating_fails_instead_of_using_bands(self) -> None:
        package = _package("missing", {**_all(8), "soundness": 4})
        with self.assertRaises(ValueError) as caught:
            rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertIn("decision", str(caught.exception))

    def test_cheap_package_ranks_without_dimension_bands(self) -> None:
        package = _package("cheap", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {"holistic_rating": {"rating": 4.0, "verified": True, "samples": [3, 4, 5]}},
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.value, 4)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        self.assertEqual(scored.metadata["candidates"]["decision-k1"], 3)
        self.assertEqual(scored.metadata["candidates"]["decision-k2"], 3.5)
        self.assertEqual(scored.metadata["candidates"]["decision-k3"], 4)
        self.assertEqual(scored.metadata["candidates"]["decision-k3-bin"], 6)
        self.assertNotIn("band-continuous", scored.metadata["candidates"])

    def test_cap_v2_value_stays_the_integer_bin(self) -> None:
        scored = rate_venue(_package("high", _all(8)))
        self.assertEqual(scored.value, 8)
        self.assertEqual(scored.metadata["venue_bin"], 8)

    def test_venue_mean_ranks_and_the_decision_sets_the_bin(self) -> None:
        package = _package("both", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.5,
                        "predicted_mean": 5.15,
                        "verified": True,
                        "samples": [3, 4],
                        "mean_samples": [4.2, 6.1],
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 5.15)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k1"], 4.2)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k2"], 5.15)
        self.assertEqual(scored.metadata["candidates"]["decision-k1"], 3)
        self.assertEqual(scored.metadata["fusion_variant"], "mean")

    def test_three_venue_means_are_the_locked_rank(self) -> None:
        package = _package("panel", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.6667,
                        "predicted_mean": 6.1,
                        "verified": True,
                        "samples": [3.0, 4.0, 4.0],
                        "mean_samples": [5.0, 6.2, 7.1],
                        "isolate_samples": [2.0],
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 6.1)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k1"], 5.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k2"], 5.6)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k3"], 6.1)
        self.assertEqual(scored.metadata["candidates"]["decision-k1"], 3.0)
        self.assertEqual(scored.metadata["fusion_variant"], "mean-k3")
        self.assertIn("mean-k3", scored.metadata["formula"])
        self.assertAlmostEqual(scored.metadata["candidates"]["isolate-k1"], 2.0)
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("three venue-mean samples", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("三次会场均分", zh)

    def test_near_tie_prefers_fewer_samples(self) -> None:
        self.assertEqual(LOCKED_CANDIDATE, "mean-k3")
        self.assertEqual(FUSION_VARIANT, LOCKED_CANDIDATE)
        self.assertEqual(SHIPPABLE[0], "mean-k1")
        self.assertLess(SHIPPABLE.index("mean-k1"), SHIPPABLE.index("isolate-k1"))
        self.assertLess(SHIPPABLE.index("mean-k1"), SHIPPABLE.index("mean-k3"))
        self.assertLess(SHIPPABLE.index("isolate-k1"), SHIPPABLE.index("place-k1"))
        metrics = {
            "decision-k1": {"spearman": 0.70, "auc": 0.74},
            "decision-k2": {"spearman": 0.71, "auc": 0.75},
            "decision-k3": {"spearman": 0.715, "auc": 0.752},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "decision-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        self.assertEqual(select_variant(metrics), "decision-k1")

    def test_ladder_placement_ranks_and_the_decision_sets_the_bin(self) -> None:
        package = _package("placed", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "predicted_mean": 5.4,
                        "verified": True,
                        "samples": [3.0],
                        "mean_samples": [5.4],
                        "place_samples": [4.75, 6.25],
                        "count_samples": [4.6, 6.4],
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 5.5)
        self.assertEqual(scored.metadata["venue_bin"], 5)
        self.assertAlmostEqual(scored.metadata["candidates"]["place-k1"], 4.75)
        self.assertAlmostEqual(scored.metadata["candidates"]["place-k2"], 5.5)
        self.assertAlmostEqual(scored.metadata["candidates"]["count-k1"], 4.6)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k1"], 5.4)
        self.assertEqual(scored.metadata["candidates"]["decision-k1"], 3.0)
        self.assertEqual(scored.metadata["fusion_variant"], "place")

    def test_isolated_pair_ranks_and_a_later_pair_stays_off_the_rank(self) -> None:
        package = _package("isolated", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "verified": True,
                        "samples": [4.0, 2.0],
                        "isolate_samples": [6.5, 3.5],
                        "poles": [
                            {
                                "sample": 0,
                                "isolate": 6.5,
                                "anchors": [
                                    {"id": "weak-reject", "relation": "better", "margin": 4, "implied": 6.5},
                                    {"id": "strong-accept", "relation": "worse", "margin": 2, "implied": 6.5},
                                ],
                            }
                        ],
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 6.5)
        self.assertEqual(scored.metadata["venue_bin"], 5)
        self.assertAlmostEqual(scored.metadata["candidates"]["decision"], 3.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["isolate-k1"], 6.5)
        self.assertAlmostEqual(scored.metadata["candidates"]["isolate-k2"], 5.0)
        self.assertEqual(scored.metadata["candidates"]["decision-k1"], 4.0)
        self.assertEqual(scored.metadata["fusion_variant"], "isolate-k1")
        self.assertEqual(scored.metadata["poles"][0]["isolate"], 6.5)
        self.assertIn("isolate-k1", scored.metadata["formula"])

    def test_isolated_pair_wins_over_an_older_placement(self) -> None:
        package = _package("both-ranks", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "verified": True,
                        "samples": [3.0],
                        "place_samples": [4.75],
                        "isolate_samples": [7.5],
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 7.5)
        self.assertEqual(scored.metadata["fusion_variant"], "isolate-k1")
        self.assertEqual(scored.metadata["venue_bin"], 5)

    def test_placement_without_a_decision_fails(self) -> None:
        package = _package("place-only", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {"holistic_rating": {"verified": True, "place_samples": [4.75]}},
            }
        )
        with self.assertRaises(ValueError) as caught:
            rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertIn("decision", str(caught.exception))

    def test_verified_cap_stays_off_the_placement_rank(self) -> None:
        package = _package(
            "placed-cap",
            _all(8),
            issues=[_fatal("I-leak", FatalClass.leakage)],
        ).model_copy(
            update={
                "metadata": {
                    "holistic_rating": {
                        "rating": 5.0,
                        "verified": True,
                        "place_samples": [7.2],
                        "mean_samples": [6.0],
                    }
                }
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 7.2)
        self.assertEqual(scored.metadata["venue_bin"], 8)
        self.assertLess(scored.metadata["candidates"]["decision-k1-capped"], 5.0)

    def test_same_k_prefers_the_venue_mean_over_the_isolated_pair(self) -> None:
        metrics = {
            "isolate-k1": {"spearman": 0.71, "auc": 0.81},
            "mean-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "mean-k1")
        self.assertEqual(report["preregistered"], "mean-k3")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")

    def test_same_k_prefers_the_venue_mean_over_placement(self) -> None:
        metrics = {
            "place-k1": {"spearman": 0.71, "auc": 0.81},
            "mean-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "mean-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")

    def test_a_wide_gap_can_keep_the_venue_mean(self) -> None:
        metrics = {
            "place-k1": {"spearman": 0.40, "auc": 0.55},
            "mean-k1": {"spearman": 0.62, "auc": 0.74},
        }
        self.assertEqual(select_variant(metrics), "mean-k1")

    def test_vote_count_is_not_a_selection_candidate(self) -> None:
        metrics = {
            "place-k1": {"spearman": 0.50, "auc": 0.60},
            "count-k1": {"spearman": 0.90, "auc": 0.90},
            "mean-k1": {"spearman": 0.50, "auc": 0.60},
        }
        self.assertEqual(select_variant(metrics), "mean-k1")

    def test_same_k_prefers_the_venue_mean(self) -> None:
        metrics = {
            "mean-k1": {"spearman": 0.70, "auc": 0.80},
            "decision-k1": {"spearman": 0.71, "auc": 0.81},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "mean-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")

    def test_a_wide_gap_can_keep_the_decision(self) -> None:
        metrics = {
            "mean-k1": {"spearman": 0.40, "auc": 0.55},
            "decision-k1": {"spearman": 0.62, "auc": 0.74},
        }
        self.assertEqual(select_variant(metrics), "decision-k1")

    def test_a_wide_gap_keeps_the_stronger_sample_count(self) -> None:
        metrics = {
            "decision-k1": {"spearman": 0.40, "auc": 0.55},
            "decision-k3": {"spearman": 0.62, "auc": 0.74},
        }
        self.assertEqual(select_variant(metrics), "decision-k3")
        self.assertEqual(selection_report(metrics)["rule"], "within-0.02-then-cheapest")

    def test_crossed_metrics_fall_back_to_spearman(self) -> None:
        metrics = {
            "decision-k1": {"spearman": 0.70, "auc": 0.50},
            "decision-k3": {"spearman": 0.50, "auc": 0.75},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "decision-k1")
        self.assertEqual(report["rule"], "spearman-then-auc")
        self.assertEqual(report["tied"], [])

    def test_cap_enters_only_when_both_metrics_rise(self) -> None:
        improved = {
            "decision-k1": {"spearman": 0.50, "auc": 0.60},
            "decision-k1-capped": {"spearman": 0.55, "auc": 0.62},
        }
        self.assertEqual(select_variant(improved), "decision-k1-capped")
        self.assertTrue(selection_report(improved)["cap_adopted"])
        tied = {
            "decision-k1": {"spearman": 0.50, "auc": 0.60},
            "decision-k1-capped": {"spearman": 0.50, "auc": 0.60},
        }
        self.assertEqual(select_variant(tied), "decision-k1")
        one_sided = {
            "decision-k1": {"spearman": 0.50, "auc": 0.70},
            "decision-k1-capped": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(one_sided), "decision-k1")


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

    def test_candidate_map_keeps_the_decision_rank_above_a_diagnostic_band(self) -> None:
        signals = RankSignals(
            weighted=6.0,
            gate=4.0,
            claims=4.0,
            soundness=4.0,
            band_mean=6.0,
            venue_cap=None,
            holistic=5.2,
        )
        scores = candidate_scores(signals)
        self.assertEqual(scores["decision"], 5.2)
        self.assertEqual(scores["decision-k1"], 5.2)
        self.assertEqual(scores["decision-k1-bin"], 8)
        self.assertLess(scores["band-continuous"], scores["decision"])
        self.assertNotIn("blend-60-gated", scores)

    def test_missing_decision_omits_shippable_scores(self) -> None:
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
        self.assertNotIn("decision", scores)
        self.assertNotIn("decision-k1", scores)
        self.assertIn("band-continuous", scores)

    def test_holistic_sample_count_defaults_to_the_locked_eval(self) -> None:
        import inspect

        from autopaperreview.anchors import LOCKED_HOLISTIC_SAMPLES, LOCKED_HOLISTIC_TEMPERATURE
        from autopaperreview.cli import build_parser
        from autopaperreview.model_exec import DEFAULT_HOLISTIC_SAMPLES
        from autopaperreview.public_calibration import _write_project, run_cache

        params = ExecuteReviewParams(adapter="autopaperreview.model_exec:OpenAICompatibleAdapter")
        self.assertEqual(LOCKED_HOLISTIC_SAMPLES, 3)
        self.assertEqual(DEFAULT_HOLISTIC_SAMPLES, LOCKED_HOLISTIC_SAMPLES)
        self.assertEqual(params.holistic_samples, LOCKED_HOLISTIC_SAMPLES)
        self.assertEqual(params.holistic_temperature, LOCKED_HOLISTIC_TEMPERATURE)
        self.assertEqual(LOCKED_HOLISTIC_TEMPERATURE, 0.7)
        self.assertTrue(params.cheap is False)
        args = build_parser().parse_args(["calibrate-eval", "run", "--cache", "unused"])
        self.assertEqual(args.holistic_samples, LOCKED_HOLISTIC_SAMPLES)
        self.assertEqual(
            inspect.signature(run_cache).parameters["holistic_samples"].default,
            LOCKED_HOLISTIC_SAMPLES,
        )
        self.assertEqual(
            inspect.signature(_write_project).parameters["holistic_samples"].default,
            LOCKED_HOLISTIC_SAMPLES,
        )
