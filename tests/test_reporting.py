from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from autopaperreview.hashing import sha256_file
from autopaperreview.i18n import MissingTranslationError, contains_cjk, ensure_package_languages
from autopaperreview.migration import load_review_input
from autopaperreview.models import (
    DimensionScore,
    EvidenceRecord,
    LocalizedText,
    ReviewClaim,
    ReviewClaimKind,
    ReviewPackage,
    SarDimension,
    SourceRecord,
)
from autopaperreview.pipeline import run_pipeline
from autopaperreview.pdf_report import _markup, reportlab_available, write_report_pdf
from autopaperreview.reporting import record_anchor, render_markdown

from tests.support import localized, make_artifact, make_issue


def _source_and_evidence() -> tuple[SourceRecord, EvidenceRecord]:
    source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="manuscript.txt")
    evidence = EvidenceRecord(id="E1", source_id="S1", locator="Results", claim="One accuracy value")
    return source, evidence


def _package_with_english_only_rationale() -> ReviewPackage:
    source, evidence = _source_and_evidence()
    return ReviewPackage(
        project_id="report-test",
        manuscript=make_artifact(),
        sources=[source],
        evidence=[evidence],
        issues=[make_issue(evidence_ids=["E1"], source_ids=["S1"])],
        summary=localized("Legacy summary without an evidence suffix."),
        claims=[
            ReviewClaim(
                id="C-summary",
                kind=ReviewClaimKind.summary,
                text=localized("A short paper claims deployment readiness."),
                evidence_ids=["E1"],
            ),
            ReviewClaim(
                id="C-strength",
                kind=ReviewClaimKind.strength,
                text=localized("The manuscript states a concrete deployment-oriented objective."),
                evidence_ids=["E1"],
            ),
            ReviewClaim(
                id="C-weakness",
                kind=ReviewClaimKind.weakness,
                text=localized(
                    "The evaluation does not establish sequence independence, so the deployment claim is unsupported."
                ),
                evidence_ids=["E1"],
            ),
            ReviewClaim(
                id="C-question",
                kind=ReviewClaimKind.question,
                text=localized("Where is the specimen-level split?"),
                evidence_ids=["E1"],
            ),
        ],
        dimension_scores=[
            DimensionScore(
                dimension=SarDimension.claims_supported,
                score=2.0,
                rationale=localized("The claim is not supported."),
                evidence_ids=["E1"],
            )
        ],
    )


class LocalizedTextLookupTests(unittest.TestCase):
    def test_get_does_not_fall_back_to_primary(self) -> None:
        text = LocalizedText(primary="Hello", language="en")
        self.assertIsNone(text.get("zh-Hans"))
        self.assertFalse(text.has_language("zh-Hans"))
        self.assertEqual(text.resolve("zh-Hans"), "Hello")


