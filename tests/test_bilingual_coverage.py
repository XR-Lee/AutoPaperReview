from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopaperreview.bilingual_coverage import markdown_bilingual_gaps, require_bilingual_markdown
from autopaperreview.i18n import MissingTranslationError, translate_text
from autopaperreview.migration import load_review_input
from autopaperreview.pdf_report import _tokens, reportlab_available, write_report_pdf
from autopaperreview.reporting import render_markdown

from tests.support import make_artifact


class BilingualCoverageTests(unittest.TestCase):
    def test_synthetic_bilingual_markdown_has_no_gaps(self) -> None:
        package = load_review_input(
            Path("examples/synthetic/review_input.json"),
            manuscript=make_artifact(),
            project_id="synthetic-tracking-review",
        )
        markdown = render_markdown(package, bilingual=True)
        self.assertEqual(markdown_bilingual_gaps(markdown), [])

    def test_unpaired_english_issue_is_a_gap(self) -> None:
        markdown = """# Review Report: demo

- Languages: en, zh-Hans

## Critical Issues / 严重问题

### R01 Growth reference is still circular

**Evidence.** Ablations use the same manually reviewed projections as the matcher comparison.
"""
        gaps = markdown_bilingual_gaps(markdown)
        self.assertTrue(any("English-only prose" in item for item in gaps))
        with self.assertRaises(MissingTranslationError):
            require_bilingual_markdown(markdown)

    def test_paired_language_prefixes_are_complete(self) -> None:
        markdown = """# Review Report: demo

- Languages: en, zh-Hans

## Strengths / 优点

- **en:** The pipeline is complete rather than a lone detector.
- **zh-Hans:** 这是完整流水线，而不是单独的检测器。
"""
        self.assertEqual(markdown_bilingual_gaps(markdown), [])

    def test_language_blocks_are_not_false_positives(self) -> None:
        markdown = """# Review Report: demo

- Languages: en, zh-Hans

## Weaknesses / 不足

### en

- Growth truth still comes from manually checked projections of the same geometric family.

### zh-Hans

- 增长真值仍来自同一套几何映射的人工核对投影。
"""
        self.assertEqual(markdown_bilingual_gaps(markdown), [])

    def test_unpaired_chinese_issue_is_a_gap(self) -> None:
        markdown = """# Review Report: demo

- Languages: en, zh-Hans

## Summary / 摘要

未给出总体分数。七个维度各自按一到十打分，结论按会议或期刊分别给出。
"""
        gaps = markdown_bilingual_gaps(markdown)
        self.assertTrue(any("Chinese-only prose" in item for item in gaps))

    def test_mismatched_en_zh_prefix_counts_are_gaps(self) -> None:
        markdown = """# Review Report: demo

- Languages: en, zh-Hans

## Weaknesses / 不足

- **en:** Growth truth is still circular.
"""
        gaps = markdown_bilingual_gaps(markdown)
        self.assertTrue(any("**en:**" in item for item in gaps))

    def test_long_unknown_english_is_not_word_salad_filled(self) -> None:
        with self.assertRaises(MissingTranslationError):
            translate_text(
                "Growth truth is still built from manually checked projections "
                "of the same geometric family being compared in the ablation.",
                source="en",
                target="zh-Hans",
            )

    def test_pdf_tokenizer_does_not_merge_language_lines(self) -> None:
        markdown = (
            "**en:** The rewrite is real, not cosmetic.\n"
            "**zh-Hans:** 这轮不是只改措辞。\n"
        )
        kinds = [kind for kind, _, _ in _tokens(markdown)]
        self.assertEqual(kinds, ["langline", "langline"])


class BilingualPdfGateTests(unittest.TestCase):
    @unittest.skipUnless(reportlab_available(), "reportlab extra not installed")
    def test_pdf_refuses_incomplete_bilingual_markdown(self) -> None:
        markdown = """# Review Report: demo

- Languages: en, zh-Hans

## Summary / 摘要

This 2026-09-04 revision repairs several protocol defects from the August review
without a matching Chinese paragraph.
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.pdf"
            with self.assertRaises(MissingTranslationError):
                write_report_pdf(markdown, path, title="Incomplete")
