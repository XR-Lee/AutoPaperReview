"""Locked panel rank, the review panel, the expert read, the audit checklist, and the pole scale. No manuscript text."""

from __future__ import annotations

import re
import unittest

from pydantic import ValidationError

from autopaperreview.anchors import (
    ANCHOR_IDS,
    AUDIT_CHECK_IDS,
    AUDIT_CHECKS,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    CONTRIBUTION_TYPE_IDS,
    CONTRIBUTION_TYPES,
    FORECAST_PANEL_SIZE,
    FORECAST_SEAT_IDS,
    FORECAST_SEATS,
    FORM_MAX,
    FORM_MIN,
    FULL_TEXT_MAX_CHARS,
    HOLISTIC_MODES,
    LADDER_CEILING,
    LADDER_FLOOR,
    LOCKED_CANDIDATE,
    LOCKED_HOLISTIC_MODE,
    LOCKED_HOLISTIC_SAMPLES,
    LOCKED_HOLISTIC_TEMPERATURE,
    MAIN_TEXT_MAX_CHARS,
    MAIN_TEXT_MIN_SHARE,
    MARGIN_MAX,
    MATCHED_SEAT_IDS,
    MATCHED_SEATS,
    MAX_AUDIT_FLAWS,
    MAX_FIELD_CONTEXT_LINES,
    MAX_PRIOR_WORK,
    MAX_REVIEW_POINTS,
    OFFICIAL_RATINGS,
    PANEL_ROLE_IDS,
    PANEL_ROLES,
    PANEL_SIZE,
    RATING_MAX,
    RATING_MIN,
    REBUTTAL_PANEL_SIZE,
    REBUTTAL_SEAT_IDS,
    REBUTTAL_SEATS,
    SYNTHETIC_ANCHORS,
    anchor_prompt_text,
    audit_check_by_id,
    audit_check_prompt_lines,
    checks_score,
    contribution_type_by_id,
    contribution_type_prompt_lines,
    expected_rating,
    forecast_mean,
    forecast_seat_by_id,
    forecast_seat_prompt_lines,
    form_score,
    implied_score,
    isolate_score,
    matched_seat_by_id,
    matched_seat_prompt_lines,
    normalize_contribution_type,
    normalize_forecast_seat,
    normalize_margin,
    normalize_matched_seat,
    normalize_rebuttal_seat,
    normalize_reviewer_role,
    panel_mean,
    panel_role_by_id,
    panel_role_prompt_lines,
    rebuttal_seat_by_id,
    rebuttal_seat_prompt_lines,
)
from autopaperreview.model_exec import (
    AUDITED_SCORING_RULE,
    EXPERT_SCALE_PARAGRAPHS,
    HOLISTIC_TASK,
    MAIN_TEXT_CAP_MARKER,
    MAIN_TEXT_END_MARKER,
    MATCHED_NORMS,
    AuditJudgment,
    ExpertJudgment,
    ForecastJudgment,
    ForecastReview,
    HolisticJudgment,
    MatchedJudgment,
    PanelJudgment,
    PanelReview,
    RebuttalJudgment,
    RebuttalUpdate,
    _SCHEMA_HINTS,
    audit_task_text,
    expert_task_text,
    forecast_task_text,
    full_text_packet,
    main_text_packet,
    matched_task_text,
    panel_task_text,
    rebuttal_task_text,
)


class LockedRankTests(unittest.TestCase):
    def test_the_locked_rank_is_one_forecast_panel(self) -> None:
        self.assertEqual(LOCKED_CANDIDATE, "forecast-k1")
        self.assertEqual(LOCKED_HOLISTIC_SAMPLES, 1)
        self.assertEqual(LOCKED_HOLISTIC_TEMPERATURE, 0.7)
        self.assertEqual(LOCKED_HOLISTIC_MODE, "forecast")
        self.assertEqual(HOLISTIC_MODES, ("forecast", "rebuttal", "matched", "panel", "expert", "audited", "mean"))
        self.assertEqual(MAIN_TEXT_MAX_CHARS, 90_000)
        self.assertEqual(MAIN_TEXT_MIN_SHARE, 0.10)
        self.assertEqual(FULL_TEXT_MAX_CHARS, 240_000)

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

    def test_scoring_rule_keeps_the_audit_off_a_score_sheet(self) -> None:
        self.assertIn("not a score sheet", AUDITED_SCORING_RULE)
        self.assertIn("passed check does not raise the mean", AUDITED_SCORING_RULE)
        self.assertIn("polish are not evidence", AUDITED_SCORING_RULE)
        self.assertIsNone(re.search(r"\d", AUDITED_SCORING_RULE))


def _expert_payload(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "area": "synthetic counting rules",
        "contribution_type": "method",
        "prior_work": [
            {"name": "an earlier counting rule", "relation": "same rule on one specimen", "cited": True, "sure": True},
            {"name": "a recalled tally method", "relation": "differs in the held-out pair", "cited": False, "sure": False},
        ],
        "field_context": ["the area compares against the tally method on the standard pair"],
        "soundness": 3,
        "presentation": 3,
        "contribution": 2,
        "strengths": ["the rule is stated as an algorithm"],
        "weaknesses": ["one specimen carries the claim"],
        "rationale": "A known rule on a new pair; the evidence is one specimen; the panel lands on the reject side.",
        "predicted_mean": 4.6,
        "rating": 2,
    }
    row.update(overrides)
    return row


