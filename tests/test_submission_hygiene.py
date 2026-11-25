"""Camera-ready anonymizer, original-PDF choice, and target-paper exclusion."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autopaperreview.iclr_anonymize import (
    anonymize_iclr_submission,
    audit_by_tier,
    residual_markers,
    residual_total,
)
from autopaperreview.public_calibration import (
    PublicCalibrationError,
    _write_project,
    prefer_original_pdf,
)
from autopaperreview.related_work import RawHit, _guard_prior_hits, work_id_keys

_PAD = (
    " This sentence is padding so the synthetic note stays long enough after the author block is removed."
    " Lattice-7 remains the name of the invented benchmark."
)


def _camera_ready() -> str:
    body = (
        "Prism Sort reorders color tokens before a linear count classifier. "
        "On the invented Lattice-7 benchmark a single item-level split supports a qualified accuracy claim."
    )
    return (
        "arXiv:2401.00001v1 [cs.LG] 1 Jan 2024\n"
        "\n"
        "Published as a conference paper at ICLR 2024\n"
        "\n"
        "Prism Sort on the Lattice-7 benchmark\n"
        "\n"
        "Ada Example, Bo Example\n"
        "University of North Nowhere\n"
        "ada.example@nowhere.edu\n"
        "https://github.com/ada-example/prism-sort\n"
        "\n"
        "Abstract. "
        + body
        + _PAD * 8
        + "\n\n"
        "Acknowledgements\n"
        "We thank the Synthetic Grant Agency, award SG-000.\n"
        "\n"
        "References\n"
        "[1] A synthetic neighbor. arXiv:1999.00001.\n"
    )


class AnonymizerTests(unittest.TestCase):
    def test_camera_ready_markers_are_removed_and_the_title_stays(self) -> None:
        cleaned, removed = anonymize_iclr_submission(_camera_ready())
        self.assertIn("Prism Sort on the Lattice-7 benchmark", cleaned)
        self.assertIn("Lattice-7", cleaned)
        self.assertIn("Abstract", cleaned)
        self.assertNotIn("Ada Example", cleaned)
        self.assertNotIn("University of North Nowhere", cleaned)
        self.assertNotIn("ada.example@nowhere.edu", cleaned)
        self.assertNotIn("github.com/ada-example", cleaned)
        self.assertNotIn("Published as a conference paper", cleaned)
        self.assertNotIn("Acknowledgements", cleaned)
        self.assertNotIn("Synthetic Grant Agency", cleaned)
        self.assertNotIn("arXiv:2401.00001", cleaned)
        self.assertIn("arXiv:1999.00001", cleaned)
        self.assertGreater(removed["email"], 0)
        self.assertEqual(residual_total(residual_markers(cleaned)), 0)
        again, _second = anonymize_iclr_submission(cleaned)
        self.assertEqual(again, cleaned)

    def test_anonymous_github_and_inline_status_are_handled(self) -> None:
        text = (
            "Toy Title\n\n"
            "Abstract. A short invented abstract with no authors.\n\n"
            "Code is at https://github.com/anonymous/toy and also github.com/named-lab/toy.\n"
            "The camera-ready version of this paper is not the submission.\n"
        )
        cleaned, _removed = anonymize_iclr_submission(text)
        self.assertIn("github.com/anonymous/toy", cleaned)
        self.assertNotIn("github.com/named-lab", cleaned)
        self.assertIn("[URL]", cleaned)
        self.assertNotIn("camera-ready", cleaned.lower())

    def test_audit_counts_by_tier_and_drops_identifiers(self) -> None:
        report = audit_by_tier(
            [
                {"tier": "clear_accept", "forum_id": "SHOULD-NOT-APPEAR", "text": _camera_ready()},
                {"tier": "clear_reject", "text": "Toy Title\n\nAbstract. Anonymous body with no leak markers.\n"},
            ]
        )
        encoded = json.dumps(report)
        self.assertNotIn("SHOULD-NOT-APPEAR", encoded)
        self.assertNotIn("Ada Example", encoded)
        self.assertEqual(report["tiers"]["clear_accept"]["papers_with_any_marker"], 0)
        self.assertEqual(report["tiers"]["clear_reject"]["papers"], 1)
        raw = audit_by_tier(
            [{"tier": "clear_accept", "text": _camera_ready()}],
            apply_anonymizer=False,
        )
        self.assertGreater(raw["tiers"]["clear_accept"]["email"], 0)
        self.assertGreater(raw["tiers"]["clear_accept"]["papers_with_any_marker"], 0)


class OriginalPdfTests(unittest.TestCase):
    def test_latest_non_camera_ready_pdf_is_preferred(self) -> None:
        submission = {
            "id": "SYNTH",
            "forum": "SYNTH",
            "tcdate": 30,
            "invitations": ["ICLR.cc/2025/Conference/-/Camera_Ready_Revision"],
            "content": {"pdf": {"value": "/pdf/camera.pdf"}, "title": {"value": "Toy"}},
        }
        edits = [
            {
                "tcdate": 10,
                "invitation": "ICLR.cc/2025/Conference/-/Submission",
                "note": {"content": {"pdf": {"value": "/pdf/original.pdf"}}},
            },
            {
                "tcdate": 20,
                "invitation": "ICLR.cc/2025/Conference/-/Revision",
                "note": {"content": {"pdf": {"value": "/pdf/rebuttal.pdf"}}},
            },
            {
                "tcdate": 30,
                "invitation": "ICLR.cc/2025/Conference/-/Camera_Ready_Revision",
                "note": {"content": {"pdf": {"value": "/pdf/camera.pdf"}}},
            },
        ]
        self.assertEqual(prefer_original_pdf(submission, edits), ("openreview:original", "/pdf/rebuttal.pdf"))

    def test_current_pdf_is_used_when_nothing_is_camera_ready(self) -> None:
        submission = {
            "id": "SYNTH",
            "tcdate": 12,
            "invitations": ["ICLR.cc/2025/Conference/-/Submission"],
            "content": {"pdf": {"value": "/pdf/submission.pdf"}},
        }
        self.assertEqual(
            prefer_original_pdf(submission, []),
            ("openreview:submission", "/pdf/submission.pdf"),
        )

    def test_camera_ready_only_returns_none_so_text_is_anonymized(self) -> None:
        submission = {
            "id": "SYNTH",
            "tcdate": 12,
            "invitations": ["ICLR.cc/2025/Conference/-/Camera_Ready_Revision"],
            "content": {"pdf": {"value": "/pdf/camera.pdf"}},
        }
        self.assertIsNone(prefer_original_pdf(submission, []))


class TargetExclusionTests(unittest.TestCase):
    def test_title_and_arxiv_version_are_excluded(self) -> None:
        twin = RawHit(
            title="Prism Sort: on the Lattice-7 benchmark",
            summary="synthetic",
            venue="arXiv",
            year=2024,
            published="2024-02-01",
            arxiv_id="2099.00001v3",
            doi=None,
            openalex_id=None,
            s2_id=None,
            link="https://example.invalid/twin",
            provider="pool",
            query_ids=["Q1"],
        )
        neighbor = RawHit(
            title="A different synthetic neighbor",
            summary="synthetic",
            venue="arXiv",
            year=2023,
            published="2023-01-01",
            arxiv_id="1999.00002",
            doi=None,
            openalex_id=None,
            s2_id=None,
            link="https://example.invalid/neighbor",
            provider="pool",
            query_ids=["Q1"],
        )
        kept, leakage, _logs = _guard_prior_hits(
            [twin, neighbor],
            cutoff=None,
            blocked=work_id_keys("arxiv:2099.00001"),
            blocked_titles=set(),
        )
        self.assertEqual([item.title for item in kept], ["A different synthetic neighbor"])
        self.assertTrue(leakage)
        kept_by_title, leakage_title, logs = _guard_prior_hits(
            [twin, neighbor],
            cutoff=None,
            blocked=set(),
            blocked_titles={"prism sort on the lattice 7 benchmark"},
        )
        self.assertEqual([item.title for item in kept_by_title], ["A different synthetic neighbor"])
        self.assertTrue(leakage_title)
        self.assertTrue(any("by title" in entry.detail for entry in logs))
        self.assertFalse(any("Prism Sort" in entry.detail for entry in logs))

    def test_missing_venue_is_not_silently_iclr2027(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / "manuscript.txt").write_text("synthetic manuscript\n", encoding="utf-8")
            with self.assertRaises(PublicCalibrationError):
                _write_project(
                    destination,
                    project_id="pub-synthetic",
                    phase="run",
                    adapter="autopaperreview.adapters:AgentJudgmentAdapter",
                    network_policy="deny",
                    requires_network=False,
                )
