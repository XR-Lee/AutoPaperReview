"""Public calibration eval. Synthetic venue notes only; no network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autopaperreview.adapters import AgentJudgmentAdapter, digest_packet_text
from autopaperreview.models import ReviewPackage
from autopaperreview.public_calibration import (
    AGENT_ADAPTER,
    ChallengeRequired,
    PublicCalibrationError,
    ScoreRow,
    _resolve_adapter,
    assert_local_only,
    fetch_manifest,
    parse_rating,
    prepare_cache,
    repo_root,
    run_cache,
    summarize,
    write_report,
)
from autopaperreview.rating import WEIGHTS
from tests.support import make_artifact

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "calibration"


class FakeClient:
    def __init__(self, submission: dict, reviews: list[dict]) -> None:
        self.submission = submission
        self.reviews = reviews

    def get_json(self, path: str, params: dict | None = None) -> dict:
        params = params or {}
        if path == "/notes":
            raise ChallengeRequired("browser check")
        if path != "/notes/search":
            raise AssertionError(path)
        invitation = str(params.get("invitation") or "")
        group = str(params.get("group") or "")
        if "Official_Review" in invitation or "Conference/Submission" in group:
            return {"notes": self.reviews, "count": len(self.reviews)}
        return {"notes": [self.submission], "count": 1}

    def get_bytes(self, url: str) -> bytes:
        raise ChallengeRequired(url)


class MetricTests(unittest.TestCase):
    def test_metrics_are_report_only(self) -> None:
        before = dict(WEIGHTS)
        rows = [
            ScoreRow(
                project_id="pub-a",
                forum_id="SYNTHA",
                title="Synthetic accept",
                decision="accept",
                human_ratings=[8, 6],
                our_bin=8,
                pdf_source="openreview",
            ),
            ScoreRow(
                project_id="pub-b",
                forum_id="SYNTHB",
                title="Synthetic reject",
                decision="reject",
                human_ratings=[3, 3],
                our_bin=3,
                pdf_source="openreview",
            ),
        ]
        metrics = summarize(rows)
        self.assertEqual(before, dict(WEIGHTS))
        self.assertFalse(metrics["fitted"])
        self.assertEqual(metrics["coefficient_set"], "apr-iclr-cap-v1")
        self.assertAlmostEqual(metrics["spearman"], 1.0)
        self.assertAlmostEqual(metrics["mae"], 0.5)
        self.assertAlmostEqual(metrics["signed_error"], 0.5)
        self.assertAlmostEqual(metrics["bin_agreement"], 0.5)
        self.assertAlmostEqual(metrics["snap_bin_agreement"], 1.0)
        self.assertAlmostEqual(metrics["accept_reject_auc"], 1.0)
        self.assertEqual(metrics["unique_bins"], [3, 8])
        self.assertTrue(metrics["clustered"])
        self.assertIsNone(parse_rating("7: not a venue bin"))
        self.assertEqual(parse_rating("6: marginally above the acceptance threshold"), 6)
        self.assertEqual(parse_rating({"value": "3: reject, not good enough"}), 3)

    def test_cache_inside_the_checkout_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            assert_local_only(repo_root() / "examples" / "not-a-calibration-cache")
        allowed = assert_local_only(repo_root() / "workdir" / "calibration-eval")
        self.assertTrue(str(allowed).endswith("workdir/calibration-eval"))


class AdapterTests(unittest.TestCase):
    def test_agent_drop_must_match_packets_and_cannot_set_the_venue_bin(self) -> None:
        packets = {"M0": "# packet\n"}
        digest = digest_packet_text(packets["M0"])
        judgment = json.loads((FIXTURE / "judgment.json").read_text(encoding="utf-8"))
        judgment["packet_sha256"] = {"M0": digest}
        judgment["overall_score"] = {
            "value": 10,
            "scale_min": 1,
            "scale_max": 10,
            "method": "human",
            "notes": "must be ignored",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "judgment.json"
            path.write_text(json.dumps(judgment), encoding="utf-8")
            from autopaperreview.adapters import ReviewRequest

            request = ReviewRequest(
                project_id="pub-synthetic",
                manuscript=make_artifact(),
                packets=packets,
                fixture_path=str(path),
            )
            result = AgentJudgmentAdapter().execute(request, root / "scratch")
            self.assertIsNone(result.package.overall_score)
            self.assertEqual(len(result.package.dimension_scores), 7)
            self.assertTrue(result.metadata["stripped_overall_score"])
            (root / "scratch").mkdir(exist_ok=True)
            judgment["packet_sha256"] = {"M0": "0" * 64}
            path.write_text(json.dumps(judgment), encoding="utf-8")
            with self.assertRaises(ValueError):
                AgentJudgmentAdapter().execute(request, root / "scratch")

    def test_missing_model_adapter_is_reported(self) -> None:
        self.assertEqual(_resolve_adapter("agent", None), AGENT_ADAPTER)
        with self.assertRaises(PublicCalibrationError):
            _resolve_adapter("adapter", "autopaperreview.missing_adapter:NotInstalled")


class EndToEndTests(unittest.TestCase):
    def test_fetch_prepare_run_and_report_use_the_declared_formula(self) -> None:
        submission = json.loads((FIXTURE / "submission.json").read_text(encoding="utf-8"))
        reviews = json.loads((FIXTURE / "reviews.json").read_text(encoding="utf-8"))
        manuscript = (FIXTURE / "manuscript.txt").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(manuscript.strip()), 800)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            report = root / "calibration_report.md"
            statuses = fetch_manifest(
                FIXTURE / "manifest.json",
                cache,
                pdf_fallback="arxiv",
                client=FakeClient(submission, reviews),
                text_extractor=lambda _path: manuscript,
                arxiv_finder=lambda _title: ("0000.00000v1", b"%PDF-1.4\nsynthetic\n"),
            )
            self.assertEqual(statuses[0]["state"], "fetched")
            self.assertEqual(statuses[0]["n_ratings"], 2)
            self.assertEqual(statuses[0]["pdf_source"], "arxiv:0000.00000v1")
            self.assertNotIn("ratings", statuses[0])
            heldout = (cache / "papers" / "SYNTHETIC-FORUM-001" / "heldout" / "human.json").read_text(
                encoding="utf-8"
            )
            project_text = cache / "papers" / "SYNTHETIC-FORUM-001" / "manuscript.txt"
            self.assertNotIn("8: accept", project_text.read_text(encoding="utf-8"))
            self.assertIn("8", heldout)

            prepared = prepare_cache(cache)
            self.assertEqual(prepared[0]["state"], "prepared")
            project = cache / "papers" / "SYNTHETIC-FORUM-001" / "project"
            index = json.loads((project / "packet_index.json").read_text(encoding="utf-8"))
            self.assertEqual(set(index["packets"]), {"M0", "M2", "M5", "M8"})
            packet = (project / ".runs").glob("*/stages/prompt_packets/M0.md")
            packet_text = next(packet).read_text(encoding="utf-8")
            self.assertIn("First-Read Writing Contract", packet_text)
            self.assertIn("Lattice-7", packet_text)
            self.assertNotIn("8: accept", packet_text)

            dropped = json.loads((FIXTURE / "judgment.json").read_text(encoding="utf-8"))
            dropped["packet_sha256"] = index["packets"]
            (project / "judgment.json").write_text(json.dumps(dropped), encoding="utf-8")
            missing = run_cache(cache, mode="agent")
            self.assertEqual(missing["scored"][0]["our_bin"], 6)
            self.assertEqual(missing["errors"], [])
            score = json.loads((project / "our_score.json").read_text(encoding="utf-8"))
            self.assertEqual(score["coefficient_set"], "apr-iclr-cap-v1")
            self.assertNotIn("human", score)

            metrics = write_report(cache, report)
            self.assertFalse(metrics["fitted"])
            self.assertAlmostEqual(metrics["mae"], 2.0)
            self.assertAlmostEqual(metrics["bin_agreement"], 0.0)
            text = report.read_text(encoding="utf-8")
            self.assertIn("does not fit", text)
            self.assertIn("SYNTHETIC-FORUM-001", text)
            self.assertIn("6", text)
            package = ReviewPackage.model_validate_json(
                next((project / ".runs").glob("*/stages/rate/package.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(package.overall_score.coefficient_set, "apr-iclr-cap-v1")
            self.assertEqual(package.overall_score.value, 6)

            awaiting_dir = cache / "papers" / "SYNTHETIC-FORUM-001" / "project"
            (awaiting_dir / "judgment.json").unlink()
            # state is scored; run_cache still looks for the drop when state is scored
            waiting = run_cache(cache, mode="agent")
            self.assertEqual(waiting["awaiting_judgment"][0]["forum_id"], "SYNTHETIC-FORUM-001")

        self.assertEqual(AGENT_ADAPTER, "autopaperreview.adapters:AgentJudgmentAdapter")


if __name__ == "__main__":
    unittest.main()
