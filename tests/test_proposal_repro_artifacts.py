"""Headline aggregates stay in git. Per-paper rows are external."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
