"""Small-caps from pdftotext join; ordinary prose does not."""

from __future__ import annotations

import unittest

from benchmarks.promotion.pdftotext_norm import collapse_smallcaps


class SmallcapsTests(unittest.TestCase):
    def test_heading_letters_join(self) -> None:
        text = "A BSTRACT\nI NTRODUCTION\nA novel method stays split."
        collapsed = collapse_smallcaps(text)
        self.assertIn("ABSTRACT", collapsed)
        self.assertIn("INTRODUCTION", collapsed)
        self.assertIn("A novel method stays split.", collapsed)
        self.assertNotIn("Anovel", collapsed)

    def test_repeated_splits_on_one_line_join(self) -> None:
        collapsed = collapse_smallcaps("I NTEGRATING O BJECT V IEW")
        self.assertEqual(collapsed, "INTEGRATING OBJECT VIEW")