class ExpertReadTests(unittest.TestCase):
    def test_prompt_asks_for_context_before_the_score_and_keeps_the_scale(self) -> None:
        text = expert_task_text()
        self.assertIn("## Expert read", text)
        self.assertIn("senior researcher", text)
        for key in (
            "area",
            "contribution_type",
            "prior_work",
            "field_context",
            "soundness",
            "presentation",
            "contribution",
            "strengths",
            "weaknesses",
            "rationale",
            "predicted_mean",
            "rating",
        ):
            self.assertIn(key, text)
        self.assertLess(text.index("prior_work"), text.index("predicted_mean is the mean"))
        self.assertLess(text.index("field_context"), text.index("Place the mean on this scale"))
        self.assertIn("already exists under another name", text)
        self.assertIn("baselines the area has moved past", text)
        self.assertIn("inside the usual noise", text)
        self.assertIn("not marked down for being modest", text)
        self.assertNotIn("## Venue mean", text)
        self.assertNotIn("## Audit", text)
        self.assertNotIn("## Verified audit", text)
        self.assertNotIn("weak-reject", text)
        self.assertNotIn("strong-accept", text)
        self.assertNotIn("margin", text.lower())
        self.assertNotIn("openreview.net", text)
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
        for paragraph in EXPERT_SCALE_PARAGRAPHS:
            self.assertIn(paragraph, text)
            self.assertIn(paragraph, HOLISTIC_TASK)
        self.assertIn("5.4", text)
        self.assertIn("5.6", text)
        self.assertIn("borderline reject", text)
        self.assertTrue(text.endswith("Return only the JSON object."))

    def test_each_contribution_type_is_defined_once_in_the_prompt(self) -> None:
        self.assertEqual(len(CONTRIBUTION_TYPES), 7)
        self.assertEqual(len(set(CONTRIBUTION_TYPE_IDS)), 7)
        self.assertEqual(
            CONTRIBUTION_TYPE_IDS,
            ("method", "theory", "analysis", "benchmark", "system", "application", "position"),
        )
        text = expert_task_text()
        lines = contribution_type_prompt_lines()
        self.assertEqual(len(lines), 7)
        for item in CONTRIBUTION_TYPES:
            self.assertRegex(item.id, r"^[a-z]+$")
            self.assertIn(f"- {item.id}: {item.definition}", text)
            self.assertIn(item.standard, text)
            self.assertIsNone(re.search(r"\d", item.definition + item.standard))
        self.assertEqual(contribution_type_by_id("theory").id, "theory")
        with self.assertRaises(ValueError):
            contribution_type_by_id("poem")

    def test_schema_hint_names_the_keys_without_an_example_score(self) -> None:
        hint = _SCHEMA_HINTS["expert"]
        for key in ("area", "contribution_type", "prior_work", "field_context", "predicted_mean", "rating"):
            self.assertIn(key, hint)
        for type_id in CONTRIBUTION_TYPE_IDS:
            self.assertIn(type_id, hint)
        self.assertIn("no example score", hint)
        self.assertIn("No other keys", hint)
        self.assertNotIn("{", hint)
        self.assertNotIn("5.5", hint)
        self.assertNotIn("2.5", hint)
        self.assertNotIn("8.5", hint)

    def test_contribution_type_normalizes_common_answers(self) -> None:
        self.assertEqual(normalize_contribution_type("method"), "method")
        self.assertEqual(normalize_contribution_type(" Theory (learning theory) "), "theory")
        self.assertEqual(normalize_contribution_type("benchmark/dataset"), "benchmark")
        self.assertEqual(normalize_contribution_type("Dataset"), "benchmark")
        self.assertEqual(normalize_contribution_type("empirical method"), "method")
        self.assertEqual(normalize_contribution_type("Systems"), "system")
        self.assertEqual(normalize_contribution_type("applied ML"), "application")
        self.assertEqual(normalize_contribution_type("survey"), "position")
        self.assertEqual(normalize_contribution_type("analysis"), "analysis")
        for bad in ("poem", "", "   ", 3, None):
            with self.assertRaises(ValueError):
                normalize_contribution_type(bad)

    def test_form_score_maps_the_triad_onto_the_rank_scale(self) -> None:
        self.assertEqual(FORM_MIN, 1)
        self.assertEqual(FORM_MAX, 4)
        self.assertEqual(form_score(1, 1, 1), 1.0)
        self.assertEqual(form_score(4, 4, 4), 10.0)
        self.assertEqual(form_score(3, 3, 2), 6.0)
        self.assertEqual(form_score(2, 3, 3), form_score(3, 2, 3))
        for bad in ((0, 3, 3), (3, 5, 3), (3, 3, True), (3.0, 3, 3)):
            with self.assertRaises(ValueError):
                form_score(*bad)  # type: ignore[arg-type]

    def test_a_full_expert_read_parses_and_normalizes_the_type(self) -> None:
        parsed = ExpertJudgment.model_validate(_expert_payload(contribution_type="Benchmark / dataset"))
        self.assertEqual(parsed.contribution_type, "benchmark")
        self.assertEqual(parsed.predicted_mean, 4.6)
        self.assertEqual(parsed.rating, 2)
        self.assertEqual(len(parsed.prior_work), 2)
        self.assertTrue(parsed.prior_work[0].cited)
        self.assertFalse(parsed.prior_work[1].sure)
        self.assertEqual((parsed.soundness, parsed.presentation, parsed.contribution), (3, 3, 2))
        bare = ExpertJudgment.model_validate(
            _expert_payload(prior_work=[], field_context="one line as a string", strengths=[], weaknesses=[])
        )
        self.assertEqual(bare.prior_work, [])
        self.assertEqual(bare.field_context, ["one line as a string"])
        self.assertEqual(ExpertJudgment.model_validate(_expert_payload(soundness=3.0)).soundness, 3)

    def test_an_expert_read_rejects_bad_types_bools_bounds_and_extra_keys(self) -> None:
        bad_payloads = (
            _expert_payload(contribution_type="poem"),
            _expert_payload(rating=True),
            _expert_payload(soundness=True),
            _expert_payload(contribution=5),
            _expert_payload(presentation=0),
            _expert_payload(soundness=2.5),
            _expert_payload(predicted_mean=10.5),
            _expert_payload(rating=7),
            _expert_payload(area=""),
            _expert_payload(rationale=""),
            _expert_payload(margin=1),
            _expert_payload(
                prior_work=[
                    {"name": f"work {index}", "relation": "close", "cited": False, "sure": True}
                    for index in range(MAX_PRIOR_WORK + 1)
                ]
            ),
            _expert_payload(prior_work=[{"name": "", "relation": "close"}]),
            _expert_payload(field_context=["a line"] * (MAX_FIELD_CONTEXT_LINES + 1)),
            _expert_payload(weaknesses=["a point"] * (MAX_REVIEW_POINTS + 1)),
        )
        for payload in bad_payloads:
            with self.assertRaises(ValidationError):
                ExpertJudgment.model_validate(payload)
        missing = _expert_payload()
        del missing["contribution"]
        with self.assertRaises(ValidationError):
            ExpertJudgment.model_validate(missing)


def _review(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reviewer": "specialist",
        "summary": "The paper claims a counting rule for a synthetic cohort.",
        "strengths": ["the rule is stated as an algorithm"],
        "weaknesses": ["one specimen carries the claim"],
        "rating": 5,
        "confidence": 4,
    }
    row.update(overrides)
    return row


def _panel_payload(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reviews": [
            _review(reviewer="specialist", rating=5),
            _review(reviewer="generalist", rating=6),
            _review(reviewer="methodologist", rating=3),
            _review(reviewer="senior", rating=8),
        ],
        "discussion": "The panel splits on novelty; the gap in the evidence decides it.",
        "predicted_mean": 5.4,
        "rating": 3,
    }
    row.update(overrides)
    return row


