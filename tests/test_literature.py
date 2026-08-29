from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from autopaperreview.literature import generate_related_work_queries, parse_manuscript_text
from autopaperreview.models import (
    OverallScore,
    OverallScoreMethod,
    ReviewClaim,
    ReviewClaimKind,
    ReviewPackage,
    SarDimension,
)
from autopaperreview.pipeline import PipelineError, run_pipeline
from autopaperreview.reporting import render_markdown

from tests.support import localized, make_artifact, make_issue


class LiteratureGroundingTests(unittest.TestCase):
    def test_generates_three_sar_perspectives_from_synthetic_manuscript(self) -> None:
        manuscript = Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8")
        parsed = parse_manuscript_text(manuscript)
        self.assertEqual(parsed["title"], "A Synthetic Tracking Study")
        self.assertIn("95% accuracy", parsed["abstract"])

        queries = generate_related_work_queries(manuscript)
        self.assertEqual([query.id for query in queries], ["Q-baselines-1", "Q-same_problem-1", "Q-related_techniques-1"])
        self.assertEqual(
            [query.perspective.value for query in queries],
            ["baselines", "same_problem", "related_techniques"],
        )
        self.assertTrue(all("Synthetic Tracking Study" in query.query for query in queries))

    def test_offline_snapshot_is_recorded_under_deny_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text(
                Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (workspace / "snapshots.json").write_text(
                Path("examples/synthetic/related_work_snapshot.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "lit-offline"
                    title = "Offline literature"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "literature"
                    type = "literature_grounding"
                    [stages.params]
                    snapshot_path = "snapshots.json"
                    requires_network = false
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config_path)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "literature"
            package = ReviewPackage.model_validate_json((stage_dir / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(len(package.related_work_queries), 3)
            self.assertEqual([snapshot.id for snapshot in package.retrieved_snapshots], ["RW1"])
            self.assertEqual(package.retrieved_snapshots[0].content_kind.value, "abstract")
            self.assertEqual(package.retrieved_snapshots[0].arxiv_id, "0000.00000")
            self.assertEqual(package.sources[0].kind, "retrieved-abstract")
            self.assertEqual(package.evidence[0].id, "E-RW1")
            self.assertIsNone(package.overall_score)
            self.assertFalse(any("tavily" in str(artifact.path).lower() for artifact in manifest.stage("literature").outputs))

    def test_deny_policy_blocks_declared_live_retriever_before_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("Title: Blocked retrieval\n", encoding="utf-8")
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "lit-deny"
                    title = "Deny live retrieval"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "literature"
                    type = "literature_grounding"
                    [stages.params]
                    requires_network = true
                    network_scope = "metadata"
                    retriever = "tests.support:unavailable_retriever"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PipelineError, "requests metadata network access under policy deny"):
                run_pipeline(config_path)
            self.assertFalse((workspace / ".runs").exists())


class SarSchemaTests(unittest.TestCase):
    def test_overall_score_is_absent_by_default_and_rejects_bare_llm_direct(self) -> None:
        package = ReviewPackage(project_id="score-test", manuscript=make_artifact())
        self.assertIsNone(package.overall_score)
        with self.assertRaisesRegex(ValidationError, "uncalibrated"):
            OverallScore(value=7.0, method=OverallScoreMethod.llm_direct)
        with self.assertRaisesRegex(ValidationError, "coefficient_set"):
            OverallScore(value=7.0, method=OverallScoreMethod.linear_regression)
        scored = OverallScore(
            value=6.5,
            method=OverallScoreMethod.human,
            scorer="synthetic-reviewer",
            notes="Example judgment; not an ICLR mapping.",
        )
        self.assertEqual(scored.method, OverallScoreMethod.human)

    def test_claims_and_dimension_scores_require_known_evidence(self) -> None:
        from autopaperreview.models import DimensionScore, EvidenceRecord, SourceRecord

        source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="manuscript.txt")
        evidence = EvidenceRecord(id="E1", source_id="S1", locator="p. 1", claim="Observed text")
        claim = ReviewClaim(
            id="C1",
            kind=ReviewClaimKind.strength,
            text=localized("Clear writing"),
            evidence_ids=["missing"],
        )
        with self.assertRaisesRegex(ValidationError, "claims reference unknown evidence"):
            ReviewPackage(
                project_id="claim-test",
                manuscript=make_artifact(),
                sources=[source],
                evidence=[evidence],
                claims=[claim],
            )
        with self.assertRaisesRegex(ValidationError, "dimension scores reference unknown evidence"):
            ReviewPackage(
                project_id="score-test",
                manuscript=make_artifact(),
                sources=[source],
                evidence=[evidence],
                dimension_scores=[
                    DimensionScore(
                        dimension=SarDimension.originality,
                        score=5.0,
                        evidence_ids=["missing"],
                    )
                ],
            )

    def test_snapshots_require_known_query_ids_even_without_a_query_catalog(self) -> None:
        from autopaperreview.models import RetrievedSourceSnapshot, SnapshotContentKind

        with self.assertRaisesRegex(ValidationError, "retrieved snapshots reference unknown queries"):
            ReviewPackage(
                project_id="snapshot-test",
                manuscript=make_artifact(),
                retrieved_snapshots=[
                    RetrievedSourceSnapshot(
                        id="RW1",
                        query_ids=["Q-missing"],
                        title="Orphan snapshot",
                        retrieved_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                        content_kind=SnapshotContentKind.abstract,
                        locator="fixtures/orphan.json#RW1",
                    )
                ],
            )

    def test_report_emits_sar_sections_with_evidence_ids_and_no_invented_overall(self) -> None:
        from autopaperreview.models import (
            DimensionScore,
            EvidenceRecord,
            ReviewClaim,
            SourceRecord,
        )

        source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="manuscript.txt")
        evidence = EvidenceRecord(id="E1", source_id="S1", locator="Results", claim="One accuracy value")
        package = ReviewPackage(
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
                    id="C-q",
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
        markdown = render_markdown(package, language="en")
        self.assertIn("## Summary", markdown)
        self.assertIn("A short paper claims deployment readiness. [E1]", markdown)
        self.assertNotIn("Legacy summary without an evidence suffix.", markdown)
        self.assertIn("## Questions", markdown)
        self.assertIn("## Dimension Scores", markdown)
        self.assertIn("[E1]", markdown)
        self.assertIn("does not emit a raw LLM 0–10", markdown)
        self.assertNotIn("Spearman", markdown)


if __name__ == "__main__":
    unittest.main()
