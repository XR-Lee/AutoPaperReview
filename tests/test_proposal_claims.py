"""Synthetic regression tests for source-backed proposal-claim review."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.proposal import claim_review as review


def clean_judge(_):
    return {"verdict": "clean"}


class ClaimReviewTest(TestCase):
    def test_probabilities_have_neutral_mass_and_no_grade_override(self):
        zeros = [[0., 0.] for _ in range(14)]
        z = review.estimate({"text": "A synthetic design.", "field": "problem"}, [30, 15, 1], zeros)
        self.assertAlmostEqual(z["estimated_linked_support"], 1/3)
        self.assertAlmostEqual(z["independent_review_opposition"], 2/9)
        cases = [{"id": "toy", "overall": None, "proposal": {}}]
        result = review.evaluate(cases, {"simple_event": {"coef": zeros}}, Path("."), True, leak_judge=clean_judge)[0]
        self.assertIsNone(result["views"]["verified"]["deployed_score"])
        self.assertEqual(result["views"]["verified"]["statements"], [])

    def test_source_refusals_and_partial_evidence_restore_original(self):
        original = [{"field": "problem", "text": "An original synthetic question.", "origin": "original"}]
        extras = [{"field": "problem", "text": "An added synthetic goal."}]
        source = {"u1": "An added synthetic goal is proposed."}
        def stage(which, body, cache, offline, *, leak_judge):
            if which == "ground":
                return "ground", {"state": "response", "output": {"x1": {"source": "u1", "quote": "added synthetic goal"}}}
            if which == "prospective":
                return "second", {"state": "response", "output": {"a1": {"judgment": "partial", "quote": "added synthetic goal"}}}
            return "forecast", {"state": "response", "output": {k: [90, 0, 1] if v.startswith("An added") else [0, 0, 0] for k, v in body.items()}}
        with patch.object(review, "candidates", return_value=(original, extras, source)), patch.object(review, "request", side_effect=stage):
            result, _, audit = review.select_sources({"id": "synthetic"}, Path("."), True, leak_judge=clean_judge)
        self.assertEqual(result, original)
        self.assertEqual(audit["decisions"][0]["judgment"], "partial")
        with self.assertRaises(ValueError):
            review.validate("prospective", {"a1": {"judgment": "full_prospective", "quote": "unseen text"}},
                            {"a1": {"claim": "synthetic", "source": source["u1"]}})

    def test_cache_only_and_invalid_coefficients_fail_before_model_calls(self):
        with TemporaryDirectory() as tmp, patch.object(review, "run_command") as live:
            with self.assertRaises(ValueError):
                review.request("forecast", {"s1": "A synthetic question."}, Path(tmp), True, leak_judge=clean_judge)
            live.assert_not_called()
        for value in (None, [[float("inf"), 0.]] * 14, [[0, 0]]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.coefficients({"simple_event": {"coef": value}})

    def test_multi_candidate_inputs_need_distinct_ids(self):
        with self.assertRaises(ValueError):
            review.evaluate([{"id": "same"}, {"id": "same"}], {"simple_event": {"coef": [[0, 0]] * 14}}, Path("."), True, leak_judge=clean_judge)