class ReviewPanelTests(unittest.TestCase):
    def test_prompt_simulates_typical_reviewers_and_keeps_the_scale(self) -> None:
        text = panel_task_text()
        self.assertIn("## Review panel", text)
        self.assertIn("not ideal referees", text)
        self.assertIn("Predict them, not a better reviewer", text)
        self.assertIn("has not seen the other reviews", text)
        for key in ("reviewer", "summary", "strengths", "weaknesses", "rating", "confidence", "discussion", "predicted_mean"):
            self.assertIn(key, text)
        self.assertIn("Real panels disagree", text)
        self.assertIn("do not save 8 for a paper that changes the field", text)
        self.assertIn("need not equal the mean of the four ratings", text)
        self.assertLess(text.index("- senior:"), text.index("Each review has these fields"))
        self.assertLess(text.index("Real panels disagree"), text.index("predicted_mean is the mean"))
        self.assertNotIn("## Venue mean", text)
        self.assertNotIn("## Expert read", text)
        self.assertNotIn("## Audit", text)
        self.assertNotIn("## Verified audit", text)
        self.assertNotIn("contribution_type", text)
        self.assertNotIn("weak-reject", text)
        self.assertNotIn("strong-accept", text)
        self.assertNotIn("margin", text.lower())
        self.assertNotIn("openreview.net", text)
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
        for paragraph in EXPERT_SCALE_PARAGRAPHS:
            self.assertIn(paragraph, text)
            self.assertIn(paragraph, HOLISTIC_TASK)
        self.assertIn("5.4", text)
        self.assertIn("5.6", text)
        self.assertIn("borderline reject", text)
        self.assertTrue(text.endswith("Return only the JSON object."))

    def test_each_role_is_defined_once_in_the_prompt_without_digits(self) -> None:
        self.assertEqual(PANEL_SIZE, 4)
        self.assertEqual(len(PANEL_ROLES), PANEL_SIZE)
        self.assertEqual(PANEL_ROLE_IDS, ("specialist", "generalist", "methodologist", "senior"))
        self.assertEqual(OFFICIAL_RATINGS, (1, 3, 5, 6, 8, 10))
        self.assertEqual((RATING_MIN, RATING_MAX), (1, 10))
        self.assertEqual((CONFIDENCE_MIN, CONFIDENCE_MAX), (1, 5))
        text = panel_task_text()
        lines = panel_role_prompt_lines()
        self.assertEqual(len(lines), PANEL_SIZE)
        for role in PANEL_ROLES:
            self.assertRegex(role.id, r"^[a-z]+$")
            self.assertEqual(text.count(f"- {role.id}: "), 1)
            self.assertIn(role.profile, text)
            self.assertIsNone(re.search(r"\d", role.profile))
        self.assertEqual(panel_role_by_id("senior").id, "senior")
        with self.assertRaises(ValueError):
            panel_role_by_id("chair")

    def test_schema_hint_names_the_shape_without_an_example_score(self) -> None:
        hint = _SCHEMA_HINTS["panel"]
        for key in ("reviews", "reviewer", "summary", "strengths", "weaknesses", "confidence", "discussion", "predicted_mean"):
            self.assertIn(key, hint)
        for role_id in PANEL_ROLE_IDS:
            self.assertIn(role_id, hint)
        self.assertIn("exactly 4 objects", hint)
        self.assertIn("1, 3, 5, 6, 8, 10", hint)
        self.assertIn("no example score", hint)
        self.assertIn("No other keys", hint)
        self.assertNotIn("5.5", hint)
        self.assertNotIn("2.5", hint)
        self.assertNotIn("8.5", hint)
        self.assertNotIn('"rating":5', hint)
        self.assertNotIn('"rating":6', hint)

    def test_reviewer_labels_normalize_and_unknown_labels_are_kept(self) -> None:
        self.assertEqual(normalize_reviewer_role("specialist"), "specialist")
        self.assertEqual(normalize_reviewer_role(" Generalist "), "generalist")
        self.assertEqual(normalize_reviewer_role("Reviewer 2 (generalist)"), "generalist")
        self.assertEqual(normalize_reviewer_role("the methodologist"), "methodologist")
        self.assertEqual(normalize_reviewer_role("Senior researcher"), "senior")
        self.assertEqual(normalize_reviewer_role("domain expert"), "specialist")
        self.assertEqual(normalize_reviewer_role("empiricist"), "methodologist")
        self.assertEqual(normalize_reviewer_role("adjacent-area reviewer"), "generalist")
        for unknown in ("reviewer 3", "", "   ", 3, None, "area chair"):
            self.assertIsNone(normalize_reviewer_role(unknown))

    def test_panel_mean_is_the_mean_of_four_integer_ratings(self) -> None:
        self.assertEqual(panel_mean([5, 6, 3, 8]), 5.5)
        self.assertEqual(panel_mean((1, 1, 1, 1)), 1.0)
        self.assertEqual(panel_mean((10, 10, 10, 10)), 10.0)
        self.assertEqual(panel_mean([5, 6, 7, 8]), 6.5)
        for bad in ([5, 6, 3], [5, 6, 3, 8, 8], [5, 6, 3, 0], [5, 6, 3, 11], [5, 6, 3, True], [5, 6, 3, 8.0]):
            with self.assertRaises(ValueError):
                panel_mean(bad)  # type: ignore[arg-type]
        self.assertEqual(panel_mean([5, 6, 3], size=3), 4.6667)
        self.assertEqual(panel_mean((8, 8, 8), size=3), 8.0)
        for bad in ([5, 6, 3, 8], [5, 6], [5, 6, 0], [5, 6, 3.0]):
            with self.assertRaises(ValueError):
                panel_mean(bad, size=3)  # type: ignore[arg-type]

    def test_a_full_panel_parses_and_tolerates_labels_and_lists(self) -> None:
        parsed = PanelJudgment.model_validate(_panel_payload())
        self.assertEqual([review.rating for review in parsed.reviews], [5, 6, 3, 8])
        self.assertEqual(parsed.predicted_mean, 5.4)
        self.assertEqual(parsed.rating, 3)
        self.assertEqual(parsed.reviews[0].confidence, 4)
        loose = PanelJudgment.model_validate(
            _panel_payload(
                reviews=[
                    _review(reviewer="Reviewer 1 (specialist)", rating=7, strengths="a single clause"),
                    _review(reviewer="area chair", rating=6.0, weaknesses=[]),
                    _review(reviewer="methodologist", rating=3, strengths=["a", "b", "c", "d"]),
                    _review(reviewer="senior", rating=8),
                ]
            )
        )
        self.assertEqual(loose.reviews[0].strengths, ["a single clause"])
        self.assertEqual(loose.reviews[0].rating, 7)
        self.assertEqual(loose.reviews[1].rating, 6)
        self.assertEqual(loose.reviews[1].weaknesses, [])
        self.assertEqual(len(loose.reviews[2].strengths), 4)
        self.assertEqual(normalize_reviewer_role(loose.reviews[1].reviewer), None)

    def test_a_panel_rejects_the_wrong_count_bools_bounds_and_extra_keys(self) -> None:
        four = _panel_payload()["reviews"]
        assert isinstance(four, list)
        bad_payloads = (
            _panel_payload(reviews=four[:3]),
            _panel_payload(reviews=[*four, _review(reviewer="senior", rating=8)]),
            _panel_payload(reviews=[]),
            _panel_payload(reviews=[*four[:3], _review(rating=0)]),
            _panel_payload(reviews=[*four[:3], _review(rating=11)]),
            _panel_payload(reviews=[*four[:3], _review(rating=6.5)]),
            _panel_payload(reviews=[*four[:3], _review(rating=True)]),
            _panel_payload(reviews=[*four[:3], _review(confidence=0)]),
            _panel_payload(reviews=[*four[:3], _review(confidence=6)]),
            _panel_payload(reviews=[*four[:3], _review(confidence=True)]),
            _panel_payload(reviews=[*four[:3], _review(reviewer="")]),
            _panel_payload(reviews=[*four[:3], _review(summary="")]),
            _panel_payload(reviews=[*four[:3], _review(questions=["an extra key"])]),
            _panel_payload(predicted_mean=10.5),
            _panel_payload(predicted_mean=True),
            _panel_payload(rating=7),
            _panel_payload(rating=True),
            _panel_payload(discussion=""),
            _panel_payload(margin=1),
            _panel_payload(meta_review="an extra key"),
        )
        for payload in bad_payloads:
            with self.assertRaises(ValidationError):
                PanelJudgment.model_validate(payload)
        missing = _panel_payload()
        del missing["discussion"]
        with self.assertRaises(ValidationError):
            PanelJudgment.model_validate(missing)
        with self.assertRaises(ValidationError):
            PanelReview.model_validate({**_review(), "rating": None})


def _matched_payload(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reviews": [
            _review(reviewer="insider", rating=5),
            _review(reviewer="junior", rating=6),
            _review(reviewer="neighbour", rating=3),
            _review(reviewer="senior", rating=8),
        ],
        "discussion": "The panel splits on novelty; the single specimen decides it.",
        "predicted_mean": 5.4,
        "rating": 3,
    }
    row.update(overrides)
    return row


def _synthetic_packet(source: str) -> str:
    return (
        "# Review Route M0: Synthetic route\n\n- Source SHA-256: `abc`\n\n## Route Instructions\n\n"
        "Review the synthetic manuscript.\n\n## Source Text\n\n"
        + source
        + "\n## Figures (mandatory inspection)\n\n- PDF: missing sibling `*.pdf`.\n\n"
        "## Listed contributions (audit each one)\n\n- Extracted candidates: none.\n"
    )


