"""Round-11 fixes: same-filesystem publish, legacy receipts, eligibility, imports."""

import errno
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal import claim_review, contrast, file_io, priority, reviewer_panel as panel
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.fixed_benchmark import _content_block
from autopaperreview.proposal.leak_audit import (
    HISTORY_PROMPT, PROMPT, apply_leak_audit, model_leak_judge,
)
from autopaperreview.proposal.prior_index import (
    calibration_rows, pre_judge_eligible, proposal_body, reset_history_judge, use_history_judge,
)
from autopaperreview.proposal.sanitized import to_json_data
from autopaperreview.proposal.stages import _invoke_leak_judge

ROOT = Path(__file__).resolve().parents[1]
PLAN = "Could violet tags index archival baskets by location?"
TARGET = {
    "problem": (
        "sparse archival indexing scans diary pages by paper tab shape and "
        "ledger colour under a warm lamp beside the oak shelf"
    ),
}
HIGH = {
    "problem": (
        "sparse archival indexing scans diary pages by paper tab shape and "
        "ledger colour under a warm lamp beside the maple shelf"
    ),
}
MID = {
    "problem": (
        "sparse archival indexing scans diary pages by paper tab shape and "
        "ledger colour under a cool lantern beside the maple shelf"
    ),
}


def _row(**changes):
    row = {
        "title": "Other violet basket index",
        "abstract": "A different shelf note.",
        "year": 2020,
        "revision_ms": 5,
        "forum": "synthetic-other",
        "split": "dev",
        "available_ms": 5,
        "key": "synthetic-other",
        "opinion_ms": 4,
        "target": 3,
        "proposal": {"problem": "Ocean buoy zero tracks kelp drift."},
        "opinions": ["A synthetic opinion."],
    }
    row.update(changes)
    return row


class PublishRound11Tests(TestCase):
    def test_receipt_is_written_when_tmpdir_would_cross_devices(self):
        outside = []
        parents = []
        real_mkdtemp = file_io.tempfile.mkdtemp
        real_rename = file_io._renameat2

        def tracking(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            directory = kwargs.get("dir")
            parents.append(directory)
            if directory is None:
                outside.append(Path(path))
            return path

        def renaming(src_dirfd, src_name, dst_dirfd, dst_name, flags):
            src = file_io._directory_path(src_dirfd)
            for root in outside:
                try:
                    src.relative_to(root)
                except ValueError:
                    continue
                raise OSError(errno.EXDEV, "simulated cross-device link", dst_name)
            return real_rename(src_dirfd, src_name, dst_dirfd, dst_name, flags)

        with TemporaryDirectory(prefix="apr-round11-exdev-") as folder:
            cache = Path(folder) / "cache"
            with (
                patch.object(file_io.tempfile, "mkdtemp", tracking),
                patch.object(file_io, "_renameat2", renaming),
                patch(
                    "autopaperreview.proposal.stages.run_command",
                    return_value=('{"verdict":"clean"}', 0.01),
                ),
            ):
                result = _invoke_leak_judge(
                    "synthetic payload", PROMPT, cache, "stub-model", ["stub"], 5, cached_only=False,
                )
            self.assertEqual(result, {"verdict": "clean"})
            receipts = list((cache / "leak_audit").glob("*.json"))
            self.assertEqual(len(receipts), 1)
            self.assertIn('"clean"', receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(outside, [])
            self.assertTrue(parents)
            self.assertTrue(all(item is not None for item in parents))
            self.assertFalse(list(cache.rglob(".apr-staging-*")))

    def test_unmarked_overwrite_does_not_need_user_xattr(self):
        with TemporaryDirectory(prefix="apr-round11-xattr-") as folder:
            path = Path(folder) / "report.json"
            path.write_text("OLD", encoding="utf-8")
            with patch("os.setxattr", side_effect=OSError(errno.ENOTSUP, "xattr")):
                file_io.write_text(path, "NEW")
            self.assertEqual(path.read_text(encoding="utf-8"), "NEW")


class LegacyReceiptRound11Tests(TestCase):
    def _promote(self, module, stage, body, payload):
        judge = lambda _body: {"verdict": "clean"}
        wrapped = json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": payload,
        })
        layout = "claims" if module is claim_review else "panel"
        with TemporaryDirectory(prefix="apr-round11-legacy-") as folder:
            root = Path(folder)
            with patch.object(module, "run_command", return_value=(wrapped, 0.01)):
                key, first = module.request(stage, body, root, False, leak_judge=judge)
            new_path = root / layout / stage / f"{key}.json"
            legacy = root / stage / f"{key}.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            original = new_path.read_bytes()
            legacy.write_bytes(original)
            new_path.unlink()
            with patch.object(module, "run_command", side_effect=AssertionError("model")):
                again_key, again = module.request(stage, body, root, False, leak_judge=judge)
            self.assertEqual(again_key, key)
            self.assertEqual(again["output"], first["output"])
            self.assertEqual(legacy.read_bytes(), original)
            self.assertTrue(new_path.is_file())
            self.assertNotEqual(new_path.resolve(), legacy.resolve())
            with patch.object(module, "run_command", side_effect=AssertionError("model")):
                offline_key, offline = module.request(stage, body, root, True, leak_judge=judge)
            self.assertEqual(offline_key, key)
            self.assertEqual(offline["output"], first["output"])
            self.assertEqual(legacy.read_bytes(), original)

    def test_online_then_offline_from_an_fd9799e_receipt(self):
        self._promote(claim_review, "forecast", {"c1": PLAN}, {"c1": [50, 30, 1]})
        self._promote(
            panel, "forecast", {"s1": PLAN},
            [{"n": 20, "grade": 3, "pro": None, "con": None}],
        )