class BilingualRenderTests(unittest.TestCase):
    def test_monolingual_render_stays_single_language(self) -> None:
        package = _package_with_english_only_rationale()
        markdown = render_markdown(package, language="en")
        self.assertIn("## Summary", markdown)
        self.assertIn("A short paper claims deployment readiness. [[E1](#record-e1)]", markdown)
        self.assertIn("## Dimension Scores", markdown)
        self.assertIn("The claim is not supported.", markdown)
        self.assertNotIn("### en", markdown)
        self.assertNotIn("### zh-Hans", markdown)
        self.assertNotIn("## Summary / 摘要", markdown)
        self.assertNotIn("- Languages:", markdown)

    def test_monolingual_zh_hans_may_still_resolve_english_primary(self) -> None:
        package = _package_with_english_only_rationale()
        markdown = render_markdown(package, language="zh-Hans")
        self.assertIn("The claim is not supported.", markdown)
        self.assertNotIn("### zh-Hans", markdown)

    def test_bilingual_render_includes_en_and_zh_hans_section_by_section(self) -> None:
        package = load_review_input(
            Path("examples/synthetic/review_input.json"),
            manuscript=make_artifact(),
            project_id="synthetic-tracking-review",
        )
        markdown = render_markdown(package, bilingual=True)
        via_languages = render_markdown(package, languages=["en", "zh-Hans"])
        self.assertEqual(markdown, via_languages)
        self.assertEqual(markdown, render_markdown(package, bilingual=True, fill_missing=False))

        self.assertIn("- Languages: en, zh-Hans", markdown)
        self.assertIn("## Summary / 摘要", markdown)
        self.assertIn("## Strengths / 优点", markdown)
        self.assertIn("## Weaknesses / 不足", markdown)
        self.assertIn("## Questions / 问题", markdown)
        self.assertIn("## Dimension Scores / 维度评分", markdown)
        self.assertIn("### en", markdown)
        self.assertIn("### zh-Hans", markdown)

        self.assertIn(
            "The paper reports 95% accuracy on two held-out images from the same specimen",
            markdown,
        )
        self.assertIn("论文在同一试件的两张留出图像上报告 95% 准确率", markdown)
        self.assertIn("The manuscript states a concrete deployment-oriented objective.", markdown)
        self.assertIn("稿件提出了明确的部署导向目标。", markdown)
        self.assertIn("The evaluation does not establish sequence independence", markdown)
        self.assertIn("评估没有建立序列独立性", markdown)
        self.assertIn("What is the specimen-level accuracy", markdown)
        self.assertIn("在真正留出的序列上，带重复种子的试件级准确率是多少？", markdown)

        for dimension in SarDimension:
            with self.subTest(dimension=dimension.value):
                self.assertIn(f"**{dimension.value}:**", markdown)

        self.assertIn("The tracking objective is concrete but the method is not compared", markdown)
        self.assertIn("跟踪目标具体，但本夹具中的方法未与已有工作进行比较。", markdown)
        self.assertIn("The image-level split does not establish sequence independence", markdown)
        self.assertIn("图像级拆分没有建立序列独立性", markdown)
        self.assertIn("**Evidence / 证据.**", markdown)
        self.assertGreaterEqual(markdown.count("### en"), 3)
        self.assertGreaterEqual(markdown.count("### zh-Hans"), 3)

        self.assertIsNone(package.overall_score)
        self.assertIn("No overall score is assigned", markdown)
        self.assertIn("未给出总体分数", markdown)
        self.assertIn("## Evidence Index / 证据索引", markdown)
        self.assertIn("[E1](#record-e1)", markdown)
        self.assertIn('id="record-e1"', markdown)
        self.assertIn("`E1`", markdown)
        self.assertIn("## Venue Conclusions / 会议与期刊结论", markdown)
        self.assertIn("#### ICLR", markdown)
        self.assertIn("#### TMLR", markdown)
        self.assertIn("`reject`", markdown)
        self.assertIn("`major_revision`", markdown)
        self.assertNotIn("**Score:**", markdown)

    def test_bilingual_render_fills_english_only_dimension_rationale(self) -> None:
        package = _package_with_english_only_rationale()
        self.assertIsNone(package.dimension_scores[0].rationale.get("zh-Hans"))

        filled = ensure_package_languages(package, ["en", "zh-Hans"])
        rationale = filled.dimension_scores[0].rationale
        self.assertIsNotNone(rationale)
        zh = rationale.get("zh-Hans")
        self.assertIsNotNone(zh)
        self.assertTrue(contains_cjk(zh or ""))
        self.assertNotEqual(zh, rationale.primary)

        markdown = render_markdown(package, bilingual=True)
        self.assertIn("The claim is not supported.", markdown)
        self.assertIn("该主张缺少支持。", markdown)
        self.assertIn("A short paper claims deployment readiness.", markdown)
        self.assertIn("这篇短文声称已具备部署条件。", markdown)
        self.assertIn("The manuscript states a concrete deployment-oriented objective.", markdown)
        self.assertIn("稿件提出了明确的部署导向目标。", markdown)
        self.assertIn("评估没有建立序列独立性", markdown)
        self.assertIn("试件级拆分在哪里？", markdown)
        self.assertIn("评估不完整", markdown)

    def test_bilingual_render_without_fill_refuses_silent_fallback(self) -> None:
        package = _package_with_english_only_rationale()
        with self.assertRaisesRegex(MissingTranslationError, "silent"):
            render_markdown(package, bilingual=True, fill_missing=False)

    def test_synthetic_fixture_dimension_rationales_are_not_english_only(self) -> None:
        payload = json.loads(Path("examples/synthetic/review_input.json").read_text(encoding="utf-8"))
        scores = payload["dimension_scores"]
        self.assertEqual(len(scores), 7)
        seen = {item["dimension"] for item in scores}
        self.assertEqual(seen, {dimension.value for dimension in SarDimension})
        for item in scores:
            with self.subTest(dimension=item["dimension"]):
                rationale = item["rationale"]
                zh = rationale["translations"]["zh-Hans"]
                self.assertTrue(contains_cjk(zh))
                self.assertNotEqual(zh, rationale["primary"])
                self.assertTrue(any("A" <= ch <= "z" for ch in rationale["primary"]))


