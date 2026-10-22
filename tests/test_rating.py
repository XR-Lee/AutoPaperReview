"""Declared-formula rating, fatal caps, and batch calibration. Synthetic numbers only."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from pydantic import ValidationError

from autopaperreview.bilingual_coverage import require_bilingual_markdown
from autopaperreview.models import (
    CapExemption,
    DimensionScore,
    EvidenceRecord,
    FatalClass,
    FormatCheck,
    LocalizedText,
    OverallScoreMethod,
    ReviewIssue,
    ReviewPackage,
    SarDimension,
    SourceRecord,
)
from autopaperreview.reporting import render_markdown
from autopaperreview.rating import apply_rating, calibrate_packages, rate_venue, snap_down
from autopaperreview.rating import TieNote
from autopaperreview.related_work import preview_venue_bin
from autopaperreview.related_work import snap_down as preview_snap
from autopaperreview.rubrics import CLAUSES, COEFFICIENT_SET
from tests.support import localized, make_artifact, make_issue


def _scores(**values: float) -> list[DimensionScore]:
    named = {
        "originality": SarDimension.originality,
        "importance": SarDimension.importance_of_research_question,
        "claims": SarDimension.claims_supported,
        "soundness": SarDimension.experimental_soundness,
        "writing": SarDimension.writing_clarity,
        "community": SarDimension.community_value,
        "prior": SarDimension.prior_work_contextualization,
    }
    if set(values) != set(named):
        raise AssertionError(f"need all seven dimensions, got {sorted(values)}")
    return [
        DimensionScore(dimension=named[key], score=values[key], evidence_ids=["E1"])
        for key in named
    ]


def _package(project_id: str, scores: dict[str, float], issues: list[ReviewIssue] | None = None, **kwargs) -> ReviewPackage:
    return ReviewPackage(
        project_id=project_id,
        manuscript=make_artifact(),
        sources=[SourceRecord(id="S1", kind="manuscript", title="Synthetic", locator="manuscript.txt")],
        evidence=[EvidenceRecord(id="E1", source_id="S1", locator="p. 1", claim="Observed.")],
        dimension_scores=_scores(**scores),
        issues=issues or [],
        **kwargs,
    )


def _all(value: float) -> dict[str, float]:
    return {
        "originality": value,
        "importance": value,
        "claims": value,
        "soundness": value,
        "writing": value,
        "community": value,
        "prior": value,
    }


def _preview_uniform(value: float) -> dict[str, float]:
    return {dimension.value: float(value) for dimension in SarDimension}


def _fatal(issue_id: str, fatal_class: FatalClass) -> ReviewIssue:
    return make_issue(
        id=issue_id,
        severity="major",
        fatal_class=fatal_class,
        decision_relevant=True,
        evidence_ids=["E1"],
    )


# Anonymous score rows. They are numbers, not manuscript text.
ROW_A = dict(originality=7, importance=8, claims=6, soundness=6, writing=7, community=7, prior=6)
ROW_B = dict(originality=6, importance=6, claims=7, soundness=6, writing=8, community=5, prior=7)
ROW_C = dict(originality=7, importance=7, claims=5, soundness=5, writing=6, community=6, prior=7)
ROW_D = dict(originality=6, importance=7, claims=5, soundness=5, writing=5, community=6, prior=6)
ROW_E = dict(originality=6, importance=7, claims=5, soundness=5, writing=7, community=6, prior=6)


class RatingFormulaTests(unittest.TestCase):
    def test_high_bands_without_a_cap_snap_to_8(self) -> None:
        scored = rate_venue(_package("high", _all(8)))
        self.assertEqual(scored.value, 8)
        self.assertEqual(scored.method, OverallScoreMethod.declared_formula)
        self.assertEqual(scored.coefficient_set, COEFFICIENT_SET)
        self.assertEqual(scored.metadata["cap_ids"], [])

    def test_uniform_six_stays_at_six(self) -> None:
        scored = rate_venue(_package("mid", _all(6)))
        self.assertEqual(scored.value, 6)
        self.assertEqual(scored.metadata["gate"], 6)

    def test_exact_bin_boundary_does_not_follow_cpython_sum(self) -> None:
        # CPython 3.11 sum() of the uniform-6 products is 5.999999999999998, and
        # of the uniform-3 products is 2.999999999999999. snap_down of those
        # residuals is 5 and 1. The declared dot product is exactly the bin, and
        # a value that really is below the cut still snaps down.
        for score in (3, 6):
            scored = rate_venue(_package(f"boundary-{score}", _all(float(score))))
            self.assertEqual(scored.value, score)
            self.assertEqual(scored.metadata["cap_ids"], [])
            self.assertGreaterEqual(scored.metadata["capped"], score)
            self.assertEqual(preview_venue_bin(_preview_uniform(score), []), score)

        mixed = dict(originality=2, importance=8, claims=6, soundness=6, writing=6, community=10, prior=8)
        scored = rate_venue(_package("boundary-mixed", mixed))
        self.assertEqual(scored.metadata["cap_ids"], [])
        self.assertGreaterEqual(scored.metadata["raw"], 6)
        self.assertEqual(scored.value, 6)
        self.assertEqual(
            preview_venue_bin(
                {
                    "experimental_soundness": 6,
                    "claims_supported": 6,
                    "originality": 2,
                    "importance_of_research_question": 8,
                    "prior_work_contextualization": 8,
                    "writing_clarity": 6,
                    "community_value": 10,
                },
                [],
            ),
            6,
        )
        self.assertEqual(snap_down(6.0), 6)
        self.assertEqual(snap_down(5.999999999999998), 5)
        self.assertEqual(preview_snap(5.999999999999998), 5)
        self.assertEqual(snap_down(2.999999999999999), 1)
        self.assertEqual(snap_down(math.nextafter(6.0, 0.0)), 5)
        self.assertEqual(snap_down(math.nextafter(3.0, 0.0)), 1)

    def test_raw_seven_lands_in_six(self) -> None:
        scored = rate_venue(_package("seven", _all(7)))
        self.assertEqual(scored.metadata["raw"], 7)
        self.assertEqual(scored.metadata["cap_ids"], [])
        self.assertEqual(scored.value, 6)

    def test_sound_claims_and_experiments_can_leave_bin_8(self) -> None:
        scored = rate_venue(
            _package(
                "leave-8",
                dict(originality=6, importance=6, claims=8, soundness=8, writing=6, community=6, prior=6),
            )
        )
        self.assertEqual(scored.metadata["cap_ids"], [])
        self.assertGreater(scored.metadata["raw"], 7)
        self.assertLess(scored.metadata["raw"], 8)
        self.assertEqual(scored.value, 6)

    def test_nonfatal_weak_paper_can_reach_bin_3(self) -> None:
        scored = rate_venue(_package("weak", _all(4)))
        self.assertEqual(scored.metadata["cap_ids"], [])
        self.assertEqual(scored.metadata["raw"], 4)
        self.assertEqual(scored.value, 3)

    def test_snap_never_rounds_up_past_the_evidence(self) -> None:
        self.assertEqual(
            [snap_down(value) for value in (0, 1, 2, 3, 4, 5, 5.5, 6, 7, 7.9, 8, 9, 10, 11)],
            [1, 1, 1, 3, 3, 5, 5, 6, 6, 6, 8, 8, 10, 10],
        )
        for value in (0, 1, 2, 3, 4, 5, 5.5, 6, 7, 7.1, 8, 9, 10, 12):
            self.assertEqual(snap_down(value), preview_snap(value), value)

    def test_missing_closest_neighbor_caps_at_six_and_lowers_originality(self) -> None:
        package = _package(
            "neighbor",
            _all(8),
            issues=[_fatal("I-miss", FatalClass.missing_closest_neighbor)],
        )
        scored = rate_venue(package)
        self.assertEqual(scored.value, 6)
        self.assertEqual(scored.metadata["cap_ids"], ["I-miss"])
        bands = scored.metadata["bands_after_ceiling"]
        self.assertEqual(bands["prior_work_contextualization"], 2.0)
        self.assertEqual(bands["originality"], 4.0)
        self.assertEqual(scored.metadata["related_work_comparison"], None)

    def test_leakage_cap_overrides_high_bands(self) -> None:
        package = _package("leak", _all(8), issues=[_fatal("I-leak", FatalClass.leakage)])
        scored = rate_venue(package)
        self.assertEqual(scored.value, 3)
        self.assertEqual(scored.metadata["cap_ids"], ["I-leak"])
        self.assertLessEqual(scored.metadata["bands_after_ceiling"]["experimental_soundness"], 2)

    def test_frozen_rows_follow_the_floor_snap(self) -> None:
        # Raw means are about 6.49, 6.38, 5.80, 5.52, 5.62. The venue bin is the
        # largest bin that does not exceed that raw value, so C/D/E land in 5.
        expected = {"A": 6, "B": 6, "C": 5, "D": 5, "E": 5}
        rows = {"A": ROW_A, "B": ROW_B, "C": ROW_C, "D": ROW_D, "E": ROW_E}
        for name, scores in rows.items():
            with self.subTest(name=name):
                self.assertEqual(rate_venue(_package(name, scores)).value, expected[name])

    def test_caps_on_the_same_rows_widen_the_bins(self) -> None:
        specs = [
            ("A", ROW_A, FatalClass.unfair_baseline, 5),
            ("B", ROW_B, None, 6),
            ("C", ROW_C, FatalClass.unsupported_claim, 5),
            ("D", ROW_D, FatalClass.unsupported_claim, 5),
            ("E", ROW_E, FatalClass.invalid_protocol, 3),
        ]
        for name, scores, fatal, expected in specs:
            issues = [] if fatal is None else [_fatal(f"I-{name}", fatal)]
            scored = rate_venue(_package(name, scores, issues=issues))
            with self.subTest(name=name):
                self.assertEqual(scored.value, expected)
                if fatal is None:
                    self.assertEqual(scored.metadata["cap_ids"], [])
                else:
                    self.assertEqual(scored.metadata["cap_ids"], [f"I-{name}"])

    def test_exemption_can_raise_a_five_cap_to_six(self) -> None:
        blocked = _fatal("I-base", FatalClass.unfair_baseline)
        package = _package(
            "exempt",
            _all(8),
            issues=[blocked],
            cap_exemptions=[
                CapExemption(
                    id="EX1",
                    issue_id="I-base",
                    raises_cap_to=6,
                    decision_basis_evidence_ids=["E1"],
                    excluded_evidence_ids=[],
                )
            ],
        )
        # excluded list must be allowed to be empty; basis must exist.
        self.assertEqual(rate_venue(package).value, 6)
        self.assertEqual(rate_venue(package).metadata["exemption_ids"], ["EX1"])

    def test_exemption_cannot_cite_excluded_evidence_as_the_basis(self) -> None:
        with self.assertRaises(ValidationError):
            CapExemption(
                id="EX-bad",
                issue_id="I-base",
                raises_cap_to=6,
                decision_basis_evidence_ids=["E1"],
                excluded_evidence_ids=["E1"],
            )

    def test_rationale_text_does_not_change_the_bin(self) -> None:
        left = _package("same", _all(6))
        right = _package("same", _all(6))
        right.dimension_scores[0].rationale = localized("Different words, same band.")
        self.assertEqual(rate_venue(left).value, rate_venue(right).value)
        self.assertEqual(rate_venue(left).metadata["weighted"], rate_venue(right).metadata["weighted"])

    def test_non_blocking_fatal_class_does_not_cap(self) -> None:
        issue = _fatal("I-soft", FatalClass.leakage)
        issue.severity = "minor"  # type: ignore[assignment]
        issue.first_read = None
        scored = rate_venue(_package("soft", _all(8), issues=[issue]))
        self.assertEqual(scored.value, 8)
        self.assertEqual(scored.metadata["cap_ids"], [])

    def test_clause_must_match_its_score(self) -> None:
        with self.assertRaises(ValidationError):
            DimensionScore(
                dimension=SarDimension.claims_supported,
                score=5,
                clause_id="cs-4",
                evidence_ids=["E1"],
            )
        score = DimensionScore(
            dimension=SarDimension.claims_supported,
            score=4,
            clause_id="cs-4",
            rubric_id="sar_bands_v2",
            evidence_ids=["E1"],
        )
        self.assertEqual(score.score, 4)

    def test_rubric_file_lists_every_clause(self) -> None:
        text = Path("src/autopaperreview/prompts/sar_bands_v2.md").read_text(encoding="utf-8")
        for clause_id in CLAUSES:
            self.assertIn(f"`{clause_id}`", text)

    def test_bilingual_report_includes_the_declared_bin(self) -> None:
        issue = _fatal("I-leak", FatalClass.leakage)
        for field in ("title", "evidence", "impact", "required_action"):
            getattr(issue, field).translations["zh-Hans"] = "合成夹具译文。"
        package = apply_rating(_package("prov", _all(8), issues=[issue]))
        package.format_checks = [
            FormatCheck(
                id="FMT-pages",
                check="content_page_limit",
                passed=False,
                fatal_class=FatalClass.format_desk_reject,
                detail=LocalizedText(
                    primary="Main text exceeds the page limit.",
                    language="en",
                    translations={"zh-Hans": "正文超过页数上限。"},
                ),
            )
        ]
        package = apply_rating(package)
        markdown = render_markdown(package, languages=["en", "zh-Hans"], fill_missing=False)
        require_bilingual_markdown(markdown)
        self.assertIn("apr-iclr-cap-v2", markdown)
        self.assertIn("已声明评分", markdown)
        self.assertIn("FMT-pages", markdown)
        self.assertEqual(package.overall_score.value, 1)

    def test_apply_rating_records_provenance(self) -> None:
        package = apply_rating(_package("prov", _all(8), issues=[_fatal("I-leak", FatalClass.leakage)]))
        assert package.overall_score is not None
        self.assertIn("not a fitted ICLR regression", package.overall_score.notes or "")
        self.assertEqual(package.overall_score.metadata["visual_status"], "unchecked")


class CalibrationTests(unittest.TestCase):
    def test_different_caps_cannot_share_a_bin(self) -> None:
        low_cap = _package("low", _all(8), issues=[_fatal("I-low", FatalClass.invalid_protocol)])
        high_cap = _package("high", _all(4), issues=[_fatal("I-high", FatalClass.unfair_baseline)])
        with self.assertRaisesRegex(ValueError, "different active caps"):
            calibrate_packages([low_cap, high_cap])

    def test_same_bin_requires_a_structured_reason(self) -> None:
        left = _package("left", _all(4))
        right = _package("right", _all(4))
        with self.assertRaisesRegex(ValueError, "same_bin_justified"):
            calibrate_packages([left, right])
        batch = calibrate_packages(
            [left, right],
            [
                TieNote(
                    "left",
                    "right",
                    "evidence_fairness",
                    "Both sit in the same bin because neither cap differs and the weighted means match.",
                )
            ],
        )
        self.assertEqual(batch.pairs[0].decision_gap, "same_cap")
        self.assertEqual(batch.pairs[0].axis, "evidence_fairness")
        self.assertIn(batch.order[0], {"left", "right"})

    def test_distinct_bins_do_not_require_a_tie_note(self) -> None:
        strong = _package("strong", _all(8))
        weak = _package("weak", _all(8), issues=[_fatal("I-weak", FatalClass.leakage)])
        batch = calibrate_packages([weak, strong])
        self.assertEqual(batch.order, ["strong", "weak"])
        self.assertEqual(batch.pairs[0].left_value, 8)
        self.assertEqual(batch.pairs[0].right_value, 3)
        self.assertIsNone(batch.pairs[0].same_bin_justified)


if __name__ == "__main__":
    unittest.main()
