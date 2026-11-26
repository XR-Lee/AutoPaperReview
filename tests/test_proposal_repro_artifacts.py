"""The committed proposal artifacts regenerate the published headlines."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.proposal.repro_metrics import PUBLISHED, reproduce_headlines  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProposalArtifactTests(unittest.TestCase):
    def test_reproduced_numbers_match_within_1e_6(self) -> None:
        got = reproduce_headlines(ROOT / "benchmarks" / "proposal" / "repro")
        self.assertEqual(got["published_headlines"], PUBLISHED)
        self.assertEqual(PUBLISHED, "reproduced from committed artifacts")
        expected = {
            "fixed100": {
                "n": 100,
                "decision_n": 100,
                "rho": 0.6768495256982237,
                "rho_ci95": [0.5592595277263185, 0.7719101281336322],
                "auc": 0.8120978120978121,
                "hits": [12, 12],
                "random_expected": 5.0,
            },
            "confirm100": {
                "n": 100,
                "decision_n": 100,
                "rho": 0.5972059705044885,
                "rho_ci95": [0.4650658687336852, 0.7126145126816626],
                "auc": 0.7942942942942943,
                "hits": [11, 11],
                "random_expected": 5.0,
            },
            "replications199": {
                "n": 199,
                "decision_n": 141,
                "enrolled": 200,
                "rho": 0.5215290007489868,
                "rho_ci95": [0.40019913238862875, 0.6373511024333817],
                "auc": 0.8045454545454546,
                "hits": [23, 23],
                "random_expected": 10.050251256281406,
            },
        }
        for name, want in expected.items():
            cohort = got[name]
            self.assertEqual(cohort["n"], want["n"], name)
            self.assertEqual(cohort["decision_n"], want["decision_n"], name)
            self.assertAlmostEqual(cohort["rho"], want["rho"], delta=1e-6)
            self.assertAlmostEqual(cohort["auc"], want["auc"], delta=1e-6)
            self.assertAlmostEqual(cohort["rho_ci95"][0], want["rho_ci95"][0], delta=1e-6)
            self.assertAlmostEqual(cohort["rho_ci95"][1], want["rho_ci95"][1], delta=1e-6)
            self.assertEqual(cohort["top20"]["included_positive_range"], want["hits"])
            self.assertAlmostEqual(
                cohort["top20"]["random_expected"], want["random_expected"], delta=1e-6
            )
            self.assertAlmostEqual(cohort["evaluate_rank"]["apr_rho"], want["rho"], delta=1e-6)
            self.assertAlmostEqual(cohort["evaluate_rank"]["apr_auc"], want["auc"], delta=1e-6)
        self.assertEqual(got["replications199"]["enrolled"], 200)
        nlp = got["nlp84"]
        self.assertEqual(nlp["n"], 84)
        self.assertAlmostEqual(nlp["rho"], 0.27260518273206585, delta=1e-6)
        self.assertAlmostEqual(nlp["auc"], 0.6805555555555556, delta=1e-6)
        self.assertAlmostEqual(nlp["rho_ci95"][0], 0.04536137130794999, delta=1e-6)
        self.assertAlmostEqual(nlp["rho_ci95"][1], 0.47156712182523136, delta=1e-6)
        self.assertAlmostEqual(nlp["auc_ci95"][0], 0.43415637860082307, delta=1e-6)
        self.assertAlmostEqual(nlp["auc_ci95"][1], 0.8846153846153846, delta=1e-6)

    def test_production_prediction_hashes(self) -> None:
        root = ROOT / "benchmarks" / "proposal" / "repro"
        self.assertEqual(
            _sha256(root / "fixed100" / "fixed-predictions.json"),
            "105253f784ab3a8f2db210d77881bc79729443b70b1525f3e1fd6029490a1ce0",
        )
        self.assertEqual(
            _sha256(root / "confirm100" / "fresh-predictions.json"),
            "8e15748f1a43d5dd4d966e8b5fb504751c8debb87b64f856a5cd381073df2d50",
        )
        self.assertEqual(
            _sha256(root / "replications199" / "joined.json"),
            "06e43684cf123ad673e4e6285b64657c90de393b703b747a74f6512426742d68",
        )

    def test_committed_artifacts_omit_review_prose_and_forum_urls(self) -> None:
        root = ROOT / "benchmarks" / "proposal" / "repro"
        for name in ("fixed100", "confirm100"):
            for row in json.loads((root / name / "references.json").read_text(encoding="utf-8")):
                self.assertNotIn("opinion_evidence", row)
                self.assertNotIn("quotes", row)
        index = json.loads(
            (root / "replications199" / "blind-index.json").read_text(encoding="utf-8")
        )
        self.assertTrue(index)
        self.assertNotIn("plan", index[0])
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("openreview.net/" + "forum?id=", text, path.name)
            self.assertNotIn("openreview.net/" + "pdf?id=", text, path.name)

    def test_reproduce_script_prints_committed_headlines(self) -> None:
        script = ROOT / "scripts" / "reproduce_proposal_rank.py"
        completed = subprocess.run(
            [sys.executable, str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        saved = json.loads(completed.stdout)
        self.assertEqual(saved["published_headlines"], "reproduced from committed artifacts")
        self.assertAlmostEqual(saved["fixed100"]["rho"], 0.6768495256982237, delta=1e-6)
        self.assertAlmostEqual(saved["nlp84"]["auc"], 0.6805555555555556, delta=1e-6)
        self.assertNotIn("not yet reproducible", completed.stdout)
        self.assertNotIn("not yet reproducible", completed.stderr)


if __name__ == "__main__":
    unittest.main()