class EligibilityRound11Tests(TestCase):
    def test_overlap_threshold_runs_before_the_history_judge(self):
        calls = []

        def judge(body):
            calls.append(body)
            return {"relation": "distinct"}

        peers = [
            _row(key=f"peer-{index}", forum=f"peer-{index}", target=grade)
            for index, grade in enumerate((1, 2, 4, 5))
        ]
        high = _row(key="high-overlap", forum="high", target=3, proposal=HIGH)
        mid = _row(key="mid-overlap", forum="mid", target=3, proposal=MID)
        self.assertGreaterEqual(contrast._local_overlap(contrast.terms(TARGET), HIGH), 0.85)
        self.assertLess(contrast._local_overlap(contrast.terms(TARGET), MID), 0.85)
        token = use_history_judge(judge)
        try:
            packet, _scores = contrast.packet(TARGET, [*peers, high, mid], target_year=2024, cutoff_ms=20)
        finally:
            reset_history_judge(token)
        sent = json.dumps(calls, default=to_json_data)
        self.assertNotIn("warm lamp beside the maple", sent)
        self.assertIn("cool lantern", sent)
        self.assertIn("examples", packet)

    def test_malformed_rows_are_dropped_before_distinct(self):
        bounds = (2024, 50)
        exclude = proposal_body(TARGET)
        calls = []

        def judge(body):
            calls.append(body)
            return {"relation": "distinct"}

        near = _row(key="near", forum="near", proposal=MID)
        malformed = [
            _row(key="bad-target", forum="bad-target", proposal=MID, target="high"),
            _row(key="bad-bool", forum="bad-bool", proposal=MID, target=True),
            _row(key="bad-nan", forum="bad-nan", proposal=MID, target=float("nan")),
            _row(key="bad-proposal", forum="bad-proposal", proposal="not a mapping"),
            _row(key="bad-field", forum="bad-field", proposal={"problem": 3}),
        ]
        for row in malformed:
            self.assertFalse(pre_judge_eligible(
                row, bounds, excerpt_field="opinions", exclude_text=exclude,
            ))
            self.assertEqual(list(calibration_rows(
                [row], bounds, excerpt_field="opinions", exclude_text=exclude, history_judge=judge,
            )), [])
        self.assertEqual(calls, [])
        kept = list(calibration_rows(
            [near, *malformed], bounds, excerpt_field="opinions", exclude_text=exclude, history_judge=judge,
        ))
        self.assertEqual([row["key"] for row, _dated in kept], ["near"])
        self.assertEqual(len(calls), 1)

    def test_normalized_keys_are_deduped_before_the_judge(self):
        bounds = (2024, 50)
        exclude = proposal_body(TARGET)
        calls = []

        def judge(body):
            calls.append(body)
            return {"relation": "distinct"}

        first = _row(key=" Anchor ", forum="first")
        second = _row(key="anchor", forum="second", proposal=MID)
        third = _row(key=7, forum="third")
        fourth = _row(key="7", forum="fourth", proposal=MID)
        rows = list(calibration_rows(
            [first, second, third, fourth], bounds,
            excerpt_field="opinions", exclude_text=exclude, history_judge=judge,
        ))
        self.assertEqual([row["key"] for row, _dated in rows], [" Anchor ", 7])
        self.assertEqual(calls, [])


