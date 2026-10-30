"""Two-column extraction and anonymization regressions using invented text only."""

from __future__ import annotations

import unittest

from autopaperreview.cvpr_anonymize import (
    PREAMBLE_CHARS,
    TITLE_NGRAM,
    anonymize,
    extract_meta,
    leak_counts,
    leak_failed,
)
from autopaperreview.cvpr_probe import build_target, extract_abstract, extraction_rate_exceeded


TITLE = "Velvet Widget Counting with Amber Baskets"
ABSTRACT = "This invented example counts velvet widgets inside amber baskets."
GAP = " " * 8


def columns(*rows: tuple[str, str]) -> str:
    return "\n".join(left + (GAP + right if right else "") for left, right in rows) + "\n"


def manuscript(body: str, title: str = TITLE) -> str:
    return f"{title}\n\nAvery Example\nSynthetic Academy\n\nAbstract\n{ABSTRACT}\n1 Introduction\n{body}"


class TwoColumnAbstractTests(unittest.TestCase):
    def test_blank_lines_are_soft_breaks(self) -> None:
        text = columns(
            (TITLE, ""),
            ("", ""),
            ("Abstract", "1 Introduction"),
            ("This invented", "The right column is unrelated."),
            (" \t ", ""),
            ("example counts velvet widgets", "2 Methods"),
            ("", ""),
            ("inside amber baskets.", "Keep this column out."),
            ("1 Introduction", "The opposite column continues."),
            ("Do not include this body sentence.", ""),
        )
        self.assertEqual(extract_abstract(text), ABSTRACT)

    def test_numbered_headings_stop_the_abstract(self) -> None:
        for heading in ("Introduction", "INTRODUCTION", "1. Introduction", "2 Methods", "2.3. Setup", "2. methods"):
            with self.subTest(heading=heading):
                text = columns(("Abstract", ""), (ABSTRACT, "Other material."), (heading, "More material."))
                self.assertEqual(extract_abstract(text), ABSTRACT)

    def test_word_cap_includes_inline_lead(self) -> None:
        for heading in ("Abstract\n", "Abstract. "):
            with self.subTest(heading=heading):
                text = heading + " ".join(["synthetic"] * 450) + "\nExtra text follows.\n"
                self.assertEqual(len(extract_abstract(text).split()), 400)

    def test_invalid_layout_abstract_uses_raw_fallback(self) -> None:
        layout = columns((TITLE, ""), ("", ""), ("Abstract", ""), ("Too short", ""), ("1 Introduction", ""))
        raw = manuscript("")
        self.assertEqual(extract_abstract(layout), "")
        self.assertEqual(extract_abstract(layout, alternate=raw), ABSTRACT)

    def test_invalid_sources_still_fail_closed(self) -> None:
        record, reason = build_target("synthetic-missing", 2024, TITLE + "\n\nAbstract\nTiny\n1 Introduction\n")
        self.assertIsNone(record)
        self.assertEqual(reason, "no_abstract")
        self.assertFalse(extraction_rate_exceeded(1, 20))
        self.assertTrue(extraction_rate_exceeded(1, 19))


class TwoColumnTitleExtractionTests(unittest.TestCase):
    def test_compact_preamble_does_not_become_an_overlong_title(self) -> None:
        raw = columns(
            (TITLE, ""),
            ("Avery Example", "Briar Placeholder"),
            ("Synthetic Academy " + "Laboratory " * 28, ""),
            ("", ""),
            ("Abstract", "1 Introduction"),
            (ABSTRACT, "Unrelated prose."),
            ("1 Introduction", ""),
        )
        record, reason = build_target("synthetic-compact", 2024, raw)
        self.assertIsNone(reason)
        assert record is not None
        self.assertEqual(record["title"], TITLE)
        self.assertIn("Example", record["surnames"])

    def test_wrapped_title_survives_compact_author_columns(self) -> None:
        raw = manuscript("").replace(TITLE + "\n\nAvery Example", "Velvet Widget Counting\nwith Amber Baskets\nAvery Example" + GAP + "Briar Placeholder")
        self.assertEqual(extract_meta(raw)["title"], TITLE)

    def test_subtitle_after_blank_line_remains_part_of_title(self) -> None:
        raw = manuscript("", "Velvet Widgets:\n\nCounting with Amber Baskets")
        self.assertEqual(extract_meta(raw)["title"], "Velvet Widgets: Counting with Amber Baskets")

    def test_unusable_title_can_fall_back_without_relaxing_limit(self) -> None:
        layout = manuscript("", " ".join(["Synthetic"] * 45))
        record, reason = build_target("synthetic-title-fallback", 2024, layout, alternate=manuscript(""))
        self.assertIsNone(reason)
        assert record is not None
        self.assertEqual(record["title"], TITLE)
        record, reason = build_target("synthetic-title-missing", 2024, layout)
        self.assertIsNone(record)
        self.assertEqual(reason, "no_title")


