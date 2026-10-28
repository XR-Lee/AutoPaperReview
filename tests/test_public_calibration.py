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
    MODEL_ADAPTER,
    ChallengeRequired,
    PublicCalibrationError,
    ScoreRow,
    _resolve_adapter,
    _write_project,
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
from autopaperreview.rubrics import COEFFICIENT_SET
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
        self.assertEqual(metrics["coefficient_set"], COEFFICIENT_SET)
        self.assertAlmostEqual(metrics["spearman"], 1.0)
        self.assertAlmostEqual(metrics["kendall"], 1.0)
        self.assertEqual(metrics["confusion"]["counts"]["6"]["8"], 1)
        self.assertEqual(metrics["confusion"]["counts"]["3"]["3"], 1)
        self.assertAlmostEqual(metrics["mae"], 0.5)
        self.assertAlmostEqual(metrics["signed_error"], 0.5)
        self.assertAlmostEqual(metrics["bin_agreement"], 0.5)
        # Human mean 7 snaps to 6, so it does not match the synthetic bin 8.
        self.assertAlmostEqual(metrics["snap_bin_agreement"], 0.5)
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
        self.assertEqual(allowed.parts[-2:], ("workdir", "calibration-eval"))


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
            self.assertEqual(score["coefficient_set"], COEFFICIENT_SET)
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
            self.assertEqual(package.overall_score.coefficient_set, COEFFICIENT_SET)
            self.assertEqual(package.overall_score.value, 6)

            awaiting_dir = cache / "papers" / "SYNTHETIC-FORUM-001" / "project"
            (awaiting_dir / "judgment.json").unlink()
            # state is scored; run_cache still looks for the drop when state is scored
            waiting = run_cache(cache, mode="agent")
            self.assertEqual(waiting["awaiting_judgment"][0]["forum_id"], "SYNTHETIC-FORUM-001")
            toml = (project / "review.toml").read_text(encoding="utf-8")
            self.assertIn('type = "related_work_search"', toml)
            self.assertIn('mode = "off"', toml)
            self.assertIn('depends_on = ["consensus"]', toml)

        self.assertEqual(AGENT_ADAPTER, "autopaperreview.adapters:AgentJudgmentAdapter")

    def test_openai_adapter_is_wired_into_the_run_config(self) -> None:
        self.assertEqual(
            _resolve_adapter("adapter", MODEL_ADAPTER),
            MODEL_ADAPTER,
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "paper"
            destination.mkdir()
            (destination / "manuscript.txt").write_text("synthetic manuscript\n", encoding="utf-8")
            _write_project(
                destination,
                project_id="pub-synthetic",
                phase="run",
                adapter=MODEL_ADAPTER,
                network_policy="deny",
                requires_network=False,
                related_work="off",
                base_url="http://127.0.0.1:8000/v1",
                model="local-reviewer",
            )
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn(MODEL_ADAPTER, toml)
            self.assertIn('base_url = "http://127.0.0.1:8000/v1"', toml)
            self.assertIn('model = "local-reviewer"', toml)
            self.assertIn('dimensions = "M2"', toml)
            self.assertIn('type = "related_work_search"', toml)
            with self.assertRaises(PublicCalibrationError):
                run_cache(Path(temporary) / "cache", mode="adapter", adapter=MODEL_ADAPTER)

    def test_cheap_run_config_declares_the_locked_holistic_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "paper"
            destination.mkdir()
            (destination / "manuscript.txt").write_text("synthetic manuscript\n", encoding="utf-8")
            common = dict(
                project_id="pub-synthetic",
                phase="run",
                adapter=MODEL_ADAPTER,
                network_policy="deny",
                requires_network=False,
                related_work="off",
                base_url="http://127.0.0.1:8000/v1",
                model="local-reviewer",
                coefficient_set="apr-fusion-v1",
                cheap=True,
            )
            _write_project(destination, **common)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn("cheap = true", toml)
            self.assertIn("holistic_samples = 1", toml)
            self.assertIn('holistic_mode = "forecast"', toml)
            self.assertIn('holistic = "M0"', toml)
            self.assertNotIn('dimensions = "M2"', toml)
            _write_project(destination, **common, holistic_mode="rebuttal", holistic_samples=2)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn('holistic_mode = "rebuttal"', toml)
            self.assertIn("holistic_samples = 2", toml)
            _write_project(destination, **common, holistic_mode="matched", holistic_samples=2)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn('holistic_mode = "matched"', toml)
            self.assertIn("holistic_samples = 2", toml)
            _write_project(destination, **common, holistic_mode="panel", holistic_samples=2)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn('holistic_mode = "panel"', toml)
            self.assertIn("holistic_samples = 2", toml)
            _write_project(destination, **common, holistic_mode="expert", holistic_samples=2)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn('holistic_mode = "expert"', toml)
            self.assertIn("holistic_samples = 2", toml)
            _write_project(destination, **common, holistic_mode="mean", holistic_samples=3)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn('holistic_mode = "mean"', toml)
            self.assertIn("holistic_samples = 3", toml)
            _write_project(destination, **common, holistic_mode="audited", holistic_samples=1)
            toml = (destination / "project" / "review.toml").read_text(encoding="utf-8")
            self.assertIn('holistic_mode = "audited"', toml)
            self.assertIn("holistic_samples = 1", toml)
            with self.assertRaises(PublicCalibrationError):
                _write_project(destination, **common, holistic_mode="other")
            with self.assertRaises(PublicCalibrationError):
                run_cache(
                    Path(temporary) / "cache",
                    mode="adapter",
                    adapter=MODEL_ADAPTER,
                    base_url="http://127.0.0.1:8000/v1",
                    model="local-reviewer",
                    holistic_mode="other",
                )

    def test_audit_summary_keeps_verdicts_and_drops_text(self) -> None:
        from autopaperreview.public_calibration import audit_summary

        summary = audit_summary(
            [
                {
                    "sample": 0,
                    "checks_score": 7.75,
                    "counts": {"pass": 9, "fail": 1, "unclear": 2},
                    "checks": [
                        {
                            "id": "claim_quantified",
                            "verdict": "pass",
                            "claimed": "pass",
                            "verified": True,
                            "excerpt": "quoted synthetic text",
                            "note": "a note",
                        }
                    ],
                    "flaws": [
                        {
                            "id": "F1",
                            "severity": "major",
                            "title": "a title",
                            "why": "a reason",
                            "excerpt": "quoted synthetic text",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["checks_score"], 7.75)
        self.assertEqual(summary[0]["counts"]["pass"], 9)
        self.assertEqual(
            summary[0]["checks"],
            [{"id": "claim_quantified", "verdict": "pass", "claimed": "pass", "verified": True}],
        )
        self.assertEqual(summary[0]["flaws"], [{"id": "F1", "severity": "major"}])
        blob = json.dumps(summary)
        for text in ("quoted synthetic text", "a note", "a title", "a reason"):
            self.assertNotIn(text, blob)
        self.assertEqual(audit_summary(None), [])
        self.assertEqual(audit_summary(["not-a-dict"]), [])

    def test_expert_summary_keeps_the_numbers_and_drops_text(self) -> None:
        from autopaperreview.public_calibration import expert_summary

        summary = expert_summary(
            [
                {
                    "sample": 0,
                    "contribution_type": "analysis",
                    "soundness": 3,
                    "presentation": 4,
                    "contribution": 3,
                    "form_score": 8.0,
                    "prior_work": 2,
                    "prior_work_sure": 1,
                    "prior_work_uncited_sure": 1,
                    "field_context_lines": 2,
                    "area": "a synthetic subfield",
                    "rationale": "a synthetic rationale",
                    "prior_work_names": ["a synthetic work"],
                },
                "not-a-dict",
            ]
        )
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["contribution_type"], "analysis")
        self.assertEqual(summary[0]["form_score"], 8.0)
        self.assertEqual(summary[0]["prior_work_uncited_sure"], 1)
        self.assertEqual(
            sorted(summary[0]),
            sorted(
                [
                    "sample",
                    "contribution_type",
                    "soundness",
                    "presentation",
                    "contribution",
                    "form_score",
                    "prior_work",
                    "prior_work_sure",
                    "prior_work_uncited_sure",
                    "field_context_lines",
                ]
            ),
        )
        blob = json.dumps(summary)
        for text in ("a synthetic subfield", "a synthetic rationale", "a synthetic work"):
            self.assertNotIn(text, blob)
        self.assertEqual(expert_summary(None), [])
        self.assertEqual(expert_summary({"sample": 0}), [])

    def test_panel_summary_keeps_roles_and_numbers_and_drops_text(self) -> None:
        from autopaperreview.public_calibration import panel_summary

        summary = panel_summary(
            [
                {
                    "sample": 0,
                    "reviews": [
                        {"role": "specialist", "rating": 5, "confidence": 4, "summary": "a synthetic summary"},
                        {"role": "other", "rating": 7, "confidence": 2, "weaknesses": ["a synthetic weakness"]},
                        "not-a-dict",
                    ],
                    "panel_mean": 5.5,
                    "high": 8,
                    "low": 3,
                    "off_scale": 1,
                    "chair_mean": 5.7,
                    "decision": 4.0,
                    "discussion": "a synthetic discussion",
                    "reviewer_names": ["a synthetic reviewer"],
                },
                "not-a-dict",
            ]
        )
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["panel_mean"], 5.5)
        self.assertEqual(summary[0]["chair_mean"], 5.7)
        self.assertEqual(summary[0]["off_scale"], 1)
        self.assertEqual(
            summary[0]["reviews"],
            [
                {"role": "specialist", "rating": 5, "confidence": 4},
                {"role": "other", "rating": 7, "confidence": 2},
            ],
        )
        self.assertEqual(
            sorted(summary[0]),
            sorted(["sample", "reviews", "panel_mean", "high", "low", "off_scale", "chair_mean", "decision"]),
        )
        blob = json.dumps(summary)
        for text in ("a synthetic summary", "a synthetic weakness", "a synthetic discussion", "a synthetic reviewer"):
            self.assertNotIn(text, blob)
        self.assertEqual(panel_summary(None), [])
        self.assertEqual(panel_summary({"sample": 0}), [])

    def test_matched_rows_reuse_the_panel_summary_and_the_main_text_record_keeps_numbers(self) -> None:
        from autopaperreview.public_calibration import main_text_summary, panel_summary

        rows = panel_summary(
            [
                {
                    "sample": 0,
                    "reviews": [
                        {"role": "insider", "rating": 8, "confidence": 4, "summary": "a synthetic summary"},
                        {"role": "junior", "rating": 6, "confidence": 3},
                        {"role": "neighbour", "rating": 5, "confidence": 3},
                        {"role": "senior", "rating": 8, "confidence": 4, "strengths": ["a synthetic strength"]},
                    ],
                    "panel_mean": 6.75,
                    "high": 8,
                    "low": 5,
                    "off_scale": 0,
                    "chair_mean": 6.6,
                    "decision": 5.0,
                    "discussion": "a synthetic discussion",
                }
            ]
        )
        self.assertEqual([review["role"] for review in rows[0]["reviews"]], ["insider", "junior", "neighbour", "senior"])
        self.assertEqual(rows[0]["panel_mean"], 6.75)
        self.assertNotIn("a synthetic", json.dumps(rows))
        self.assertEqual(
            main_text_summary({"source_chars": 1200, "shown_chars": 700, "cut": "references", "excerpt": "a synthetic excerpt"}),
            {"source_chars": 1200, "shown_chars": 700, "cut": "references"},
        )
        self.assertEqual(main_text_summary({"shown_chars": 10}), {"shown_chars": 10})
        self.assertEqual(main_text_summary(None), {})
        self.assertEqual(main_text_summary([1, 2]), {})

    def test_rebuttal_rows_keep_seats_first_and_final_ratings_and_drop_text(self) -> None:
        from autopaperreview.public_calibration import panel_summary, rebuttal_summary

        rows = rebuttal_summary(
            [
                {
                    "sample": 0,
                    "reviews": [
                        {"role": "specialist", "rating": 5, "confidence": 4, "final_rating": 6, "summary": "a synthetic summary"},
                        {"role": "other", "rating": 6, "confidence": 3, "final_rating": 6, "weaknesses": ["a synthetic weakness"]},
                        {"role": "senior", "rating": 3, "confidence": 3, "final_rating": 3},
                        "not-a-dict",
                    ],
                    "initial_mean": 4.6667,
                    "final_mean": 5.0,
                    "lift": 0.3333,
                    "raised": 1,
                    "lowered": 0,
                    "high": 6,
                    "low": 3,
                    "off_scale": 0,
                    "label_mismatch": 0,
                    "chair_mean": 5.2,
                    "decision": 3.0,
                    "response": "a synthetic response",
                    "discussion": "a synthetic discussion",
                },
                "not-a-dict",
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["reviews"],
            [
                {"role": "specialist", "rating": 5, "confidence": 4, "final_rating": 6},
                {"role": "other", "rating": 6, "confidence": 3, "final_rating": 6},
                {"role": "senior", "rating": 3, "confidence": 3, "final_rating": 3},
            ],
        )
        self.assertEqual(rows[0]["initial_mean"], 4.6667)
        self.assertEqual(rows[0]["final_mean"], 5.0)
        self.assertEqual((rows[0]["lift"], rows[0]["raised"], rows[0]["lowered"]), (0.3333, 1, 0))
        self.assertEqual((rows[0]["label_mismatch"], rows[0]["chair_mean"], rows[0]["decision"]), (0, 5.2, 3.0))
        self.assertEqual(
            sorted(rows[0]),
            sorted(
                [
                    "sample",
                    "reviews",
                    "initial_mean",
                    "final_mean",
                    "lift",
                    "raised",
                    "lowered",
                    "high",
                    "low",
                    "off_scale",
                    "label_mismatch",
                    "chair_mean",
                    "decision",
                ]
            ),
        )
        blob = json.dumps(rows)
        for text in ("a synthetic summary", "a synthetic weakness", "a synthetic response", "a synthetic discussion"):
            self.assertNotIn(text, blob)
        self.assertEqual(rebuttal_summary(None), [])
        self.assertEqual(rebuttal_summary({"sample": 0}), [])
        # The panel summary keeps its own keys and does not grow a final rating.
        panel_rows = panel_summary(
            [{"sample": 0, "reviews": [{"role": "specialist", "rating": 5, "confidence": 4, "final_rating": 6}], "panel_mean": 5.0}]
        )
        self.assertNotIn("final_rating", json.dumps(panel_rows))
        self.assertEqual(panel_rows[0]["panel_mean"], 5.0)

    def test_forecast_rows_keep_seats_probabilities_and_expected_ratings_and_drop_text(self) -> None:
        from autopaperreview.public_calibration import forecast_summary, rebuttal_summary

        rows = forecast_summary(
            [
                {
                    "sample": 0,
                    "reviews": [
                        {"role": "specialist", "rating": 5, "confidence": 4, "expected": 5.4, "peak": 0.5, "probabilities": [0.0, 0.1, 0.5, 0.3, 0.1, 0.0], "summary": "a synthetic summary"},
                        {"role": "other", "rating": 6, "confidence": 3, "expected": 6.4, "peak": 0.5, "probabilities": [0.0, 0.0, 0.2, 0.5, 0.3, 0.0], "weaknesses": ["a synthetic weakness"]},
                        {"role": "senior", "rating": 3, "confidence": 3, "expected": 3.4, "peak": 0.6, "probabilities": ["x", True, 0.3, 0.0, 0.0, 0.0]},
                        {"role": "senior", "rating": 3, "confidence": 3, "expected": 3.4, "peak": 0.6, "probabilities": "a synthetic string"},
                        "not-a-dict",
                    ],
                    "forecast_mean": 5.0667,
                    "vote_mean": 4.6667,
                    "high": 6.4,
                    "low": 3.4,
                    "off_scale": 0,
                    "renormalized": 1,
                    "mode_mismatch": 0,
                    "chair_mean": 5.2,
                    "decision": 3.0,
                    "discussion": "a synthetic discussion",
                },
                "not-a-dict",
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["reviews"][:2],
            [
                {"role": "specialist", "rating": 5, "confidence": 4, "expected": 5.4, "peak": 0.5, "probabilities": [0.0, 0.1, 0.5, 0.3, 0.1, 0.0]},
                {"role": "other", "rating": 6, "confidence": 3, "expected": 6.4, "peak": 0.5, "probabilities": [0.0, 0.0, 0.2, 0.5, 0.3, 0.0]},
            ],
        )
        self.assertEqual(rows[0]["reviews"][2]["probabilities"], [0.3, 0.0, 0.0, 0.0])
        self.assertEqual(rows[0]["reviews"][3]["probabilities"], [])
        self.assertEqual((rows[0]["forecast_mean"], rows[0]["vote_mean"]), (5.0667, 4.6667))
        self.assertEqual((rows[0]["renormalized"], rows[0]["mode_mismatch"], rows[0]["chair_mean"], rows[0]["decision"]), (1, 0, 5.2, 3.0))
        self.assertEqual(
            sorted(rows[0]),
            sorted(["sample", "reviews", "forecast_mean", "vote_mean", "high", "low", "off_scale", "renormalized", "mode_mismatch", "chair_mean", "decision"]),
        )
        blob = json.dumps(rows)
        for text in ("a synthetic summary", "a synthetic weakness", "a synthetic discussion", "a synthetic string"):
            self.assertNotIn(text, blob)
        self.assertEqual(forecast_summary(None), [])
        self.assertEqual(forecast_summary({"sample": 0}), [])
        # The discussion summary keeps its own keys and does not grow probabilities.
        rebuttal_rows = rebuttal_summary(
            [{"sample": 0, "reviews": [{"role": "specialist", "rating": 5, "confidence": 4, "final_rating": 6, "probabilities": [1, 0, 0, 0, 0, 0]}], "final_mean": 6.0}]
        )
        self.assertNotIn("probabilities", json.dumps(rebuttal_rows))
        self.assertEqual(rebuttal_rows[0]["final_mean"], 6.0)


if __name__ == "__main__":
    unittest.main()
