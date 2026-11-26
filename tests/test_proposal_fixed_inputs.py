import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.fixed_benchmark import blind_inputs, evaluate_fixed
from autopaperreview.proposal.reference_coding import code_paper, validate_cases
from autopaperreview.rating_scales import scale_for_key


class ProposalFixedInputTests(unittest.TestCase):
    forum = "synthetic-fixed"

    def manifest(self, **case_fields):
        return {
            "version": 1,
            "cases": [{"forum": self.forum, "split": "test", **case_fields}],
            "ids_sha256": digest([self.forum]),
        }

    def reference(self, **fields):
        return {"forum": self.forum, "prospective": 3, "final_rating": 4, **fields}

    def evaluate(self, lock, reference):
        return evaluate_fixed(
            lock,
            [{"forum": self.forum, "overall": 3}],
            [reference],
            bootstrap=0,
            stratified_draws=0,
        )

    def test_reference_scale_without_year_must_match_lock_year(self):
        lock = self.manifest(year=2026)
        with self.assertRaisesRegex(ValueError, "rating_scale disagrees"):
            self.evaluate(lock, self.reference(rating_scale="iclr2024"))
        self.assertEqual(self.evaluate(lock, self.reference(final_rating=0))["n"], 1)
        self.assertEqual(
            self.evaluate(lock, self.reference(rating_scale="iclr2026", final_rating=0))["n"], 1
        )
        with self.assertRaisesRegex(ValueError, "does not match rating_scale"):
            self.evaluate(self.manifest(year=2026, rating_scale="iclr2024"), self.reference())

    def test_signed_explicit_lock_range_bounds_reference(self):
        scale = scale_for_key("range:-2-4")
        self.assertEqual((scale.minimum, scale.maximum), (-2, 4))
        self.assertEqual(scale_for_key("range:-4--2").maximum, -2)
        self.assertEqual(scale_for_key("range:-2-+4").key, scale.key)
        lock = self.manifest(rating_scale=scale.key)
        for value in (-2, 0, 4):
            with self.subTest(value=value):
                ref = self.reference(final_rating=value)
                validate_cases([{**ref, "reviews": [], "rating_scale": scale.key}])
                self.assertEqual(self.evaluate(lock, ref)["n"], 1)
        for value in (-2.1, 4.1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "outside"):
                self.evaluate(lock, self.reference(final_rating=value))
        with self.assertRaisesRegex(ValueError, "rating_scale disagrees"):
            self.evaluate(lock, self.reference(rating_scale="range:-4-4"))

    def test_malformed_scale_rejected_for_lock_and_reference_before_model(self):
        for value in (None, "", " \t", [], {}, 4, False):
            with self.subTest(value=value):
                ref = self.reference(rating_scale=value)
                row = {**ref, "reviews": []}
                with self.assertRaisesRegex(ValueError, "rating_scale must be a non-empty string"):
                    validate_cases([row])
                with patch("autopaperreview.proposal.reference_coding.invoke") as call:
                    with self.assertRaisesRegex(ValueError, "rating_scale"):
                        code_paper(row, Path("."), ["synthetic"], leak_judge=lambda _: {"verdict": "clean"})
                    call.assert_not_called()
                with self.assertRaisesRegex(ValueError, "rating_scale"):
                    self.evaluate(self.manifest(), ref)
                with self.assertRaisesRegex(ValueError, "rating_scale"):
                    self.evaluate(self.manifest(rating_scale=value), self.reference())

    def test_locked_stratum_controls_resampling_and_decision_bit_is_strict(self):
        lock = self.manifest(stratum="locked-tier")
        predictions = [{"forum": self.forum, "overall": 3}]
        with patch("autopaperreview.proposal.fixed_benchmark.stratified_ci", return_value={}) as ci:
            evaluate_fixed(lock, predictions, [self.reference(stratum="supplied-tier")], bootstrap=0)
        self.assertEqual(ci.call_args.args[0][0]["stratum"], "locked-tier")
        for value in (True, False, 1.0, 0.0):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "accept"):
                self.evaluate(lock, self.reference(accept=value))

    def test_invalid_fixed_artifacts_exit_two_in_both_commands(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "reproduce_proposal_rank.py"
        pred = [{"forum": self.forum, "overall": 3}]
        refs = [self.reference()]
        invalid = (
            (self.manifest(), None, refs),
            (self.manifest(), [None], refs),
            (self.manifest(), [{"forum": self.forum}], refs),
            (self.manifest(), [{"forum": self.forum, "overall": 10**400}], refs),
            (self.manifest(), pred, [self.reference(final_rating=10**400)]),
            (self.manifest(split="unexpected"), pred, refs),
            ({**self.manifest(), "version": True}, pred, refs),
        )
        with TemporaryDirectory(prefix="apr-pr70-fix-r1-") as tmp:
            path = Path(tmp)
            output = path / "out.json"
            arguments = ["--output", str(output)]
            for name in ("manifest", "predictions", "references"):
                arguments += [f"--{name}", str(path / f"{name}.json")]
            commands = (
                [sys.executable, "-m", "autopaperreview", "proposal-review", "evaluate-rank"],
                [sys.executable, str(script)],
            )
            for index, documents in enumerate(invalid):
                for name, document in zip(("manifest", "predictions", "references"), documents):
                    write(path / f"{name}.json", document)
                for command in commands:
                    with self.subTest(case=index, command=command[-1]):
                        run = subprocess.run([*command, *arguments], capture_output=True, text=True)
                        self.assertEqual(run.returncode, 2, run.stderr)
                        self.assertNotIn("Traceback", run.stderr)
                        self.assertFalse(output.exists())

    def test_blind_cutoff_and_arxiv_lock_normalization(self):
        ids = [f"synthetic-blind-{i}" for i in range(100)]
        lock = {
            "cases": [{"forum": forum, "split": "test", "arxiv": "2301.00001"} for forum in ids],
            "ids_sha256": digest(sorted(ids)),
        }
        proposals = [
            {
                "forum": forum,
                "proposal": "Compare hypothetical grid layouts.",
                "arxiv_id": "https://export.arxiv.org/pdf/2301.00001v2.pdf?download=1#page=2",
            }
            for forum in ids
        ]
        inputs = blind_inputs(lock, proposals)
        self.assertEqual(inputs[0][2]["arxiv"], "2301.00001")
        lock["cases"][0]["cutoff_ms"] = 0
        with self.assertRaisesRegex(ValueError, "positive integer millisecond timestamp"):
            blind_inputs(lock, proposals)
