from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from pydantic import ValidationError

from autopaperreview.adapters import DeterministicManuscriptAdapter, ReviewRequest
from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.migration import migrate_legacy_issue
from autopaperreview.models import (
    CommentIntent,
    EvidenceRecord,
    FirstReadNote,
    ReviewPackage,
    SourceRecord,
)
from autopaperreview.pipeline import run_pipeline
from autopaperreview.readability import (
    FIRST_READ_PROMPT,
    issue_quote,
    resolve_first_read_contract,
)
from autopaperreview.reporting import render_markdown

from tests.support import localized, make_artifact, make_issue


def _package_with_issue(**overrides: object) -> ReviewPackage:
    source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="manuscript.txt")
    evidence = EvidenceRecord(
        id="E1",
        source_id="S1",
        locator="Methods",
        claim="Image-level split",
        excerpt="Eight temporally related images from one specimen were randomly divided at image level.",
    )
    issue = make_issue(evidence_ids=["E1"], source_ids=["S1"], **overrides)
    return ReviewPackage(
        project_id="first-read",
        manuscript=make_artifact(),
        sources=[source],
        evidence=[evidence],
        issues=[issue],
    )


class FirstReadModelTests(unittest.TestCase):
    def test_existing_packages_remain_valid_without_first_read(self) -> None:
        package = _package_with_issue()
        self.assertIsNone(package.issues[0].first_read)

    def test_first_read_note_requires_restatement_and_explanation(self) -> None:
        with self.assertRaises(ValidationError):
            FirstReadNote(explanation=localized("Why it matters."))


class FirstReadGateTests(unittest.TestCase):
    def test_gate_can_require_first_read_and_quote_on_major_issues(self) -> None:
        package = _package_with_issue()
        errors = export_gate_errors(
            package,
            ExportGateParams(require_first_read_on_major=True, require_quote_on_major=True),
        )
        self.assertTrue(any("first-read" in item for item in errors))
        self.assertFalse(any("quoted passage" in item for item in errors))

        package.issues[0].first_read = FirstReadNote(
            paper_said=localized("The paper splits eight related images at image level."),
            explanation=localized(
                "A first-time reader may think this is an ordinary split; the test images are near-duplicates."
            ),
            quote="Eight temporally related images from one specimen were randomly divided at image level.",
            intent=CommentIntent.issue,
            blocking=True,
        )
        self.assertEqual(
            export_gate_errors(
                package,
                ExportGateParams(require_first_read_on_major=True, require_quote_on_major=True),
            ),
            [],
        )

    def test_quote_falls_back_to_evidence_excerpt(self) -> None:
        package = _package_with_issue()
        self.assertIn("image level", issue_quote(package.issues[0], package) or "")


class FirstReadReportTests(unittest.TestCase):
    def test_report_explains_the_argument_to_a_first_time_reader(self) -> None:
        package = _package_with_issue(
            first_read=FirstReadNote(
                paper_said=localized("The paper splits eight related images at image level."),
                explanation=localized(
                    "A first-time reader may think this is an ordinary split; the test images are near-duplicates."
                ),
                quote="Eight temporally related images from one specimen were randomly divided at image level.",
                intent=CommentIntent.issue,
                blocking=True,
            )
        )
        markdown = render_markdown(package, language="en")
        self.assertIn("How to Read This Review", markdown)
        self.assertIn("seeing the manuscript for the first time", markdown)
        self.assertIn("**What the paper said.**", markdown)
        self.assertIn("**Quoted passage.**", markdown)
        self.assertIn("**Why this matters.**", markdown)
        self.assertIn("Issue (blocking)", markdown)
        self.assertIn("## Evidence Catalog", markdown)
        self.assertIn("Eight temporally related images from one specimen", markdown)


class FirstReadPacketTests(unittest.TestCase):
    def test_prompt_packet_prepends_the_first_read_contract(self) -> None:
        contract = resolve_first_read_contract()
        self.assertIn("First-read writing contract", contract)
        self.assertIn(FIRST_READ_PROMPT, "first_read_v1.md")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("Synthetic.\n", encoding="utf-8")
            (workspace / "prompt.md").write_text("Review the paper.\n", encoding="utf-8")
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "first-read-packet"
                    title = "First-read packet"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M0"
                    name = "Closed book"
                    kind = "prompt"
                    prompt = "prompt.md"

                    [[stages]]
                    id = "packet"
                    type = "prompt_packet"
                    [stages.params]
                    route_ids = ["M0"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config_path)
            packet = (
                workspace / ".runs" / manifest.run_id / "stages" / "packet" / "M0.md"
            ).read_text(encoding="utf-8")
            self.assertIn("## First-Read Writing Contract", packet)
            self.assertIn("Write as if the reader of this review is seeing the manuscript", packet)
            self.assertIn("## Route Instructions", packet)
            self.assertIn("Review the paper.", packet)


class FirstReadAdapterAndMigrationTests(unittest.TestCase):
    def test_deterministic_adapter_fills_first_read_notes(self) -> None:
        text = Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            result = DeterministicManuscriptAdapter().execute(
                ReviewRequest(
                    project_id="det",
                    manuscript=make_artifact(size_bytes=len(text)),
                    source_text=text,
                ),
                Path(temporary),
            )
        majors = [issue for issue in result.package.issues if issue.id in {"I001", "I002"}]
        self.assertEqual(len(majors), 2)
        for issue in majors:
            self.assertIsNotNone(issue.first_read)
            assert issue.first_read is not None
            self.assertTrue(issue.first_read.quote)
            self.assertIn("first-time reader", issue.first_read.explanation.primary)

    def test_legacy_issue_can_carry_first_read_fields(self) -> None:
        issue = migrate_legacy_issue(
            {
                "id": "I001",
                "severity": "major",
                "category": "evaluation",
                "title_en": "Evaluation is incomplete",
                "location": "Results",
                "evidence_en": "Only one run is reported.",
                "impact_en": "Stability is unknown.",
                "required_action_en": "Report repeated runs.",
                "paper_said_en": "The paper reports one accuracy number.",
                "explanation_en": "A first-time reader cannot tell whether the number is stable.",
                "quote": "Accuracy was reported on the two held-out images.",
                "confidence": 0.9,
            }
        )
        self.assertIsNotNone(issue.first_read)
        assert issue.first_read is not None
        self.assertEqual(issue.first_read.paper_said.primary, "The paper reports one accuracy number.")
        self.assertEqual(issue.first_read.quote, "Accuracy was reported on the two held-out images.")


if __name__ == "__main__":
    unittest.main()