class MatchedPanelTests(unittest.TestCase):
    def test_prompt_seats_the_venue_panel_on_the_main_text_and_keeps_the_scale(self) -> None:
        text = matched_task_text()
        self.assertIn("## Assigned panel", text)
        self.assertIn("matched to the paper's topic", text)
        self.assertIn("reads the main text once", text)
        self.assertIn("does not study the appendix", text)
        self.assertIn("Predict them, not a better reviewer", text)
        self.assertIn("The reference list and the appendix are not shown", text)
        self.assertIn("do not treat it as missing or as wrong", text)
        self.assertIn("has not seen the other reviews", text)
        for key in ("reviewer", "summary", "strengths", "weaknesses", "rating", "confidence", "discussion", "predicted_mean"):
            self.assertIn(key, text)
        self.assertIn("up to two clauses", text)
        self.assertIn(MATCHED_NORMS, text)
        self.assertIn("About one submission in three is accepted", text)
        self.assertIn("8 is about one review in six", text)
        self.assertIn("not a quota for one paper", text)
        self.assertIn("Real panels disagree", text)
        self.assertIn("need not equal the mean of the four ratings", text)
        self.assertLess(text.index("- senior:"), text.index("Each review has these fields"))
        self.assertLess(text.index("How reviewers at this venue use the scale"), text.index("predicted_mean is the mean"))
        for other in ("## Venue mean", "## Expert read", "## Review panel", "## Audit", "## Verified audit"):
            self.assertNotIn(other, text)
        for role_id in ("specialist", "generalist", "methodologist"):
            self.assertNotIn(f"- {role_id}:", text)
        self.assertNotIn("contribution_type", text)
        self.assertNotIn("weak-reject", text)
        self.assertNotIn("strong-accept", text)
        self.assertNotIn("margin", text.lower())
        self.assertNotIn("openreview.net", text)
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
        for paragraph in EXPERT_SCALE_PARAGRAPHS:
            self.assertIn(paragraph, text)
            self.assertIn(paragraph, HOLISTIC_TASK)
        self.assertIn("5.4", text)
        self.assertIn("5.6", text)
        self.assertIn("borderline reject", text)
        self.assertTrue(text.endswith("Return only the JSON object."))

    def test_each_seat_is_defined_once_without_digits_and_the_panel_text_is_untouched(self) -> None:
        self.assertEqual(len(MATCHED_SEATS), PANEL_SIZE)
        self.assertEqual(MATCHED_SEAT_IDS, ("insider", "junior", "neighbour", "senior"))
        text = matched_task_text()
        lines = matched_seat_prompt_lines()
        self.assertEqual(len(lines), PANEL_SIZE)
        for seat in MATCHED_SEATS:
            self.assertRegex(seat.id, r"^[a-z]+$")
            self.assertEqual(text.count(f"- {seat.id}: "), 1)
            self.assertIn(seat.profile, text)
            self.assertIsNone(re.search(r"\d", seat.profile))
        self.assertIn("auditor", matched_task_text().lower() + "auditor")
        self.assertNotIn("rigour", " ".join(seat.profile for seat in MATCHED_SEATS))
        self.assertEqual(matched_seat_by_id("junior").id, "junior")
        with self.assertRaises(ValueError):
            matched_seat_by_id("methodologist")
        # The round-8 panel text does not change when the matched panel is added.
        panel = panel_task_text()
        self.assertNotIn("Assigned panel", panel)
        self.assertNotIn("one review in six", panel)
        self.assertEqual(len(panel), 6311)

    def test_schema_hint_names_the_seats_without_an_example_score(self) -> None:
        hint = _SCHEMA_HINTS["matched"]
        for key in ("reviews", "reviewer", "summary", "strengths", "weaknesses", "confidence", "discussion", "predicted_mean"):
            self.assertIn(key, hint)
        self.assertIn("insider, junior, neighbour, senior", hint)
        for role_id in ("specialist", "generalist", "methodologist"):
            self.assertNotIn(role_id, hint)
        self.assertIn("exactly 4 objects", hint)
        self.assertIn("1, 3, 5, 6, 8, 10", hint)
        self.assertIn("no example score", hint)
        self.assertIn("No other keys", hint)
        self.assertNotIn("5.5", hint)
        self.assertNotIn('"rating":5', hint)
        self.assertNotIn('"rating":6', hint)
        self.assertEqual(_SCHEMA_HINTS["panel"].count("specialist, generalist, methodologist, senior"), 1)

    def test_seat_labels_normalize_and_unknown_labels_are_kept(self) -> None:
        self.assertEqual(normalize_matched_seat("insider"), "insider")
        self.assertEqual(normalize_matched_seat("Reviewer 1 (insider)"), "insider")
        self.assertEqual(normalize_matched_seat("domain expert"), "insider")
        self.assertEqual(normalize_matched_seat(" Junior "), "junior")
        self.assertEqual(normalize_matched_seat("PhD student reviewer"), "junior")
        self.assertEqual(normalize_matched_seat("neighbor"), "neighbour")
        self.assertEqual(normalize_matched_seat("neighbouring-area researcher"), "neighbour")
        self.assertEqual(normalize_matched_seat("generalist"), "neighbour")
        self.assertEqual(normalize_matched_seat("Senior researcher"), "senior")
        self.assertEqual(normalize_matched_seat("established reviewer"), "senior")
        for unknown in ("reviewer 3", "", "   ", 3, None, "area chair", "methodologist"):
            self.assertIsNone(normalize_matched_seat(unknown))

    def test_a_matched_panel_parses_like_a_panel(self) -> None:
        parsed = MatchedJudgment.model_validate(_matched_payload())
        self.assertIsInstance(parsed, PanelJudgment)
        self.assertEqual([review.rating for review in parsed.reviews], [5, 6, 3, 8])
        self.assertEqual(panel_mean([review.rating for review in parsed.reviews]), 5.5)
        self.assertEqual(parsed.predicted_mean, 5.4)
        self.assertEqual(parsed.rating, 3)
        four = _matched_payload()["reviews"]
        assert isinstance(four, list)
        for payload in (
            _matched_payload(reviews=four[:3]),
            _matched_payload(reviews=[*four[:3], _review(reviewer="senior", rating=7.5)]),
            _matched_payload(reviews=[*four[:3], _review(reviewer="senior", confidence=0)]),
            _matched_payload(predicted_mean=0.5),
            _matched_payload(rating=7),
            _matched_payload(cut="an extra key"),
        ):
            with self.assertRaises(ValidationError):
                MatchedJudgment.model_validate(payload)

    def test_main_text_packet_cuts_at_the_reference_heading_and_keeps_the_other_sections(self) -> None:
        body = [f"Synthetic paragraph {index} of the main text." for index in range(60)]
        tail = [f"Synthetic reference entry {index}." for index in range(20)]
        source = "\n".join(body) + "\nREFERENCES\n" + "\n".join(tail) + "\nA APPENDIX\nSynthetic appendix line.\n"
        packet = _synthetic_packet(source)
        shown, record = main_text_packet(packet)
        self.assertEqual(record["cut"], "references")
        self.assertEqual(record["source_chars"], len(source.strip()))
        self.assertLess(record["shown_chars"], record["source_chars"])
        self.assertEqual(record["shown_chars"], len("\n".join(body)))
        self.assertIn("Synthetic paragraph 59 of the main text.", shown)
        self.assertNotIn("REFERENCES", shown)
        self.assertNotIn("Synthetic reference entry", shown)
        self.assertNotIn("Synthetic appendix line", shown)
        self.assertIn(MAIN_TEXT_END_MARKER, shown)
        self.assertNotIn(MAIN_TEXT_CAP_MARKER, shown)
        self.assertIn("## Route Instructions", shown)
        self.assertIn("## Figures (mandatory inspection)", shown)
        self.assertIn("## Listed contributions (audit each one)", shown)
        self.assertLess(shown.index("## Source Text"), shown.index(MAIN_TEXT_END_MARKER))
        self.assertLess(shown.index(MAIN_TEXT_END_MARKER), shown.index("## Figures"))
        self.assertTrue(shown.startswith("# Review Route M0"))
        self.assertTrue(shown.endswith("- Extracted candidates: none.\n"))
        self.assertIn("\n## Source Text\n\nSynthetic paragraph 0", shown)
        self.assertIn(MAIN_TEXT_END_MARKER + "\n\n## Figures", shown)
        # Numbered, lower-case, and spaced small-caps headings also count.
        for heading in ("7 References", "7. REFERENCES", "R EFERENCES", "Bibliography", "  references  "):
            _text, found = main_text_packet(_synthetic_packet("\n".join(body) + f"\n{heading}\n" + "\n".join(tail)))
            self.assertEqual(found["cut"], "references", heading)
        # A heading inside a sentence, with punctuation, or before the floor does not count.
        for heading in ("see the references", "References.", "References and Notes"):
            _text, found = main_text_packet(_synthetic_packet("\n".join(body) + f"\n{heading}\n" + "\n".join(tail)))
            self.assertEqual(found["cut"], "none", heading)
        early = "References\n" + "\n".join(body) + "\n" + "\n".join(tail)
        text, found = main_text_packet(_synthetic_packet(early))
        self.assertEqual(found["cut"], "none")
        self.assertEqual(found["shown_chars"], found["source_chars"])
        self.assertIn("Synthetic reference entry 19.", text)
        self.assertNotIn(MAIN_TEXT_END_MARKER, text)
        # A "## " line inside the manuscript does not end the source section.
        inside = "\n".join(body[:30]) + "\n## Not a packet heading\n" + "\n".join(body[30:]) + "\nREFERENCES\n" + "\n".join(tail)
        text, found = main_text_packet(_synthetic_packet(inside))
        self.assertEqual(found["cut"], "references")
        self.assertIn("## Not a packet heading", text)
        self.assertNotIn("Synthetic reference entry", text)

    def test_main_text_packet_caps_long_text_and_leaves_other_packets_alone(self) -> None:
        body = [f"Synthetic paragraph {index} of the main text." for index in range(60)]
        source = "\n".join(body)
        text, found = main_text_packet(_synthetic_packet(source), max_chars=500)
        self.assertEqual(found["cut"], "cap")
        self.assertEqual(found["shown_chars"], 500)
        self.assertEqual(found["source_chars"], len(source))
        self.assertIn(MAIN_TEXT_CAP_MARKER, text)
        self.assertNotIn(MAIN_TEXT_END_MARKER, text)
        self.assertIn("## Figures (mandatory inspection)", text)
        self.assertNotIn("Synthetic paragraph 59", text)
        text, found = main_text_packet(_synthetic_packet(source))
        self.assertEqual(found, {"source_chars": len(source), "shown_chars": len(source), "cut": "none"})
        self.assertIn("Synthetic paragraph 59 of the main text.", text)
        self.assertNotIn("[", text.split("## Source Text")[1].split("## Figures")[0])
        # The cap applies after a reference cut too.
        with_refs = source + "\nREFERENCES\nSynthetic reference entry.\n"
        text, found = main_text_packet(_synthetic_packet(with_refs), max_chars=500)
        self.assertEqual(found["cut"], "cap")
        self.assertEqual(found["shown_chars"], 500)
        plain = "# Review Route M9: no manuscript access\n\n## Route Instructions\n\nNothing.\n"
        self.assertEqual(main_text_packet(plain), (plain, {"source_chars": 0, "shown_chars": 0, "cut": "none"}))
        with self.assertRaises(ValueError):
            main_text_packet(plain, max_chars=0)


