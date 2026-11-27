import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.fixed_benchmark import blind_inputs, cohort_cases, evaluate_fixed
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
        for value in ("", " \t", [], {}, 4, False):
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

    def test_null_reference_scale_uses_the_lock_but_null_lock_scale_is_invalid(self):
        ref = self.reference(rating_scale=None, final_rating=0)
        validate_cases([{**ref, "year": 2026, "reviews": []}])
        self.assertEqual(self.evaluate(self.manifest(year=2026), ref)["n"], 1)
        with self.assertRaisesRegex(ValueError, "rating_scale"):
            self.evaluate(self.manifest(rating_scale=None), self.reference())

    def test_non_string_manifest_and_reference_strata_are_rejected(self):
        for value in (None, [], {}, 3, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "stratum"):
                self.evaluate(self.manifest(stratum=value), self.reference())
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "stratum"):
                self.evaluate(self.manifest(), self.reference(stratum=value))

    def test_versionless_partition_alias_cannot_enter_as_a_test_case(self):
        lock = self.manifest()
        del lock["version"]
        lock["cases"].append({"forum": "synthetic-sealed", "partition": "holdout"})
        self.assertEqual(set(cohort_cases(lock)), {self.forum})
        lock["cases"][1]["split"] = "test"
        with self.assertRaisesRegex(ValueError, "conflicting cohort split"):
            cohort_cases(lock)

    def test_versionless_tier_alias_must_be_a_string(self):
        lock = self.manifest(tier="synthetic-tier")
        del lock["version"]
        self.assertEqual(self.evaluate(lock, self.reference())["n"], 1)
        for value in (True, False, 3, None, []):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "tier must be a string"):
                lock["cases"][0]["tier"] = value
                self.evaluate(lock, self.reference())

    def test_evaluate_rank_rejects_output_alias_of_input(self):
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            root = Path(tmp)
            paths = {name: root / f"{name}.json" for name in ("manifest", "predictions", "references")}
            write(paths["manifest"], self.manifest())
            write(paths["predictions"], [{"forum": self.forum, "overall": 3}])
            write(paths["references"], [self.reference()])
            original = {path: path.read_bytes() for path in paths.values()}
            output = root / "output.json"
            output.hardlink_to(paths["predictions"])
            options = [arg for name, path in paths.items() for arg in (f"--{name}", str(path))]
            run = subprocess.run([
                sys.executable, "-m", "autopaperreview", "proposal-review", "evaluate-rank",
                *options, "--output", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("output must be distinct", run.stderr)
            self.assertEqual({path: path.read_bytes() for path in paths.values()}, original)

    def test_predict_rank_requires_historical_dates_on_the_lock(self):
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            root = Path(tmp)
            ids = [f"synthetic-locked-{i}" for i in range(100)]
            cases = [{"forum": forum, "split": "test", "year": 2024, "cutoff_ms": 20} for forum in ids]
            write(root / "proposals.json", [{"forum": forum, "proposal": "Study routing patterns."} for forum in ids])
            write(root / "profile.json", {"ranker": {"names": ["field_priority"]}})
            arguments = ["proposal-review", "predict-rank", "--screen-outcomes"]
            for name in ("manifest", "proposals", "profile", "output", "work"):
                arguments.extend([f"--{name}", str(root / f"{name}.json")])
            for field, message in (("year", "target year"), ("cutoff_ms", "cutoff")):
                first = dict(cases[0])
                del first[field]
                write(root / "manifest.json", {"version": 1, "cases": [first, *cases[1:]],
                                              "ids_sha256": digest(sorted(ids))})
                stderr = io.StringIO()
                with (self.subTest(field=field), redirect_stderr(stderr), redirect_stdout(io.StringIO()),
                      patch("autopaperreview.proposal.fixed_benchmark.model_leak_judge") as judge):
                    self.assertEqual(main(arguments), 2)
                    self.assertIn(message, stderr.getvalue())
                    judge.assert_not_called()
            self.assertFalse((root / "output.json").exists())

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
            path = Path(tmp) / "inputs"
            path.mkdir()
            output = path.parent / "out.json"
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
