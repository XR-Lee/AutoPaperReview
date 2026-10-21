from __future__ import annotations

import unittest

from autopaperreview.contributions import extract_listed_contributions
from autopaperreview.ledger import (
    PENDING_CONTRIBUTION_RISK_EN,
    apply_contribution_audits,
    build_agenda,
    build_ledger_claims,
)
from autopaperreview.literature import generate_related_work_queries
from autopaperreview.reporting import render_markdown
from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.models import LocalizedText, ReviewPackage, ReviewRoute, RouteKind

from tests.support import make_artifact, make_issue, localized


# Synthetic shapes only. Do not paste sentences from a manuscript into these fixtures.
PAREN_MANUSCRIPT = """
Title: Synthetic Ordering Fixture

Abstract
We propose a method.

Contributions. Our work makes three contributions. (1) We identify ordering of toy pairs as the knob under a fixed budget. (2) We develop a deterministic local step with placeholder matching. (3) We establish a small accuracy trade-off with a three-check protocol.

Related Work
Other methods exist.
"""

DOTTED_MANUSCRIPT = """
Contributions
The main contributions of this work are as follows:

1. We introduce a toy streaming sketch instead of raw tables or logs.
2. We construct a synthetic dataset containing labeled rows and matching notes.
3. We conduct a comparative check demonstrating a 2.50 times slowdown versus the placeholder.

Methodology
System overview follows.
"""

TWO_COLUMN = """
Contributions. Our work makes three contributions. (1) We           whereas side notes adapt
identify ordering of toy pairs as the knob. (2) We develop a        PlaceholderSide learns compact
deterministic local step with restoration. (3) We establish a       codes for this fixture only
longer toy trade-off. Related Work
"""


class ExtractListedContributionsTests(unittest.TestCase):
    def test_paren_enumeration(self) -> None:
        items = extract_listed_contributions(PAREN_MANUSCRIPT)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertIn("ordering of toy pairs", str(items[0]["text"]))
        self.assertIn("deterministic local step", str(items[1]["text"]))
        self.assertIn("three-check", str(items[2]["text"]))

    def test_dotted_enumeration(self) -> None:
        items = extract_listed_contributions(DOTTED_MANUSCRIPT)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertTrue(str(items[0]["text"]).startswith("We introduce"))
        self.assertIn("dataset", str(items[1]["text"]))
        self.assertIn("2.50", str(items[2]["text"]))

    def test_two_column_extract_keeps_left_column(self) -> None:
        items = extract_listed_contributions(TWO_COLUMN)
        self.assertEqual(len(items), 3)
        joined = " ".join(str(item["text"]) for item in items)
        self.assertNotIn("PlaceholderSide", joined)
        self.assertIn("ordering of toy pairs", joined)

    def test_roman_and_bullet_lists(self) -> None:
        roman = (
            "We contribute (i) a synthetic marker that jointly labels pose and delay "
            "on one toy manifold; (ii) a drawing loss that fuses a flat map with masks; "
            "(iii) an offline fixture path over a grid; and (iv) support "
            "for bench-left and bench-right setups.\nRelated Work\n"
        )
        items = extract_listed_contributions("Contributions. " + roman)
        self.assertEqual([item["index"] for item in items], [1, 2, 3, 4])
        bullets = """
Our contributions are:
• a practical toy sketch for a long fixture sequence.
• a geometry-primary frontend that combines a toy aligner with lifted anchors.
• an extensive fixture evaluation on stand-in logs and bench-scale capture.

Related Work
"""
        items = extract_listed_contributions(bullets)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertIn("toy sketch", str(items[0]["text"]))

    def test_unknown_roman_token_does_not_crash(self) -> None:
        text = (
            "Contributions. (vv) this token is not a contribution index and should be skipped entirely. "
            "(i) We define a synthetic task on an invented benchmark with a clear protocol. "
            "(ii) We compare against a matched baseline on that same invented benchmark. "
            "Related Work\n"
        )
        items = extract_listed_contributions(text)
        self.assertEqual([item["index"] for item in items], [1, 2])

    def test_capitalized_non_we_item_stays_in_the_list(self) -> None:
        text = """
Our contributions are threefold: (1) We propose a fixture planner that models a toy signal.
(2) Additional synthetic supervision is designed to characterize a fixture relevance score.
(3) A placeholder forecaster is introduced to predict how that fixture score changes.

Related Work
"""
        items = extract_listed_contributions(text)
        self.assertEqual([item["index"] for item in items], [1, 2, 3])
        self.assertIn("Additional synthetic supervision", str(items[1]["text"]))

    def test_long_listed_contribution_quotes_do_not_block_bilingual_export(self) -> None:
        from autopaperreview.i18n import ensure_package_languages
        from autopaperreview.bilingual_coverage import markdown_bilingual_gaps

        text = """
Contributions
(1) We model a fixture allocation score in a toy query task before a geometric check under two named conditions.
(2) We introduce a synthetic distribution over placeholder evidence and a residual gate driven by a total count.
(3) We introduce a holdout split that removes two labeled groups and report accuracy on that split.

Related Work
"""
        claims = build_ledger_claims(text, manuscript_sha256="c" * 64)
        package = ReviewPackage(
            project_id="long-contrib",
            manuscript=make_artifact(),
            ledger_claims=claims,
        )
        filled = ensure_package_languages(package, ["en", "zh-Hans"])
        self.assertTrue(all(claim.claim.get("zh-Hans") is None for claim in filled.ledger_claims))
        self.assertTrue(all(claim.risk is not None and claim.risk.get("zh-Hans") for claim in filled.ledger_claims))
        markdown = render_markdown(filled, bilingual=True)
        self.assertEqual(markdown_bilingual_gaps(markdown), [])

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


    def test_covering_issue_replaces_pending_contribution_risk(self) -> None:
        claims = build_ledger_claims(PAREN_MANUSCRIPT, manuscript_sha256="a" * 64)
        self.assertEqual(claims[0].risk.primary, PENDING_CONTRIBUTION_RISK_EN)
        issue = make_issue(
            id="I012",
            location="Contribution 1; Table 1",
            title=localized("Contribution 1 is not isolated"),
            evidence=LocalizedText(
                primary="Tests: Table 1. Comparator: none. Missing: factorial.",
                language="en",
                translations={"zh-Hans": "测了：表 1。对照：无。缺什么：因子实验。"},
            ),
            route_ids=["M7"],
            category="contribution_audit",
        )
        package = ReviewPackage(
            project_id="contrib-audit",
            manuscript=make_artifact(),
            routes=[ReviewRoute(id="M7", name="contribution audit", kind=RouteKind.prompt)],
            ledger_claims=claims,
            issues=[issue],
        )
        filled = apply_contribution_audits(package)
        self.assertEqual(filled.ledger_claims[0].metadata.get("audit"), "checked")
        self.assertIn("Tests: Table 1", filled.ledger_claims[0].risk.primary)
        self.assertNotEqual(filled.ledger_claims[0].risk.primary, PENDING_CONTRIBUTION_RISK_EN)
        self.assertEqual(filled.ledger_claims[1].risk.primary, PENDING_CONTRIBUTION_RISK_EN)
        markdown = render_markdown(filled, bilingual=True)
        listed = markdown.split("## Listed Contributions")[1].split("## ")[0]
        self.assertIn("Tests: Table 1", listed)
        self.assertIn("测了：表 1", listed)
        self.assertNotIn(PENDING_CONTRIBUTION_RISK_EN, listed.split("`C2`")[0])

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
