"""Round-10 blockers: format-agnostic facts, pre-judge eligibility, predict-rank, publish."""

import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal import claim_review, file_io, priority, runner, stages
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.leak_audit import HISTORY_PROMPT, PROMPT, parse_history_reply, parse_reply
from autopaperreview.proposal.prior_index import (
    HistoryJudgeError, calibration_rows, eligible_history, pre_judge_eligible, proposal_body,
)
from autopaperreview.proposal.sanitized import factual_leak

ROOT = Path(__file__).resolve().parents[1]
TARGET = "Violet tags index archival baskets by location on cloth shelf zero."
NEAR = "Violet tags index archival baskets by shelfplace on cloth shelf zero."
SHARED = "Violet cloth labels sit on a different shelf from the ranked basket."

# Frozen before the splitter landed. These spellings are not classifier terms.
UNSEEN_FACTS = (
    "Review status :: in committee",
    "Review status := with the editor",
    "Estado :: en comite",
    "审稿状态 :: 待委员会",
    "Manuscript status ~> galley",
    "Editorial status » embargoed",
    "Submission status │ proofs returned",
    "Review status ‡ awaiting committee",
    "Paper status ;; shelved",
    "<!-- Review status :: shelved -->",
    "/* Estado :: en prensa */",
    "% 审稿状态 :: 校样",
    "# Review status :: held for chair",
    "// Review status :: embargoed",
    "\\footnote{Review status :: in committee}",
    "[^1]: Review status :: shelved",
    "<aside>Review status :: galley</aside>",
    '{"review status": "in committee"}',
    '{"meta": {"review_status": "with the editor"}}',
    'review_status = "embargoed"',
    '<note reviewstatus="shelved"/>',
    "{'review_status': 'en comite'}",
    "status = shelved",
    "ReviewStatusShelved",
    "审稿状态待委员会",
    'reviewstatus="embargoed"',
    "statusshelved",
    "Review status\nin committee",
    "Estado de revision\nen prensa",
    "Editorial decision :: embargoed",
)
UNSEEN_CONTROLS = (
    "accuracy :: 0.91",
    "loss := 0.20",
    "reconstruction error ~> 0.2",
    '{"accuracy": 0.91, "loss": 0.2}',
    "accuracy │ 0.91",
    "<!-- accuracy :: 0.91 -->",
    "\\footnote{loss := 0.2}",
    "phase transition :: second order",
    "decision boundary := 0.5",
    "state vector ~> (0.2, 0.4)",
    "hidden state :: latent",
    "decision :: argmax",
    "outcome :: softmax",
    "phase :: warmup",
    "state :: latent",
    "The update is $x_{t+1} = x_t - \\eta g_t$.",
    "See the earlier note [Smith 2020].",
    "Use a reconstruction score between 0.1 and 0.3.",
    "Configuration\n============\n0.625",
    "we accept the assumption",
    "the proposal should be accepted",
    "gradient :: 0.02",
    "similarity score :: 0.2",
    "准确率 :: 0.91",
    "StateSpace",
    "PhaseTransition",
    "decision :: embargoed",
)
_ABSENT_SPELLINGS = (
    "shelved", "embargoed", "galley", "committee", "prensa", "待委员会", "校样", "暂缓", "comite",
)


def _row(**changes):
    row = {
        "title": "Other violet basket index",
        "abstract": SHARED,
        "year": 2020,
        "revision_ms": 5,
        "forum": "synthetic-other",
        "split": "dev",
        "available_ms": 5,
        "key": "synthetic-other",
        "opinion_ms": 4,
        "target": 3,
    }
    row.update(changes)
    return row


def _judge(calls, relation="distinct"):
    def judge(body):
        calls.append(body)
        if relation == "invalid":
            return {"verdict": "clean"}
        return {"relation": relation}

    return judge