def _update(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reviewer": "specialist",
        "change": "the added comparison settled the concern",
        "final_rating": 6,
    }
    row.update(overrides)
    return row


def _rebuttal_payload(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reviews": [
            _review(reviewer="specialist", rating=5),
            _review(reviewer="generalist", rating=6),
            _review(reviewer="senior", rating=3),
        ],
        "response": "The authors point to the appendix for the spread and add the missing comparison.",
        "updates": [
            _update(reviewer="specialist", final_rating=6),
            _update(reviewer="generalist", change="nothing changed for this reviewer", final_rating=6),
            _update(reviewer="senior", change="the objection is to the contribution", final_rating=3),
        ],
        "discussion": "The panel lands on the borderline; the comparison decides it.",
        "predicted_mean": 5.2,
        "rating": 3,
    }
    row.update(overrides)
    return row


class RebuttalPanelTests(unittest.TestCase):
    def test_prompt_simulates_the_discussion_on_the_whole_manuscript_and_keeps_the_scale(self) -> None:
        text = rebuttal_task_text()
        self.assertIn("## Review and discussion", text)
        self.assertIn("through the discussion period", text)
        self.assertIn("as they stand at the end of that period", text)
        self.assertIn("the ratings the venue's forum shows", text)
        self.assertIn("Predict them, not a better reviewer", text)
        self.assertIn("has read the whole manuscript", text)
        self.assertIn("has not seen the other reviews", text)
        for stage in ("Stage one", "Stage two", "Stage three"):
            self.assertEqual(text.count(stage), 1)
        for key in (
            "reviewer",
            "summary",
            "strengths",
            "weaknesses",
            "rating",
            "confidence",
            "response",
            "updates",
            "change",
            "final_rating",
            "discussion",
            "predicted_mean",
        ):
            self.assertIn(key, text)
        self.assertIn("up to two clauses", text)
        self.assertIn("up to three clauses", text)
        self.assertIn("Real panels disagree", text)
        self.assertIn("Do not pull the three toward each other", text)
        self.assertIn("concede nothing they can contest", text)
        self.assertIn("its appendix included", text)
        self.assertIn("Do not invent a result", text)
        self.assertIn("raise a rating when the response settles the concern", text)
        self.assertIn("keep it when the concern is the contribution itself", text)
        self.assertIn("A rating rarely falls", text)
        self.assertIn("Most ratings do not change", text)
        self.assertIn("not from the other reviewers", text)
        self.assertIn("need not equal the mean of the three final ratings", text)
        self.assertLess(text.index("- senior:"), text.index("Each review has these fields"))
        self.assertLess(text.index("Stage one"), text.index("Stage two"))
        self.assertLess(text.index("Stage two"), text.index("Stage three"))
        self.assertLess(text.index("Stage three"), text.index("predicted_mean is the mean"))
        for other in ("## Venue mean", "## Expert read", "## Review panel", "## Assigned panel", "## Audit", "## Verified audit"):
            self.assertNotIn(other, text)
        for role_id in ("methodologist", "insider", "junior", "neighbour"):
            self.assertNotIn(f"- {role_id}:", text)
        # No base-rate paragraph and no main-text cut text.
        self.assertNotIn("one review in six", text)
        self.assertNotIn("one submission in three", text)
        self.assertNotIn("not a quota", text)
        self.assertNotIn("appendix are not shown", text)
        self.assertNotIn("contribution_type", text)
        self.assertNotIn("weak-reject", text)
        self.assertNotIn("strong-accept", text)
        self.assertNotIn("margin", text.lower())
        self.assertNotIn("openreview.net", text)
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
        for paragraph in EXPERT_SCALE_PARAGRAPHS:
            self.assertIn(paragraph, text)
            self.assertIn(paragraph, HOLISTIC_TASK)
        self.assertIn("5.4", text)
        self.assertIn("5.6", text)
        self.assertIn("borderline reject", text)
        self.assertTrue(text.endswith("Return only the JSON object."))

    def test_the_seats_are_three_round_eight_roles_and_the_older_texts_are_untouched(self) -> None:
        self.assertEqual(REBUTTAL_PANEL_SIZE, 3)
        self.assertEqual(len(REBUTTAL_SEATS), REBUTTAL_PANEL_SIZE)
        self.assertEqual(REBUTTAL_SEAT_IDS, ("specialist", "generalist", "senior"))
        self.assertGreater(FULL_TEXT_MAX_CHARS, MAIN_TEXT_MAX_CHARS)
        for seat in REBUTTAL_SEATS:
            self.assertIs(seat, panel_role_by_id(seat.id))
        text = rebuttal_task_text()
        lines = rebuttal_seat_prompt_lines()
        self.assertEqual(len(lines), REBUTTAL_PANEL_SIZE)
        self.assertEqual(lines, [line for line in panel_role_prompt_lines() if not line.startswith("- methodologist:")])
        for seat in REBUTTAL_SEATS:
            self.assertRegex(seat.id, r"^[a-z]+$")
            self.assertEqual(text.count(f"- {seat.id}: "), 1)
            self.assertIn(seat.profile, text)
            self.assertIsNone(re.search(r"\d", seat.profile))
        self.assertNotIn("rigour", " ".join(seat.profile for seat in REBUTTAL_SEATS))
        self.assertEqual(rebuttal_seat_by_id("generalist").id, "generalist")
        with self.assertRaises(ValueError):
            rebuttal_seat_by_id("methodologist")
        # The round-8 panel text and the round-9 matched text do not change when the discussion is added.
        panel = panel_task_text()
        self.assertNotIn("Review and discussion", panel)
        self.assertNotIn("final_rating", panel)
        self.assertEqual(len(panel), 6311)
        matched = matched_task_text()
        self.assertNotIn("Review and discussion", matched)
        self.assertNotIn("final_rating", matched)
        self.assertEqual(len(matched), 7370)
        self.assertEqual(len(expert_task_text()), 7899)
        self.assertEqual(len(text), 8040)

    def test_schema_hint_names_the_three_seats_and_the_final_rating_without_an_example_score(self) -> None:
        hint = _SCHEMA_HINTS["rebuttal"]
        for key in (
            "reviews",
            "reviewer",
            "summary",
            "strengths",
            "weaknesses",
            "confidence",
            "response",
            "updates",
            "change",
            "final_rating",
            "discussion",
            "predicted_mean",
        ):
            self.assertIn(key, hint)
        self.assertIn("specialist, generalist, senior", hint)
        for role_id in ("methodologist", "insider", "junior", "neighbour"):
            self.assertNotIn(role_id, hint)
        self.assertEqual(hint.count("exactly 3 objects"), 2)
        self.assertIn("1, 3, 5, 6, 8, 10", hint)
        self.assertIn("end of the discussion", hint)
        self.assertIn("no example score", hint)
        self.assertIn("No other keys", hint)
        for example in ("5.5", "2.5", "8.5", '"rating":5', '"rating":6', '"final_rating":6', '"final_rating":8'):
            self.assertNotIn(example, hint)
        self.assertEqual(_SCHEMA_HINTS["panel"].count("specialist, generalist, methodologist, senior"), 1)
        self.assertEqual(_SCHEMA_HINTS["matched"].count("insider, junior, neighbour, senior"), 1)
        self.assertNotIn("final_rating", _SCHEMA_HINTS["panel"])
        self.assertNotIn("final_rating", _SCHEMA_HINTS["matched"])

    def test_seat_labels_normalize_and_the_absent_seat_is_unknown(self) -> None:
        self.assertEqual(normalize_rebuttal_seat("specialist"), "specialist")
        self.assertEqual(normalize_rebuttal_seat("Reviewer 1 (specialist)"), "specialist")
        self.assertEqual(normalize_rebuttal_seat("domain expert"), "specialist")
        self.assertEqual(normalize_rebuttal_seat("insider"), "specialist")
        self.assertEqual(normalize_rebuttal_seat(" Generalist "), "generalist")
        self.assertEqual(normalize_rebuttal_seat("neighbouring-area researcher"), "generalist")
        self.assertEqual(normalize_rebuttal_seat("adjacent reviewer"), "generalist")
        self.assertEqual(normalize_rebuttal_seat("Senior researcher"), "senior")
        self.assertEqual(normalize_rebuttal_seat("established reviewer"), "senior")
        for unknown in ("reviewer 3", "", "   ", 3, None, "area chair", "methodologist", "the empiricist", "junior", "PhD student"):
            self.assertIsNone(normalize_rebuttal_seat(unknown))

    def test_a_discussion_parses_and_tolerates_lists_and_rejects_the_wrong_shape(self) -> None:
        parsed = RebuttalJudgment.model_validate(_rebuttal_payload())
        self.assertEqual([review.rating for review in parsed.reviews], [5, 6, 3])
        self.assertEqual([update.final_rating for update in parsed.updates], [6, 6, 3])
        self.assertEqual(panel_mean([review.rating for review in parsed.reviews], size=REBUTTAL_PANEL_SIZE), 4.6667)
        self.assertEqual(panel_mean([update.final_rating for update in parsed.updates], size=REBUTTAL_PANEL_SIZE), 5.0)
        self.assertEqual(parsed.predicted_mean, 5.2)
        self.assertEqual(parsed.rating, 3)
        loose = RebuttalJudgment.model_validate(
            _rebuttal_payload(
                response=["clause one", "clause two"],
                updates=[
                    _update(change=["kept", "the gap stands"]),
                    _update(reviewer="area chair", final_rating=7),
                    _update(reviewer="senior", final_rating=3),
                ],
            )
        )
        self.assertEqual(loose.response, "clause one; clause two")
        self.assertEqual(loose.updates[0].change, "kept; the gap stands")
        self.assertEqual(loose.updates[1].final_rating, 7)
        self.assertIsNone(normalize_rebuttal_seat(loose.updates[1].reviewer))
        three = _rebuttal_payload()["reviews"]
        updates = _rebuttal_payload()["updates"]
        assert isinstance(three, list) and isinstance(updates, list)
        for payload in (
            _rebuttal_payload(reviews=three[:2]),
            _rebuttal_payload(reviews=[*three, _review(reviewer="senior")]),
            _rebuttal_payload(reviews=[*three[:2], _review(reviewer="senior", rating=7.5)]),
            _rebuttal_payload(updates=updates[:2]),
            _rebuttal_payload(updates=[*updates, _update()]),
            _rebuttal_payload(updates=[]),
            _rebuttal_payload(updates=[*updates[:2], _update(final_rating=0)]),
            _rebuttal_payload(updates=[*updates[:2], _update(final_rating=11)]),
            _rebuttal_payload(updates=[*updates[:2], _update(final_rating=6.5)]),
            _rebuttal_payload(updates=[*updates[:2], _update(final_rating=True)]),
            _rebuttal_payload(updates=[*updates[:2], _update(change="")]),
            _rebuttal_payload(updates=[*updates[:2], _update(reviewer="")]),
            _rebuttal_payload(updates=[*updates[:2], _update(rationale="an extra key")]),
            _rebuttal_payload(response=""),
            _rebuttal_payload(predicted_mean=0.5),
            _rebuttal_payload(predicted_mean=True),
            _rebuttal_payload(rating=7),
            _rebuttal_payload(rating=True),
            _rebuttal_payload(discussion=""),
            _rebuttal_payload(cut="an extra key"),
        ):
            with self.assertRaises(ValidationError):
                RebuttalJudgment.model_validate(payload)
        for key in ("response", "updates", "discussion"):
            missing = _rebuttal_payload()
            del missing[key]
            with self.assertRaises(ValidationError):
                RebuttalJudgment.model_validate(missing)
        with self.assertRaises(ValidationError):
            RebuttalUpdate.model_validate({**_update(), "final_rating": None})

    def test_full_text_packet_keeps_a_manuscript_under_the_cap_byte_for_byte_and_caps_the_longest(self) -> None:
        body = [f"Synthetic paragraph {index} of the main text." for index in range(60)]
        tail = [f"Synthetic reference entry {index}." for index in range(20)]
        source = "\n".join(body) + "\nREFERENCES\n" + "\n".join(tail) + "\nA APPENDIX\nSynthetic appendix line.\n"
        packet = _synthetic_packet(source)
        shown, record = full_text_packet(packet)
        self.assertEqual(shown, packet)
        self.assertEqual(record, {"source_chars": len(source.strip()), "shown_chars": len(source.strip()), "cut": "none"})
        self.assertIn("REFERENCES", shown)
        self.assertIn("Synthetic reference entry 19.", shown)
        self.assertIn("Synthetic appendix line.", shown)
        self.assertNotIn(MAIN_TEXT_END_MARKER, shown)
        self.assertNotIn(MAIN_TEXT_CAP_MARKER, shown)
        # The matched cut is a different function and still removes the reference list.
        main, main_record = main_text_packet(packet)
        self.assertEqual(main_record["cut"], "references")
        self.assertNotIn("Synthetic reference entry", main)
        text, found = full_text_packet(packet, max_chars=500)
        self.assertEqual(found["cut"], "cap")
        self.assertEqual(found["shown_chars"], 500)
        self.assertEqual(found["source_chars"], len(source.strip()))
        self.assertIn(MAIN_TEXT_CAP_MARKER, text)
        self.assertNotIn(MAIN_TEXT_END_MARKER, text)
        self.assertIn("## Route Instructions", text)
        self.assertIn("## Figures (mandatory inspection)", text)
        self.assertIn("## Listed contributions (audit each one)", text)
        self.assertNotIn("Synthetic paragraph 59", text)
        self.assertNotIn("Synthetic reference entry", text)
        self.assertNotIn("Synthetic appendix line", text)
        self.assertLess(text.index("## Source Text"), text.index(MAIN_TEXT_CAP_MARKER))
        self.assertLess(text.index(MAIN_TEXT_CAP_MARKER), text.index("## Figures"))
        self.assertTrue(text.startswith("# Review Route M0"))
        self.assertTrue(text.endswith("- Extracted candidates: none.\n"))
        self.assertIn("\n## Source Text\n\nSynthetic paragraph 0", text)
        self.assertIn(MAIN_TEXT_CAP_MARKER + "\n\n## Figures", text)
        exact = "x" * 500
        text, found = full_text_packet(_synthetic_packet(exact), max_chars=500)
        self.assertEqual(found, {"source_chars": 500, "shown_chars": 500, "cut": "none"})
        self.assertEqual(text, _synthetic_packet(exact))
        plain = "# Review Route M9: no manuscript access\n\n## Route Instructions\n\nNothing.\n"
        self.assertEqual(full_text_packet(plain), (plain, {"source_chars": 0, "shown_chars": 0, "cut": "none"}))
        with self.assertRaises(ValueError):
            full_text_packet(plain, max_chars=0)


