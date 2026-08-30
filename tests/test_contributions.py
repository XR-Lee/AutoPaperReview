from __future__ import annotations

import unittest

from autopaperreview.contributions import extract_listed_contributions
from autopaperreview.ledger import build_agenda, build_ledger_claims
from autopaperreview.literature import generate_related_work_queries
from autopaperreview.reporting import render_markdown
from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.models import ReviewPackage

from tests.support import make_artifact, make_issue


PAREN_MANUSCRIPT = """
Title: Token Merge Study

Abstract
We propose a method.

Contributions. Our work makes three contributions. (1) We identify pair construction as the key knob under compression. (2) We develop a deterministic local merge with value-feature matching. (3) We establish an accuracy-efficiency trade-off with a five-model protocol.

Related Work
Other methods exist.
"""

DOTTED_MANUSCRIPT = """
Contributions
The main contributions of this work are as follows:

1. We introduce a graph-streaming framework instead of raw images or point-clouds.
2. We construct a new dataset containing RGB-D images and corresponding 3D environments.
3. We conduct a comparative analysis demonstrating a 6.33 times latency reduction versus image streaming.

Methodology
System overview follows.
"""

TWO_COLUMN = """
Contributions. Our work makes three contributions. (1) We           whereas token methods adapt
identify pair construction as the key knob. (2) We develop a        TokenLearner learns compact
deterministic merge with restoration. (3) We establish a trade-off. Related Work
"""


class ExtractListedContributionsTests(unittest.TestCase):
    def test_paren_enumeration(self) -> None:
        items = extract_listed_contributions(PAREN_MANUSCRIPT)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertIn("pair construction", str(items[0]["text"]))
        self.assertIn("deterministic local merge", str(items[1]["text"]))
        self.assertIn("accuracy-efficiency", str(items[2]["text"]))

    def test_dotted_enumeration(self) -> None:
        items = extract_listed_contributions(DOTTED_MANUSCRIPT)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertTrue(str(items[0]["text"]).startswith("We introduce"))
        self.assertIn("dataset", str(items[1]["text"]))
        self.assertIn("6.33", str(items[2]["text"]))

    def test_two_column_extract_keeps_left_column(self) -> None:
        items = extract_listed_contributions(TWO_COLUMN)
        self.assertEqual(len(items), 3)
        joined = " ".join(str(item["text"]) for item in items)
        self.assertNotIn("TokenLearner", joined)
        self.assertIn("pair construction", joined)

    def test_roman_and_bullet_lists(self) -> None:
        roman = (
            "We contribute (i) a markerless framework that jointly solves pose and latency "
            "on one manifold; (ii) a 2D-3D rendering loss fusing metric depth with masks; "
            "(iii) an offline excitation-maximizing trajectory planner; and (iv) support "
            "for Eye-to-Hand and Eye-in-Hand setups.\nRelated Work\n"
        )
        items = extract_listed_contributions("Contributions. " + roman)
        self.assertEqual([item["index"] for item in items], [1, 2, 3, 4])
        bullets = """
Our contributions are:
• a practical depth-centric RGB-D SLAM architecture for long-horizon reconstruction.
• a geometry-primary frontend that combines GICP with depth-lifted anchors.
• an extensive all-real evaluation on public benchmarks and floor-scale capture.

Related Work
"""
        items = extract_listed_contributions(bullets)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertIn("depth-centric", str(items[0]["text"]))

    def test_synthetic_manuscript_has_no_listed_contributions(self) -> None:
        from pathlib import Path

        text = Path("examples/synthetic/manuscript.txt").read_text(encoding="utf-8")
        self.assertEqual(extract_listed_contributions(text), [])


class ContributionLedgerTests(unittest.TestCase):
    def test_listed_contributions_become_ledger_units_and_agenda(self) -> None:
        claims = build_ledger_claims(PAREN_MANUSCRIPT, manuscript_sha256="a" * 64)
        self.assertEqual([claim.id for claim in claims], ["C1", "C2", "C3"])
        self.assertTrue(all(claim.metadata.get("kind") == "listed_contribution" for claim in claims))
        agenda = build_agenda(claims)
        kinds = {item.metadata.get("kind") for item in agenda}
        self.assertEqual(kinds, {"evidence_completeness", "targeted_retrieval"})
        self.assertEqual(len(agenda), 6)

    def test_queries_include_per_contribution_retrieval(self) -> None:
        queries = generate_related_work_queries(PAREN_MANUSCRIPT)
        ids = [query.id for query in queries]
        self.assertIn("Q-baselines-1", ids)
        self.assertIn("Q-C1-same_problem", ids)
        self.assertIn("Q-C3-related_techniques", ids)
        targeted = [query for query in queries if query.generated_from == "listed_contribution"]
        self.assertEqual(len(targeted), 6)

    def test_report_renders_listed_contributions_section(self) -> None:
        claims = build_ledger_claims(PAREN_MANUSCRIPT, manuscript_sha256="a" * 64)
        package = ReviewPackage(
            project_id="contrib",
            manuscript=make_artifact(),
            ledger_claims=claims,
            issues=[make_issue(location="Contribution 1; Table 1")],
        )
        markdown = render_markdown(package, language="en")
        self.assertIn("## Listed Contributions", markdown)
        self.assertIn("`C1`", markdown)
        self.assertNotIn("## Claim Ledger", markdown)

    def test_export_gate_requires_each_listed_contribution_to_be_covered(self) -> None:
        claims = build_ledger_claims(PAREN_MANUSCRIPT, manuscript_sha256="a" * 64)
        package = ReviewPackage(
            project_id="contrib-gate",
            manuscript=make_artifact(),
            ledger_claims=claims,
            issues=[make_issue(location="Contribution 1")],
        )
        errors = export_gate_errors(package, ExportGateParams(require_contribution_coverage=True))
        self.assertTrue(any("C2" in item and "C3" in item for item in errors))
        package.issues = [
            make_issue(id="I001", location="Contribution 1"),
            make_issue(id="I002", location="Contribution 2; Table 2"),
            make_issue(id="I003", location="C3 ablation"),
        ]
        errors = export_gate_errors(package, ExportGateParams(require_contribution_coverage=True))
        self.assertFalse(any("listed contribution" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
