from __future__ import annotations

import io
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from pydantic import ValidationError

from autopaperreview.adapters import DeterministicManuscriptAdapter, ReviewRequest
from autopaperreview.cli import main
from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.hashing import digest_excerpt
from autopaperreview.integrity import build_integrity_records, collect_inventory, reporting_is_present
from autopaperreview.ledger import build_agenda, build_ledger_claims
from autopaperreview.models import (
    Anchor,
    AnchorKind,
    Artifact,
    IntegrityVerdict,
    NoveltyTag,
    RetrievedSourceSnapshot,
    ReviewPackage,
    SnapshotContentKind,
)
from autopaperreview.novelty import assess_novelty
from autopaperreview.pipeline import PipelineError, run_pipeline

from tests.support import make_artifact


class ContractTests(unittest.TestCase):
    def test_excerpt_hash_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "excerpt_hash does not match"):
            Anchor(
                kind=AnchorKind.quote,
                display="p.1",
                excerpt="hello",
                excerpt_hash="0" * 64,
            )

    def test_quote_anchor_binds_excerpt_hash(self) -> None:
        anchor = Anchor(kind=AnchorKind.quote, display="Methods", excerpt="image-level split")
        self.assertEqual(anchor.excerpt_hash, digest_excerpt("image-level split"))

    def test_export_gate_refuses_empty_package(self) -> None:
        package = ReviewPackage(project_id="gate", manuscript=make_artifact())
        errors = export_gate_errors(package, ExportGateParams(min_issues=1, min_claims=1))
        self.assertTrue(any("empty" in item or "at least" in item for item in errors))

    def test_deterministic_adapter_reviews_synthetic_manuscript(self) -> None:
        text = Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8")
        manuscript = Artifact(
            path="manuscript.txt",
            sha256="a" * 64,
            size_bytes=len(text),
            role="manuscript.source",
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = DeterministicManuscriptAdapter().execute(
                ReviewRequest(
                    project_id="det",
                    manuscript=manuscript,
                    source_text=text,
                ),
                Path(temporary),
            )
        self.assertGreaterEqual(len(result.package.issues), 2)
        self.assertTrue(any(issue.id == "I001" for issue in result.package.issues))
        self.assertTrue(any(issue.id == "I002" for issue in result.package.issues))
        self.assertTrue(all(issue.anchor is not None for issue in result.package.issues))
        first_reads = [issue.first_read for issue in result.package.issues if issue.id in {"I001", "I002"}]
        self.assertTrue(all(note is not None and note.quote for note in first_reads))

    def test_ledger_and_novelty_use_matched_setting_gate(self) -> None:
        text = Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8")
        claims = build_ledger_claims(text, manuscript_sha256="a" * 64)
        self.assertGreaterEqual(len(claims), 1)
        self.assertTrue(build_agenda(claims))
        from datetime import datetime, timezone

        snapshot = RetrievedSourceSnapshot(
            id="RW1",
            query_ids=["Q-baselines-1"],
            title="Other split",
            retrieved_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            content_kind=SnapshotContentKind.abstract,
            locator="fixture",
            excerpt="sequence-level holdout protocol",
            task="tracking",
            dataset="sequence-level holdout",
            metric="accuracy",
        )
        assessments = assess_novelty(
            claims=claims,
            snapshots=[snapshot],
            evidence=[],
            manuscript_text=text,
        )
        self.assertTrue(assessments)
        self.assertEqual(assessments[0].tag, NoveltyTag.not_comparable)
        self.assertFalse(assessments[0].matched_setting)

    def test_workspace_inspect_refuses_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with self.assertRaisesRegex(PermissionError, "outside workspace"):
                collect_inventory(["/etc/passwd"], workspace=workspace, generated_by="test")

    def test_init_then_run_produces_a_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "paper"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(["init", str(project), "--project-id", "init-review"]), 0)
            (project / "manuscript.txt").write_text(
                Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            manifest = run_pipeline(project / "review.toml")
            self.assertTrue(all(stage.status.value in {"success", "skipped"} for stage in manifest.stages))
            report = next(Path(project / ".runs").glob("*/stages/report/review_package.json"))
            package = ReviewPackage.model_validate_json(report.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(package.issues), 1)
            self.assertTrue(package.ledger_claims)
            self.assertTrue(package.integrity_records)

    def test_export_gate_blocks_report_without_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("Title: Empty\n", encoding="utf-8")
            (workspace / "issues.json").write_text("[]\n", encoding="utf-8")
            (workspace / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1
                    [project]
                    id = "gate-fail"
                    title = "Gate"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"
                    [[stages]]
                    id = "ingest"
                    type = "ingest"
                    [[stages]]
                    id = "issues"
                    type = "import_issues"
                    depends_on = ["ingest"]
                    [stages.params]
                    path = "issues.json"
                    [[stages]]
                    id = "report"
                    type = "report"
                    depends_on = ["issues"]
                    [stages.params]
                    min_issues = 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PipelineError, "export gate"):
                run_pipeline(workspace / "review.toml")

    def test_negated_reporting_is_not_treated_as_present(self) -> None:
        text = Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8")
        self.assertFalse(reporting_is_present(text, "confidence interval"))
        self.assertFalse(reporting_is_present(text, "seed"))
        self.assertTrue(
            reporting_is_present("We report a 95% confidence interval and seed 7.", "confidence interval")
        )
        records = build_integrity_records(
            manuscript_text=text,
            snapshots=[],
            inventory=[],
            evidence=[],
            workspace=Path("examples/synthetic"),
        )
        repro = next(item for item in records if item.id == "INT-REPRO-reporting")
        self.assertEqual(repro.verdict, IntegrityVerdict.major)

    def test_reference_integrity_marks_unresolved_identifiers_unclear(self) -> None:
        from datetime import datetime, timezone

        snapshot = RetrievedSourceSnapshot(
            id="RWLive",
            query_ids=["Q-baselines-1"],
            title="A real-looking identifier",
            retrieved_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            content_kind=SnapshotContentKind.abstract,
            locator="offline",
            arxiv_id="1234.56789",
            excerpt="abstract text",
        )
        records = build_integrity_records(
            manuscript_text="Accuracy 10%.",
            snapshots=[snapshot],
            inventory=[],
            evidence=[],
            workspace=Path("examples/synthetic"),
        )
        ref = next(item for item in records if item.id == "INT-REF-RWLive")
        self.assertEqual(ref.verdict, IntegrityVerdict.unclear)

    def test_synthetic_example_run_passes_export_gate(self) -> None:
        manifest = run_pipeline(Path("examples/synthetic/review.toml"), force=True)
        self.assertTrue(all(stage.status.value in {"success", "skipped"} for stage in manifest.stages))
        report = next(Path("examples/synthetic/.runs").glob("*/stages/report/review_package.json"))
        package = ReviewPackage.model_validate_json(report.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(package.issues), 2)
        self.assertTrue(package.ledger_claims)
        self.assertTrue(package.integrity_records)
        self.assertTrue(package.novelty_assessments)
        self.assertTrue(all(issue.anchor is not None for issue in package.issues))
        self.assertTrue(all(item.excerpt for item in package.retrieved_snapshots))
        repro = next(item for item in package.integrity_records if item.id == "INT-REPRO-reporting")
        self.assertEqual(repro.verdict, IntegrityVerdict.major)
        results = next(item for item in package.integrity_records if item.id == "INT-RES-accuracy")
        self.assertEqual(results.verdict, IntegrityVerdict.mismatch)


if __name__ == "__main__":
    unittest.main()
