"""Fixed evaluation manifest. Synthetic holdout rows only; the committed list is public."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from autopaperreview.cli import build_parser, main
from autopaperreview.cvpr_sets import id_list_digest as cvpr_id_list_digest
from autopaperreview.fixed_eval import (
    DEV_PER_TIER,
    HOLDOUT_PER_TIER,
    OPUS_COLD_PER_TIER,
    OPUS_PER_TIER,
    OPUS_SEED,
    TIER_NAMES,
    FixedEvalError,
    assert_cvpr_stage,
    assert_holdout_file,
    assert_manifest,
    assert_run_ids,
    content_digest,
    dev_ids,
    dev_papers,
    holdout_record,
    id_list_digest,
    load_baseline_cache,
    main as fixed_main,
    manifest_path,
    opus_slice,
    select_fixed,
    tier_for,
)
from autopaperreview.public_calibration import repo_root

EXCLUDED = "cvpr2024-e71e5cd119bbc5797164fb0cd7fd94a4"
S24P_DIGEST = "81b04fa5031fcc594d0074702c8280d855f36c40835d05969205a26cdb04d29f"


def _rank_eval():
    path = repo_root() / "scripts" / "rank_eval.py"
    spec = importlib.util.spec_from_file_location("rank_eval_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("rank_eval.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paper(forum_id: str, ratings: list[int], *, area: str, decision: str, mean: float) -> dict:
    return {
        "decision": decision,
        "forum_id": forum_id,
        "mean_rating": mean,
        "primary_area": area,
        "ratings": ratings,
        "tier": tier_for(mean),
        "title": "Synthetic holdout paper",
    }


def _holdout_papers() -> list[dict]:
    rows = []
    plan = (
        ("clear_reject", [1, 3], 2.0, "reject"),
        ("borderline_reject", [3, 5], 4.0, "reject"),
        ("borderline_accept", [5, 6], 5.5, "accept"),
        ("clear_accept", [8, 10], 9.0, "accept"),
    )
    for index, (tier, ratings, mean, decision) in enumerate(plan):
        for slot in range(HOLDOUT_PER_TIER):
            rows.append(
                _paper(
                    f"HoldSyn{index}{slot}x",
                    ratings,
                    area=f"area-{tier}-{slot}",
                    decision=decision,
                    mean=mean,
                )
            )
    return rows


def _score(cache: Path, forum_id: str, rank: float, ratings: list[int]) -> None:
    project = cache / "papers" / forum_id / "project"
    held = cache / "papers" / forum_id / "heldout"
    project.mkdir(parents=True)
    held.mkdir()
    (project / "our_score.json").write_text(
        json.dumps({"forum_id": forum_id, "rank_score": rank, "our_bin": 5, "candidates": {}}),
        encoding="utf-8",
    )
    (held / "human.json").write_text(
        json.dumps({"decision": "accept" if ratings[0] >= 6 else "reject", "ratings": ratings}),
        encoding="utf-8",
    )


class FixedEvalManifestTests(unittest.TestCase):
    def test_committed_manifest_is_locked_and_balanced(self) -> None:
        payload = assert_manifest()
        self.assertEqual(payload["status"], "locked")
        self.assertEqual(payload["content_sha256"], content_digest(payload))
        digest = payload["content_sha256"]
        proposal = (repo_root() / "benchmarks" / "fixed_eval" / "PROPOSAL.md").read_text(encoding="utf-8")
        benchmark = (repo_root() / "benchmarks" / "promotion" / "benchmark-v1.md").read_text(encoding="utf-8")
        self.assertIn("Status: **locked**.", proposal)
        self.assertIn(digest, proposal)
        self.assertIn("Status is **locked**.", benchmark)
        self.assertIn(digest, benchmark)
        self.assertIn("36-per-tier slice (144 papers)", benchmark)
        self.assertIn("first 18 per tier (72 papers)", benchmark)
        self.assertIn("about $95", benchmark)
        self.assertIn("$100 cap", benchmark)
        self.assertIn(
            "The baseline cache is valid only while the main commit, the model id and effort, and the scoring path match.",
            benchmark,
        )
        self.assertIn("192 dev papers", benchmark)
        self.assertIn("24 papers, 6 per tier", benchmark)
        papers = dev_papers(payload)
        self.assertEqual(len(papers), 192)
        counts = {name: 0 for name in TIER_NAMES}
        for paper in papers:
            counts[paper["tier"]] += 1
        self.assertEqual(counts, {name: DEV_PER_TIER for name in TIER_NAMES})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fixed_main(["check"]), 0)
        blob = json.dumps(payload["holdout"])
        self.assertNotIn("forum_id", blob)
        self.assertNotIn("title", blob)
        text = manifest_path().read_text(encoding="utf-8")
        proposal = (repo_root() / "benchmarks" / "fixed_eval" / "PROPOSAL.md").read_text(encoding="utf-8")
        for document in (text, proposal):
            self.assertNotIn("openreview.net", document.lower())
            self.assertNotIn("forum?id=", document.lower())
        previous = {
            "1SO93f7sVf", "2Ed7b52z53", "2NwHLAffZZ", "48CXLrx7K3", "4y3GDTFv70",
            "50P9TDPEsh", "6s5uXNWGIh", "Aemqy6Hjdj", "DS5qRs0tQz", "Dr4qD9bzZd",
            "E6EbeJR20o", "GRMfXcAAFh", "Ge0GEOvifh", "Ha6RTeWMd0", "MpWRCiw8g5",
            "NTWtNjlThd", "NYN1b8GRGS", "Ng7OYC3PT8", "PoB6QGAM38", "Pujt3ADZgI",
            "QFo2wxQEW6", "RFJGFrMvYj", "RuP17cJtZo", "SJPUmX4LXD", "UpgRVWexaD",
            "VyWv7GSh5i", "WnEnU2K3Rb", "bjFJrdK0nO", "eRAXvtP0gA", "esYrEndGsr",
            "fWk5Qx0exc", "g3xuCtrG6H", "iAYIRHOYy8", "itGkF993gz", "kGvXIlIVLM",
            "kaZAKvjLro", "llXCyLhOY4", "pxI5IPeWgW", "rUf9G9k2im", "t7P5BUKcYv",
            "vo9t20wsmd", "wcka3bd7P4", "x5txICnnjC", "xByvdb3DCm", "xsts7MRLey",
            "xw3fStKCwm", "yrgQdA5NkI", "zhJDD85QHD",
        }
        self.assertTrue(previous <= set(dev_ids(payload)))
        opus = payload["opus_validation"]
        self.assertEqual(opus["seed"], OPUS_SEED)
        self.assertEqual(opus["papers_per_tier"], OPUS_PER_TIER)
        self.assertEqual(opus["cold_start_per_tier"], OPUS_COLD_PER_TIER)
        self.assertEqual(
            opus["cache"]["valid_while"],
            ["main commit", "model id", "effort", "scoring path"],
        )
        self.assertIn("opus-validation:{seed}:{tier}", opus["ordering"])
        drawn = opus_slice(papers, seed=OPUS_SEED, per_tier=OPUS_PER_TIER)
        self.assertEqual(opus["paper_ids_by_tier"], drawn)
        dev = set(dev_ids(payload))
        seen = set()
        for tier in TIER_NAMES:
            ids = opus["paper_ids_by_tier"][tier]
            self.assertEqual(ids, drawn[tier])
            self.assertEqual(ids[:OPUS_COLD_PER_TIER], drawn[tier][:OPUS_COLD_PER_TIER])
            self.assertEqual(len(ids), OPUS_PER_TIER)
            self.assertEqual(len(set(ids)), OPUS_PER_TIER)
            self.assertTrue(set(ids) <= dev)
            self.assertTrue(seen.isdisjoint(ids))
            seen.update(ids)
            for paper in papers:
                if paper["forum_id"] in ids:
                    self.assertEqual(paper["tier"], tier)

    def test_tier_bounds(self) -> None:
        self.assertEqual(tier_for(0), "clear_reject")
        self.assertEqual(tier_for(3.999), "clear_reject")
        self.assertEqual(tier_for(4.0), "borderline_reject")
        self.assertEqual(tier_for(5.499), "borderline_reject")
        self.assertEqual(tier_for(5.5), "borderline_accept")
        self.assertEqual(tier_for(6.999), "borderline_accept")
        self.assertEqual(tier_for(7.0), "clear_accept")
        self.assertEqual(tier_for(10), "clear_accept")
        with self.assertRaises(FixedEvalError):
            tier_for(10.1)

    def test_tampered_copy_and_wrong_ids_fail(self) -> None:
        payload = json.loads(manifest_path().read_text(encoding="utf-8"))
        payload["iclr"]["papers"][0]["title"] = "Tampered title"
        with self.assertRaises(FixedEvalError):
            assert_manifest(payload)
        with self.assertRaises(FixedEvalError):
            assert_run_ids(["not-the-dev-set"], kind="iclr-dev")
        self.assertEqual(len(dev_ids(assert_run_ids(dev_ids(), kind="iclr-dev"))), 192)

    def test_id_list_digest_matches_cvpr_sets(self) -> None:
        self.assertEqual(id_list_digest(["b", "a", "a"]), cvpr_id_list_digest(["a", "b"]))

    def test_hmac_roundtrip_on_synthetic_holdout(self) -> None:
        salt = b"0123456789abcdef"
        papers = _holdout_papers()
        payload = json.loads(manifest_path().read_text(encoding="utf-8"))
        payload["holdout"] = holdout_record(salt, papers)
        payload["content_sha256"] = content_digest(payload)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "holdout.json"
            path.write_text(
                json.dumps({"papers": papers, "salt_hex": salt.hex()}),
                encoding="utf-8",
            )
            checked = assert_holdout_file(path, payload)
            self.assertEqual(len(checked), len(papers))
            path.write_text(
                json.dumps({"papers": papers, "salt_hex": (b"fedcba9876543210").hex()}),
                encoding="utf-8",
            )
            with self.assertRaises(FixedEvalError):
                assert_holdout_file(path, payload)
            overlapped = json.loads(json.dumps(papers))
            overlapped[0]["forum_id"] = dev_ids(payload)[0]
            path.write_text(
                json.dumps({"papers": overlapped, "salt_hex": salt.hex()}),
                encoding="utf-8",
            )
            with self.assertRaises(FixedEvalError) as caught:
                assert_holdout_file(path, payload)
            self.assertIn("overlap", str(caught.exception))

    def test_operator_file_when_present(self) -> None:
        path = repo_root() / "workdir" / "fixed-eval" / "private-holdout.json"
        if not path.is_file():
            self.skipTest("operator file is not on this machine")
        papers = assert_holdout_file(path)
        self.assertEqual(len(papers), 24)

    def test_select_fixed_is_deterministic_and_disjoint(self) -> None:
        pool = []
        for code, mean in (
            ("cr", 2.0),
            ("br", 4.5),
            ("ba", 6.0),
            ("ca", 8.0),
        ):
            for index in range(5):
                pool.append(
                    {
                        "forum_id": f"{code}{index:02d}xxxx",
                        "mean": mean,
                        "primary_area": "only-area" if index < 4 else "other-area",
                    }
                )
        first_dev, first_hold = select_fixed(pool, seed=7, dev_n=2, hold_n=1, area_cap=1)
        second_dev, second_hold = select_fixed(list(reversed(pool)), seed=7, dev_n=2, hold_n=1, area_cap=1)
        dev_ids_drawn = [item["forum_id"] for item in first_dev]
        hold_ids_drawn = [item["forum_id"] for item in first_hold]
        self.assertEqual(dev_ids_drawn, [item["forum_id"] for item in second_dev])
        self.assertEqual(hold_ids_drawn, [item["forum_id"] for item in second_hold])
        self.assertEqual(len(dev_ids_drawn), 8)
        self.assertEqual(len(hold_ids_drawn), 4)
        self.assertFalse(set(dev_ids_drawn) & set(hold_ids_drawn))


class FixedEvalCvprTests(unittest.TestCase):
    def test_s24_p_digest_and_exclusion(self) -> None:
        payload = assert_manifest()
        pilot = next(item for item in payload["cvpr"]["sets"] if item["id"] == "S24-P")
        second = next(item for item in payload["cvpr"]["sets"] if item["id"] == "S24-D2")
        self.assertEqual(len(pilot["paper_ids"]), 50)
        self.assertEqual(pilot["id_list_sha256"], S24P_DIGEST)
        self.assertEqual(id_list_digest(pilot["paper_ids"]), S24P_DIGEST)
        self.assertEqual(second["excluded_paper_ids"], [EXCLUDED])
        self.assertNotIn(EXCLUDED, pilot["paper_ids"])
        self.assertIsNone(second["id_list_sha256"])
        assert_run_ids(pilot["paper_ids"], kind="S24-P")
        with self.assertRaises(FixedEvalError):
            assert_run_ids(pilot["paper_ids"][:-1], kind="S24-P")
        for name in ("S24-C1", "S24-C2"):
            with self.assertRaises(FixedEvalError) as caught:
                assert_run_ids(["sealed-id"], kind=name)
            self.assertIn("sealed", str(caught.exception))

    def test_s24_d2_stage_rules(self) -> None:
        payload = assert_manifest()
        pilot = set(next(item for item in payload["cvpr"]["sets"] if item["id"] == "S24-P")["paper_ids"])
        clean = [f"d2synth{index:04d}" for index in range(49)]
        self.assertFalse(set(clean) & pilot)
        note = assert_cvpr_stage("S24-D2", clean, stage="run")
        self.assertEqual(note["valid_n"], 49)
        fetched = assert_cvpr_stage("S24-D2", [*clean, EXCLUDED], stage="fetch")
        self.assertEqual(fetched["valid_n"], 49)
        self.assertTrue(fetched["excluded_present"])
        with self.assertRaises(FixedEvalError):
            assert_cvpr_stage("S24-D2", [*clean, EXCLUDED], stage="run")
        overlapped = clean[:-1] + [next(iter(pilot))]
        with self.assertRaises(FixedEvalError):
            assert_run_ids(overlapped, kind="S24-D2")
        with self.assertRaises(FixedEvalError):
            assert_run_ids(clean + ["extra-paper"], kind="S24-D2")
        self.assertIsNone(assert_cvpr_stage("SYN-DEV", clean, stage="run"))


class FixedEvalCommandTests(unittest.TestCase):
    def test_rank_report_counts_records_from_the_grounded_feedback_prompt(self) -> None:
        rank_eval = _rank_eval()
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            output = Path(temporary) / "metrics.json"
            _score(cache, "synthetic-regular", 4.0, [1, 3])
            _score(cache, "synthetic-grounded", 7.0, [6, 8])
            path = cache / "papers" / "synthetic-grounded" / "project" / "our_score.json"
            score = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps({**score, "grounded_weaknesses": True}), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = rank_eval.main([
                    "--cache", str(cache), "--output", str(output), "--fixed-set", "none",
                    "--resamples", "10",
                ])
            self.assertEqual(code, 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["grounded_weaknesses_records"], 1)
            self.assertEqual(result["forecast_prompt"], "mixed")
            self.assertEqual(result["n"], 2)

    def test_fixed_confirmation_checks_prompt_and_stamp(self) -> None:
        rank = _rank_eval()
        ids = ["synthetic-circle", "synthetic-square"]
        manifest = {"status": "locked", "content_sha256": "a" * 64,
                    "iclr": {"papers": [{"forum_id": i, "tier": "toy"} for i in ids]}}
        stamp = {"fixed_set": "iclr-dev", "model_id": "toy", "effort": "high",
                 "status": "locked", "content_sha256": manifest["content_sha256"],
                 "cheap": True, "holistic_samples": 1, "holistic_mode": "forecast",
                 "coefficient_set": "apr-fusion-v1", "related_work": "off"}
        with tempfile.TemporaryDirectory() as temp, patch.object(rank, "assert_manifest", return_value=manifest), \
             patch.object(rank, "dev_ids", return_value=ids), patch.object(rank, "RANK_BOOTSTRAP", {"resamples": 10}):
            cache = Path(temp) / "cache"
            output = Path(temp) / "result.json"
            for item, score, votes in zip(ids, [2.0, 8.0], [[1, 3], [6, 8]]):
                _score(cache, item, score, votes)
            args = ["--cache", str(cache), "--output", str(output), "--fixed-set", "iclr-dev",
                    "--model-id", "toy", "--effort", "high", "--resamples", "10"]
            def run():
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    return rank.main(args)
            def score_flag(item, value):
                path = cache / "papers" / item / "project" / "our_score.json"
                score = json.loads(path.read_text())
                path.write_text(json.dumps({**score, "grounded_weaknesses": value}))
            file = cache / "fixed-eval-run.json"
            self.assertEqual(run(), 0)  # Historical baseline without a stamp.
            file.write_text(json.dumps(stamp))
            self.assertEqual(run(), 0)  # Historical baseline with the original stamp.
            score_flag(ids[0], True)
            self.assertEqual(run(), 2)  # Mixed arms.
            score_flag(ids[1], True)
            self.assertEqual(run(), 2)  # All experimental, previous baseline stamp.
            file.unlink()
            self.assertEqual(run(), 0)  # Homogeneous rows assembled across workers without a stamp.
            self.assertEqual(json.loads(output.read_text())["forecast_prompt"], "grounded-weaknesses")
            file.write_text(json.dumps({**stamp, "grounded_weaknesses": True}))
            self.assertEqual(run(), 0)  # Matching legacy stamp without a run-state field.
            complete = {**stamp, "grounded_weaknesses": True, "run_state": "complete", "run_id": "b" * 32}
            for invalid in ("{", "[]", "{}", json.dumps({**complete, "run_state": "incomplete"}),
                            json.dumps({**complete, "effort": "other"}), json.dumps({**complete, "grounded_weaknesses": 1}),
                            json.dumps({**complete, "run_id": ""}), json.dumps({**complete, "holistic_samples": True})):
                with self.subTest(invalid=invalid):
                    file.write_text(invalid)
                    self.assertEqual(run(), 2)
            file.write_text(json.dumps(complete))
            self.assertEqual(run(), 0)
            reported = json.loads(output.read_text())
            self.assertEqual(reported["grounded_weaknesses_records"], 2)
            self.assertEqual(reported["fixed_run_id"], complete["run_id"])
            self.assertEqual(reported["forecast_prompt"], "grounded-weaknesses")
            score_flag(ids[1], "false")
            self.assertEqual(run(), 2)

    def test_fixed_run_marks_interruption_and_replaces_stale_stamp_only_on_completion(self) -> None:
        ids = ["synthetic-red", "synthetic-blue"]
        manifest = {"status": "locked", "content_sha256": "c" * 64}
        with tempfile.TemporaryDirectory() as temp, patch("autopaperreview.fixed_eval.assert_manifest", return_value=manifest), \
             patch("autopaperreview.fixed_eval.dev_ids", return_value=ids):
            cache = Path(temp)
            file = cache / "fixed-eval-run.json"
            args = ["calibrate-eval", "run", "--cache", temp, "--fixed-set", "iclr-dev", "--cheap",
                    "--coefficient-set", "apr-fusion-v1", "--model-id", "toy", "--effort", "high",
                    "--grounded-weaknesses"]
            complete_ids = [{"forum_id": i} for i in ids]
            outcomes = [
                ({"scored": complete_ids, "awaiting_judgment": [], "errors": []}, 0, "complete"),
                ({"scored": [], "awaiting_judgment": [], "errors": [{"error": "toy"}]}, 2, "incomplete"),
                ({"scored": complete_ids[:1], "awaiting_judgment": [], "errors": []}, 2, "incomplete"),
                ({"scored": [], "awaiting_judgment": [{"awaiting": True}], "errors": []}, 3, "incomplete"),
                (RuntimeError("synthetic interrupted run"), 2, "incomplete"),
            ]
            previous = None
            for result, code, state in outcomes:
                def run(*args, **kwargs):
                    self.assertEqual(json.loads(file.read_text())["run_state"], "incomplete")
                    if isinstance(result, Exception):
                        raise result
                    return result
                with patch("autopaperreview.public_calibration.run_cache", side_effect=run), \
                     redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args), code)
                stored = json.loads(file.read_text())
                self.assertEqual(stored["run_state"], state)
                self.assertNotEqual(stored["run_id"], previous)
                previous = stored["run_id"]

    def test_rank_eval_requires_model_and_refuses_an_incomplete_cache(self) -> None:
        rank_eval = _rank_eval()
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            output = Path(temporary) / "out.json"
            ids = dev_ids()
            _score(cache, ids[0], 3.0, [3, 5])
            _score(cache, ids[1], 7.0, [8, 8])
            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                missing = rank_eval.main(["--cache", str(cache), "--output", str(output)])
            self.assertEqual(missing, 2)
            self.assertIn("model-id", stderr.getvalue())
            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                incomplete = rank_eval.main(
                    [
                        "--cache",
                        str(cache),
                        "--output",
                        str(output),
                        "--model-id",
                        "grok-4.7",
                        "--effort",
                        "xhigh",
                    ]
                )
            self.assertEqual(incomplete, 2)
            self.assertIn("incomplete", stderr.getvalue())
            self.assertNotIn(ids[0], stderr.getvalue())
            with redirect_stdout(io.StringIO()):
                code = rank_eval.main(
                    [
                        "--cache",
                        str(cache),
                        "--output",
                        str(output),
                        "--fixed-set",
                        "none",
                    ]
                )
            self.assertEqual(code, 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["n"], 2)
            self.assertNotIn("model_id", saved)

    def test_rank_eval_records_a_complete_dev_cache(self) -> None:
        rank_eval = _rank_eval()
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            output = Path(temporary) / "out.json"
            for index, forum_id in enumerate(dev_ids()):
                _score(cache, forum_id, 1.0 + index / 10, [1, 3] if index % 2 == 0 else [8, 10])
            stderr = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                code = rank_eval.main(
                    [
                        "--cache",
                        str(cache),
                        "--output",
                        str(output),
                        "--model-id",
                        "grok-4.7",
                        "--effort",
                        "xhigh",
                        "--resamples",
                        "20",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("resamples", stderr.getvalue())
            with redirect_stdout(io.StringIO()):
                code = rank_eval.main(
                    [
                        "--cache",
                        str(cache),
                        "--output",
                        str(output),
                        "--model-id",
                        "grok-4.7",
                        "--effort",
                        "xhigh",
                    ]
                )
            self.assertEqual(code, 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["fixed_set"], "iclr-dev")
            self.assertEqual(saved["status"], "locked")
            self.assertEqual(saved["model_id"], "grok-4.7")
            self.assertEqual(saved["effort"], "xhigh")
            self.assertEqual(saved["content_sha256"], assert_manifest()["content_sha256"])
            self.assertEqual(saved["n"], 192)
            self.assertEqual(len({row["stratum"] for row in saved["rows"]}), 4)

    def test_baseline_cache_fills_missing_scores_and_rejects_a_mismatch(self) -> None:
        rank_eval = _rank_eval()
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            baseline = root / "baseline"
            output = root / "out.json"
            held = cache / "papers" / "SynBase01" / "heldout"
            held.mkdir(parents=True)
            (held / "human.json").write_text(
                json.dumps({"decision": "reject", "ratings": [1, 3]}),
                encoding="utf-8",
            )
            held = cache / "papers" / "SynBase02" / "heldout"
            held.mkdir(parents=True)
            (held / "human.json").write_text(
                json.dumps({"decision": "accept", "ratings": [8, 10]}),
                encoding="utf-8",
            )
            for forum_id, rank in (("SynBase01", 2.0), ("SynBase02", 8.0)):
                directory = baseline / "papers" / forum_id
                directory.mkdir(parents=True)
                (directory / "our_score.json").write_text(
                    json.dumps({"forum_id": forum_id, "rank_score": rank, "our_bin": 3, "candidates": {}}),
                    encoding="utf-8",
                )
            meta = {
                "commit": "abc123",
                "content_sha256": digest,
                "effort": "xhigh",
                "model_id": "grok-4.7",
                "n": 2,
            }
            (baseline / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            loaded = load_baseline_cache(
                baseline,
                commit="abc123",
                model_id="grok-4.7",
                effort="xhigh",
                content_sha256=digest,
            )
            self.assertEqual(set(loaded["scores"]), {"SynBase01", "SynBase02"})
            with self.assertRaises(FixedEvalError) as caught:
                load_baseline_cache(
                    baseline,
                    commit="other",
                    model_id="grok-4.7",
                    effort="xhigh",
                    content_sha256=digest,
                )
            self.assertIn("commit", str(caught.exception))
            self.assertNotIn("SynBase01", str(caught.exception))
            with redirect_stdout(io.StringIO()):
                code = rank_eval.main(
                    [
                        "--cache",
                        str(cache),
                        "--output",
                        str(output),
                        "--fixed-set",
                        "none",
                        "--baseline-cache",
                        str(baseline),
                        "--baseline-commit",
                        "abc123",
                        "--baseline-digest",
                        digest,
                        "--model-id",
                        "grok-4.7",
                        "--effort",
                        "xhigh",
                    ]
                )
            self.assertEqual(code, 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["n"], 2)
            self.assertEqual({row["rank_score"] for row in saved["rows"]}, {2.0, 8.0})

    def test_rank_eval_exits_2_when_the_digest_does_not_match(self) -> None:
        rank_eval = _rank_eval()
        payload = json.loads(manifest_path().read_text(encoding="utf-8"))
        payload["content_sha256"] = "0" * 64
        original = rank_eval.assert_manifest

        def mismatched(manifest=None):
            return original(payload if manifest is None else manifest)

        rank_eval.assert_manifest = mismatched
        try:
            with tempfile.TemporaryDirectory() as temporary:
                cache = Path(temporary) / "cache"
                output = Path(temporary) / "out.json"
                stderr = io.StringIO()
                with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                    code = rank_eval.main(
                        [
                            "--cache",
                            str(cache),
                            "--output",
                            str(output),
                            "--model-id",
                            "grok-4.7",
                            "--effort",
                            "xhigh",
                        ]
                    )
            self.assertEqual(code, 2)
            self.assertIn("digest", stderr.getvalue())
        finally:
            rank_eval.assert_manifest = original

    def test_calibrate_eval_fixed_set_is_opt_in(self) -> None:
        parser = build_parser()
        defaults = parser.parse_args(["calibrate-eval", "run", "--cache", "workdir/cache"])
        self.assertEqual(defaults.fixed_set, "none")
        synthetic = {"content_sha256": "b" * 64, "status": "locked"}
        with tempfile.TemporaryDirectory() as temporary, patch("autopaperreview.fixed_eval.assert_manifest", return_value=synthetic), \
             patch("autopaperreview.fixed_eval.dev_ids", return_value=["synthetic-missing"]):
            cache = Path(temporary) / "cache"
            cache.mkdir()
            ids = cache / "ids.json"
            ids.write_text("[]\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "calibrate-eval",
                        "run",
                        "--cache",
                        str(cache),
                        "--fixed-set",
                        "iclr-dev",
                        "--model-id",
                        "grok-4.7",
                        "--effort",
                        "xhigh",
                        "--only-ids",
                        str(ids),
                    ]
                )
            self.assertEqual(code, 2)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "calibrate-eval",
                        "run",
                        "--cache",
                        str(cache),
                        "--fixed-set",
                        "iclr-dev",
                        "--model-id",
                        "grok-4.7",
                        "--effort",
                        "xhigh",
                        "--cheap",
                        "--holistic-mode",
                        "forecast",
                        "--holistic-samples",
                        "1",
                        "--related-work",
                        "off",
                        "--coefficient-set",
                        "apr-fusion-v1",
                    ]
                )
            self.assertEqual(code, 2)
            stamp = json.loads((cache / "fixed-eval-run.json").read_text(encoding="utf-8"))
            self.assertEqual(stamp["model_id"], "grok-4.7")
            self.assertEqual(stamp["effort"], "xhigh")
            self.assertEqual(stamp["status"], "locked")
            self.assertEqual(stamp["content_sha256"], synthetic["content_sha256"])
            self.assertEqual(stamp["run_state"], "incomplete")

    def test_cvpr_run_refuses_sealed_and_the_excluded_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                sealed = main(
                [
                    "calibrate-eval",
                    "cvpr-tier",
                    "run",
                    "--cache",
                    str(cache),
                    "--model-id",
                    "grok-4.7-cursor",
                    "--backend",
                    "command",
                    "--set",
                    "S24-C1",
                ]
            )
            self.assertEqual(sealed, 2)
            material = cache / "sets" / "S24-D2"
            material.mkdir(parents=True)
            papers = [{"paper_id": f"d2synth{index:04d}"} for index in range(49)]
            papers.append({"paper_id": EXCLUDED})
            (material / "sample_manifest.json").write_text(
                json.dumps({"papers": papers}),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                refused = main(
                [
                    "calibrate-eval",
                    "cvpr-tier",
                    "run",
                    "--cache",
                    str(cache),
                    "--model-id",
                    "grok-4.7-cursor",
                    "--backend",
                    "command",
                    "--set",
                    "S24-D2",
                ]
            )
            self.assertEqual(refused, 2)


if __name__ == "__main__":
    unittest.main()
