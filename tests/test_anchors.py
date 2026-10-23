"""Isolated pole comparisons. No manuscript text."""

from __future__ import annotations

import re
import unittest

from pydantic import ValidationError

from autopaperreview.anchors import (
    ANCHOR_IDS,
    LADDER_CEILING,
    LADDER_FLOOR,
    LOCKED_CANDIDATE,
    MARGIN_MAX,
    SYNTHETIC_ANCHORS,
    anchor_prompt_text,
    implied_score,
    isolate_score,
    normalize_margin,
)
from autopaperreview.model_exec import (
    IsolatedComparison,
    IsolatedDecision,
    _SCHEMA_HINTS,
    isolated_task_text,
)


class PoleScaleTests(unittest.TestCase):
    def test_positions_are_a_declared_two_pole_scale(self) -> None:
        positions = [anchor.position for anchor in SYNTHETIC_ANCHORS]
        self.assertEqual(list(ANCHOR_IDS), ["weak-reject", "strong-accept"])
        self.assertEqual(positions, [2.5, 8.5])
        self.assertEqual(positions[1] - positions[0], MARGIN_MAX)
        self.assertEqual(positions[0] - LADDER_FLOOR, 1.5)
        self.assertEqual(LADDER_CEILING - positions[-1], 1.5)
        self.assertEqual(LOCKED_CANDIDATE, "isolate-k1")

    def test_prompt_text_names_one_reference_and_hides_positions(self) -> None:
        for anchor in SYNTHETIC_ANCHORS:
            text = anchor_prompt_text(anchor)
            self.assertIsNone(re.search(r"\d", text))
            self.assertNotIn("openreview.net", text)
            self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
            self.assertIn(anchor.id, text)
            self.assertIn(anchor.text, text)
            self.assertNotIn(str(anchor.position), text)
            task = isolated_task_text(anchor.id, decision=anchor.id == "strong-accept")
            self.assertNotIn("2.5", task)
            self.assertNotIn("8.5", task)
            other = next(item for item in SYNTHETIC_ANCHORS if item.id != anchor.id)
            self.assertNotIn(other.id, task)
            self.assertNotIn(other.text, task)
        weak = isolated_task_text("weak-reject", decision=False)
        strong = isolated_task_text("strong-accept", decision=True)
        self.assertNotIn("borderline", weak)
        self.assertIn("borderline reject", strong)
        self.assertNotIn("predicted_mean", strong)

    def test_schema_hints_have_no_copied_score(self) -> None:
        weak = _SCHEMA_HINTS["holistic-weak-reject"]
        strong = _SCHEMA_HINTS["holistic-strong-accept"]
        self.assertNotIn("rating", weak)
        self.assertIn("rating", strong)
        for hint in (weak, strong):
            self.assertNotIn("5.5", hint)
            self.assertNotIn("2.5", hint)
            self.assertNotIn("predicted_mean", hint)


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


class IsolatedSchemaTests(unittest.TestCase):
    def test_relation_aliases_canonicalize(self) -> None:
        parsed = IsolatedComparison.model_validate(
            {"rationale": "The gain is one a reviewer would rely on.", "relation": "Above", "margin": 4}
        )
        self.assertEqual(parsed.relation, "better")
        decision = IsolatedDecision.model_validate(
            {
                "rationale": "The gain is not established.",
                "relation": "below",
                "margin": 2,
                "rating": 3,
            }
        )
        self.assertEqual(decision.relation, "worse")
        self.assertEqual(decision.rating, 3)

    def test_inconsistent_margin_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            IsolatedComparison.model_validate(
                {"rationale": "Tied with the reference.", "relation": "tie", "margin": 1}
            )
        with self.assertRaises(ValidationError):
            IsolatedDecision.model_validate(
                {"rationale": "Better than the reference.", "relation": "better", "margin": 0, "rating": 5}
            )
