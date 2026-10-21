"""Synthetic score agreement. No manuscripts and no network."""

from __future__ import annotations

import unittest

from autopaperreview.external_baseline import (
    manuscript_excerpt,
    parse_json_field,
    parse_last_rating,
    parse_rating_heading,
    summarize_scores,
)


class ExcerptTests(unittest.TestCase):
    def test_drops_appendix_and_caps_length(self) -> None:
        text = "ABSTRACT\n" + ("A" * 1600) + "\n\nReferences\n\nappendix-marker"
        excerpt = manuscript_excerpt(text, limit=4000, start_at_abstract=True)
        self.assertTrue(excerpt.startswith("ABSTRACT"))
        self.assertNotIn("appendix-marker", excerpt)
        self.assertGreater(len(excerpt), 1500)


class ParserTests(unittest.TestCase):
    def test_json_overall_inside_fence(self) -> None:
        text = 'THOUGHT:\nnope\n```json\n{"Overall": 6, "Decision": "Accept"}\n```'
        self.assertEqual(parse_json_field(text, "Overall"), 6)

    def test_rating_heading_keeps_ten(self) -> None:
        text = "# Review\n\n## Rating\n10: strong accept\n\n## Confidence\n4"
        self.assertEqual(parse_rating_heading(text), 10)

    def test_last_rating_wins(self) -> None:
        text = "## Rating\n3\n\n## Rating\n8"
        self.assertEqual(parse_last_rating(text), 8)

    def test_rejects_out_of_range(self) -> None:
        self.assertIsNone(parse_json_field('{"Overall": 42}', "Overall"))


class SummaryTests(unittest.TestCase):
    def test_perfect_order_and_bias(self) -> None:
        scores = [2.0, 5.0, 8.0, 9.0]
        means = [2.0, 5.0, 8.0, 9.0]
        decisions = ["reject", "reject", "accept", "accept"]
        ratings = [[1, 3], [5, 5], [8, 8], [8, 10]]
        metrics = summarize_scores(scores, means, decisions, ratings)
        self.assertAlmostEqual(metrics["spearman"], 1.0)
        self.assertEqual(metrics["mae"], 0)
        self.assertEqual(metrics["bias"], 0)
        self.assertEqual(metrics["accept_reject_auc"], 1)
        self.assertEqual(metrics["bin_agreement"], 1)
        self.assertEqual(metrics["n_accept"], 2)

    def test_leniency_is_positive_bias(self) -> None:
        metrics = summarize_scores([8.0, 8.0], [3.0, 5.0], ["reject", "reject"])
        self.assertGreater(metrics["bias"], 0)
        self.assertEqual(metrics["histogram"]["8"], 2)


if __name__ == "__main__":
    unittest.main()
