from __future__ import annotations

import copy
import unittest

from autopaperreview.migration import migrate_legacy_package
from autopaperreview.models import ReviewPackage, RouteKind

from tests.support import legacy_issue, make_artifact


class LegacyMigrationTests(unittest.TestCase):
    def test_migrates_bare_issue_array_without_mutating_input(self) -> None:
        raw = [legacy_issue(legacy_note={"reviewer": "synthetic"})]
        original = copy.deepcopy(raw)

        package = migrate_legacy_package(raw, manuscript=make_artifact(), project_id="legacy-array")

        self.assertEqual(raw, original)
        self.assertEqual(package.schema_version, "1.0")
        self.assertEqual(package.project_id, "legacy-array")
        self.assertEqual(package.metadata["imported_from"], "legacy-v0-issue-array")
        self.assertEqual(len(package.issues), 1)
        issue = package.issues[0]
        self.assertEqual(issue.title.resolve("zh-Hans"), "评估不完整")
        self.assertEqual(issue.route_ids, ["M0"])
        self.assertEqual(issue.source_ids, ["S1"])
        self.assertEqual(issue.confidence, 0.75)
        self.assertEqual(issue.metadata["legacy_v0"]["legacy_note"], {"reviewer": "synthetic"})
        self.assertIsNone(issue.first_read)
        self.assertEqual([route.id for route in package.routes], ["M0"])
        self.assertTrue(package.routes[0].metadata["legacy_placeholder"])
        self.assertEqual([source.id for source in package.sources], ["S1"])
        self.assertTrue(package.sources[0].metadata["legacy_placeholder"])

    def test_migrates_legacy_package_routes_sources_and_localized_sections(self) -> None:
        raw = {
            "manuscript": {
                "visible_title": "Synthetic paper",
                "recommendation_en": "Major revision",
                "recommendation_zh": "大修",
            },
            "methods": [
                {"id": "M1", "name_en": "OOXML audit", "execution": "deterministic"},
            ],
            "sources": [
                {"id": "S1", "type": "manuscript", "title": "Synthetic paper", "path": "paper.docx"},
            ],
            "issues": [legacy_issue()],
            "strengths_en": ["Clear objective"],
            "strengths_zh": ["目标明确"],
            "acceptance_gate_en": ["Repeat the experiment"],
            "acceptance_gate_zh": ["重复实验"],
        }

        package = migrate_legacy_package(raw, manuscript=make_artifact(), project_id="legacy-package")

        self.assertEqual(package.metadata["imported_from"], "legacy-v0-package")
        self.assertEqual(package.metadata["legacy_manuscript"]["visible_title"], "Synthetic paper")
        self.assertEqual(package.recommendation.resolve("zh-Hans"), "大修")
        self.assertEqual(package.routes[0].id, "M1")
        self.assertEqual(package.routes[0].kind, RouteKind.deterministic)
        self.assertEqual(package.routes[0].metadata["execution"], "deterministic")
        self.assertEqual(package.sources[0].locator, "paper.docx")
        self.assertEqual(package.strengths["zh-Hans"], ["目标明确"])
        self.assertEqual(package.acceptance_gate["en"], ["Repeat the experiment"])

    def test_rejects_canonical_package_binding_mismatches(self) -> None:
        canonical = ReviewPackage(
            project_id="canonical-project",
            manuscript=make_artifact(),
        ).model_dump(mode="json")

        with self.subTest(binding="project_id"):
            with self.assertRaisesRegex(ValueError, "canonical package project_id"):
                migrate_legacy_package(
                    canonical,
                    manuscript=make_artifact(),
                    project_id="different-project",
                )

        with self.subTest(binding="manuscript_sha256"):
            mismatched = copy.deepcopy(canonical)
            mismatched["manuscript"]["sha256"] = "b" * 64
            with self.assertRaisesRegex(ValueError, "canonical package manuscript hash"):
                migrate_legacy_package(
                    mismatched,
                    manuscript=make_artifact(),
                    project_id="canonical-project",
                )

    def test_rejects_legacy_claimed_source_hash_mismatch(self) -> None:
        for field in ("source_sha256", "sha256"):
            with self.subTest(field=field):
                raw = {
                    "manuscript": {field: "b" * 64},
                    "issues": [],
                }
                with self.assertRaisesRegex(ValueError, "legacy package manuscript hash"):
                    migrate_legacy_package(
                        raw,
                        manuscript=make_artifact(),
                        project_id="legacy-package",
                    )


if __name__ == "__main__":
    unittest.main()