class UnseenFactRound10Tests(TestCase):
    def test_probe_spellings_are_not_classifier_terms(self):
        source = (ROOT / "src/autopaperreview/proposal/sanitized.py").read_text(encoding="utf-8")
        present = [word for word in _ABSENT_SPELLINGS if word in source]
        self.assertEqual(present, [])

    def test_unseen_formats_block_and_scientific_controls_stay(self):
        missed = [text for text in UNSEEN_FACTS if not factual_leak(text)]
        false_blocks = [text for text in UNSEEN_CONTROLS if factual_leak(text)]
        self.assertEqual(missed, [], f"blocked {len(UNSEEN_FACTS) - len(missed)}/{len(UNSEEN_FACTS)}")
        self.assertEqual(false_blocks, [], f"false blocks {len(false_blocks)}/{len(UNSEEN_CONTROLS)}")
        self.assertTrue(factual_leak("status :: 0.91"))
        self.assertTrue(factual_leak("Overall rating\n8"))
        self.assertFalse(factual_leak("accuracy: 0.91"))


class PreJudgeRound10Tests(TestCase):
    def test_local_key_type_anchor_and_grade_time_never_call_the_judge(self):
        bounds = (2024, 50)
        exclude = proposal_body({"problem": TARGET})
        calls = []
        judge = _judge(calls)
        anchor = _row(key="same-anchor", forum="synthetic-anchor", proposal="Ocean buoy zero tracks kelp drift.")
        duplicate = _row(key="same-anchor", forum="synthetic-near", proposal=NEAR)
        rows = list(calibration_rows(
            [anchor, duplicate], bounds, excerpt_field="opinions", exclude_text=exclude, history_judge=judge,
        ))
        self.assertEqual([row["key"] for row, _dated in rows], ["same-anchor"])
        self.assertEqual(calls, [])

        calls.clear()
        object_key = _row(proposal=NEAR, forum="synthetic-object")
        object_key["key"] = {"nested": "synthetic"}
        self.assertFalse(pre_judge_eligible(
            object_key, bounds, excerpt_field="opinions", exclude_text=exclude,
        ))
        self.assertFalse(eligible_history(object_key, bounds, exclude_text=exclude, history_judge=judge))
        self.assertEqual(list(calibration_rows(
            [object_key], bounds, excerpt_field="opinions", exclude_text=exclude, history_judge=judge,
        )), [])
        boolean_key = _row(proposal=NEAR, forum="synthetic-bool")
        boolean_key["key"] = True
        self.assertFalse(eligible_history(boolean_key, bounds, exclude_text=exclude, history_judge=judge))
        self.assertEqual(calls, [])

        calls.clear()
        undated = _row(proposal=NEAR, forum="synthetic-undated", key="synthetic-undated")
        undated.pop("opinion_ms")
        self.assertFalse(pre_judge_eligible(
            undated, bounds, excerpt_field="opinions", exclude_text=exclude,
        ))
        self.assertEqual(list(calibration_rows(
            [undated], bounds, excerpt_field="opinions", exclude_text=exclude, history_judge=judge,
        )), [])
        self.assertEqual(calls, [])

    def test_distinct_keeps_a_near_copy_and_an_invalid_relation_stops(self):
        bounds = (2024, 50)
        exclude = proposal_body({"problem": TARGET})
        calls = []
        row = _row(proposal=NEAR, forum="synthetic-open", key="synthetic-open")
        got = list(calibration_rows(
            [row], bounds, excerpt_field="opinions", exclude_text=exclude, history_judge=_judge(calls),
        ))
        self.assertEqual(len(calls), 1)
        self.assertEqual([item[0]["key"] for item in got], ["synthetic-open"])
        with self.assertRaises(HistoryJudgeError):
            eligible_history(row, bounds, exclude_text=exclude, history_judge=_judge([], "invalid"))


