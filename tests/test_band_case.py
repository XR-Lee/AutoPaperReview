"""Band evidence is required before a dimension clause. Synthetic text only."""

from __future__ import annotations

import unittest

from autopaperreview.rubrics import CLAUSES, band_case_errors


SOURCE = (
    "The ablation removes the hue term and accuracy falls from 0.8 to 0.5. "
    "The table reports one seed and no interval."
)


def _item(excerpt: str, observable: str) -> dict[str, str]:
    return {"excerpt": excerpt, "location": "Table 1", "observable": observable}


class BandCaseTests(unittest.TestCase):
    def test_soundness_band_4_does_not_swallow_a_moving_ablation(self) -> None:
        english = CLAUSES["es-4"][2]
        self.assertIn("no ablation removes the claimed piece", english)
        self.assertIn("no external number is copied", english)

    def test_band_without_evidence_is_rejected(self) -> None:
        errors = band_case_errors(
            dimension="experimental_soundness",
            score=4,
            supporting=[],
            opposing=[],
            move_up="Adding an interval on the decision metric would move this to band 6.",
            move_down="A train-test leak that decides the number would move this to band 2.",
            source_text=SOURCE,
        )
        self.assertTrue(any("evidence is required" in item for item in errors))

    def test_band_6_without_a_band_8_gap_is_flagged(self) -> None:
        errors = band_case_errors(
            dimension="experimental_soundness",
            score=6,
            supporting=[_item(SOURCE[:60], "ablation_moves_decision_metric")],
            opposing=[_item(SOURCE[61:], "single_seed_no_interval")],
            move_up="An interval or a second seed on the decision metric would make band 8 true.",
            move_down="If the ablation did not remove the claimed term, the band would be 4.",
            source_text=SOURCE,
        )
        self.assertTrue(any("band6_default" in item for item in errors))

    def test_band_6_with_both_neighbors_is_accepted(self) -> None:
        errors = band_case_errors(
            dimension="experimental_soundness",
            score=6,
            supporting=[_item("The ablation removes the hue term and accuracy falls from 0.8 to 0.5.", "ablation_moves_decision_metric")],
            opposing=[_item("The table reports one seed and no interval.", "interval_or_multiseed")],
            move_up="Rerunning the baselines with an interval would make the band-8 conditions true.",
            move_down="If that ablation left the claimed term in place, the score would fall to band 4.",
            source_text=SOURCE,
        )
        self.assertEqual(errors, [])

    def test_nonfatal_band_4_can_be_chosen_when_the_gap_is_quoted(self) -> None:
        errors = band_case_errors(
            dimension="experimental_soundness",
            score=4,
            supporting=[_item("The table reports one seed and no interval.", "single_seed_no_interval")],
            opposing=[_item("The ablation removes the hue term and accuracy falls from 0.8 to 0.5.", "ablation_moves_decision_metric")],
            move_up="Showing that this ablation moves the decision metric would raise the band to 6.",
            move_down="A leak that creates the headline number would drop the band to 2.",
            source_text=SOURCE,
        )
        self.assertEqual(errors, [])
