"""Scale-aware official ratings. Synthetic notes only."""

from __future__ import annotations

import unittest

from autopaperreview.public_calibration import (
    RatingScaleError,
    collect_official_ratings,
    parse_rating,
)
from autopaperreview.rating_scales import scale_for_year


def _review(signature: str, rating: str | None) -> dict:
    content = {} if rating is None else {"rating": {"value": rating}}
    return {
        "id": signature,
        "signatures": [signature],
        "tmdate": 1,
        "invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"],
        "content": content,
    }


class RatingScaleTests(unittest.TestCase):
    def test_default_parse_keeps_the_2024_bins(self) -> None:
        self.assertIsNone(parse_rating("7: not a venue bin"))
        self.assertEqual(parse_rating("6: marginally above the acceptance threshold"), 6)
        self.assertIsNone(parse_rating(0))
        self.assertIsNone(parse_rating(2))
        self.assertIsNone(parse_rating(4))

    def test_iclr_2026_even_scale_is_kept(self) -> None:
        scale = scale_for_year(2026)
        self.assertEqual(scale.key, "iclr2026")
        for number in (0, 2, 4, 6, 8, 10):
            self.assertEqual(parse_rating(f"{number}: label", scale=scale), number)
        for number in (1, 3, 5):
            self.assertIsNone(parse_rating(number, scale=scale))
        notes = [
            _review("Reviewer_A", "4: weak accept"),
            _review("Reviewer_B", "6: accept"),
            _review("Reviewer_C", None),
        ]
        parsed = collect_official_ratings(notes, scale=scale)
        self.assertEqual(parsed.ratings, [4, 6])
        self.assertEqual(parsed.missing, 1)
        self.assertEqual(parsed.unparsed, 0)

    def test_off_scale_rating_fails_instead_of_being_dropped(self) -> None:
        scale = scale_for_year(2026)
        notes = [
            _review("Reviewer_A", "4: weak accept"),
            _review("Reviewer_B", "6: accept"),
            _review("Reviewer_C", "5: not on the 2026 scale"),
        ]
        with self.assertRaises(RatingScaleError) as caught:
            collect_official_ratings(notes, scale=scale)
        self.assertEqual(caught.exception.unparsed, 1)
        self.assertEqual(caught.exception.parsed, 2)
        self.assertIn("5", caught.exception.off_scale_tokens if hasattr(caught.exception, "off_scale_tokens") else str(caught.exception))

    def test_unregistered_year_uses_the_explicit_range(self) -> None:
        scale = scale_for_year(2027)
        self.assertIsNone(scale.values)
        self.assertEqual(parse_rating(1, scale=scale), 1)
        self.assertEqual(parse_rating(4, scale=scale), 4)
        self.assertIsNone(parse_rating(11, scale=scale))
        self.assertTrue(scale.key.startswith("range:"))

    def test_a_raised_limit_records_the_drop_without_hiding_it(self) -> None:
        scale = scale_for_year(2024)
        notes = [
            _review("Reviewer_A", "8: accept"),
            _review("Reviewer_B", "6: accept"),
            _review("Reviewer_C", "4: off the 2024 scale"),
        ]
        parsed = collect_official_ratings(notes, scale=scale, unparsed_fraction_limit=0.5)
        self.assertEqual(parsed.ratings, [8, 6])
        self.assertEqual(parsed.unparsed, 1)
        self.assertEqual(parsed.off_scale_tokens, ["4"])