class PredictRankAbstractRound10Tests(TestCase):
    def _bank(self):
        peers = [
            _row(
                key=f"hist-{index}",
                forum=f"history-{index}",
                title=f"Kelp note {index}",
                abstract=f"Peer note {index}.",
                target=1 + (index % 5),
                proposal={"problem": f"Ocean buoy {index} tracks kelp drift."},
            )
            for index in range(8)
        ]
        near = _row(
            key="hist-near",
            forum="history-near",
            title="Other violet basket index",
            abstract=SHARED,
            proposal={"problem": NEAR},
        )
        return [*peers, near]

    def _profile(self, root):
        write(root / "bank.json", self._bank())
        write(root / "profile.json", {
            "ranker": {"method": "mean", "names": ["anchored"]},
            "prompts": {"match": digest(priority.PROMPTS["match"])},
        })

    def test_library_rank_uses_the_known_abstract_before_the_judge(self):
        bounds_calls = []

        def judge(body):
            task = body.get("task") if isinstance(body, Mapping) else None
            if task == "self_history":
                bounds_calls.append(body)
                return {"relation": "distinct"}
            return {"verdict": "clean"}

        def transport(_argv, *, system, prompt, **_options):
            self.assertNotEqual(system, HISTORY_PROMPT)
            body = json.loads(prompt)
            return json.dumps({
                "favorable": "Concrete plan.",
                "unfavorable": "Thin comparison.",
                "wins": dict.fromkeys(body["examples"], 0.5),
                "estimate": 3.0,
                "confidence": 0.5,
            }), 0.01

        with TemporaryDirectory(prefix="apr-round10-rank-") as folder:
            root = Path(folder)
            self._profile(root)
            profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
            with patch.object(stages, "run_command", side_effect=transport):
                ranked = priority.rank(
                    {"problem": TARGET}, root, profile, root,
                    abstract=SHARED, title="Synthetic basket label", year=2024, cutoff_ms=50,
                    leak_judge=judge,
                )
            self.assertEqual(bounds_calls, [])
            self.assertIn("overall", ranked)
            bounds_calls.clear()
            with patch.object(stages, "run_command", side_effect=transport) as calls:
                priority.rank(
                    {"problem": TARGET}, root / "again", profile, root,
                    abstract="A different shelf note.", title="Synthetic basket label",
                    year=2024, cutoff_ms=50, leak_judge=judge,
                )
            self.assertEqual(len(bounds_calls), 1)
            sent = json.loads(calls.call_args.kwargs["prompt"])
            self.assertIn("shelfplace", json.dumps(sent))

    def test_predict_rank_cli_excludes_the_known_abstract_without_a_history_call(self):
        history = []
        match_prompts = []

        def transport(_argv, *, system, prompt, **_options):
            if system == HISTORY_PROMPT:
                history.append(prompt)
                return '{"verdict":"clean"}', 0.01
            if system == PROMPT:
                return '{"verdict":"clean"}', 0.01
            match_prompts.append(prompt)
            body = json.loads(prompt)
            return json.dumps({
                "favorable": "Concrete plan.",
                "unfavorable": "Thin comparison.",
                "wins": dict.fromkeys(body["examples"], 0.5),
                "estimate": 3.0,
                "confidence": 0.5,
            }), 0.01

        with TemporaryDirectory(prefix="apr-round10-predict-") as folder:
            root = Path(folder)
            self._profile(root)
            ids = [f"synthetic-{index:02d}" for index in range(100)]
            write(root / "cohort.json", {
                "version": 1,
                "cases": [
                    {"forum": fid, "split": "test", "year": 2024, "cutoff_ms": 50}
                    for fid in ids
                ],
                "ids_sha256": digest(sorted(ids)),
            })
            write(root / "blind.json", [
                {
                    "forum": fid,
                    "proposal": TARGET if index == 0 else f"Warehouse bay {index} keeps maple ribbons.",
                    "title": "Synthetic basket label" if index == 0 else f"Bay label {index}",
                    "abstract": SHARED if index == 0 else f"Queue note {index}.",
                }
                for index, fid in enumerate(ids)
            ])
            output = root / "predictions.json"
            err = io.StringIO()
            with (
                redirect_stderr(err), redirect_stdout(io.StringIO()),
                patch("autopaperreview.command_transport.run_command", side_effect=transport),
                patch("autopaperreview.proposal.stages.run_command", side_effect=transport),
            ):
                code = main([
                    "proposal-review", "predict-rank",
                    "--manifest", str(root / "cohort.json"),
                    "--proposals", str(root / "blind.json"),
                    "--profile", str(root / "profile.json"),
                    "--work", str(root / "work"),
                    "--output", str(output),
                    "--jobs", "1",
                ])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(history, [])
            predictions = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(predictions), 100)
            target_prompts = [prompt for prompt in match_prompts if "location on cloth" in prompt]
            self.assertEqual(len(target_prompts), 1)
            self.assertNotIn("shelfplace", target_prompts[0])
            direct = []
            self.assertFalse(eligible_history(
                self._bank()[-1], (2024, 50),
                exclude_abstract=SHARED, exclude_text=proposal_body({"problem": TARGET}),
                history_judge=_judge(direct),
            ))
            self.assertEqual(direct, [])


