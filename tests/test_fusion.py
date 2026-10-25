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

    def test_two_simulated_discussions_are_the_locked_rank(self) -> None:
        panels = [
            {
                "sample": 0,
                "reviews": [
                    {"role": "specialist", "rating": 5, "confidence": 4, "final_rating": 6},
                    {"role": "generalist", "rating": 6, "confidence": 3, "final_rating": 6},
                    {"role": "senior", "rating": 3, "confidence": 3, "final_rating": 3},
                ],
                "initial_mean": 4.6667,
                "final_mean": 5.0,
                "lift": 0.3333,
                "raised": 1,
                "lowered": 0,
                "high": 6,
                "low": 3,
                "off_scale": 0,
                "label_mismatch": 0,
                "chair_mean": 5.2,
                "decision": 3.0,
            },
            {
                "sample": 1,
                "reviews": [
                    {"role": "specialist", "rating": 6, "confidence": 4, "final_rating": 8},
                    {"role": "other", "rating": 5, "confidence": 5, "final_rating": 6},
                    {"role": "senior", "rating": 8, "confidence": 3, "final_rating": 7},
                ],
                "initial_mean": 6.3333,
                "final_mean": 7.0,
                "lift": 0.6667,
                "raised": 2,
                "lowered": 1,
                "high": 8,
                "low": 6,
                "off_scale": 1,
                "label_mismatch": 0,
                "chair_mean": 6.8,
                "decision": 5.0,
            },
        ]
        main_text = {"source_chars": 1000, "shown_chars": 1000, "cut": "none"}
        package = _package("rebuttal", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [3.0, 5.0],
                        "rebuttal_mean": 6.0,
                        "rebuttal_samples": [5.0, 7.0],
                        "initial_mean": 5.5,
                        "initial_samples": [4.6667, 6.3333],
                        "chair_mean": 6.0,
                        "chair_samples": [5.2, 6.8],
                        "high_samples": [6.0, 8.0],
                        "low_samples": [3.0, 6.0],
                        "rebuttal_panels": panels,
                        "main_text": main_text,
                        "verified": True,
                        "mode": "rebuttal",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 6.0)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        candidates = scored.metadata["candidates"]
        self.assertAlmostEqual(candidates["rebuttal-k1"], 5.0)
        self.assertAlmostEqual(candidates["rebuttal-k2"], 6.0)
        self.assertAlmostEqual(candidates["rebuttal"], 6.0)
        self.assertAlmostEqual(candidates["initial-k1"], 4.6667)
        self.assertAlmostEqual(candidates["initial-k2"], 5.5)
        self.assertAlmostEqual(candidates["initial"], 5.5)
        self.assertAlmostEqual(candidates["chair-k2"], 6.0)
        self.assertAlmostEqual(candidates["high-k2"], 7.0)
        self.assertAlmostEqual(candidates["low-k2"], 4.5)
        self.assertEqual(candidates["decision-k2"], 4.0)
        for absent in ("matched-k1", "matched-k2", "panel-k1", "panel-k2", "expert-k1", "form-k1", "audited-k1", "mean-k1"):
            self.assertNotIn(absent, candidates)
        self.assertEqual(scored.metadata["fusion_variant"], "rebuttal-k2")
        self.assertIn("rank = rebuttal-k2;", scored.metadata["formula"])
        self.assertIn("end of the discussion", scored.metadata["formula"])
        self.assertIn("initial-k2", scored.metadata["formula"])
        self.assertIn("chair-k2", scored.metadata["formula"])
        self.assertIn("authors' response", scored.notes or "")
        self.assertIn("initial-k2", scored.notes or "")
        self.assertEqual(scored.metadata["rebuttal_samples"], [5.0, 7.0])
        self.assertEqual(scored.metadata["initial_samples"], [4.6667, 6.3333])
        self.assertEqual(scored.metadata["matched_samples"], [])
        self.assertEqual(scored.metadata["panel_samples"], [])
        self.assertEqual(scored.metadata["rebuttal_panels"], panels)
        self.assertEqual(scored.metadata["matched_panels"], [])
        self.assertEqual(scored.metadata["panels"], [])
        self.assertEqual(scored.metadata["main_text"], main_text)
        self.assertEqual(scored.metadata["holistic_mode"], "rebuttal")
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("end of the discussion period", note)
        self.assertIn("three typical reviewers", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("作者回应", zh)

    def test_one_discussion_ranks_by_the_stored_mean_and_three_rank_by_the_first_two(self) -> None:
        package = _package("rebuttal-one", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 2.0,
                        "samples": [2.0],
                        "rebuttal_samples": [4.3333],
                        "initial_samples": [4.0],
                        "chair_samples": [4.6],
                        "high_samples": [6.0],
                        "low_samples": [3.0],
                        "verified": True,
                        "mode": "rebuttal",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 4.3333)
        self.assertEqual(scored.metadata["venue_bin"], 3)
        self.assertEqual(scored.metadata["fusion_variant"], "rebuttal")
        self.assertNotIn("rebuttal-k2", scored.metadata["candidates"])
        self.assertAlmostEqual(scored.metadata["candidates"]["rebuttal-k1"], 4.3333)
        self.assertAlmostEqual(scored.metadata["candidates"]["initial-k1"], 4.0)
        self.assertIn("rank = rebuttal;", scored.metadata["formula"])
        self.assertEqual(scored.metadata["main_text"], {})
        self.assertEqual(scored.metadata["rebuttal_panels"], [])
        from autopaperreview.reporting import _fusion_rank_note

        self.assertIn("end of the discussion period", _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max))
        three = _package("rebuttal-three", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0, 4.0],
                        "rebuttal_samples": [6.0, 7.0, 2.0],
                        "verified": True,
                        "mode": "rebuttal",
                    }
                },
            }
        )
        scored = rate_venue(three, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.5)
        self.assertEqual(scored.metadata["fusion_variant"], "rebuttal-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["rebuttal-k3"], 5.0)

    def test_discussions_win_over_every_older_family(self) -> None:
        package = _package("every-family-rebuttal", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "rebuttal_samples": [6.5, 6.0],
                        "initial_samples": [5.5, 5.0],
                        "matched_samples": [7.0, 7.5],
                        "panel_samples": [7.0, 7.5],
                        "expert_samples": [7.4, 7.0],
                        "audited_samples": [3.0],
                        "mean_samples": [5.0, 5.2, 5.4],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.25)
        self.assertEqual(scored.metadata["fusion_variant"], "rebuttal-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["initial-k2"], 5.25)
        self.assertAlmostEqual(scored.metadata["candidates"]["matched-k2"], 7.25)
        self.assertAlmostEqual(scored.metadata["candidates"]["panel-k2"], 7.25)
        self.assertAlmostEqual(scored.metadata["candidates"]["expert-k2"], 7.2)

    def test_one_forecast_panel_is_the_locked_rank(self) -> None:
        panels = [
            {
                "sample": 0,
                "reviews": [
                    {"role": "specialist", "rating": 5, "confidence": 4, "expected": 5.4, "peak": 0.5, "probabilities": [0.0, 0.1, 0.5, 0.3, 0.1, 0.0]},
                    {"role": "other", "rating": 6, "confidence": 3, "expected": 6.4, "peak": 0.5, "probabilities": [0.0, 0.0, 0.2, 0.5, 0.3, 0.0]},
                    {"role": "senior", "rating": 3, "confidence": 3, "expected": 3.4, "peak": 0.6, "probabilities": [0.1, 0.6, 0.3, 0.0, 0.0, 0.0]},
                ],
                "forecast_mean": 5.0667,
                "vote_mean": 4.6667,
                "high": 6.4,
                "low": 3.4,
                "off_scale": 0,
                "renormalized": 1,
                "mode_mismatch": 0,
                "chair_mean": 5.2,
                "decision": 3.0,
            }
        ]
        main_text = {"source_chars": 1000, "shown_chars": 1000, "cut": "none"}
        package = _package("forecast", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "samples": [3.0],
                        "forecast_mean": 5.0667,
                        "forecast_samples": [5.0667],
                        "vote_mean": 4.6667,
                        "vote_samples": [4.6667],
                        "chair_mean": 5.2,
                        "chair_samples": [5.2],
                        "high_samples": [6.4],
                        "low_samples": [3.4],
                        "forecast_panels": panels,
                        "main_text": main_text,
                        "verified": True,
                        "mode": "forecast",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 5.0667)
        self.assertEqual(scored.metadata["venue_bin"], 5)
        candidates = scored.metadata["candidates"]
        self.assertAlmostEqual(candidates["forecast-k1"], 5.0667)
        self.assertAlmostEqual(candidates["forecast"], 5.0667)
        self.assertAlmostEqual(candidates["vote-k1"], 4.6667)
        self.assertAlmostEqual(candidates["vote"], 4.6667)
        self.assertAlmostEqual(candidates["chair-k1"], 5.2)
        self.assertAlmostEqual(candidates["high-k1"], 6.4)
        self.assertAlmostEqual(candidates["low-k1"], 3.4)
        self.assertEqual(candidates["decision-k1"], 3.0)
        for absent in ("forecast-k2", "vote-k2", "rebuttal-k1", "rebuttal-k2", "initial-k1", "matched-k1", "panel-k1", "expert-k1", "form-k1", "audited-k1", "mean-k1"):
            self.assertNotIn(absent, candidates)
        self.assertEqual(scored.metadata["fusion_variant"], "forecast-k1")
        self.assertIn("rank = forecast-k1;", scored.metadata["formula"])
        self.assertIn("expected rating", scored.metadata["formula"])
        self.assertIn("vote-k1", scored.metadata["formula"])
        self.assertIn("chair-k1", scored.metadata["formula"])
        self.assertIn("probability over the six official ratings", scored.notes or "")
        self.assertIn("vote-k1", scored.notes or "")
        self.assertEqual(scored.metadata["forecast_samples"], [5.0667])
        self.assertEqual(scored.metadata["vote_samples"], [4.6667])
        self.assertEqual(scored.metadata["rebuttal_samples"], [])
        self.assertEqual(scored.metadata["initial_samples"], [])
        self.assertEqual(scored.metadata["forecast_panels"], panels)
        self.assertEqual(scored.metadata["rebuttal_panels"], [])
        self.assertEqual(scored.metadata["panels"], [])
        self.assertEqual(scored.metadata["main_text"], main_text)
        self.assertEqual(scored.metadata["holistic_mode"], "forecast")
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("mean expected official rating", note)
        self.assertIn("three typical reviewers", note)
        self.assertIn("probability over the official ratings", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("期望评分", zh)

    def test_two_forecast_panels_still_rank_by_the_first_and_a_forecast_wins_over_every_older_family(self) -> None:
        package = _package("forecast-two", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0],
                        "forecast_samples": [5.0, 7.0],
                        "vote_samples": [4.6667, 6.3333],
                        "chair_samples": [5.2, 6.8],
                        "high_samples": [6.4, 8.0],
                        "low_samples": [3.4, 6.0],
                        "verified": True,
                        "mode": "forecast",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 5.0)
        self.assertEqual(scored.metadata["fusion_variant"], "forecast-k1")
        self.assertAlmostEqual(scored.metadata["candidates"]["forecast-k2"], 6.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["forecast"], 6.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["vote-k2"], 5.5)
        self.assertEqual(scored.metadata["forecast_panels"], [])
        self.assertEqual(scored.metadata["main_text"], {})
        every = _package("every-family-forecast", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "forecast_samples": [6.1],
                        "vote_samples": [6.0],
                        "rebuttal_samples": [6.5, 6.0],
                        "initial_samples": [5.5, 5.0],
                        "matched_samples": [7.0, 7.5],
                        "panel_samples": [7.0, 7.5],
                        "expert_samples": [7.4, 7.0],
                        "audited_samples": [3.0],
                        "mean_samples": [5.0, 5.2, 5.4],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(every, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.1)
        self.assertEqual(scored.metadata["fusion_variant"], "forecast-k1")
        self.assertAlmostEqual(scored.metadata["candidates"]["rebuttal-k2"], 6.25)
        self.assertAlmostEqual(scored.metadata["candidates"]["vote-k1"], 6.0)
        # An out-of-range forecast sample is not a verified sample; the package falls through.
        broken = _package("forecast-broken", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "forecast_samples": [10.5],
                        "rebuttal_samples": [6.5, 6.0],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(broken, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.metadata["fusion_variant"], "rebuttal-k2")
        self.assertNotIn("forecast-k1", scored.metadata["candidates"])

    def test_two_simulated_matched_panels_are_the_previous_rank(self) -> None:
        panels = [
            {
                "sample": 0,
                "reviews": [
                    {"role": "insider", "rating": 5, "confidence": 4},
                    {"role": "junior", "rating": 6, "confidence": 3},
                    {"role": "neighbour", "rating": 3, "confidence": 4},
                    {"role": "senior", "rating": 8, "confidence": 3},
                ],
                "panel_mean": 5.5,
                "high": 8,
                "low": 3,
                "off_scale": 0,
                "chair_mean": 5.7,
                "decision": 4.0,
            },
            {
                "sample": 1,
                "reviews": [
                    {"role": "insider", "rating": 6, "confidence": 4},
                    {"role": "junior", "rating": 6, "confidence": 3},
                    {"role": "other", "rating": 5, "confidence": 5},
                    {"role": "senior", "rating": 7, "confidence": 3},
                ],
                "panel_mean": 6.0,
                "high": 7,
                "low": 5,
                "off_scale": 1,
                "chair_mean": 6.1,
                "decision": 4.0,
            },
        ]
        main_text = {"source_chars": 1000, "shown_chars": 600, "cut": "references"}
        package = _package("matched", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0],
                        "matched_mean": 5.75,
                        "matched_samples": [5.5, 6.0],
                        "chair_mean": 5.9,
                        "chair_samples": [5.7, 6.1],
                        "high_samples": [8.0, 7.0],
                        "low_samples": [3.0, 5.0],
                        "matched_panels": panels,
                        "main_text": main_text,
                        "verified": True,
                        "mode": "matched",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 5.75)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        candidates = scored.metadata["candidates"]
        self.assertAlmostEqual(candidates["matched-k1"], 5.5)
        self.assertAlmostEqual(candidates["matched-k2"], 5.75)
        self.assertAlmostEqual(candidates["matched"], 5.75)
        self.assertAlmostEqual(candidates["chair-k2"], 5.9)
        self.assertAlmostEqual(candidates["high-k2"], 7.5)
        self.assertAlmostEqual(candidates["low-k2"], 4.0)
        self.assertEqual(candidates["decision-k2"], 4.0)
        for absent in ("panel-k1", "panel-k2", "expert-k1", "form-k1", "audited-k1", "mean-k1"):
            self.assertNotIn(absent, candidates)
        self.assertEqual(scored.metadata["fusion_variant"], "matched-k2")
        self.assertIn("rank = matched-k2;", scored.metadata["formula"])
        self.assertIn("on the main text", scored.metadata["formula"])
        self.assertIn("chair-k2", scored.metadata["formula"])
        self.assertIn("main text", scored.notes or "")
        self.assertEqual(scored.metadata["matched_samples"], [5.5, 6.0])
        self.assertEqual(scored.metadata["panel_samples"], [])
        self.assertEqual(scored.metadata["matched_panels"], panels)
        self.assertEqual(scored.metadata["main_text"], main_text)
        self.assertEqual(scored.metadata["panels"], [])
        self.assertEqual(scored.metadata["holistic_mode"], "matched")
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("topic-matched panel", note)
        self.assertIn("main text", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("主题匹配", zh)

    def test_one_matched_panel_ranks_by_the_stored_mean_and_three_rank_by_the_first_two(self) -> None:
        package = _package("matched-one", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 2.0,
                        "samples": [2.0],
                        "matched_samples": [4.25],
                        "chair_samples": [4.6],
                        "high_samples": [6.0],
                        "low_samples": [3.0],
                        "verified": True,
                        "mode": "matched",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 4.25)
        self.assertEqual(scored.metadata["venue_bin"], 3)
        self.assertEqual(scored.metadata["fusion_variant"], "matched")
        self.assertNotIn("matched-k2", scored.metadata["candidates"])
        self.assertAlmostEqual(scored.metadata["candidates"]["matched-k1"], 4.25)
        self.assertIn("rank = matched;", scored.metadata["formula"])
        self.assertEqual(scored.metadata["main_text"], {})
        from autopaperreview.reporting import _fusion_rank_note

        self.assertIn("topic-matched panel", _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max))
        three = _package("matched-three", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0, 4.0],
                        "matched_samples": [6.0, 7.0, 2.0],
                        "verified": True,
                        "mode": "matched",
                    }
                },
            }
        )
        scored = rate_venue(three, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.5)
        self.assertEqual(scored.metadata["fusion_variant"], "matched-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["matched-k3"], 5.0)

    def test_matched_panels_win_over_every_older_family(self) -> None:
        package = _package("every-family-matched", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "matched_samples": [6.5, 6.0],
                        "panel_samples": [7.0, 7.5],
                        "expert_samples": [7.4, 7.0],
                        "audited_samples": [3.0],
                        "mean_samples": [5.0, 5.2, 5.4],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.25)
        self.assertEqual(scored.metadata["fusion_variant"], "matched-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["panel-k2"], 7.25)
        self.assertAlmostEqual(scored.metadata["candidates"]["expert-k2"], 7.2)

    def test_two_simulated_panels_are_the_locked_rank(self) -> None:
        panels = [
            {
                "sample": 0,
                "reviews": [
                    {"role": "specialist", "rating": 5, "confidence": 4},
                    {"role": "generalist", "rating": 6, "confidence": 3},
                    {"role": "methodologist", "rating": 3, "confidence": 4},
                    {"role": "senior", "rating": 8, "confidence": 3},
                ],
                "panel_mean": 5.5,
                "high": 8,
                "low": 3,
                "off_scale": 0,
                "chair_mean": 5.7,
                "decision": 4.0,
            },
            {
                "sample": 1,
                "reviews": [
                    {"role": "specialist", "rating": 6, "confidence": 4},
                    {"role": "generalist", "rating": 6, "confidence": 3},
                    {"role": "methodologist", "rating": 5, "confidence": 5},
                    {"role": "senior", "rating": 7, "confidence": 3},
                ],
                "panel_mean": 6.0,
                "high": 7,
                "low": 5,
                "off_scale": 1,
                "chair_mean": 6.1,
                "decision": 4.0,
            },
        ]
        package = _package("panel", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0],
                        "panel_mean": 5.75,
                        "panel_samples": [5.5, 6.0],
                        "chair_mean": 5.9,
                        "chair_samples": [5.7, 6.1],
                        "high_samples": [8.0, 7.0],
                        "low_samples": [3.0, 5.0],
                        "panels": panels,
                        "verified": True,
                        "mode": "panel",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 5.75)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        candidates = scored.metadata["candidates"]
        self.assertAlmostEqual(candidates["panel-k1"], 5.5)
        self.assertAlmostEqual(candidates["panel-k2"], 5.75)
        self.assertAlmostEqual(candidates["panel"], 5.75)
        self.assertAlmostEqual(candidates["chair-k1"], 5.7)
        self.assertAlmostEqual(candidates["chair-k2"], 5.9)
        self.assertAlmostEqual(candidates["high-k2"], 7.5)
        self.assertAlmostEqual(candidates["low-k2"], 4.0)
        self.assertEqual(candidates["decision-k2"], 4.0)
        for absent in ("matched-k1", "expert-k1", "form-k1", "audited-k1", "mean-k1"):
            self.assertNotIn(absent, candidates)
        self.assertEqual(scored.metadata["fusion_variant"], "panel-k2")
        self.assertIn("rank = panel-k2;", scored.metadata["formula"])
        self.assertIn("four simulated official ratings", scored.metadata["formula"])
        self.assertIn("chair-k2", scored.metadata["formula"])
        self.assertEqual(scored.metadata["panel_samples"], [5.5, 6.0])
        self.assertEqual(scored.metadata["chair_samples"], [5.7, 6.1])
        self.assertEqual(scored.metadata["high_samples"], [8.0, 7.0])
        self.assertEqual(scored.metadata["low_samples"], [3.0, 5.0])
        self.assertEqual(scored.metadata["panels"], panels)
        self.assertEqual(scored.metadata["holistic_mode"], "panel")
        self.assertEqual(scored.metadata["forms"], [])
        self.assertEqual(scored.metadata["audits"], [])
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("simulated panel", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("审稿组", zh)

    def test_one_panel_ranks_by_the_stored_panel_mean(self) -> None:
        package = _package("panel-one", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 2.0,
                        "samples": [2.0],
                        "panel_samples": [4.25],
                        "chair_samples": [4.6],
                        "high_samples": [6.0],
                        "low_samples": [3.0],
                        "verified": True,
                        "mode": "panel",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 4.25)
        self.assertEqual(scored.metadata["venue_bin"], 3)
        self.assertEqual(scored.metadata["fusion_variant"], "panel")
        self.assertNotIn("panel-k2", scored.metadata["candidates"])
        self.assertAlmostEqual(scored.metadata["candidates"]["panel-k1"], 4.25)
        self.assertAlmostEqual(scored.metadata["candidates"]["chair"], 4.6)
        self.assertIn("rank = panel;", scored.metadata["formula"])
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("simulated panel", note)

    def test_three_panels_rank_by_the_first_two(self) -> None:
        package = _package("panel-three", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0, 4.0],
                        "panel_samples": [6.0, 7.0, 2.0],
                        "verified": True,
                        "mode": "panel",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.5)
        self.assertEqual(scored.metadata["fusion_variant"], "panel-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["panel-k3"], 5.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["panel"], 5.0)

    def test_panels_win_over_expert_reads_an_audited_mean_and_venue_means(self) -> None:
        package = _package("every-family", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "panel_samples": [6.5, 6.0],
                        "expert_samples": [7.4, 7.0],
                        "audited_samples": [3.0],
                        "mean_samples": [5.0, 5.2, 5.4],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.25)
        self.assertEqual(scored.metadata["fusion_variant"], "panel-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["expert-k2"], 7.2)
        self.assertAlmostEqual(scored.metadata["candidates"]["audited-k1"], 3.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k3"], 5.2)

    def test_chair_and_extremes_alone_are_not_a_rank(self) -> None:
        package = _package("chair-only", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "samples": [3.0],
                        "chair_samples": [9.0],
                        "high_samples": [10.0],
                        "low_samples": [8.0],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.metadata["fusion_variant"], "decision")
        self.assertAlmostEqual(scored.metadata["candidates"]["chair-k1"], 9.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["high-k1"], 10.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["low-k1"], 8.0)
        self.assertAlmostEqual(scored.value, 3.0)

    def test_two_expert_reads_are_the_locked_rank(self) -> None:
        forms = [
            {
                "sample": 0,
                "contribution_type": "method",
                "soundness": 3,
                "presentation": 3,
                "contribution": 2,
                "form_score": 6.0,
                "prior_work": 2,
                "prior_work_sure": 1,
                "prior_work_uncited_sure": 0,
                "field_context_lines": 2,
            },
            {
                "sample": 1,
                "contribution_type": "analysis",
                "soundness": 3,
                "presentation": 4,
                "contribution": 3,
                "form_score": 8.0,
                "prior_work": 0,
                "prior_work_sure": 0,
                "prior_work_uncited_sure": 0,
                "field_context_lines": 1,
            },
        ]
        package = _package("expert", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.5,
                        "samples": [3.0, 4.0],
                        "expert_mean": 5.6,
                        "expert_samples": [5.2, 6.0],
                        "form_samples": [6.0, 8.0],
                        "forms": forms,
                        "verified": True,
                        "mode": "expert",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 5.6)
        self.assertEqual(scored.metadata["venue_bin"], 6)
        candidates = scored.metadata["candidates"]
        self.assertAlmostEqual(candidates["expert-k1"], 5.2)
        self.assertAlmostEqual(candidates["expert-k2"], 5.6)
        self.assertAlmostEqual(candidates["expert"], 5.6)
        self.assertAlmostEqual(candidates["form-k1"], 6.0)
        self.assertAlmostEqual(candidates["form-k2"], 7.0)
        self.assertEqual(candidates["decision-k1"], 3.0)
        self.assertEqual(candidates["decision-k2"], 3.5)
        self.assertNotIn("panel-k1", candidates)
        self.assertNotIn("audited-k1", candidates)
        self.assertNotIn("mean-k1", candidates)
        self.assertEqual(scored.metadata["fusion_variant"], "expert-k2")
        self.assertIn("expert-k2", scored.metadata["formula"])
        self.assertIn("form-k2", scored.metadata["formula"])
        self.assertEqual(scored.metadata["expert_samples"], [5.2, 6.0])
        self.assertEqual(scored.metadata["form_samples"], [6.0, 8.0])
        self.assertEqual(scored.metadata["forms"], forms)
        self.assertEqual(scored.metadata["holistic_mode"], "expert")
        self.assertEqual(scored.metadata["audits"], [])
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("expert-read", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("专家", zh)

    def test_one_expert_read_ranks_by_the_stored_mean(self) -> None:
        package = _package("expert-one", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 2.0,
                        "samples": [2.0],
                        "expert_samples": [4.3],
                        "form_samples": [5.0],
                        "verified": True,
                        "mode": "expert",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 4.3)
        self.assertEqual(scored.metadata["venue_bin"], 3)
        self.assertEqual(scored.metadata["fusion_variant"], "expert")
        self.assertNotIn("expert-k2", scored.metadata["candidates"])
        self.assertAlmostEqual(scored.metadata["candidates"]["expert-k1"], 4.3)
        self.assertIn("rank = expert;", scored.metadata["formula"])
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("expert-read", note)

    def test_three_expert_reads_rank_by_the_first_two(self) -> None:
        package = _package("expert-three", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0, 4.0, 4.0],
                        "expert_samples": [6.0, 7.0, 2.0],
                        "verified": True,
                        "mode": "expert",
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 6.5)
        self.assertEqual(scored.metadata["fusion_variant"], "expert-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["expert-k3"], 5.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["expert"], 5.0)

    def test_expert_reads_win_over_an_audited_mean_and_venue_means(self) -> None:
        package = _package("all-families", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "expert_samples": [7.4, 7.0],
                        "audited_samples": [3.0],
                        "mean_samples": [5.0, 5.2, 5.4],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 7.2)
        self.assertEqual(scored.metadata["fusion_variant"], "expert-k2")
        self.assertAlmostEqual(scored.metadata["candidates"]["audited-k1"], 3.0)
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k3"], 5.2)

    def test_form_triad_alone_is_not_a_rank(self) -> None:
        package = _package("form-only", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "samples": [3.0],
                        "form_samples": [9.0],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.metadata["fusion_variant"], "decision")
        self.assertAlmostEqual(scored.metadata["candidates"]["form-k1"], 9.0)
        self.assertAlmostEqual(scored.value, 3.0)

    def test_audited_mean_still_ranks_an_audited_package(self) -> None:
        package = _package("audited", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "samples": [3.0],
                        "audited_mean": 4.7,
                        "audited_samples": [4.7],
                        "checks_samples": [5.5],
                        "verified": True,
                        "mode": "audited",
                        "audits": [
                            {
                                "sample": 0,
                                "counts": {"pass": 6, "fail": 4, "unclear": 2},
                                "checks": [],
                                "flaws": [],
                                "checks_score": 5.5,
                            }
                        ],
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.scale_max, 10)
        self.assertAlmostEqual(scored.value, 4.7)
        self.assertEqual(scored.metadata["venue_bin"], 5)
        candidates = scored.metadata["candidates"]
        self.assertAlmostEqual(candidates["audited-k1"], 4.7)
        self.assertAlmostEqual(candidates["audited"], 4.7)
        self.assertAlmostEqual(candidates["checks-k1"], 5.5)
        self.assertEqual(candidates["decision-k1"], 3.0)
        self.assertNotIn("mean-k1", candidates)
        self.assertNotIn("mean", candidates)
        self.assertEqual(scored.metadata["fusion_variant"], "audited-k1")
        self.assertIn("audited-k1", scored.metadata["formula"])
        self.assertIn("checks-k1", scored.metadata["formula"])
        self.assertEqual(scored.metadata["audited_samples"], [4.7])
        self.assertEqual(scored.metadata["checks_samples"], [5.5])
        self.assertEqual(scored.metadata["holistic_mode"], "audited")
        self.assertEqual(scored.metadata["audits"][0]["counts"]["pass"], 6)
        from autopaperreview.reporting import _fusion_rank_note

        note = _fusion_rank_note(scored.metadata, "en", scored.value, scored.scale_max)
        self.assertIn("verified audit", note)
        self.assertIn("1–6 decision", note)
        zh = _fusion_rank_note(scored.metadata, "zh-Hans", scored.value, scored.scale_max)
        self.assertIn("核验", zh)

    def test_audited_mean_wins_over_three_venue_means(self) -> None:
        package = _package("both-families", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 4.0,
                        "samples": [4.0],
                        "audited_samples": [7.0],
                        "mean_samples": [5.0, 5.2, 5.4],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertAlmostEqual(scored.value, 7.0)
        self.assertEqual(scored.metadata["fusion_variant"], "audited-k1")
        self.assertAlmostEqual(scored.metadata["candidates"]["mean-k3"], 5.2)
        self.assertEqual(scored.metadata["venue_bin"], 6)

    def test_checks_count_alone_is_not_a_rank(self) -> None:
        package = _package("checks-only", _all(8)).model_copy(
            update={
                "dimension_scores": [],
                "metadata": {
                    "holistic_rating": {
                        "rating": 3.0,
                        "samples": [3.0],
                        "checks_samples": [8.5],
                        "verified": True,
                    }
                },
            }
        )
        scored = rate_venue(package, coefficient_set=FUSION_COEFFICIENT_SET)
        self.assertEqual(scored.metadata["fusion_variant"], "decision")
        self.assertAlmostEqual(scored.metadata["candidates"]["checks-k1"], 8.5)
        self.assertAlmostEqual(scored.value, 3.0)

    def test_three_venue_means_still_rank_by_mean_k3(self) -> None:
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
        self.assertEqual(LOCKED_CANDIDATE, "forecast-k1")
        self.assertEqual(FUSION_VARIANT, LOCKED_CANDIDATE)
        self.assertEqual(SHIPPABLE[0], "forecast-k1")
        self.assertLess(SHIPPABLE.index("forecast-k1"), SHIPPABLE.index("rebuttal-k1"))
        self.assertLess(SHIPPABLE.index("decision-k1"), SHIPPABLE.index("forecast-k2"))
        self.assertLess(SHIPPABLE.index("forecast-k2"), SHIPPABLE.index("rebuttal-k2"))
        self.assertLess(SHIPPABLE.index("decision-k2"), SHIPPABLE.index("forecast-k3"))
        self.assertNotIn("vote-k1", SHIPPABLE)
        self.assertLess(SHIPPABLE.index("rebuttal-k1"), SHIPPABLE.index("matched-k1"))
        self.assertLess(SHIPPABLE.index("matched-k1"), SHIPPABLE.index("panel-k1"))
        self.assertLess(SHIPPABLE.index("panel-k1"), SHIPPABLE.index("expert-k1"))
        self.assertLess(SHIPPABLE.index("mean-k1"), SHIPPABLE.index("rebuttal-k2"))
        self.assertLess(SHIPPABLE.index("rebuttal-k2"), SHIPPABLE.index("matched-k2"))
        self.assertLess(SHIPPABLE.index("matched-k2"), SHIPPABLE.index("panel-k2"))
        self.assertLess(SHIPPABLE.index("decision-k2"), SHIPPABLE.index("rebuttal-k3"))
        self.assertNotIn("initial-k1", SHIPPABLE)
        self.assertNotIn("initial-k2", SHIPPABLE)
        self.assertLess(SHIPPABLE.index("expert-k1"), SHIPPABLE.index("audited-k1"))
        self.assertLess(SHIPPABLE.index("audited-k1"), SHIPPABLE.index("mean-k1"))
        self.assertLess(SHIPPABLE.index("mean-k1"), SHIPPABLE.index("isolate-k1"))
        self.assertLess(SHIPPABLE.index("mean-k1"), SHIPPABLE.index("mean-k3"))
        self.assertLess(SHIPPABLE.index("mean-k1"), SHIPPABLE.index("panel-k2"))
        self.assertLess(SHIPPABLE.index("panel-k2"), SHIPPABLE.index("expert-k2"))
        self.assertLess(SHIPPABLE.index("expert-k2"), SHIPPABLE.index("audited-k2"))
        self.assertLess(SHIPPABLE.index("isolate-k1"), SHIPPABLE.index("place-k1"))
        self.assertNotIn("checks-k1", SHIPPABLE)
        self.assertNotIn("form-k1", SHIPPABLE)
        self.assertNotIn("chair-k1", SHIPPABLE)
        self.assertNotIn("high-k1", SHIPPABLE)
        self.assertNotIn("low-k1", SHIPPABLE)
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
        self.assertEqual(report["preregistered"], "forecast-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")

    def test_same_k_prefers_the_forecast_over_the_discussion(self) -> None:
        from autopaperreview.fusion import DIAGNOSTIC

        metrics = {
            "rebuttal-k1": {"spearman": 0.71, "auc": 0.81},
            "forecast-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "forecast-k1")
        self.assertEqual(report["preregistered"], "forecast-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        wide = {
            "rebuttal-k1": {"spearman": 0.75, "auc": 0.85},
            "forecast-k1": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(wide), "rebuttal-k1")
        longer = {
            "forecast-k1": {"spearman": 0.70, "auc": 0.80},
            "forecast-k2": {"spearman": 0.71, "auc": 0.81},
        }
        self.assertEqual(select_variant(longer), "forecast-k1")
        # The integer-vote control never wins a table, however high.
        diagnostic = {
            "forecast-k1": {"spearman": 0.60, "auc": 0.70},
            "vote-k1": {"spearman": 0.90, "auc": 0.95},
            "chair-k1": {"spearman": 0.90, "auc": 0.95},
        }
        self.assertEqual(select_variant(diagnostic), "forecast-k1")
        for name in ("vote-k1", "vote-k2", "vote-k3"):
            self.assertIn(name, DIAGNOSTIC)
            self.assertNotIn(name, SHIPPABLE)
        self.assertEqual(SHIPPABLE[:3], ("forecast-k1", "rebuttal-k1", "matched-k1"))

    def test_same_k_prefers_the_discussion_over_the_matched_panel(self) -> None:
        from autopaperreview.fusion import DIAGNOSTIC

        metrics = {
            "matched-k1": {"spearman": 0.71, "auc": 0.81},
            "rebuttal-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "rebuttal-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        wide = {
            "matched-k1": {"spearman": 0.75, "auc": 0.85},
            "rebuttal-k1": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(wide), "matched-k1")
        longer = {
            "rebuttal-k1": {"spearman": 0.70, "auc": 0.80},
            "rebuttal-k2": {"spearman": 0.71, "auc": 0.81},
        }
        self.assertEqual(select_variant(longer), "rebuttal-k1")
        # A diagnostic family never wins a table, however high.
        diagnostic = {
            "rebuttal-k2": {"spearman": 0.60, "auc": 0.70},
            "initial-k2": {"spearman": 0.90, "auc": 0.95},
        }
        self.assertEqual(select_variant(diagnostic), "rebuttal-k2")
        self.assertEqual(SHIPPABLE[1:4], ("rebuttal-k1", "matched-k1", "panel-k1"))
        for name in ("initial-k1", "initial-k2", "initial-k3"):
            self.assertIn(name, DIAGNOSTIC)
            self.assertNotIn(name, SHIPPABLE)
        self.assertIn("rebuttal-k2", SHIPPABLE)
        self.assertIn("rebuttal-k3", SHIPPABLE)
        self.assertEqual(FUSION_VARIANT, "forecast-k1")

    def test_same_k_prefers_the_matched_panel_over_the_panel(self) -> None:
        metrics = {
            "panel-k1": {"spearman": 0.71, "auc": 0.81},
            "matched-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "matched-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        wide = {
            "panel-k1": {"spearman": 0.75, "auc": 0.85},
            "matched-k1": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(wide), "panel-k1")
        longer = {
            "matched-k1": {"spearman": 0.70, "auc": 0.80},
            "matched-k2": {"spearman": 0.71, "auc": 0.81},
        }
        self.assertEqual(select_variant(longer), "matched-k1")
        self.assertEqual(SHIPPABLE[2:4], ("matched-k1", "panel-k1"))
        self.assertIn("matched-k2", SHIPPABLE)
        self.assertIn("matched-k3", SHIPPABLE)
        self.assertEqual(FUSION_VARIANT, "forecast-k1")

    def test_same_k_prefers_the_panel_over_the_expert_read(self) -> None:
        metrics = {
            "expert-k1": {"spearman": 0.71, "auc": 0.81},
            "panel-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "panel-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        wide = {
            "expert-k1": {"spearman": 0.75, "auc": 0.85},
            "panel-k1": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(wide), "expert-k1")
        longer = {
            "panel-k1": {"spearman": 0.70, "auc": 0.80},
            "panel-k2": {"spearman": 0.71, "auc": 0.81},
        }
        self.assertEqual(select_variant(longer), "panel-k1")

    def test_chair_and_extremes_are_not_selection_candidates(self) -> None:
        metrics = {
            "panel-k2": {"spearman": 0.50, "auc": 0.60},
            "chair-k2": {"spearman": 0.90, "auc": 0.90},
            "high-k2": {"spearman": 0.90, "auc": 0.90},
            "low-k2": {"spearman": 0.90, "auc": 0.90},
        }
        self.assertEqual(select_variant(metrics), "panel-k2")

    def test_same_k_prefers_the_expert_read_over_the_audited_mean(self) -> None:
        metrics = {
            "audited-k1": {"spearman": 0.71, "auc": 0.81},
            "expert-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "expert-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        wide = {
            "audited-k1": {"spearman": 0.75, "auc": 0.85},
            "expert-k1": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(wide), "audited-k1")
        longer = {
            "expert-k1": {"spearman": 0.70, "auc": 0.80},
            "expert-k2": {"spearman": 0.71, "auc": 0.81},
        }
        self.assertEqual(select_variant(longer), "expert-k1")

    def test_form_triad_is_not_a_selection_candidate(self) -> None:
        metrics = {
            "expert-k2": {"spearman": 0.50, "auc": 0.60},
            "form-k2": {"spearman": 0.90, "auc": 0.90},
        }
        self.assertEqual(select_variant(metrics), "expert-k2")

    def test_same_k_prefers_the_audited_mean_over_the_venue_mean(self) -> None:
        metrics = {
            "mean-k1": {"spearman": 0.71, "auc": 0.81},
            "audited-k1": {"spearman": 0.70, "auc": 0.80},
        }
        report = selection_report(metrics)
        self.assertEqual(report["chosen"], "audited-k1")
        self.assertEqual(report["rule"], "within-0.02-then-cheapest")
        wide = {
            "mean-k1": {"spearman": 0.75, "auc": 0.85},
            "audited-k1": {"spearman": 0.60, "auc": 0.70},
        }
        self.assertEqual(select_variant(wide), "mean-k1")

    def test_checks_count_is_not_a_selection_candidate(self) -> None:
        metrics = {
            "audited-k1": {"spearman": 0.50, "auc": 0.60},
            "checks-k1": {"spearman": 0.90, "auc": 0.90},
        }
        self.assertEqual(select_variant(metrics), "audited-k1")

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

        from pydantic import ValidationError

        from autopaperreview.anchors import LOCKED_HOLISTIC_MODE
        from autopaperreview.model_exec import DEFAULT_HOLISTIC_MODE

        params = ExecuteReviewParams(adapter="autopaperreview.model_exec:OpenAICompatibleAdapter")
        self.assertEqual(LOCKED_HOLISTIC_SAMPLES, 1)
        self.assertEqual(DEFAULT_HOLISTIC_SAMPLES, LOCKED_HOLISTIC_SAMPLES)
        self.assertEqual(params.holistic_samples, LOCKED_HOLISTIC_SAMPLES)
        self.assertEqual(params.holistic_temperature, LOCKED_HOLISTIC_TEMPERATURE)
        self.assertEqual(LOCKED_HOLISTIC_TEMPERATURE, 0.7)
        self.assertEqual(LOCKED_HOLISTIC_MODE, "forecast")
        self.assertEqual(DEFAULT_HOLISTIC_MODE, LOCKED_HOLISTIC_MODE)
        self.assertEqual(params.holistic_mode, LOCKED_HOLISTIC_MODE)
        self.assertTrue(params.cheap is False)
        relaxed = ExecuteReviewParams(
            adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
            holistic_mode=" Mean ",
        )
        self.assertEqual(relaxed.holistic_mode, "mean")
        rebuttal = ExecuteReviewParams(
            adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
            holistic_mode="rebuttal",
            holistic_samples=2,
        )
        self.assertEqual((rebuttal.holistic_mode, rebuttal.holistic_samples), ("rebuttal", 2))
        matched = ExecuteReviewParams(
            adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
            holistic_mode="matched",
            holistic_samples=2,
        )
        self.assertEqual((matched.holistic_mode, matched.holistic_samples), ("matched", 2))
        panel = ExecuteReviewParams(
            adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
            holistic_mode="panel",
            holistic_samples=2,
        )
        self.assertEqual((panel.holistic_mode, panel.holistic_samples), ("panel", 2))
        expert = ExecuteReviewParams(
            adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
            holistic_mode="expert",
            holistic_samples=2,
        )
        self.assertEqual((expert.holistic_mode, expert.holistic_samples), ("expert", 2))
        audited = ExecuteReviewParams(
            adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
            holistic_mode="audited",
            holistic_samples=1,
        )
        self.assertEqual((audited.holistic_mode, audited.holistic_samples), ("audited", 1))
        with self.assertRaises(ValidationError):
            ExecuteReviewParams(
                adapter="autopaperreview.model_exec:OpenAICompatibleAdapter",
                holistic_mode="other",
            )
        args = build_parser().parse_args(["calibrate-eval", "run", "--cache", "unused"])
        self.assertEqual(args.holistic_samples, LOCKED_HOLISTIC_SAMPLES)
        self.assertEqual(args.holistic_mode, LOCKED_HOLISTIC_MODE)
        for function in (run_cache, _write_project):
            self.assertEqual(
                inspect.signature(function).parameters["holistic_samples"].default,
                LOCKED_HOLISTIC_SAMPLES,
            )
            self.assertEqual(
                inspect.signature(function).parameters["holistic_mode"].default,
                LOCKED_HOLISTIC_MODE,
            )
