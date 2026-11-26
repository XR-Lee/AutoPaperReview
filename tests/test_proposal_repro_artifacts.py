"""Headline aggregates stay in git. Per-paper rows are external."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.proposal.repro_metrics import (  # noqa: E402
    EXTERNAL_DIR_ENV,
    PUBLISHED,
    _top_fifth,
    committed_aggregates,
    headlines_match,
    identifier_key,
    nlp_from_rows,
    normalize_table,
    reproduce_headlines,
)
from autopaperreview.public_calibration import spearman  # noqa: E402
from autopaperreview.proposal.overall_rank import top_fraction  # noqa: E402
from scripts import rank_eval, reproduce_proposal_rank  # noqa: E402


def _external_dir() -> Path | None:
    raw = os.environ.get(EXTERNAL_DIR_ENV)
    if not raw:
        return None
    path = Path(raw)
    marker = path / "fixed100" / "fixed-predictions.json"
    return path if marker.is_file() else None


class ProposalArtifactTests(unittest.TestCase):
    def test_committed_aggregates_have_no_recompute_claim(self) -> None:
        saved = committed_aggregates()
        self.assertEqual(saved["published_headlines"], PUBLISHED)
        self.assertEqual(PUBLISHED, "headline aggregates are committed; per-paper rows stay outside git")
        self.assertFalse(saved["recomputed"])
        self.assertEqual(saved["resampling"]["iclr_draws"], 3000)
        self.assertEqual(saved["resampling"]["nlp84_draws"], 2000)
        self.assertIn("not the 3000-draw", saved["resampling"]["nlp84_method"])
        self.assertAlmostEqual(saved["fixed100"]["rho"], 0.6768495256982237, delta=1e-12)
        self.assertAlmostEqual(saved["nlp84"]["auc"], 0.6805555555555556, delta=1e-12)
        self.assertNotIn("forum", json.dumps(saved))

    def test_synthetic_rows_match_the_rank_math(self) -> None:
        import numpy as np

        rows = [
            {"forum": "synthetic-a", "stratum": "low", "score": 1.0, "final": 2.0, "accept": 0},
            {"forum": "synthetic-b", "stratum": "low", "score": 2.0, "final": 4.0, "accept": 0},
            {"forum": "synthetic-c", "stratum": "high", "score": 3.0, "final": 8.0, "accept": 1},
            {"forum": "synthetic-d", "stratum": "high", "score": 4.0, "final": 9.0, "accept": 1},
        ]
        scores = [row["score"] for row in rows]
        finals = [row["final"] for row in rows]
        self.assertAlmostEqual(spearman(scores, finals), 1.0)
        array = np.array(scores, dtype=float)
        positive = np.array([row["final"] >= 7 for row in rows])
        top = _top_fifth(array, positive, np)
        self.assertEqual(top["k"], 1)
        self.assertEqual(top["included_positive_range"], [1, 1])
        self.assertAlmostEqual(top["random_expected"], 0.5)
        proposals = [
            {"topic": "alpha", "overall": 1.0, "human": 1.0},
            {"topic": "alpha", "overall": 2.0, "human": 3.0},
            {"topic": "beta", "overall": 2.0, "human": 9.0},
        ]
        nlp = nlp_from_rows(proposals)
        self.assertEqual(nlp["n"], 3)
        self.assertEqual(nlp["same_topic_comparable_pairs"], 1)
        self.assertEqual(nlp["same_topic_concordance"], 1.0)
        self.assertEqual(nlp["draws"], 2000)
        salt = "synthetic-salt-not-for-real-ids"
        keyed = identifier_key("synthetic-a", salt)
        self.assertEqual(len(keyed), 64)
        self.assertNotEqual(keyed, identifier_key("synthetic-b", salt))
        self.assertEqual(
            normalize_table("left , right\r\nbeta, gamma \r\n"),
            [["left", "right"], ["beta", "gamma"]],
        )

    @unittest.skipUnless(_external_dir() is not None, "APR_PROPOSAL_REPRO_DIR is not set")
    def test_external_rows_match_committed_aggregates(self) -> None:
        got = reproduce_headlines(_external_dir())
        self.assertTrue(got["recomputed"])
        self.assertTrue(headlines_match(got), got)

    def test_reproduce_script_prints_committed_headlines_without_external_rows(self) -> None:
        script = ROOT / "scripts" / "reproduce_proposal_rank.py"
        env = os.environ.copy()
        env.pop(EXTERNAL_DIR_ENV, None)
        completed = subprocess.run(
            [sys.executable, str(script)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        saved = json.loads(completed.stdout)
        self.assertEqual(saved["published_headlines"], PUBLISHED)
        self.assertFalse(saved["recomputed"])
        self.assertAlmostEqual(saved["fixed100"]["rho"], 0.6768495256982237, delta=1e-6)
        self.assertAlmostEqual(saved["nlp84"]["auc"], 0.6805555555555556, delta=1e-6)
        self.assertNotIn("not yet reproducible", completed.stdout)
        absent = subprocess.run(
            [sys.executable, str(script), "--root", str(ROOT / "tests" / "no-such-repro")],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(absent.returncode, 2)
        self.assertIn("not in the repository", absent.stderr)
        self.assertNotIn("reproduced from committed artifacts", absent.stderr)

    def test_headlines_match_rejects_nan(self) -> None:
        committed = committed_aggregates()
        got = json.loads(json.dumps(committed))
        got["recomputed"] = True
        got["fixed100"]["rho"] = float("nan")
        self.assertFalse(headlines_match(got, committed))

    def test_headlines_match_checks_count_types_and_resampling_settings(self) -> None:
        committed = committed_aggregates()
        got = json.loads(json.dumps(committed))
        got["recomputed"] = True
        self.assertTrue(headlines_match(got, committed))
        for field, value in (
            ("n", 100.0000001), ("n", "100"), ("rho", str(got["fixed100"]["rho"])),
            ("draws", 2999), ("seed", 0),
        ):
            with self.subTest(field=field, value=value):
                changed = json.loads(json.dumps(got))
                changed["fixed100"][field] = value
                self.assertFalse(headlines_match(changed, committed))
        got["nlp84"]["threshold"] += 1
        self.assertFalse(headlines_match(got, committed))

    def test_top_fraction_reports_the_same_cutoff_ties_after_reordering(self) -> None:
        import numpy as np

        scores = [3] * 6
        ratings = [8, 8, 2, 2, 2, 2]
        expected = top_fraction(scores, ratings)
        self.assertEqual(expected["k"], 2)
        self.assertIsNone(expected["held"])
        self.assertEqual(expected["included_positive_range"], [0, 2])
        self.assertEqual(top_fraction(scores, list(reversed(ratings))), expected)
        published = _top_fifth(np.array(scores), np.array(ratings) >= 7, np)
        self.assertEqual(published["included_positive_range"], expected["included_positive_range"])

    def test_normalize_table_handles_multiline_tsv_header_and_rejects_bad_quoting(self) -> None:
        self.assertEqual(
            normalize_table('"left\r\n column"\tright\r\nvalue\titem\r\n'),
            [["left\n column", "right"], ["value", "item"]],
        )
        for text in ('"unclosed\n', 'left,right\n"one"extra,two\n'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "invalid CSV or TSV"):
                normalize_table(text)

    def test_metrics_reject_nonfinite_and_degenerate_samples(self) -> None:
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be finite"):
                top_fraction([1, value], [3, 7])
        for pairs in ((), ((1, 3), (2, 4)), ((1, 3), (1, 7))):
            rows = [{"topic": "synthetic", "overall": score, "human": rating} for score, rating in pairs]
            with self.subTest(pairs=pairs), self.assertRaisesRegex(ValueError, "needs varying scores"):
                nlp_from_rows(rows)

    def test_custom_reproduction_refuses_all_output_input_aliases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            root = Path(tmp)
            args = []
            for arg in ("manifest", "predictions", "references"):
                path = root / f"{arg}.json"
                path.write_text("{}\n", encoding="utf-8")
                args.extend([f"--{arg}", str(path)])
            for arg in ("manifest", "predictions", "references"):
                source = root / f"{arg}.json"
                symlink = root / f"{arg}.symlink"
                symlink.symlink_to(source)
                hardlink = root / f"{arg}.hardlink"
                os.link(source, hardlink)
                for output in (source, symlink, hardlink):
                    with self.subTest(arg=arg, output=output.name), redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as stdout:
                        self.assertEqual(reproduce_proposal_rank.main([*args, "--output", str(output)]), 2)
                        self.assertIn("output must be distinct", error.getvalue())
                        self.assertFalse(stdout.getvalue())
                    self.assertEqual(source.read_text(encoding="utf-8"), "{}\n")

    def test_rank_eval_refuses_hardlink_to_ids_before_reading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            root = Path(tmp)
            ids = root / "ids.json"
            ids.write_text("synthetic unreadable ID list", encoding="utf-8")
            output = root / "result.json"
            os.link(ids, output)
            with redirect_stderr(StringIO()) as error:
                self.assertEqual(rank_eval.main([
                    "--cache", str(root), "--ids", str(ids), "--output", str(output), "--fixed-set", "none",
                ]), 2)
            self.assertIn("output must be distinct", error.getvalue())
            self.assertEqual(ids.read_text(encoding="utf-8"), "synthetic unreadable ID list")

    def test_rank_eval_refuses_hardlink_to_fixed_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text("synthetic manifest", encoding="utf-8")
            output = root / "result.json"
            os.link(manifest, output)
            with patch.object(rank_eval, "manifest_path", return_value=manifest), redirect_stderr(StringIO()) as error:
                self.assertEqual(rank_eval.main(["--cache", str(root), "--output", str(output)]), 2)
            self.assertIn("output must be distinct", error.getvalue())
            self.assertEqual(manifest.read_text(encoding="utf-8"), "synthetic manifest")

    def test_rekey_script_replaces_synthetic_ids_with_hmac(self) -> None:
        script = ROOT / "scripts" / "rekey_external_repro.py"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "in"
            output = Path(temporary) / "out"
            cohort = source / "fixed100"
            cohort.mkdir(parents=True)
            (cohort / "references.json").write_text(
                json.dumps([{"key": "synthetic-forum", "final_rating": 6, "accept": 1}]),
                encoding="utf-8",
            )
            (cohort / "rows.csv").write_text(
                "key,final_rating\nsynthetic-forum,6\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["APR_PROPOSAL_REPRO_SALT"] = "synthetic-salt-not-for-real-ids"
            completed = subprocess.run(
                [sys.executable, str(script), "--root", str(source), "--output", str(output)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rewritten = (output / "fixed100" / "references.json").read_text(encoding="utf-8")
            table = (output / "fixed100" / "rows.csv").read_text(encoding="utf-8")
        self.assertNotIn("synthetic-forum", rewritten)
        self.assertNotIn("synthetic-forum", table)
        self.assertNotIn("synthetic-forum", completed.stdout)
        self.assertIn("rewrote", completed.stdout)


if __name__ == "__main__":
    unittest.main()