class HistoryResultRound10Tests(TestCase):
    def _wrap(self, result):
        return json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": result,
        })

    def test_string_and_object_results_are_both_strict(self):
        relation = {"relation": "distinct"}
        self.assertEqual(parse_history_reply(self._wrap(json.dumps(relation))), relation)
        self.assertEqual(parse_history_reply(self._wrap(relation)), relation)
        extra = {"relation": "distinct", "note": "synthetic"}
        for result in (json.dumps(extra), extra):
            with self.assertRaises(ValueError):
                parse_history_reply(self._wrap(result))
        nested = {"type": "result", "result": relation}
        with self.assertRaises(ValueError):
            parse_history_reply(self._wrap(json.dumps(nested)))
        with self.assertRaises(ValueError):
            parse_history_reply(self._wrap(nested))
        with self.assertRaises(ValueError):
            parse_reply(self._wrap({"verdict": "clean"}))


class ReceiptTypeRound10Tests(TestCase):
    def test_pointer_and_receipt_roots_exit_2(self):
        with TemporaryDirectory(prefix="apr-round10-pointer-") as folder:
            root = Path(folder)
            write(root / "manifest.json", {"cases": [{"key": "synthetic", "bundle": "bundle"}]})
            write(root / "inputs" / "bundle" / "provenance.json", {"forum": "synthetic"})
            pointer = root / "runs" / "synthetic" / "review.json"
            for value in ([1, 2, 3], "synthetic", 4):
                write(pointer, value)
                err = io.StringIO()
                with redirect_stderr(err), redirect_stdout(io.StringIO()):
                    code = main(["proposal-review", "export", "--work", str(root), "--reviewer", "review"])
                self.assertEqual(code, 2, err.getvalue())
                self.assertIn("stale", err.getvalue())
                with self.assertRaisesRegex(ValueError, "stale"):
                    runner.stage_result(root, {"key": "synthetic", "bundle": "bundle"}, "review")
            write(pointer, {
                "bundle": "bundle",
                "cache": "cache/review/stub.json",
                "model": "stub-model",
                "command_sha256": "a" * 64,
            })
            write(root / "cache" / "review" / "stub.json", [1, 2, 3])
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = main(["proposal-review", "export", "--work", str(root), "--reviewer", "review"])
            self.assertEqual(code, 2, err.getvalue())
            self.assertIn("stale", err.getvalue())

    def test_claim_receipt_array_is_invalid_before_a_model_call(self):
        judge = lambda _body: {"verdict": "clean"}
        body = {"c1": "Could violet tags index archival baskets by location?"}
        wrapped = json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": {"c1": [50, 30, 1]},
        })
        with TemporaryDirectory(prefix="apr-round10-claim-") as folder:
            root = Path(folder)
            with patch.object(claim_review, "run_command", return_value=(wrapped, 0.01)):
                key, _first = claim_review.request("forecast", body, root, False, leak_judge=judge)
            path = root / "claims" / "forecast" / f"{key}.json"
            write(path, [1, 2, 3])
            with patch.object(claim_review, "run_command") as command:
                with self.assertRaisesRegex(ValueError, "invalid model receipt"):
                    claim_review.request("forecast", body, root, True, leak_judge=judge)
            command.assert_not_called()


_PIN = b'{"files":{"synthetic":"pin"}}\n'
_CHILD = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, os.environ['APR_SRC'])\n"
    "from autopaperreview.proposal import file_io\n"
    "try:\n"
    "    file_io.write_text(Path(os.environ['APR_DEST']), 'NEW')\n"
    "except ValueError:\n"
    "    sys.exit(2)\n"
    "sys.exit(0)\n"
)