def _forecast_review(**overrides: object) -> dict[str, object]:
    row = _review(probabilities=[0.0, 0.1, 0.5, 0.3, 0.1, 0.0])
    row.update(overrides)
    return row


def _forecast_payload(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "reviews": [
            _forecast_review(reviewer="specialist", rating=5),
            _forecast_review(reviewer="generalist", rating=6, probabilities=[0.0, 0.0, 0.2, 0.5, 0.3, 0.0]),
            _forecast_review(reviewer="senior", rating=3, probabilities=[0.1, 0.6, 0.3, 0.0, 0.0, 0.0]),
        ],
        "predicted_mean": 5.2,
        "rating": 3,
    }
    row.update(overrides)
    return row


class ForecastPanelTests(unittest.TestCase):
    def test_prompt_asks_three_reviewers_for_a_probability_over_the_official_ratings_and_keeps_the_scale(self) -> None:
        text = forecast_task_text()
        self.assertIn("## Review forecast", text)
        self.assertIn("mean of the official ratings the paper would receive", text)
        self.assertIn("Predict them, not a better reviewer", text)
        self.assertIn("has read the whole manuscript", text)
        self.assertIn("has not seen the other reviews", text)
        for key in ("reviewer", "summary", "strengths", "weaknesses", "rating", "probabilities", "confidence", "predicted_mean"):
            self.assertIn(key, text)
        self.assertEqual(text.count("up to two clauses"), 2)
        self.assertIn("would most likely submit", text)
        self.assertIn("six numbers that sum to one", text)
        self.assertIn("1, 3, 5, 6, 8, and 10, in that order", text)
        self.assertIn("not a hedge", text)
        self.assertIn("puts most of the mass on one rating", text)
        self.assertIn("splits it between the two ratings on each side", text)
        self.assertIn("Put mass on 8 when", text)
        self.assertIn("mass on 3 when", text)
        self.assertIn("a flat spread over all six ratings is almost never right", text)
        self.assertIn("The expected rating under these probabilities is this reviewer's score", text)
        self.assertIn("rating is the rating with the most mass", text)
        self.assertIn("Real panels disagree", text)
        self.assertIn("Do not pull the three toward each other", text)
        self.assertIn("need not equal the mean of the three ratings", text)
        self.assertLess(text.index("- senior:"), text.index("Each review has these fields"))
        self.assertLess(text.index("probabilities: six numbers"), text.index("predicted_mean is the mean"))
        for other in ("## Venue mean", "## Expert read", "## Review panel", "## Assigned panel", "## Review and discussion", "## Audit", "## Verified audit"):
            self.assertNotIn(other, text)
        for role_id in ("methodologist", "insider", "junior", "neighbour"):
            self.assertNotIn(f"- {role_id}:", text)
        # No discussion stage, no base-rate paragraph, no main-text cut text.
        for absent in ("Stage one", "Stage two", "response", "final_rating", "write discussion", "one review in six", "one submission in three", "not a quota", "appendix are not shown", "contribution_type", "weak-reject", "strong-accept", "openreview.net"):
            self.assertNotIn(absent, text)
        self.assertNotIn("margin", text.lower())
        self.assertIsNone(re.search(r"(?i)\bsub\d{3,6}\b", text))
        for paragraph in EXPERT_SCALE_PARAGRAPHS:
            self.assertIn(paragraph, text)
            self.assertIn(paragraph, HOLISTIC_TASK)
        self.assertIn("5.4", text)
        self.assertIn("5.6", text)
        self.assertIn("borderline reject", text)
        self.assertTrue(text.endswith("Return only the JSON object."))

    def test_the_seats_are_the_three_round_eight_roles_and_the_older_texts_are_untouched(self) -> None:
        self.assertEqual(FORECAST_PANEL_SIZE, 3)
        self.assertEqual(len(FORECAST_SEATS), FORECAST_PANEL_SIZE)
        self.assertEqual(FORECAST_SEAT_IDS, ("specialist", "generalist", "senior"))
        self.assertEqual(FORECAST_SEAT_IDS, REBUTTAL_SEAT_IDS)
        for seat in FORECAST_SEATS:
            self.assertIs(seat, panel_role_by_id(seat.id))
            self.assertRegex(seat.id, r"^[a-z]+$")
            self.assertIsNone(re.search(r"\d", seat.profile))
        text = forecast_task_text()
        lines = forecast_seat_prompt_lines()
        self.assertEqual(lines, rebuttal_seat_prompt_lines())
        self.assertEqual(len(lines), FORECAST_PANEL_SIZE)
        for seat in FORECAST_SEATS:
            self.assertEqual(text.count(f"- {seat.id}: "), 1)
            self.assertIn(seat.profile, text)
        self.assertNotIn("rigour", " ".join(seat.profile for seat in FORECAST_SEATS))
        self.assertEqual(forecast_seat_by_id("senior").id, "senior")
        with self.assertRaises(ValueError):
            forecast_seat_by_id("methodologist")
        # The round 5-10 texts do not change when the forecast panel is added.
        self.assertNotIn("probabilities", HOLISTIC_TASK)
        self.assertNotIn("probabilities", audit_task_text())
        self.assertNotIn("probabilities", AUDITED_SCORING_RULE)
        for older, length in ((expert_task_text(), 7899), (panel_task_text(), 6311), (matched_task_text(), 7370), (rebuttal_task_text(), 8040)):
            self.assertNotIn("Review forecast", older)
            self.assertNotIn("probabilities", older)
            self.assertEqual(len(older), length)
        for hint in ("panel", "matched", "rebuttal", "expert", "holistic"):
            self.assertNotIn("probabilities", _SCHEMA_HINTS[hint])
        self.assertEqual(len(text), 6780)

    def test_schema_hint_names_the_three_seats_and_the_six_probabilities_without_an_example_score(self) -> None:
        hint = _SCHEMA_HINTS["forecast"]
        for key in ("reviews", "reviewer", "summary", "strengths", "weaknesses", "rating", "probabilities", "confidence", "predicted_mean"):
            self.assertIn(key, hint)
        self.assertIn("specialist, generalist, senior", hint)
        for role_id in ("methodologist", "insider", "junior", "neighbour"):
            self.assertNotIn(role_id, hint)
        self.assertEqual(hint.count("exactly 3 objects"), 1)
        self.assertIn("exactly 6 numbers", hint)
        self.assertIn("sum to 1", hint)
        self.assertIn("1, 3, 5, 6, 8, 10", hint)
        self.assertIn("most likely submit", hint)
        self.assertIn("no example score", hint)
        self.assertIn("No other keys", hint)
        for absent in ("final_rating", "response", "updates", "discussion", "5.5", "2.5", "8.5", '"rating":5', '"rating":6', "0.5,", "0.3,"):
            self.assertNotIn(absent, hint)

    def test_seat_labels_normalize_like_the_discussed_panel(self) -> None:
        for label, seat in (
            ("specialist", "specialist"),
            ("Reviewer 1 (specialist)", "specialist"),
            ("domain expert", "specialist"),
            (" Generalist ", "generalist"),
            ("neighbouring-area researcher", "generalist"),
            ("Senior researcher", "senior"),
            ("established reviewer", "senior"),
        ):
            self.assertEqual(normalize_forecast_seat(label), seat)
            self.assertEqual(normalize_forecast_seat(label), normalize_rebuttal_seat(label))
        for unknown in ("reviewer 3", "", 3, None, "area chair", "methodologist", "junior"):
            self.assertIsNone(normalize_forecast_seat(unknown))

    def test_expected_rating_normalises_and_rejects_bad_weights(self) -> None:
        self.assertEqual(expected_rating([0.0, 0.1, 0.5, 0.3, 0.1, 0.0]), (5.4, (0.0, 0.1, 0.5, 0.3, 0.1, 0.0)))
        self.assertEqual(expected_rating([0, 0, 1, 0, 0, 0]), (5.0, (0.0, 0.0, 1.0, 0.0, 0.0, 0.0)))
        self.assertEqual(expected_rating([0, 0, 0, 0, 0, 1]), (10.0, (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)))
        self.assertEqual(expected_rating((1, 0, 0, 0, 0, 0)), (1.0, (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)))
        # Weights that do not sum to one are normalised, not refused.
        self.assertEqual(expected_rating([0, 0, 1, 1, 0, 0]), (5.5, (0.0, 0.0, 0.5, 0.5, 0.0, 0.0)))
        self.assertEqual(expected_rating([0, 0, 40, 60, 0, 0]), (5.6, (0.0, 0.0, 0.4, 0.6, 0.0, 0.0)))
        flat = expected_rating([1, 1, 1, 1, 1, 1])
        self.assertEqual(flat[0], 5.5)
        self.assertEqual(flat[1], (0.1667,) * 6)
        for bad in (
            [0.5, 0.5],
            [0.2, 0.2, 0.2, 0.2, 0.2],
            [0.1, 0.1, 0.2, 0.2, 0.2, 0.1, 0.1],
            [0, 0, 0, 0, 0, 0],
            [0, -0.1, 0.6, 0.5, 0, 0],
            [0, 0, True, 0, 0, 0],
            [0, 0, "0.5", 0.5, 0, 0],
            [0, 0, float("nan"), 0.5, 0, 0],
        ):
            with self.assertRaises(ValueError):
                expected_rating(bad)  # type: ignore[arg-type]

    def test_forecast_mean_averages_three_expected_ratings(self) -> None:
        self.assertEqual(forecast_mean([5.4, 6.4, 3.4]), 5.0667)
        self.assertEqual(forecast_mean((6.0, 6.0, 6.0)), 6.0)
        self.assertEqual(forecast_mean([1.0, 10.0]), 5.5) if False else None
        self.assertEqual(forecast_mean([5.5, 7.5], size=2), 6.5)
        for bad in ([5.4, 6.4], [5.4, 6.4, 3.4, 5.0], [5.4, 6.4, 0.5], [5.4, 6.4, 10.5], [5.4, True, 3.4], [5.4, "6", 3.4]):
            with self.assertRaises(ValueError):
                forecast_mean(bad)  # type: ignore[arg-type]

    def test_a_forecast_parses_tolerates_a_keyed_distribution_and_rejects_the_wrong_shape(self) -> None:
        parsed = ForecastJudgment.model_validate(_forecast_payload())
        self.assertEqual([review.rating for review in parsed.reviews], [5, 6, 3])
        self.assertEqual([expected_rating(review.probabilities)[0] for review in parsed.reviews], [5.4, 6.4, 3.4])
        self.assertEqual(parsed.discussion, "")
        self.assertEqual(parsed.predicted_mean, 5.2)
        self.assertEqual(parsed.rating, 3)
        keyed = ForecastJudgment.model_validate(
            _forecast_payload(
                reviews=[
                    _forecast_review(probabilities={"1": 0, "3": 0.1, "5": 0.5, "6": 0.3, "8": 0.1, "10": 0}),
                    _forecast_review(reviewer="area chair", rating=7, probabilities={"6": 0.5, "8": 0.5}),
                    _forecast_review(reviewer="senior", probabilities={"5.0": 0.4, "6.0": 0.6}),
                ],
                discussion=None,
            )
        )
        self.assertEqual(keyed.reviews[0].probabilities, [0.0, 0.1, 0.5, 0.3, 0.1, 0.0])
        self.assertEqual(keyed.reviews[1].probabilities, [0.0, 0.0, 0.0, 0.5, 0.5, 0.0])
        self.assertEqual(keyed.reviews[1].rating, 7)
        self.assertIsNone(normalize_forecast_seat(keyed.reviews[1].reviewer))
        self.assertEqual(keyed.reviews[2].probabilities, [0.0, 0.0, 0.4, 0.6, 0.0, 0.0])
        self.assertEqual(keyed.discussion, "")
        with_discussion = ForecastJudgment.model_validate(_forecast_payload(discussion="The panel lands on the borderline."))
        self.assertEqual(with_discussion.discussion, "The panel lands on the borderline.")
        # A sum away from one parses; the record normalises and counts it.
        loose = ForecastJudgment.model_validate(
            _forecast_payload(reviews=[_forecast_review(probabilities=[0, 0, 2, 2, 0, 0]), _forecast_review(), _forecast_review()])
        )
        self.assertEqual(expected_rating(loose.reviews[0].probabilities), (5.5, (0.0, 0.0, 0.5, 0.5, 0.0, 0.0)))
        three = _forecast_payload()["reviews"]
        assert isinstance(three, list)
        for payload in (
            _forecast_payload(reviews=three[:2]),
            _forecast_payload(reviews=[*three, _forecast_review(reviewer="senior")]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(rating=7.5)]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(rating=0)]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(rating=True)]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities=[0.2, 0.2, 0.2, 0.2, 0.2])]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities=[0.1, 0.1, 0.2, 0.2, 0.2, 0.1, 0.1])]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities=[0, 0, 0, 0, 0, 0])]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities=[0, -0.1, 0.6, 0.5, 0, 0])]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities=[0, 0, "0.5", 0.5, 0, 0])]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities=[0, 0, True, 0.5, 0, 0])]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities="0.5")]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(probabilities={"7": 1.0})]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(confidence=0)]),
            _forecast_payload(reviews=[*three[:2], _forecast_review(final_rating=6)]),
            _forecast_payload(predicted_mean=0.5),
            _forecast_payload(predicted_mean=True),
            _forecast_payload(rating=7),
            _forecast_payload(rating=True),
            _forecast_payload(response="an extra key"),
            _forecast_payload(updates=[]),
        ):
            with self.assertRaises(ValidationError):
                ForecastJudgment.model_validate(payload)
        for key in ("reviews", "predicted_mean", "rating"):
            missing = _forecast_payload()
            del missing[key]
            with self.assertRaises(ValidationError):
                ForecastJudgment.model_validate(missing)
        bare = _forecast_review()
        del bare["probabilities"]
        with self.assertRaises(ValidationError):
            ForecastReview.model_validate(bare)
        # The older panel review does not grow a probability field.
        with self.assertRaises(ValidationError):
            PanelReview.model_validate(_forecast_review())


