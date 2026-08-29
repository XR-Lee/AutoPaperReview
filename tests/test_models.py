from __future__ import annotations

import unittest

from pydantic import ValidationError

from autopaperreview.models import (
    EvidenceRecord,
    LocalizedText,
    ReviewPackage,
    ReviewRoute,
    RouteKind,
    SourceRecord,
    VenueConclusion,
    VenueKind,
    VenueOutcome,
)

from tests.support import make_artifact, make_issue


class ReviewModelTests(unittest.TestCase):
    def test_confidence_accepts_closed_interval_endpoints(self) -> None:
        self.assertEqual(make_issue(confidence=0.0).confidence, 0.0)
        self.assertEqual(make_issue(confidence=1.0).confidence, 1.0)

    def test_confidence_rejects_values_outside_closed_interval(self) -> None:
        for value in (-0.0001, 1.0001):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    make_issue(confidence=value)

    def test_package_rejects_duplicate_issue_ids(self) -> None:
        with self.assertRaisesRegex(ValidationError, "issue IDs must be unique"):
            ReviewPackage(
                project_id="model-test",
                manuscript=make_artifact(),
                issues=[make_issue(), make_issue(title={"primary": "Different title"})],
            )

    def test_package_rejects_unknown_references_when_catalogs_exist(self) -> None:
        route = ReviewRoute(id="M0", name="Closed book", kind=RouteKind.prompt)
        source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="manuscript.txt")
        evidence = EvidenceRecord(id="E1", source_id="S1", locator="p. 1", claim="Observed text")

        cases = (
            ("route", make_issue(route_ids=["missing"]), "unknown routes"),
            ("source", make_issue(source_ids=["missing"]), "unknown sources"),
            ("evidence", make_issue(evidence_ids=["missing"]), "unknown evidence"),
        )
        for name, issue, message in cases:
            with self.subTest(reference=name):
                with self.assertRaisesRegex(ValidationError, message):
                    ReviewPackage(
                        project_id="model-test",
                        manuscript=make_artifact(),
                        routes=[route],
                        sources=[source],
                        evidence=[evidence],
                        issues=[issue],
                    )

    def test_package_rejects_references_when_catalogs_are_empty(self) -> None:
        cases = (
            ("route", make_issue(route_ids=["missing"]), "unknown routes"),
            ("source", make_issue(source_ids=["missing"]), "unknown sources"),
            ("evidence", make_issue(evidence_ids=["missing"]), "unknown evidence"),
        )
        for name, issue, message in cases:
            with self.subTest(reference=name):
                with self.assertRaisesRegex(ValidationError, message):
                    ReviewPackage(
                        project_id="model-test",
                        manuscript=make_artifact(),
                        issues=[issue],
                    )

    def test_package_rejects_unknown_venue_conclusion_evidence(self) -> None:
        source = SourceRecord(id="S1", kind="manuscript", title="Paper", locator="manuscript.txt")
        evidence = EvidenceRecord(id="E1", source_id="S1", locator="p. 1", claim="Observed text")
        with self.assertRaisesRegex(ValidationError, "venue conclusions reference unknown evidence"):
            ReviewPackage(
                project_id="model-test",
                manuscript=make_artifact(),
                sources=[source],
                evidence=[evidence],
                venue_conclusions=[
                    VenueConclusion(
                        id="ICLR",
                        kind=VenueKind.conference,
                        outcome=VenueOutcome.reject,
                        label=LocalizedText(primary="Reject", language="en"),
                        rationale=LocalizedText(primary="Unsupported claim.", language="en"),
                        evidence_ids=["missing"],
                    )
                ],
            )

    def test_package_rejects_evidence_source_when_sources_are_empty(self) -> None:
        evidence = EvidenceRecord(id="E1", source_id="missing", locator="p. 1", claim="Observed text")
        with self.assertRaisesRegex(ValidationError, "evidence references unknown sources"):
            ReviewPackage(
                project_id="model-test",
                manuscript=make_artifact(),
                evidence=[evidence],
            )


if __name__ == "__main__":
    unittest.main()