class ImportAndLeakRound11Tests(TestCase):
    def test_main_cli_imports_when_fcntl_is_absent(self):
        script = (
            "import builtins, sys\n"
            "real = builtins.__import__\n"
            "def blocked(name, globals=None, locals=None, fromlist=(), level=0):\n"
            "    if name == 'fcntl':\n"
            "        raise ModuleNotFoundError('No module named fcntl')\n"
            "    return real(name, globals, locals, fromlist, level)\n"
            "builtins.__import__ = blocked\n"
            "sys.modules.pop('fcntl', None)\n"
            "from autopaperreview.cli import build_parser\n"
            "build_parser()\n"
            "print('ok')\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ok", completed.stdout)

    def test_io_errors_are_not_reported_as_leak_blocks(self):
        packet = {"problem": "Ocean buoy tracks kelp drift today."}
        with self.assertRaises(OSError) as caught:
            apply_leak_audit(packet, lambda _body: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left on device")))
        self.assertNotIn("leak audit blocked", str(caught.exception))
        with TemporaryDirectory(prefix="apr-round11-io-") as folder:
            judge = model_leak_judge(Path(folder), "stub-model", ["stub"], 5)
            with (
                patch(
                    "autopaperreview.proposal.stages.run_command",
                    return_value=('{"verdict":"clean"}', 0.01),
                ),
                patch(
                    "autopaperreview.proposal.stages.write",
                    side_effect=OSError(errno.EIO, "Input/output error"),
                ),
                self.assertRaises(OSError),
            ):
                apply_leak_audit(packet, judge)
        with self.assertRaisesRegex(ValueError, "leak audit blocked"):
            apply_leak_audit(packet, lambda _body: {"verdict": "leak"})

    def test_one_leak_block_does_not_fail_predict_rank(self):
        self.assertFalse(_content_block(ValueError("empty proposal")))
        self.assertTrue(_content_block(ValueError("proposal contains an identity leak")))

        def transport(_argv, *, system, prompt, **_options):
            if system in (HISTORY_PROMPT, PROMPT):
                return '{"verdict":"clean"}', 0.01
            body = json.loads(prompt)
            return json.dumps({
                "favorable": "Concrete plan.",
                "unfavorable": "Thin comparison.",
                "wins": dict.fromkeys(body["examples"], 0.5),
                "estimate": 3.0,
                "confidence": 0.5,
            }), 0.01

        with TemporaryDirectory(prefix="apr-round11-batch-") as folder:
            root = Path(folder)
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
            write(root / "bank.json", peers)
            write(root / "profile.json", {
                "ranker": {"method": "mean", "names": ["anchored"]},
                "prompts": {"match": digest(priority.PROMPTS["match"])},
            })
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
                    "proposal": "Decision: Accept as Oral." if index == 0 else f"Warehouse bay {index} keeps maple ribbons.",
                    "title": "Leak" if index == 0 else f"Bay label {index}",
                    "abstract": "Leak." if index == 0 else f"Queue note {index}.",
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
            self.assertNotIn("leak audit blocked", err.getvalue())
            predictions = json.loads(output.read_text(encoding="utf-8"))
            by_forum = {row["forum"]: row for row in predictions}
            self.assertEqual(set(by_forum), set(ids))
            self.assertIsNone(by_forum["synthetic-00"]["overall"])
            self.assertEqual(by_forum["synthetic-00"]["status"], "leak_blocked")
            self.assertIsInstance(by_forum["synthetic-01"]["overall"], (int, float))

            empty_ids = [f"empty-{index:02d}" for index in range(100)]
            write(root / "blind-empty.json", [
                {
                    "forum": fid,
                    "proposal": "" if index == 0 else f"Warehouse bay {index} keeps maple ribbons.",
                    "title": "Empty" if index == 0 else f"Bay label {index}",
                    "abstract": "Empty." if index == 0 else f"Queue note {index}.",
                }
                for index, fid in enumerate(empty_ids)
            ])
            write(root / "cohort-empty.json", {
                "version": 1,
                "cases": [
                    {"forum": fid, "split": "test", "year": 2024, "cutoff_ms": 50}
                    for fid in empty_ids
                ],
                "ids_sha256": digest(sorted(empty_ids)),
            })
            empty_output = root / "empty.json"
            err = io.StringIO()
            with (
                redirect_stderr(err), redirect_stdout(io.StringIO()),
                patch("autopaperreview.command_transport.run_command", side_effect=transport),
                patch("autopaperreview.proposal.stages.run_command", side_effect=transport),
            ):
                code = main([
                    "proposal-review", "predict-rank",
                    "--manifest", str(root / "cohort-empty.json"),
                    "--proposals", str(root / "blind-empty.json"),
                    "--profile", str(root / "profile.json"),
                    "--work", str(root / "work-empty"),
                    "--output", str(empty_output),
                    "--jobs", "1",
                ])
            self.assertEqual(code, 2, err.getvalue())
            self.assertIn("empty proposal", err.getvalue())
            self.assertFalse(empty_output.exists())
