"""Synthetic ladder placement. No manuscript text."""

from __future__ import annotations

import re
import unittest

from pydantic import ValidationError

from autopaperreview.anchors import (
    ANCHOR_IDS,
    LADDER_CEILING,
    LADDER_FLOOR,
    SYNTHETIC_ANCHORS,
    anchor_ladder_text,
    count_score,
    describe_placement,
    is_single_cut,
    placement_score,
)
from autopaperreview.model_exec import HolisticJudgment, _SCHEMA_HINTS


def _votes(*relations: str) -> dict[str, str]:
    if len(relations) != len(ANCHOR_IDS):
        raise AssertionError("one relation per reference")
    return dict(zip(ANCHOR_IDS, relations, strict=True))


def _payload(relations: dict[str, str], *, lean: str = "mid", rating: float = 3) -> dict[str, object]:
    return {
        "rationale": "Synthetic placement against the fictional ladder.",
        "rating": rating,
        "predicted_mean": 5.0,
        "comparisons": [
            {"anchor_id": anchor_id, "relation": relation}
            for anchor_id, relation in relations.items()
        ],
        "gap_lean": lean,
        "strength_excerpt": "",
        "weakness_excerpt": "",
    }


class LadderScaleTests(unittest.TestCase):
    def test_positions_are_an_even_declared_scale(self) -> None:
        positions = [anchor.position for anchor in SYNTHETIC_ANCHORS]
        self.assertEqual(positions, [2.5, 4.0, 5.5, 7.0, 8.5])
        gaps = [right - left for left, right in zip(positions, positions[1:])]
        self.assertEqual(gaps, [1.5, 1.5, 1.5, 1.5])
        self.assertEqual(positions[0] - LADDER_FLOOR, 1.5)
        self.assertEqual(LADDER_CEILING - positions[-1], 1.5)

    def test_prompt_text_names_references_and_hides_positions(self) -> None:
        text = anchor_ladder_text()
        self.assertIsNone(re.search(r"\d", text))
        self.assertNotIn("openreview.net", text)
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
        for anchor in SYNTHETIC_ANCHORS:
            self.assertIn(anchor.id, text)
            self.assertIn(anchor.text, text)
            self.assertNotIn(str(anchor.position), text)

    def test_schema_hint_names_each_reference_and_has_no_copied_score(self) -> None:
        hint = _SCHEMA_HINTS["holistic"]
        for anchor_id in ANCHOR_IDS:
            self.assertIn(anchor_id, hint)
        self.assertNotIn("5.5", hint)
        self.assertNotIn("predicted_mean\": 5", hint)


class PlacementTests(unittest.TestCase):
    def test_open_gap_uses_the_lean_and_a_tie_ignores_it(self) -> None:
        gap = _votes("better", "better", "worse", "worse", "worse")
        self.assertTrue(is_single_cut(gap))
        self.assertEqual(placement_score(gap, "low"), 4.375)
        self.assertEqual(placement_score(gap, "mid"), 4.75)
        self.assertEqual(placement_score(gap, "high"), 5.125)
        self.assertEqual(count_score(gap), 4.6)
        tied = _votes("better", "better", "tie", "worse", "worse")
        self.assertEqual(placement_score(tied, "low"), 5.5)
        self.assertEqual(placement_score(tied, "high"), 5.5)
        described = describe_placement(tied, "high")
        self.assertFalse(described["lean_applied"])
        self.assertEqual(described["cut_id"], "split-decision")
        self.assertEqual(described["cut_relation"], "tie")

    def test_ends_of_the_ladder(self) -> None:
        below = _votes("worse", "worse", "worse", "worse", "worse")
        above = _votes("better", "better", "better", "better", "better")
        self.assertEqual(placement_score(below, "low"), 1.375)
        self.assertEqual(placement_score(above, "high"), 9.625)
        self.assertEqual(count_score(below), 1.0)
        self.assertEqual(count_score(above), 10.0)
        self.assertEqual(describe_placement(above, "mid")["cut_id"], "above-all")

    def test_non_monotone_votes_stop_at_the_first_failure(self) -> None:
        relations = _votes("better", "worse", "better", "worse", "worse")
        self.assertFalse(is_single_cut(relations))
        self.assertEqual(placement_score(relations, "mid"), 3.25)
        self.assertEqual(count_score(relations), 4.6)
        described = describe_placement(relations, "mid")
        self.assertEqual(described["place"], 3.25)
        self.assertEqual(described["count"], 4.6)
        self.assertEqual(described["cut_id"], "ordinary-reject")
        self.assertEqual(described["comparisons"][2]["relation"], "better")

    def test_missing_or_unknown_relation_raises(self) -> None:
        with self.assertRaises(ValueError):
            placement_score({"weak-reject": "better"}, "mid")
        relations = _votes("better", "better", "worse", "worse", "worse")
        relations["split-decision"] = "same"
        with self.assertRaises(ValueError):
            placement_score(relations, "mid")
        with self.assertRaises(ValueError):
            placement_score(_votes("better", "better", "worse", "worse", "worse"), "center")


class HolisticSchemaTests(unittest.TestCase):
    def test_map_form_and_aliases_canonicalize(self) -> None:
        payload = _payload(_votes("better", "better", "worse", "worse", "worse"))
        payload["comparisons"] = {
            "Weak-Reject": "Above",
            "ordinary-reject": "better",
            "split-decision": "below",
            "ordinary-accept": "worse",
            "strong-accept": "tie",
        }
        payload["gap_lean"] = "Middle"
        parsed = HolisticJudgment.model_validate(payload)
        found = {item.anchor_id: item.relation for item in parsed.comparisons}
        self.assertEqual(found["weak-reject"], "better")
        self.assertEqual(found["split-decision"], "worse")
        self.assertEqual(found["strong-accept"], "tie")
        self.assertEqual(parsed.gap_lean, "mid")

    def test_missing_reference_is_rejected(self) -> None:
        payload = _payload(_votes("better", "better", "worse", "worse", "worse"))
        assert isinstance(payload["comparisons"], list)
        payload["comparisons"] = payload["comparisons"][:-1]
        payload["comparisons"].append({"anchor_id": "weak-reject", "relation": "worse"})
        with self.assertRaises(ValidationError) as caught:
            HolisticJudgment.model_validate(payload)
        self.assertIn("weak-reject", str(caught.exception))
