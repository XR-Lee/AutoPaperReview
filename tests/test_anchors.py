"""Venue-mean rank and the previous pole scale. No manuscript text."""

from __future__ import annotations

import re
import unittest

from pydantic import ValidationError

from autopaperreview.anchors import (
    ANCHOR_IDS,
    LADDER_CEILING,
    LADDER_FLOOR,
    LOCKED_CANDIDATE,
    LOCKED_HOLISTIC_SAMPLES,
    LOCKED_HOLISTIC_TEMPERATURE,
    MARGIN_MAX,
    SYNTHETIC_ANCHORS,
    anchor_prompt_text,
    implied_score,
    isolate_score,
    normalize_margin,
)
from autopaperreview.model_exec import HOLISTIC_TASK, HolisticJudgment, _SCHEMA_HINTS


class LockedRankTests(unittest.TestCase):
    def test_the_locked_rank_is_three_venue_means(self) -> None:
        self.assertEqual(LOCKED_CANDIDATE, "mean-k3")
        self.assertEqual(LOCKED_HOLISTIC_SAMPLES, 3)
        self.assertEqual(LOCKED_HOLISTIC_TEMPERATURE, 0.7)

    def test_prompt_scores_the_manuscript_and_hides_references(self) -> None:
        self.assertIn("predicted_mean", HOLISTIC_TASK)
        self.assertIn("borderline reject", HOLISTIC_TASK)
        self.assertIn("5.4", HOLISTIC_TASK)
        self.assertIn("5.6", HOLISTIC_TASK)
        self.assertNotIn("weak-reject", HOLISTIC_TASK)
        self.assertNotIn("strong-accept", HOLISTIC_TASK)
        self.assertNotIn("margin", HOLISTIC_TASK.lower())
        self.assertNotIn("openreview.net", HOLISTIC_TASK)
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", HOLISTIC_TASK))
        hint = _SCHEMA_HINTS["holistic"]
        self.assertIn("predicted_mean", hint)
        self.assertNotIn("5.5", hint)
        self.assertNotIn("2.5", hint)
        self.assertNotIn("8.5", hint)
        self.assertNotIn("{", hint)


class PoleScaleTests(unittest.TestCase):
    def test_positions_are_a_declared_two_pole_scale(self) -> None:
        positions = [anchor.position for anchor in SYNTHETIC_ANCHORS]
        self.assertEqual(list(ANCHOR_IDS), ["weak-reject", "strong-accept"])
        self.assertEqual(positions, [2.5, 8.5])
        self.assertEqual(positions[1] - positions[0], MARGIN_MAX)
        self.assertEqual(positions[0] - LADDER_FLOOR, 1.5)
        self.assertEqual(LADDER_CEILING - positions[-1], 1.5)
        for anchor in SYNTHETIC_ANCHORS:
            text = anchor_prompt_text(anchor)
            self.assertIsNone(re.search(r"\d", text))
            self.assertNotIn(str(anchor.position), text)


class IsolateScoreTests(unittest.TestCase):
    def test_agreed_poles_keep_the_end_and_equal_margins_meet_in_the_middle(self) -> None:
        near_weak = isolate_score(
            [
                implied_score(2.5, "better", 1),
                implied_score(8.5, "worse", 5),
            ]
        )
        near_strong = isolate_score(
            [
                implied_score(2.5, "better", 5),
                implied_score(8.5, "worse", 1),
            ]
        )
        middle = isolate_score(
            [
                implied_score(2.5, "better", 3),
                implied_score(8.5, "worse", 3),
            ]
        )
        self.assertEqual(near_weak, 3.5)
        self.assertEqual(near_strong, 7.5)
        self.assertEqual(middle, 5.5)
        self.assertLess(near_weak, 4)
        self.assertGreater(near_strong, 7)

    def test_a_tie_ignores_direction_and_the_ends_clamp(self) -> None:
        self.assertEqual(implied_score(2.5, "tie", 0), 2.5)
        self.assertEqual(implied_score(8.5, "worse", 6), 2.5)
        self.assertEqual(implied_score(2.5, "worse", 6), 1.0)
        self.assertEqual(implied_score(8.5, "better", 6), 10.0)
        self.assertEqual(normalize_margin("better", 1.25), 1.25)

    def test_a_tie_with_a_margin_or_a_zero_gap_raises(self) -> None:
        with self.assertRaises(ValueError):
            implied_score(2.5, "tie", 1)
        with self.assertRaises(ValueError):
            implied_score(8.5, "better", 0)
        with self.assertRaises(ValueError):
            implied_score(8.5, "same", 1)
        with self.assertRaises(ValueError):
            isolate_score([5.0])


class VenueMeanSchemaTests(unittest.TestCase):
    def test_a_venue_mean_and_a_decision_are_stored(self) -> None:
        parsed = HolisticJudgment.model_validate(
            {
                "rationale": "The comparison shows a gain a reviewer would rely on.",
                "rating": 4,
                "predicted_mean": 6.4,
            }
        )
        self.assertEqual(parsed.rating, 4)
        self.assertEqual(parsed.predicted_mean, 6.4)

    def test_a_bool_or_an_extra_key_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            HolisticJudgment.model_validate(
                {"rationale": "A gain.", "rating": True, "predicted_mean": 6.0}
            )
        with self.assertRaises(ValidationError):
            HolisticJudgment.model_validate(
                {
                    "rationale": "A gain.",
                    "rating": 4,
                    "predicted_mean": 6.0,
                    "margin": 1,
                }
            )