class PublishRound10Tests(TestCase):
    """Deterministic injections at the publish windows. No scheduler race."""

    def setUp(self):
        self._saved = dict(file_io._PROTECTED_FILES)

    def tearDown(self):
        file_io._PROTECTED_FILES.clear()
        file_io._PROTECTED_FILES.update(self._saved)

    def _child(self, destination: Path) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update({
            "APR_SRC": str(ROOT / "src"),
            "APR_DEST": str(destination),
            "PYTHONPATH": str(ROOT / "src"),
        })
        return subprocess.run(
            [sys.executable, "-c", _CHILD],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_a_marked_report_can_be_replaced(self):
        with TemporaryDirectory(prefix="apr-round10-mark-") as folder:
            path = Path(folder) / "report.json"
            file_io.write_text(path, "OLD")
            file_io.write_text(path, "NEW")
            self.assertEqual(path.read_text(encoding="utf-8"), "NEW")
            self.assertFalse(list(Path(folder).glob(".apr-output-*.tmp")))
            self.assertFalse(list(Path(folder).glob(".apr-discard-*")))

    def test_discard_rechecks_the_inode_and_leaves_a_stranger(self):
        with TemporaryDirectory(prefix="apr-round10-discard-") as folder:
            root = Path(folder)
            report = root / "report.json"
            stranger = root / "notes.json"
            file_io.write_text(report, "OLD")
            expected = file_io.file_identity(report.stat())
            stranger.write_bytes(_PIN)
            real = file_io._Output._discard_report
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                output = file_io._Output(report, directory)

                def hooked(owner, name, identity):
                    os.replace(stranger, root / name)
                    return real(owner, name, identity)

                with patch.object(file_io._Output, "_discard_report", hooked):
                    removed = output._discard_report(report.name, expected)
            finally:
                os.close(directory)
            self.assertFalse(removed)
            self.assertEqual(report.read_bytes(), _PIN)
            self.assertFalse(stranger.exists())

    def test_a_swap_before_unlink_keeps_the_protected_bytes_on_the_destination(self):
        with TemporaryDirectory(prefix="apr-round10-unlink-") as folder:
            root = Path(folder)
            destination = root / "report.json"
            protected = root / "notes.json"
            file_io.write_text(destination, "OLD")
            protected.write_bytes(_PIN)
            real = file_io._Output._discard_report
            swapped = {"done": False}

            def hooked(owner, name, identity):
                if not swapped["done"]:
                    swapped["done"] = True
                    os.replace(protected, root / name)
                return real(owner, name, identity)

            with patch.object(file_io._Output, "_discard_report", hooked):
                with self.assertRaisesRegex(ValueError, "distinct"):
                    file_io.write_text(destination, "NEW")
            self.assertTrue(swapped["done"])
            self.assertEqual(destination.read_bytes(), _PIN)
            self.assertFalse(protected.exists())
            self.assertFalse(list(root.glob(".apr-output-*.tmp")))
            self.assertFalse(list(root.glob(".apr-discard-*")))
            self.assertFalse(list(root.glob(".apr-preserved-*")))

    def test_a_swap_after_exchange_leaves_the_protected_bytes_on_the_destination(self):
        with TemporaryDirectory(prefix="apr-round10-undo-") as folder:
            root = Path(folder)
            destination = root / "report.json"
            protected = root / "notes.json"
            file_io.write_text(destination, "OLD")
            protected.write_bytes(_PIN)
            real = file_io._renameat2
            swapped = {"done": False}

            def hooked(*args):
                if args[-1] == file_io.RENAME_EXCHANGE and not swapped["done"]:
                    swapped["done"] = True
                    real(*args)
                    os.replace(protected, destination)
                    return None
                return real(*args)

            with patch.object(file_io, "_renameat2", side_effect=hooked):
                with self.assertRaisesRegex(ValueError, "distinct"):
                    file_io.write_text(destination, "NEW")
            self.assertTrue(swapped["done"])
            self.assertEqual(destination.read_bytes(), _PIN)
            self.assertFalse(protected.exists())
            self.assertFalse(list(root.glob(".apr-output-*.tmp")))
            self.assertNotEqual(destination.read_bytes(), b"OLD")

    def test_exchange_refuses_an_unmarked_destination_before_the_syscall(self):
        # A destination check that runs only for flags == 0 never sees
        # RENAME_EXCHANGE. Exchange would then swap the bytes.
        with TemporaryDirectory(prefix="apr-round10-flags-") as folder:
            root = Path(folder)
            source = root / "incoming.json"
            destination = root / "report.json"
            file_io.write_text(source, "NEW")
            destination.write_bytes(_PIN)
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with self.assertRaisesRegex(ValueError, "distinct"):
                    file_io._renameat2(
                        directory, source.name, directory, destination.name, file_io.RENAME_EXCHANGE,
                    )
            finally:
                os.close(directory)
            self.assertEqual(source.read_text(encoding="utf-8"), "NEW")
            self.assertEqual(destination.read_bytes(), _PIN)

    def test_an_unreadable_generation_still_records_the_input(self):
        with TemporaryDirectory(prefix="apr-round10-generation-") as folder:
            root = Path(folder)
            source = root / "external-hmac-sha256.json"
            destination = root / "summary.json"
            source.write_bytes(_PIN)
            with patch.object(file_io, "_generation", return_value=None):
                with file_io.protected_output(destination):
                    with file_io.open_input(source):
                        pass
                    identity = file_io.file_identity(source.stat())
                    recorded, _origin = file_io._PROTECTED_FILES[identity]
                    self.assertIsNone(recorded)
                    source.replace(destination)
                    with self.assertRaisesRegex(ValueError, "distinct"):
                        file_io.write_text(destination, "NEW")
            self.assertEqual(destination.read_bytes(), _PIN)
            self.assertFalse(source.exists())
            self.assertFalse(list(root.glob(".apr-output-*.tmp")))

    def test_a_changed_generation_is_not_the_protected_input(self):
        with TemporaryDirectory(prefix="apr-round10-reuse-") as folder:
            root = Path(folder)
            source = root / "notes.json"
            marked = root / "report.json"
            plain = root / "summary.json"
            source.write_text("PIN\n", encoding="utf-8")
            with file_io.protected_output(root / "unused.json"):
                with patch.object(file_io, "_generation", return_value=1):
                    with file_io.open_input(source):
                        pass
                identity = file_io.file_identity(source.stat())
                self.assertEqual(file_io._PROTECTED_FILES[identity][0], 1)
                with patch.object(file_io, "_generation", return_value=2):
                    self.assertFalse(file_io._inode_protected(source.stat(), 2))
            self.assertNotIn(identity, file_io._PROTECTED_FILES)
            file_io.write_text(marked, "OLD")
            file_io.write_text(marked, "NEW")
            self.assertEqual(marked.read_text(encoding="utf-8"), "NEW")
            plain.write_bytes(_PIN)
            with self.assertRaisesRegex(ValueError, "distinct"):
                file_io.write_text(plain, "NEW")
            self.assertEqual(plain.read_bytes(), _PIN)

    def test_a_new_process_refuses_an_unmarked_destination(self):
        names = (
            "external-hmac-sha256.json",
            "external-sha256.json",
            "aggregates.json",
            "summary.json",
            "notes.json",
        )
        for name in names:
            with self.subTest(name=name):
                with TemporaryDirectory(prefix="apr-round10-empty-") as folder:
                    destination = Path(folder) / name
                    destination.write_bytes(_PIN)
                    child = self._child(destination)
                    self.assertEqual(child.returncode, 2, child.stderr)
                    self.assertEqual(destination.read_bytes(), _PIN)
                    self.assertFalse(list(Path(folder).glob(".apr-output-*.tmp")))

    def test_a_new_process_replaces_a_marked_report(self):
        with TemporaryDirectory(prefix="apr-round10-child-") as folder:
            destination = Path(folder) / "report.json"
            file_io.write_text(destination, "OLD")
            child = self._child(destination)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "NEW")
            self.assertFalse(list(Path(folder).glob(".apr-output-*.tmp")))