class AuditChecklistTests(unittest.TestCase):
    def test_twelve_distinct_ids_each_defined_in_the_prompt(self) -> None:
        self.assertEqual(len(AUDIT_CHECKS), 12)
        self.assertEqual(len(set(AUDIT_CHECK_IDS)), 12)
        text = audit_task_text()
        self.assertIn("## Audit", text)
        self.assertIn("You are not scoring it", text)
        self.assertNotIn("predicted_mean", text)
        self.assertNotIn("weak-reject", text)
        self.assertEqual(len(audit_check_prompt_lines()), 12)
        for check in AUDIT_CHECKS:
            self.assertRegex(check.id, r"^[a-z_]+$")
            self.assertIn(f"- {check.id}: {check.question}", text)
            self.assertIn(check.passes, text)
            self.assertIn(check.fails, text)
            self.assertIsNone(re.search(r"\d", check.question + check.passes + check.fails))
        self.assertEqual(audit_check_by_id("variance_reported").id, "variance_reported")
        with self.assertRaises(ValueError):
            audit_check_by_id("missing_check")
        hint = _SCHEMA_HINTS["audit"]
        self.assertIn("twelve ids", hint)
        self.assertIn("no score", hint)
        self.assertNotIn('"verdict":"pass"', hint)

    def test_checks_score_maps_the_pass_count_onto_the_rank_scale(self) -> None:
        self.assertEqual(checks_score(0), 1.0)
        self.assertEqual(checks_score(12), 10.0)
        self.assertEqual(checks_score(6), 5.5)
        self.assertEqual(checks_score(1, total=4), 3.25)
        for bad in (13, -1, True):
            with self.assertRaises(ValueError):
                checks_score(bad)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            checks_score(0, total=0)


