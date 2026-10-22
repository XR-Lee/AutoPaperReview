"""Body-only export, appendix files, readability, language tags, and PDF markers."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from autopaperreview.annotate_pdf import pymupdf_available, write_annotated_pdf
from autopaperreview.i18n import MissingTranslationError
from autopaperreview.markers import assign_display_numbers, build_markers
from autopaperreview.models import (
    Anchor,
    AnchorKind,
    EvidenceRecord,
    LocalizedText,
    ReviewPackage,
    SourceRecord,
)
from autopaperreview.hashing import sha256_file
from autopaperreview.pipeline import run_pipeline
from autopaperreview.prose_edit import edit_body, measure_prose
from autopaperreview.reporting import render_appendix, render_markdown
from autopaperreview.translation import apply_translator, hook_error, parse_export_lang
from tests.support import localized, make_artifact, make_issue


def _both(english: str, chinese: str) -> LocalizedText:
    return LocalizedText(primary=english, language="en", translations={"zh-Hans": chinese})


def _package() -> ReviewPackage:
    source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="paper.pdf")
    external = SourceRecord(
        id="S-ext",
        kind="retrieved-abstract",
        title="Other paper",
        locator="https://example.invalid/paper",
    )
    in_paper = EvidenceRecord(
        id="E3",
        source_id="S1",
        locator="page 5",
        claim="The toy split is not independent.",
        excerpt="The toy split is not independent.",
        anchor=Anchor(kind=AnchorKind.pdf_region, display="p. 5", page=5, excerpt="The toy split is not independent."),
    )
    outside = EvidenceRecord(
        id="R1",
        source_id="S-ext",
        locator="https://example.invalid/paper",
        claim="An external abstract that must not enter the body.",
        excerpt="External abstract text that is long enough to notice.",
        retrieval_query="toy tracker",
    )
    return ReviewPackage(
        project_id="export-layout",
        manuscript=make_artifact(),
        sources=[source, external],
        evidence=[in_paper, outside],
        issues=[
            make_issue(
                id="I3",
                evidence_ids=["E3", "R1"],
                source_ids=["S1"],
                title=_both("The split does not support the claim", "该划分不能支持这一主张。"),
                evidence=_both("Only one run is reported.", "只报告了一次运行。"),
                impact=_both("Stability is unknown.", "稳定性未知。"),
                required_action=_both("Report repeated runs.", "报告重复运行。"),
            )
        ],
        summary=_both(
            "In order to deploy the tracker, the authors report one number.",
            "为了部署跟踪器，作者只报告了一个数字。",
        ),
    )


class LanguageSelectionTests(unittest.TestCase):
    def test_lang_tags(self) -> None:
        self.assertEqual(parse_export_lang("en"), ["en"])
        self.assertEqual(parse_export_lang("zh-hans"), ["zh-Hans"])
        self.assertEqual(parse_export_lang("bilingual"), ["en", "zh-Hans"])
        self.assertEqual(parse_export_lang("fr"), ["fr"])
        self.assertEqual(parse_export_lang("pt-br"), ["pt-BR"])
        with self.assertRaises(ValueError):
            parse_export_lang("not a tag")

    def test_other_language_names_the_hook(self) -> None:
        message = str(hook_error("fr"))
        self.assertIn("translate_package", message)
        self.assertIn("bilingual = true", message)


class BodyAppendixTests(unittest.TestCase):
    def test_default_body_cites_pages_and_omits_external_text(self) -> None:
        package = _package()
        markdown = render_markdown(package, language="en")
        self.assertIn("[[E1, p.5](#marker-e1)]", markdown)
        self.assertIn('id="marker-e1"', markdown)
        self.assertNotIn("E3", markdown)
        self.assertNotIn("`I3`", markdown)
        self.assertNotRegex(markdown, r"\b[CI]-[A-Za-z0-9]")
        self.assertNotIn("External abstract text", markdown)
        self.assertNotIn("## Evidence Index", markdown)
        self.assertNotIn("## Retrieved Sources", markdown)
        self.assertNotIn("## Related-Work Queries", markdown)
        self.assertNotIn("## Claim Ledger", markdown)
        summary = render_appendix(package, language="en", level="summary")
        self.assertIn("Retrieved sources: 0", summary)
        self.assertIn("`R1` external", summary)
        self.assertNotIn("External abstract text", summary)
        full = render_appendix(package, language="en", level="full")
        self.assertIn("External abstract text", full)
        self.assertIn("## Evidence Catalog", full)

    def test_report_stage_keeps_appendix_out_of_the_body(self) -> None:
        package = _package()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manuscript = workspace / "manuscript.txt"
            manuscript.write_text("Synthetic manuscript.\n", encoding="utf-8")
            package.manuscript = make_artifact(
                sha256=sha256_file(manuscript),
                size_bytes=manuscript.stat().st_size,
            )
            (workspace / "package.json").write_text(package.model_dump_json(), encoding="utf-8")
            config = workspace / "review.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "export-layout"
                    title = "Export layout"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    default_language = "zh-Hans"
                    network_policy = "deny"

                    [[stages]]
                    id = "issues"
                    type = "import_issues"
                    [stages.params]
                    path = "package.json"

                    [[stages]]
                    id = "report"
                    type = "report"
                    depends_on = ["issues"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "report"
            body = (stage_dir / "review_report.md").read_text(encoding="utf-8")
            summary = __import__("json").loads((stage_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["languages"], ["en"])
            self.assertEqual(summary["appendix"], "none")
            self.assertFalse((stage_dir / "review_appendix.md").exists())
            self.assertNotIn("External abstract text", body)
            self.assertIn("readability", summary)
            self.assertIn("before", summary["readability"])
            self.assertIn("after", summary["readability"])

            second = run_pipeline(config, report_lang="zh-Hans", report_appendix="full")
            self.assertEqual(second.stage("issues").metadata.get("cache_hit"), True)
            zh_dir = workspace / ".runs" / second.run_id / "stages" / "report"
            zh_body = (zh_dir / "review_report.md").read_text(encoding="utf-8")
            appendix = (zh_dir / "review_appendix.md").read_text(encoding="utf-8")
            self.assertNotIn("External abstract text", zh_body)
            self.assertIn("为了部署跟踪器", zh_body)
            self.assertIn("External abstract text", appendix)


class ReadabilityTests(unittest.TestCase):
    def test_wordy_prose_gets_shorter(self) -> None:
        source = "\n".join(
            [
                "# Review: toy",
                "",
                "## Summary",
                "",
                "In order to explain the result, it is important to note that a large number of runs were used.",
                "",
                "> In order to explain the result, keep this quote.",
                "",
            ]
        )
        edited, report = edit_body(source, language="en")
        self.assertIn("To explain the result, many runs were used.", edited)
        self.assertIn("> In order to explain the result, keep this quote.", edited)
        self.assertLess(report["after"]["word_count"], report["before"]["word_count"])
        self.assertIn("in order to", report["edits"])
        metrics = measure_prose(edited, language="en")
        self.assertEqual(metrics["structure_present"][0], "Summary")

    def test_decimals_stay_one_sentence_and_cjk_periods_split(self) -> None:
        english = measure_prose("The score is 8.0/10. The next sentence starts here.", language="en")
        self.assertEqual(english["sentence_count"], 2)
        edited, _report = edit_body("The score is 8.0/10. The next sentence starts here.", language="en")
        self.assertIn("8.0/10", edited)
        chinese = measure_prose("第一句说完了。第二句也说完了。", language="zh-Hans")
        self.assertEqual(chinese["sentence_count"], 2)

    def test_long_sentences_split_and_semicolons_become_bullets(self) -> None:
        words = " ".join(f"word{index}" for index in range(40))
        edited, report = edit_body(f"## Summary\n\n{words}.", language="en")
        metrics = measure_prose(edited, language="en")
        self.assertEqual(metrics["long_sentence_count"], 0)
        self.assertLessEqual(metrics["max_sentence_words"], 35)
        self.assertIn("long_sentence_split", report["edits"])
        bullets, bullet_report = edit_body(
            "The split is weak; the baseline is privileged; the claim is unsupported.",
            language="en",
        )
        self.assertGreaterEqual(bullets.count("\n- "), 2)
        self.assertIn("semicolon_bullets", bullet_report["edits"])
        cite = "[[E3, p.4](#marker-e3)]"
        long_bullet = "- " + " ".join(f"word{index}" for index in range(40)) + f" {cite}"
        kept, _kept_report = edit_body(long_bullet, language="en")
        self.assertIn(cite, kept)
        self.assertNotIn("marker-.", kept)


class TranslatorHookTests(unittest.TestCase):
    def test_callable_translator_fills_a_bcp47_tag(self) -> None:
        def translate_package(package: ReviewPackage, language: str, **_options: object) -> ReviewPackage:
            issue = package.issues[0]
            title = issue.title.model_copy(update={"translations": {language: "Titre traduit."}})
            return package.model_copy(update={"issues": [issue.model_copy(update={"title": title})]})

        import sys
        import types

        module = types.ModuleType("export_translator_fixture")
        module.translate_package = translate_package
        sys.modules["export_translator_fixture"] = module
        package = _package()
        updated = apply_translator("export_translator_fixture:translate_package", package, "fr", {})
        self.assertEqual(updated.issues[0].title.get("fr"), "Titre traduit.")
        with self.assertRaises(MissingTranslationError):
            render_markdown(package, language="fr", fill_missing=False)


@unittest.skipUnless(pymupdf_available(), "pymupdf extra not installed")
class AnnotatedPdfTests(unittest.TestCase):
    def test_highlights_the_cited_sentence(self) -> None:
        import pymupdf

        from autopaperreview.annotate_pdf import locate_markers

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 120), "The toy split is not independent.")
            document.save(pdf)
            document.close()
            package = _package()
            markers = assign_display_numbers(package, build_markers(package))
            markers["E3"] = markers["E3"].with_page(None)
            self.assertEqual(markers["E3"].label, "E1")
            located = locate_markers(pdf, markers)
            self.assertEqual(located["E3"].page, 1)
            self.assertEqual(located["E3"].label, "E1")
            output = root / "annotated.pdf"
            info = write_annotated_pdf(pdf, located, output)
            self.assertGreaterEqual(info["markers_placed"], 1)
            saved = pymupdf.open(output)
            try:
                self.assertGreaterEqual(saved.page_count, 2)
                self.assertGreaterEqual(len(list(saved[0].annots() or [])), 1)
                self.assertIn("E1", saved[0].get_text())
            finally:
                saved.close()


if __name__ == "__main__":
    unittest.main()
