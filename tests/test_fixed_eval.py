"""Fixed evaluation manifest. Synthetic holdout rows only; the committed list is public."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from autopaperreview.cli import build_parser, main
from autopaperreview.cvpr_sets import id_list_digest as cvpr_id_list_digest
from autopaperreview.fixed_eval import (
    DEV_PER_TIER,
    HOLDOUT_PER_TIER,
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
    main as fixed_main,
    manifest_path,
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
    def test_committed_manifest_is_proposed_and_balanced(self) -> None:
        payload = assert_manifest()
        self.assertEqual(payload["status"], "proposed")
        self.assertEqual(payload["content_sha256"], content_digest(payload))
        papers = dev_papers(payload)
        self.assertEqual(len(papers), 48)
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
        self.assertEqual(len(dev_ids(assert_run_ids(dev_ids(), kind="iclr-dev"))), 48)

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
            self.assertEqual(len(checked), 12)
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
        self.assertEqual(len(papers), 12)

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
            self.assertEqual(saved["status"], "proposed")
            self.assertEqual(saved["model_id"], "grok-4.7")
            self.assertEqual(saved["effort"], "xhigh")
            self.assertEqual(saved["content_sha256"], assert_manifest()["content_sha256"])
            self.assertEqual(saved["n"], 48)
            self.assertEqual(len({row["stratum"] for row in saved["rows"]}), 4)

    def test_calibrate_eval_fixed_set_is_opt_in(self) -> None:
        parser = build_parser()
        defaults = parser.parse_args(["calibrate-eval", "run", "--cache", "workdir/cache"])
        self.assertEqual(defaults.fixed_set, "none")
        with tempfile.TemporaryDirectory() as temporary:
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
            self.assertEqual(code, 0)
            stamp = json.loads((cache / "fixed-eval-run.json").read_text(encoding="utf-8"))
            self.assertEqual(stamp["model_id"], "grok-4.7")
            self.assertEqual(stamp["effort"], "xhigh")
            self.assertEqual(stamp["status"], "proposed")
            self.assertEqual(stamp["content_sha256"], assert_manifest()["content_sha256"])

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