class TwoColumnReferenceTests(unittest.TestCase):
    def test_anonymization_keeps_body_venue_markers_fail_closed(self) -> None:
        body = columns(
            ("CVPR 2024", ""),
            ("The body mentions CVPR 2025 in a sentence.", "Ordinary body prose."),
            ("References", ""),
            ("[1] A. Example. Toy widgets. CVPR 2026.", ""),
        )
        for variant in ("A1", "A2"):
            with self.subTest(variant=variant):
                output, _meta, counts = anonymize(manuscript(body), variant)
                self.assertEqual(counts["venue_marker"], 1)
                self.assertTrue(leak_failed(counts))
                self.assertIn("The body mentions CVPR 2025", output)
                self.assertNotIn("CVPR 2024", output)
                self.assertIn("CVPR 2026", output)

    def test_heading_can_share_line_with_other_column(self) -> None:
        for heading in ("References", "REFERENCES", "references", "Bibliography", "8. Bibliography"):
            with self.subTest(heading=heading):
                body = columns(
                    ("Body mentions CVPR 2024 in a sentence.", ""),
                    (heading, "The other column still mentions CVPR 2025."),
                    ("[1] A. Example. Invented widgets. CVPR 2024.", "Other prose remains body text."),
                )
                counts = leak_counts(body, {})
                self.assertEqual(counts["venue_marker"], 2)
                self.assertTrue(leak_failed(counts))

    def test_reference_heading_in_right_column_preserves_left_body(self) -> None:
        text = columns(
            ("The body cites CVPR 2024 here.", "References"),
            ("The body also cites CVPR 2025 here.", "[1] A. Example. Toy baskets. CVPR 2024."),
        )
        self.assertEqual(leak_counts(text, {})["venue_marker"], 2)

    def test_both_reference_columns_are_exempt(self) -> None:
        text = columns(
            ("References", "Bibliography"),
            ("[1] A. Example. Toy baskets. CVPR 2024.", "[2] B. Placeholder. Toy widgets. CVPR 2025."),
        )
        for variant in ("A1", "A2"):
            with self.subTest(variant=variant):
                output, _meta, counts = anonymize(manuscript(text), variant)
                self.assertEqual(counts["venue_marker"], 0)
                self.assertIn("A. Example", output)
                self.assertIn("CVPR 2024", output)

    def test_dense_dated_citations_without_heading(self) -> None:
        for prefix in ("[{number}]", "{number}."):
            with self.subTest(prefix=prefix):
                rows = [("The body mentions CVPR 2024 here.", "Unrelated body text.")]
                for number in range(1, 5):
                    rows.extend(
                        [
                            (prefix.format(number=number) + " A. Example. Invented basket study.", "Body prose without a venue."),
                            ("Proceedings of CVPR 2025.", ""),
                        ]
                    )
                self.assertEqual(leak_counts(columns(*rows), {})["venue_marker"], 1)

    def test_sparse_or_undated_body_citations_are_not_references(self) -> None:
        cases = (
            "[1] A body sentence mentions CVPR 2024.\n[2] Another cites CVPR 2025.\n",
            "[1] First body item.\n[2] Second body item.\n[3] Third body item.\nIEEE Xplore is mentioned here.\n",
            "[1] A body citation mentions CVPR 2024.\n" + "A body sentence.\n" * 20 + "[2] CVPR 2025.\n[3] CVPR 2026.\n",
            "References to CVPR 2024 remain in the body.\n",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertGreater(leak_counts(text, {})["venue_marker"], 0)

    def test_body_after_references_is_not_exempt(self) -> None:
        text = "References\n[1] A. Example. Toy study. CVPR 2024.\nAppendix\nA body sentence cites CVPR 2025.\n"
        self.assertEqual(leak_counts(text, {})["venue_marker"], 1)

    def test_earlier_reference_column_stays_exempt_after_reassembly(self) -> None:
        body = columns(
            ("The body mentions CVPR 2024 here.", "[7] A. Example. Toy baskets. CVPR 2025."),
            ("More body prose remains.", "[8] A. Example. Toy widgets. CVPR 2025."),
            ("References", "[9] A. Example. Toy crates. CVPR 2025."),
            ("[1] A. Example. Invented containers. CVPR 2025.", ""),
        )
        for variant in ("A1", "A2"):
            with self.subTest(variant=variant):
                output, _meta, counts = anonymize(manuscript(body), variant)
                self.assertEqual(counts["venue_marker"], 1)
                self.assertEqual(counts["author_surname"], 0)
                self.assertIn("[7] A. Example", output)

    def test_dated_paginated_continuation_before_dense_column(self) -> None:
        body = columns(
            ("References", "CVPR 2024, pages 101-109, 2024. 2"),
            ("[1] A. Example. Toy baskets. CVPR 2025.", "[7] A. Example. Toy widgets. CVPR 2025."),
            ("[2] A. Example. Toy crates. CVPR 2025.", "[8] A. Example. Toy boxes. CVPR 2025."),
            ("[3] A. Example. Toy containers. CVPR 2025.", "[9] A. Example. Toy parcels. CVPR 2025."),
        )
        for variant in ("A1", "A2"):
            with self.subTest(variant=variant):
                output, _meta, counts = anonymize(manuscript(body), variant)
                self.assertFalse(leak_failed(counts))
                self.assertIn("CVPR 2024, pages 101-109", output)

    def test_ordinary_prose_before_dense_column_is_not_exempt(self) -> None:
        body = columns(
            ("References", "The body mentions CVPR 2024 here."),
            ("[1] A. Example. Toy baskets. CVPR 2025.", "[7] A. Example. Toy widgets. CVPR 2025."),
            ("[2] A. Example. Toy crates. CVPR 2025.", "[8] A. Example. Toy boxes. CVPR 2025."),
            ("[3] A. Example. Toy containers. CVPR 2025.", "[9] A. Example. Toy parcels. CVPR 2025."),
        )
        self.assertEqual(leak_counts(body, {})["venue_marker"], 1)


class TwoColumnTitleMaskTests(unittest.TestCase):
    def test_title_split_between_rows_of_one_column(self) -> None:
        for side in (0, 1):
            with self.subTest(side=side):
                rows = [("Velvet Widget", "Unrelated comparison remains."), ("Counting with Amber Baskets", "Second comparison remains.")]
                if side:
                    rows = [(right, left) for left, right in rows]
                body = columns(*rows)
                self.assertGreater(leak_counts(body, {"title": TITLE})["title_4gram"], 0)
                output, _meta, counts = anonymize(manuscript(body), "A1")
                self.assertEqual(counts["title_4gram"], 0)
                self.assertNotIn("Velvet Widget", output)
                self.assertIn("Unrelated comparison remains.", output)
                self.assertIn("Second comparison remains.", output)

    def test_small_caps_and_subtitles_are_masked(self) -> None:
        body = "V ELVET W IDGET C OUNTING WITH A MBER B ASKETS\n"
        for title, repeated in ((TITLE, body), ("Velvet Widgets: Counting with Amber Baskets", "COUNTING WITH AMBER BASKETS\n")):
            with self.subTest(title=title):
                self.assertGreater(leak_counts(repeated, {"title": title})["title_4gram"], 0)
                output, _meta, counts = anonymize(manuscript(repeated, title), "A1")
                self.assertEqual(counts["title_4gram"], 0)
                self.assertNotIn(repeated.strip(), output)

    def test_running_headers_beyond_preamble_are_masked(self) -> None:
        body = "Ordinary synthetic discussion.\n" * 150 + "\f" + TITLE + "\n"
        output, _meta, counts = anonymize(manuscript(body), "A1")
        self.assertGreater(len(output), PREAMBLE_CHARS)
        self.assertNotIn(TITLE, output)
        self.assertEqual(counts["title_4gram"], 0)

    def test_masking_cannot_shift_an_unmasked_title_into_scan_window(self) -> None:
        for body in ((TITLE + "\n") * 90, TITLE + "\n" * 3500 + TITLE):
            with self.subTest(characters=len(body)):
                output, _meta, counts = anonymize(manuscript(body), "A1")
                self.assertEqual(counts["title_4gram"], 0)
                self.assertNotIn(TITLE, output)

    def test_hyphenated_title_across_column_rows_preserves_neighbors(self) -> None:
        body = columns(
            ("Vel-", "The neighboring sentence stays."),
            ("vet Widget Counting with Amber Baskets", "Another neighboring sentence stays."),
        )
        output, _meta, counts = anonymize(manuscript(body), "A1")
        self.assertEqual(counts["title_4gram"], 0)
        self.assertNotIn("Vel-", output)
        self.assertNotIn("vet Widget", output)
        self.assertIn("The neighboring sentence stays.", output)
        self.assertIn("Another neighboring sentence stays.", output)

    def test_short_fragments_do_not_lower_four_word_threshold(self) -> None:
        self.assertEqual(TITLE_NGRAM, 4)
        self.assertEqual(PREAMBLE_CHARS, 3000)
        text = "Velvet Widget Counting appears as a short fragment.\n"
        self.assertEqual(leak_counts(text, {"title": TITLE})["title_4gram"], 0)


if __name__ == "__main__":
    unittest.main()