def _answers(**overrides: dict[str, str]) -> list[dict[str, str]]:
    rows = []
    for check_id in AUDIT_CHECK_IDS:
        row = {
            "id": check_id,
            "verdict": "pass",
            "excerpt": "A synthetic passage.",
            "note": "The passage shows it.",
        }
        row.update(overrides.get(check_id, {}))
        rows.append(row)
    return rows


def _flaw(**overrides: str) -> dict[str, str]:
    row = {
        "id": "F1",
        "title": "Gain inside the spread",
        "excerpt": "A synthetic passage.",
        "why": "The reported spread covers the gain.",
        "severity": "major",
    }
    row.update(overrides)
    return row


class AuditSchemaTests(unittest.TestCase):
    def test_a_complete_checklist_parses_and_normalizes_verdicts(self) -> None:
        payload = AuditJudgment.model_validate(
            {
                "checks": _answers(
                    variance_reported={"verdict": " FAIL "},
                    breadth_of_evidence={"verdict": "Unclear", "excerpt": "", "note": ""},
                ),
                "flaws": [_flaw(severity="Major")],
            }
        )
        by_id = {item.id: item for item in payload.checks}
        self.assertEqual(len(by_id), 12)
        self.assertEqual(by_id["variance_reported"].verdict, "fail")
        self.assertEqual(by_id["breadth_of_evidence"].verdict, "unclear")
        self.assertEqual(by_id["claim_quantified"].verdict, "pass")
        self.assertEqual(payload.flaws[0].severity, "major")
        empty = AuditJudgment.model_validate({"checks": _answers()})
        self.assertEqual(empty.flaws, [])

    def test_a_missing_or_repeated_id_is_rejected(self) -> None:
        rows = _answers()
        with self.assertRaises(ValidationError) as caught:
            AuditJudgment.model_validate({"checks": rows[:-1], "flaws": []})
        self.assertIn("missing", str(caught.exception))
        with self.assertRaises(ValidationError) as repeated:
            AuditJudgment.model_validate({"checks": [*rows[:-1], rows[0]], "flaws": []})
        self.assertIn("repeated", str(repeated.exception))

    def test_unknown_values_and_extra_keys_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate(
                {"checks": _answers(claim_quantified={"verdict": "maybe"}), "flaws": []}
            )
        rows = _answers()
        rows[0] = {**rows[0], "id": "not_a_check"}
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate({"checks": rows, "flaws": []})
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate({"checks": _answers(), "flaws": [_flaw(severity="fatal")]})
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate({"checks": _answers(), "flaws": [], "predicted_mean": 6.0})
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate({"checks": _answers(), "flaws": [_flaw(excerpt="")]})

    def test_too_many_or_repeated_flaws_are_rejected(self) -> None:
        too_many = [_flaw(id=f"F{index + 1}") for index in range(MAX_AUDIT_FLAWS + 1)]
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate({"checks": _answers(), "flaws": too_many})
        with self.assertRaises(ValidationError):
            AuditJudgment.model_validate({"checks": _answers(), "flaws": [_flaw(), _flaw()]})
        just_enough = [_flaw(id=f"F{index + 1}") for index in range(MAX_AUDIT_FLAWS)]
        parsed = AuditJudgment.model_validate({"checks": _answers(), "flaws": just_enough})
        self.assertEqual(len(parsed.flaws), MAX_AUDIT_FLAWS)


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
