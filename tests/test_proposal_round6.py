"""Round-6 blockers: one identity rule, one receipt compare, one envelope check."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.command_transport import CommandTransportError
from autopaperreview.proposal import claim_review, clarification, priority, reviewer_panel as panel
from autopaperreview.proposal import runner, stages
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.leak_audit import payload_sha256, same_model_value, serialize_payload
from autopaperreview.proposal.prior_index import eligible_history, proposal_body

ROOT = Path(__file__).resolve().parents[1]
PLAN = "Could violet tags index archival baskets by location?"
QUOTE = (
    "Violet tags index archival baskets by location on cloth shelves "
    "under a planned layout that counts labels once."
)
METHOD = "Dissolve the dye, stir for one minute, and record the cloth temperature."
BOILER = "Members of the cloth group thank the shelf workshop for the baskets."
DATASET = "The public cloth sample lists twenty dyed squares and no hidden labels."


def _history(**changes):
    row = {
        "title": "Other violet basket index",
        "abstract": "violet archive indexing baskets labels for a different shelf.",
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


def _eligible(proposal, target):
    row = _history(proposal=proposal, key="synthetic-row", forum="synthetic-row")
    return eligible_history(row, (2024, 50), exclude_text=proposal_body(target))


def _pairs():
    core = {
        "problem": "Alpha violet tags index archival baskets by location.",
        "approach": "Beta baskets use a planned layout of cloth shelves.",
        "validation_plan": "Gamma counts the planned labels on each shelf.",
    }
    copies = [
        ("split", True, core, {
            "left": "Alpha violet tags index",
            "right": "archival baskets by location.",
            "method": core["approach"],
            "check": core["validation_plan"],
        }),
        ("merge", True, core, {
            "combined": core["problem"] + " " + core["approach"],
            "check": core["validation_plan"],
        }),
        ("reorder", True, core, {
            "gamma": core["validation_plan"],
            "alpha": core["problem"],
            "beta": core["approach"],
        }),
        ("duplicate", True, core, {
            "notes": core["problem"],
            "extra": core["problem"],
            "method": core["approach"],
            "check": core["validation_plan"],
        }),
        ("repeat", True, {"problem": "violet tags index baskets"}, {
            "problem": "violet tags index baskets violet tags index baskets",
        }),
        ("hyphen", True, {"problem": "The archival baskets hold violet tags."}, {
            "problem": "The archi-\nval baskets hold violet tags.",
        }),
        ("cjk-split", True, {"problem": "用标签索引篮筐的位置"}, {
            "left": "用标签索引",
            "right": "篮筐的位置",
        }),
        ("cjk-reorder", True, {"a": "用标签索引", "b": "篮筐的位置"}, {
            "b": "篮筐的位置",
            "a": "用标签索引",
        }),
        ("appendix", True, core, {
            **core,
            "appendix": (
                "A separate dye log records humidity, wool lots, and shelf height. "
                "Those notes are not part of the tag count."
            ),
        }),
    ]
    others = [
        ("quote-a", False, {"quote": QUOTE, "own": "This study moves tags from left to right."},
         {"quote": QUOTE, "own": "This study measures how cloth fades in sunlight."}),
        ("quote-b", False, {"quote": QUOTE, "own": "Count only the baskets on the top shelf."},
         {"quote": QUOTE, "own": "Count only the squares that lost their dye."}),
        ("quote-c", False, {"quote": QUOTE, "own": "The intervention adds a second tag."},
         {"quote": QUOTE, "own": "The intervention removes the cloth backing."}),
        ("method-a", False, {"method": METHOD, "own": "Apply it to violet tags."},
         {"method": METHOD, "own": "Apply it to blue wool."}),
        ("method-b", False, {"method": METHOD, "own": "Stop when the shelf is full."},
         {"method": METHOD, "own": "Stop when the dye is pale."}),
        ("boiler-a", False, {"note": BOILER, "own": "We index baskets."},
         {"note": BOILER, "own": "We index colors."}),
        ("boiler-b", False, {"note": BOILER, "own": "The claim is about location."},
         {"note": BOILER, "own": "The claim is about temperature."}),
        ("plus", False, {"problem": "The shelf load equals a + b for violet tags."},
         {"problem": "The shelf load equals a - b for violet tags."}),
        ("piecewise", False, {"problem": "Use x > y when the dye curve rises."},
         {"problem": "Use x < y when the dye curve rises."}),
        ("data-a", False, {"data": DATASET, "own": "We add a location tag."},
         {"data": DATASET, "own": "We add a color tag."}),
        ("data-b", False, {"data": DATASET, "own": "The shelf is cloth."},
         {"data": DATASET, "own": "The shelf is wood."}),
        ("short-a", False, {"problem": "red cloth"}, {"problem": "blue cloth"}),
        ("short-b", False, {"problem": "left shelf"}, {"problem": "right shelf"}),
        ("reversed", False, {"problem": "heat flows from the left shelf toward the right basket"},
         {"problem": "heat flows from the right basket toward the left shelf"}),
        ("one-word", False, {"problem": "violet tags index archival baskets by planned location"},
         {"problem": "violet tags index archival baskets by measured location"}),
        ("intervention", False, {"problem": "violet tags index archival baskets after adding a magnet"},
         {"problem": "violet tags index archival baskets after removing a magnet"}),
        ("trigram", False, {"problem": QUOTE + " The new claim is a magnet on each basket."},
         {"problem": "A dye study mentions violet tags index and then measures fading."}),
    ]
    return copies + others


class IdentityTests(TestCase):
    def test_labelled_pairs_report_precision_and_recall(self):
        pairs = _pairs()
        self.assertEqual(len(pairs), 26)
        true_positive = false_positive = false_negative = true_negative = 0
        misses = []
        for name, is_copy, target, proposal in pairs:
            excluded = not _eligible(proposal, target)
            if is_copy and excluded:
                true_positive += 1
            elif is_copy:
                false_negative += 1
                misses.append(name)
            elif excluded:
                false_positive += 1
                misses.append(name)
            else:
                true_negative += 1
        precision = true_positive / (true_positive + false_positive)
        recall = true_positive / (true_positive + false_negative)
        self.assertEqual(
            (false_positive, false_negative),
            (0, 0),
            f"precision {precision:.2f} recall {recall:.2f} on {len(pairs)} pairs; misses {misses}",
        )
        self.assertEqual((true_positive, true_negative), (9, 17))
        longer = {"problem": PLAN + " A separate cloth study counts dye."}
        self.assertTrue(_eligible(longer, {"problem": PLAN}))

    def test_boundary_pairs_go_through_prioritize(self):
        wanted = {"hyphen", "cjk-split", "appendix", "reversed", "plus"}
        selected = [item for item in _pairs() if item[0] in wanted]
        self.assertEqual(len(selected), 5)
        peers = [
            _history(
                key=f"synthetic-peer-{index}",
                forum=f"synthetic-peer-{index}",
                target=1 + index / 10,
                proposal={"problem": f"Index cloth archive number {index} on a separate shelf."},
                excerpts=[{"text": "PEEROPINIONKEEP labels could be useful.", "review_ms": 4}],
            )
            for index in range(8)
        ]
        for name, is_copy, target, proposal in selected:
            with self.subTest(name=name):
                copied = _history(
                    key="synthetic-copy",
                    forum="synthetic-copy",
                    target=5,
                    proposal=proposal,
                    excerpts=[{"text": f"SELFHISTORYMARKER {name} labels could be useful.", "review_ms": 4}],
                )
                prompts = []

                def transport(_argv, *, system, prompt, **_kwargs):
                    prompts.append(prompt)
                    body = json.loads(prompt)
                    return json.dumps({
                        "favorable": "Fewer labels", "unfavorable": "Scope", "estimate": 3.0,
                        "confidence": 0.5, "wins": dict.fromkeys(body["examples"], 0.5),
                    }), 0

                with TemporaryDirectory(prefix=f"apr-round6-bank-{name}-") as folder:
                    root = Path(folder)
                    write(root / "proposal.json", target)
                    write(root / "bank.json", [*peers, copied])
                    write(root / "profile.json", {
                        "ranker": {"method": "mean", "names": ["anchored"]},
                        "prompts": {"match": digest(priority.PROMPTS["match"])},
                    })
                    args = [
                        "proposal-review", "prioritize", str(root / "proposal.json"),
                        "--profile", str(root / "profile.json"), "--work", str(root / "work"),
                        "--output", str(root / "out.json"), "--target-year", "2024", "--cutoff-ms", "50",
                    ]
                    with patch.object(priority, "model_leak_judge", return_value=lambda _body: {"verdict": "clean"}), \
                            patch.object(stages, "run_command", side_effect=transport):
                        self.assertEqual(main(args), 0)
                self.assertIn("PEEROPINIONKEEP", prompts[0], name)
                if is_copy:
                    self.assertNotIn("SELFHISTORYMARKER", prompts[0], name)
                else:
                    self.assertIn("SELFHISTORYMARKER", prompts[0], name)


def _score(work, proposal, command, output):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable, "-m", "autopaperreview", "proposal-review", "score",
            str(proposal), "--work", str(work), "--command", command,
            "--output", str(output), "--timeout", "30",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


class ReceiptEnvelopeTests(TestCase):
    def test_score_request_type_swap_does_not_replay(self):
        with TemporaryDirectory(prefix="apr-round6-score-") as folder:
            root = Path(folder)
            log = root / "calls.log"
            stub = root / "stub.py"
            stub.write_text(
                "import json, sys\n"
                f"LOG = {str(log)!r}\n"
                "data = sys.stdin.read()\n"
                "kind = 'judge' if 'You judge whether a research-proposal packet leaks' in data else 'business'\n"
                "with open(LOG, 'a', encoding='utf-8') as handle:\n"
                "    handle.write(kind + '\\n')\n"
                "if kind == 'judge':\n"
                "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
                "else:\n"
                "    row = {'score': 3, 'rationale': 'The plan is specific.', 'basis': ['proposal']}\n"
                "    names = ('importance', 'novelty', 'potential_value', 'community_impact')\n"
                "    sys.stdout.write(json.dumps({name: row for name in names}))\n",
                encoding="utf-8",
            )
            command = f"{sys.executable} {stub}"
            proposal = root / "idea.txt"
            proposal.write_text(PLAN + "\n", encoding="utf-8")
            work = root / "work"
            first = _score(work, proposal, command, root / "out.json")
            self.assertEqual(first.returncode, 0, first.stderr)
            receipts = [
                path for path in (work / "cache").rglob("*.json")
                if path.parent.name == "review"
            ]
            self.assertTrue(receipts)
            original = receipts[0].read_text(encoding="utf-8")
            for replacement in (True, 1.0):
                stored = json.loads(original)
                stored["request"]["reply_protocol"] = replacement
                receipts[0].write_text(json.dumps(stored), encoding="utf-8")
                log.write_text("", encoding="utf-8")
                again = _score(work, proposal, command, root / "again.json")
                self.assertEqual(again.returncode, 2, (replacement, again.stderr))
                self.assertNotIn("business", log.read_text(encoding="utf-8"))
                self.assertFalse((root / "again.json").exists())
            self.assertFalse(same_model_value({"reply_protocol": 1}, {"reply_protocol": True}))

    def test_claims_and_panel_request_types_do_not_replay(self):
        judge = lambda _body: {"verdict": "clean"}
        with TemporaryDirectory(prefix="apr-round6-request-") as folder:
            root = Path(folder)
            for stage_module, stage, body in (
                (claim_review, "forecast", {"c1": PLAN}),
                (panel, "forecast", {"s1": PLAN}),
            ):
                with self.subTest(stage=stage_module.__name__):
                    # Claims and panel keep forecast receipts in separate directories.
                    stage_root = root / stage_module.__name__.rsplit(".", 1)[-1]
                    with patch.object(stage_module, "run_command", return_value=("not json", 0.0)):
                        key, _result = stage_module.request(stage, body, stage_root, False, leak_judge=judge)
                    prefix = "claims" if stage_module is claim_review else "panel"
                    path = stage_root / prefix / stage / f"{key}.json"
                    self.assertTrue(path.is_file(), path)
                    stored = json.loads(path.read_text(encoding="utf-8"))
                    self.assertIs(stored["request"]["version"], 1)
                    for replacement in (True, 1.0):
                        changed = json.loads(json.dumps(stored))
                        changed["request"]["version"] = replacement
                        path.write_text(json.dumps(changed), encoding="utf-8")
                        with patch.object(stage_module, "run_command", side_effect=AssertionError("business call")):
                            with self.assertRaises(ValueError):
                                stage_module.request(stage, body, stage_root, True, leak_judge=judge)
                    path.write_text(json.dumps(stored), encoding="utf-8")

    def test_legacy_reply_protocol_bool_and_float_are_stale(self):
        source = {"problem": PLAN}
        with TemporaryDirectory(prefix="apr-round6-legacy-") as folder:
            root = Path(folder)
            case = {"key": "synthetic", "bundle": "bundle"}
            bundle = root / "inputs" / "bundle"
            bundle.mkdir(parents=True)
            write(bundle / "candidate.json", source)
            output = {"problem": {"text": PLAN, "evidence": []}}
            raw = json.dumps(output)
            entry = {
                "request": {
                    "stage": "compress",
                    "input_sha256": payload_sha256(serialize_payload(source)),
                    "system_sha256": digest(stages.PROMPTS["compress"]),
                    "model": "stub",
                    "command_sha256": "a" * 64,
                    "reply_protocol": 1,
                },
                "output": output,
                "reply": raw,
                "reply_sha256": payload_sha256(raw),
                "attempts": 1,
                "latency_sec": 0.1,
            }
            cache = root / "cache" / "compress" / "receipt.json"
            write(root / "runs" / "synthetic" / "compress.json", {
                "bundle": "bundle", "cache": "cache/compress/receipt.json",
            })
            for replacement in (True, 1.0):
                entry["request"]["reply_protocol"] = replacement
                write(cache, entry)
                with self.assertRaisesRegex(ValueError, "stale"):
                    runner.stage_result(root, case, "compress")

    def test_failure_envelopes_do_not_become_refusals_and_nan_does_not_abstain(self):
        judge = lambda _body: {"verdict": "clean"}
        aborted = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "terminal_reason": "aborted",
            "errors": ["synthetic failure"],
            "result": "sorry I cannot complete that review",
        })
        with TemporaryDirectory(prefix="apr-round6-abort-") as folder:
            root = Path(folder)
            for stage_module, stage, body in (
                (claim_review, "forecast", {"c1": PLAN}),
                (panel, "forecast", {"s1": PLAN}),
            ):
                with self.subTest(stage=stage_module.__name__), \
                        patch.object(stage_module, "run_command", return_value=(aborted, 0.0)) as command:
                    with self.assertRaises(CommandTransportError):
                        stage_module.request(stage, body, root / stage, False, leak_judge=judge)
                    self.assertEqual(command.call_count, 1)
                    self.assertFalse(any((root / stage).rglob("*.json")))
            sorry = json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "sorry I cannot complete that review",
            })
            with patch.object(claim_review, "run_command", return_value=(sorry, 0.0)):
                _key, result = claim_review.request(
                    "forecast", {"c1": PLAN}, root / "sorry", False, leak_judge=judge,
                )
            self.assertEqual(result["state"], "refusal")
        nan_cases = (
            '{"type":"result","subtype":"success","is_error":false,"result":"{\\"abstain\\": true}","duration_ms":NaN}',
            '{"type":"result","subtype":"success","is_error":false,"result":"{\\"abstain\\": true}","duration_ms":Infinity}',
            '{"type":"result","subtype":"success","is_error":false,"result":"{\\"value\\": 1e9999}"}',
        )
        for raw in nan_cases:
            with self.subTest(raw=raw[-24:]), TemporaryDirectory(prefix="apr-round6-nan-") as folder:
                root = Path(folder)
                with patch.object(clarification, "run_command", return_value=(raw, 0.01)) as command:
                    with self.assertRaises(ValueError):
                        clarification.judgment(
                            "question", {"question": PLAN}, root, cached_only=False, timeout=30,
                            leak_judge=judge,
                        )
                    self.assertEqual(command.call_count, 1)
                    self.assertFalse(any(root.rglob("*.json")))


class PublishRepeatTests(TestCase):
    def test_three_threads_repeat_without_requiring_an_error(self):
        from autopaperreview.proposal import file_io

        for _ in range(20):
            with TemporaryDirectory(prefix="apr-round6-race-") as folder:
                path = Path(folder) / "report.json"
                with file_io.protected_output(path):
                    file_io.write_text(path, "OLD")
                barrier = threading.Barrier(3)

                def publish(text, gate=barrier, destination=path):
                    try:
                        gate.wait(5)
                        with file_io.protected_output(destination):
                            file_io.write_text(destination, text)
                    except ValueError:
                        return

                threads = [threading.Thread(target=publish, args=(text,)) for text in ("AAA", "BBB", "CCC")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                self.assertIn(path.read_text(encoding="utf-8"), {"AAA", "BBB", "CCC"})
                self.assertFalse(list(Path(folder).glob(".apr-output-*.tmp")))