class BilingualReportStageTests(unittest.TestCase):
    def test_report_stage_bilingual_param_writes_one_file_with_both_languages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manuscript = workspace / "manuscript.txt"
            manuscript.write_text("Synthetic manuscript.\n", encoding="utf-8")
            source, evidence = _source_and_evidence()
            package = ReviewPackage(
                project_id="bilingual-report",
                manuscript=make_artifact(
                    sha256=sha256_file(manuscript),
                    size_bytes=manuscript.stat().st_size,
                ),
                sources=[source],
                evidence=[evidence],
                issues=[make_issue(evidence_ids=["E1"], source_ids=["S1"])],
                claims=[
                    ReviewClaim(
                        id="C-summary",
                        kind=ReviewClaimKind.summary,
                        text=localized("A short paper claims deployment readiness."),
                        evidence_ids=["E1"],
                    ),
                    ReviewClaim(
                        id="C-strength",
                        kind=ReviewClaimKind.strength,
                        text=localized("The manuscript states a concrete deployment-oriented objective."),
                        evidence_ids=["E1"],
                    ),
                    ReviewClaim(
                        id="C-weakness",
                        kind=ReviewClaimKind.weakness,
                        text=localized(
                            "The evaluation does not establish sequence independence, so the deployment claim is unsupported."
                        ),
                        evidence_ids=["E1"],
                    ),
                    ReviewClaim(
                        id="C-question",
                        kind=ReviewClaimKind.question,
                        text=localized("Where is the specimen-level split?"),
                        evidence_ids=["E1"],
                    ),
                ],
                dimension_scores=[
                    DimensionScore(
                        dimension=dimension,
                        score=3.0,
                        rationale=localized("The claim is not supported."),
                        evidence_ids=["E1"],
                    )
                    for dimension in SarDimension
                ],
            )
            (workspace / "package.json").write_text(
                json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "bilingual-report"
                    title = "Bilingual report"
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
                    [stages.params]
                    bilingual = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            manifest = run_pipeline(config_path)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "report"
            markdown = (stage_dir / "review_report.md").read_text(encoding="utf-8")
            released = ReviewPackage.model_validate_json(
                (stage_dir / "review_package.json").read_text(encoding="utf-8")
            )
            summary = json.loads((stage_dir / "summary.json").read_text(encoding="utf-8"))

            self.assertTrue(summary["bilingual"])
            self.assertEqual(summary["languages"], ["en", "zh-Hans"])
            self.assertIn("### en", markdown)
            self.assertIn("### zh-Hans", markdown)
            self.assertIn("A short paper claims deployment readiness.", markdown)
            self.assertIn("这篇短文声称已具备部署条件。", markdown)
            self.assertIn("The manuscript states a concrete deployment-oriented objective.", markdown)
            self.assertIn("稿件提出了明确的部署导向目标。", markdown)
            self.assertIn("评估没有建立序列独立性", markdown)
            self.assertIn("试件级拆分在哪里？", markdown)
            self.assertEqual(len(released.dimension_scores), 7)
            for score in released.dimension_scores:
                zh = score.rationale.get("zh-Hans") if score.rationale else None
                self.assertTrue(zh and contains_cjk(zh))
                self.assertIn("该主张缺少支持。", markdown)
                self.assertIn("The claim is not supported.", markdown)
            self.assertNotIn("Spearman", markdown)
            self.assertEqual((stage_dir / "review_report.md").stat().st_size, len(markdown.encode("utf-8")))
            sidecar = workspace / "review_report.md"
            self.assertTrue(sidecar.is_file())
            self.assertEqual(sidecar.read_text(encoding="utf-8"), markdown)
            self.assertIn("[[E1](#record-e1)]", markdown)
            if reportlab_available():
                self.assertTrue((workspace / "review_report.pdf").is_file())
                self.assertTrue(summary["pdf"])


class EvidenceLinkTests(unittest.TestCase):
    def test_record_anchor_slug(self) -> None:
        self.assertEqual(record_anchor("E1"), "record-e1")
        self.assertEqual(record_anchor("E-L1"), "record-e-l1")

    def test_evidence_ids_are_markdown_links_into_the_index(self) -> None:
        package = load_review_input(
            Path("examples/synthetic/review_input.json"),
            manuscript=make_artifact(),
            project_id="synthetic-tracking-review",
        )
        markdown = render_markdown(package, bilingual=True)
        self.assertIn("[[E1](#record-e1)]", markdown)
        self.assertIn('<a id="record-e1"></a>', markdown)
        self.assertIn("## Evidence Index / 证据索引", markdown)

    def test_pdf_markup_keeps_citation_brackets_and_one_target_per_id(self) -> None:
        dests, markup = _markup(
            '- <a id="record-e2"></a>`E2` ([E2](#record-e2), [E3](#record-e3)) [[E2](#record-e2), [E3](#record-e3)]'
        )
        self.assertEqual(dests, ["record-e2"])
        self.assertIn('href="#record-e2"', markup)
        self.assertIn('href="#record-e3"', markup)
        self.assertNotIn("[[E2", markup)
        self.assertIn("[<link", markup)

    @unittest.skipUnless(reportlab_available(), "reportlab extra not installed")
    def test_pdf_contains_internal_evidence_links(self) -> None:
        package = load_review_input(
            Path("examples/synthetic/review_input.json"),
            manuscript=make_artifact(),
            project_id="synthetic-tracking-review",
        )
        markdown = render_markdown(package, bilingual=True)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.pdf"
            write_report_pdf(markdown, path, title="Synthetic review")
            payload = path.read_bytes()
            self.assertIn(b"/Link", payload)
            self.assertIn(b"/Dest", payload)
            self.assertGreaterEqual(payload.count(b"/Subtype /Link"), markdown.count("[E1](#record-e1)"))


if __name__ == "__main__":
    unittest.main()
