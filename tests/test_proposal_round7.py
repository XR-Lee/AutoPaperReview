"""Round-7 blockers: locked publish, sentence identity, facts, legacy requests, envelopes."""

import importlib.util
import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.command_transport import CommandTransportError
from autopaperreview.proposal import claim_review, clarification, reviewer_panel as panel
from autopaperreview.proposal import file_io, runner, stages
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.leak_audit import payload_sha256, serialize_payload
from autopaperreview.proposal.prior_index import HistoryJudgeError, eligible_history, proposal_body
from autopaperreview.proposal.sanitized import factual_leak
from autopaperreview.proposal.stages import SECTIONS

ROOT = Path(__file__).resolve().parents[1]
PLAN = "Could violet tags index archival baskets by location?"
SIGNAL = "The invitation to the final program arrived at dawn."


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


def _eligible(proposal, target, judge=None):
    row = _history(proposal=proposal, key="synthetic-row", forum="synthetic-row")
    return eligible_history(
        row, (2024, 50), exclude_text=proposal_body(target), history_judge=judge,
    )


def _labelled_pairs():
    """Synthetic pairs. ``route`` is code, judge, or distinct. No manuscript text."""
    pairs = []
    base = "Violet tags index archival baskets by location on cloth shelf {n}."
    for n in range(12):
        sentence = base.format(n=n)
        other = f"Blue wool measures dye loss on shelf {n}."
        pairs.append(("reorder-field", "code", {f"a{n}": sentence, f"b{n}": other},
                      {f"b{n}": other, f"a{n}": sentence}))
        pairs.append(("reorder-sentence", "code",
                      {"problem": f"{sentence} {other}"},
                      {"problem": f"{other} {sentence}"}))
        pairs.append(("paragraph", "code",
                      {"problem": f"{sentence}\n{other}"},
                      {"problem": f"{other}\n{sentence}"}))
        pairs.append(("cjk-sentence", "code",
                      {"problem": f"用标签索引篮筐{n}。染料记录在上层{n}。"},
                      {"problem": f"染料记录在上层{n}。用标签索引篮筐{n}。"}))
        pairs.append(("hyphen", "code",
                      {"problem": f"The archival baskets {n} hold violet tags."},
                      {"problem": f"The archi-\nval baskets {n} hold violet tags."}))
        pairs.append(("split", "code",
                      {"problem": f"Alpha violet tags {n} index archival baskets by location."},
                      {"left": f"Alpha violet tags {n} index", "right": "archival baskets by location."}))
        pairs.append(("appendix", "code",
                      {"problem": sentence},
                      {"problem": sentence, "appendix": f"A separate dye log {n} records humidity."}))
        pairs.append(("operator", "distinct",
                      {"problem": f"The shelf load {n} equals a + b for violet tags."},
                      {"problem": f"The shelf load {n} equals a - b for violet tags."}))
        pairs.append(("unary", "distinct",
                      {"problem": f"The offset {n} is -x when the dye curve falls."},
                      {"problem": f"The offset {n} is x when the dye curve falls."}))
        dotted = "The derivative x\u0307 of shelf {n} stays positive."
        plain = "The derivative x of shelf {n} stays positive."
        pairs.append(("derivative", "distinct", {"problem": dotted.format(n=n)},
                      {"problem": plain.format(n=n)}))
        pairs.append(("synonym", "judge",
                      {"problem": f"Violet tags index archival baskets by location on shelf {n}."},
                      {"problem": f"Violet tags index archival baskets by place on shelf {n}."}))
        pairs.append(("add", "judge",
                      {"problem": sentence},
                      {"problem": f"{sentence} A later note counts only shelf {n}."}))
        pairs.append(("delete", "judge",
                      {"problem": f"{sentence} A later note counts only shelf {n}."},
                      {"problem": sentence}))
        pairs.append(("quote", "distinct",
                      {"quote": sentence, "own": f"This study moves tags on shelf {n}."},
                      {"quote": sentence, "own": f"This study measures fading on shelf {n}."}))
        pairs.append(("different", "distinct",
                      {"problem": f"Index cloth archive number {n} on a separate shelf."},
                      {"problem": f"Count dye lots number {n} in a different room."}))
    return pairs


def _scores(rows, judge):
    true_positive = false_positive = false_negative = true_negative = 0
    misses = []
    for name, route, target, proposal in rows:
        is_copy = route in {"code", "judge"}
        excluded = not _eligible(proposal, target, judge)
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
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    return precision, recall, true_positive, false_positive, false_negative, true_negative, misses


class IdentityRound7Tests(TestCase):
    def test_author_pairs_stay_exact_and_the_new_set_uses_a_stubbed_judge(self):
        spec = importlib.util.spec_from_file_location(
            "test_proposal_round6", Path(__file__).with_name("test_proposal_round6.py"),
        )
        round6 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(round6)
        author_eligible = round6._eligible
        _pairs = round6._pairs

        def distinct_judge(_body):
            return {"relation": "distinct"}

        pairs = _pairs()
        self.assertEqual(len(pairs), 26)
        true_positive = false_positive = false_negative = true_negative = 0
        for _name, is_copy, target, proposal in pairs:
            excluded = not author_eligible(proposal, target, distinct_judge)
            if is_copy and excluded:
                true_positive += 1
            elif is_copy:
                false_negative += 1
            elif excluded:
                false_positive += 1
            else:
                true_negative += 1
        self.assertEqual((false_positive, false_negative, true_positive, true_negative), (0, 0, 9, 17))
        for _name, is_copy, target, proposal in pairs:
            if is_copy:
                self.assertFalse(author_eligible(proposal, target))
        longer = {"problem": PLAN + " A separate cloth study counts dye."}
        with self.assertRaises(HistoryJudgeError):
            author_eligible(longer, {"problem": PLAN})
        self.assertTrue(author_eligible(longer, {"problem": PLAN}, distinct_judge))

        labelled = _labelled_pairs()
        self.assertGreaterEqual(len(labelled), 100)
        code_copies = [item for item in labelled if item[1] == "code"]
        judge_copies = [item for item in labelled if item[1] == "judge"]
        distinct = [item for item in labelled if item[1] == "distinct"]
        raised = 0
        for name, route, target, proposal in labelled:
            try:
                eligible = _eligible(proposal, target)
            except HistoryJudgeError:
                self.assertIn(route, {"judge", "distinct"}, name)
                raised += 1
                continue
            if route == "code":
                self.assertFalse(eligible, name)
            else:
                self.assertTrue(eligible, name)
        quotes = [item for item in distinct if item[0] == "quote"]
        self.assertEqual(raised, len(judge_copies) + len(quotes))

        def same_judge(_body):
            return {"relation": "same"}

        def invalid_judge(_body):
            return {"verdict": "unsure"}

        relations = {}
        for _name, route, target, proposal in labelled:
            relations[(proposal_body(proposal), proposal_body(target))] = (
                "same" if route == "judge" else "distinct"
            )

        def stubbed_judge(body):
            relation = relations[(body["candidate"], body["target"])]
            return {"relation": relation}

        for name, _route, target, proposal in judge_copies:
            self.assertFalse(_eligible(proposal, target, same_judge), name)
            with self.assertRaises(HistoryJudgeError):
                _eligible(proposal, target, invalid_judge)
        for name, _route, target, proposal in distinct:
            self.assertTrue(_eligible(proposal, target, stubbed_judge), name)
            if name in {"operator", "unary", "derivative"}:
                self.assertTrue(_eligible(proposal, target, same_judge), name)
            if name == "quote":
                with self.assertRaises(HistoryJudgeError):
                    _eligible(proposal, target, invalid_judge)
            if name == "different":
                self.assertTrue(_eligible(proposal, target, invalid_judge), name)
        for name, _route, target, proposal in code_copies:
            self.assertFalse(_eligible(proposal, target, stubbed_judge), name)

        precision, recall, tp, fp, fn, tn, misses = _scores(labelled, stubbed_judge)
        self.assertEqual((fp, fn), (0, 0), misses)
        self.assertEqual(tp, len(code_copies) + len(judge_copies))
        self.assertEqual(tn, len(distinct))
        self.assertEqual((round(precision, 3), round(recall, 3)), (1.0, 1.0))
        self.assertGreaterEqual(len(labelled), 100)
        self.assertEqual(len(labelled), tp + tn)


class FactShapeTests(TestCase):
    def test_structured_shapes_block_and_scientific_ranges_stay(self):
        blocked = (
            "| Decision | major revision |",
            "| Decision: major revision |",
            "<dt>Decision</dt><dd>accepted</dd>",
            "Decision\n--------\naccepted",
            "Nota final: 8",
            "综合评分：8",
            "manuscript status: under review",
            "estado de la revision: pendiente",
            "Signature: Ada",
            "Firma: Ada",
            "签名：艾达",
            "联系方式：示例",
            "审稿意见：大修",
        )
        allowed = (
            "The reconstruction error stays between 0.1 and 0.3 under the synthetic control.",
            "the similarity score (0.2) is the planned embedding target",
            "the paper should be accepted",
            "A gradient between 0.1 and 0.3 is the planned slope.",
            "signature of the kernel stays Laplacian",
            "we accept the assumption",
        )
        for text in blocked:
            self.assertTrue(factual_leak(text), text)
        for text in allowed:
            self.assertFalse(factual_leak(text), text)

    def test_score_cli_blocks_a_table_phrase_and_keeps_a_metric(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round7-score-") as folder:
            root = Path(folder)
            stub = root / "stub.py"
            log = root / "calls.log"
            stub.write_text(
                "import json, sys\n"
                f"LOG = {str(log)!r}\n"
                "kind = 'judge' if 'leak' in sys.stdin.read().lower() else 'business'\n"
                "open(LOG, 'a', encoding='utf-8').write(kind + '\\n')\n"
                "if kind == 'judge':\n"
                "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
                "else:\n"
                "    row = {'score': 3, 'rationale': 'The plan is specific.', 'basis': ['proposal']}\n"
                "    names = ('importance', 'novelty', 'potential_value', 'community_impact')\n"
                "    sys.stdout.write(json.dumps({name: row for name in names}))\n",
                encoding="utf-8",
            )
            command = f"{sys.executable} {stub}"
            cases = (
                (2, "| Decision | major revision |\n"),
                (2, "<dt>Decision</dt><dd>major revision</dd>\n"),
                (0, "The reconstruction error stays between 0.1 and 0.3.\n"),
            )
            for index, (code, text) in enumerate(cases):
                proposal = root / f"idea-{index}.txt"
                proposal.write_text(text, encoding="utf-8")
                log.write_text("", encoding="utf-8")
                output = root / f"out-{index}.json"
                completed = subprocess.run(
                    [
                        sys.executable, "-m", "autopaperreview", "proposal-review", "score",
                        str(proposal), "--work", str(root / f"work-{index}"),
                        "--command", command, "--timeout", "30", "--output", str(output),
                    ],
                    cwd=ROOT, env=env, capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, code, completed.stderr)
                if code == 0:
                    self.assertEqual(log.read_text(encoding="utf-8").split(), ["judge", "business"])
                else:
                    self.assertEqual(log.read_text(encoding="utf-8"), "")
                    self.assertFalse(output.exists())


class LegacyRequestTests(TestCase):
    def _workspace(self, root, request):
        source = {"s1": PLAN}
        output = {section: {"text": None, "evidence": []} for section in SECTIONS}
        output["problem"] = {"text": PLAN, "evidence": ["s1"]}
        raw = json.dumps(output)
        entry = {
            "request": request,
            "output": output,
            "reply": raw,
            "reply_sha256": payload_sha256(raw),
            "attempts": 1,
            "latency_sec": 0.1,
        }
        case = {"key": "synthetic", "bundle": "bundle"}
        bundle = root / "inputs" / "bundle"
        bundle.mkdir(parents=True)
        write(bundle / "candidate.json", source)
        name = digest(request) + ".json"
        write(root / "cache" / "compress" / name, entry)
        write(root / "runs" / "synthetic" / "compress.json", {
            "bundle": "bundle",
            "cache": f"cache/compress/{name}",
            "model": "stub-model",
            "command_sha256": "a" * 64,
        })
        return case, entry, root / "cache" / "compress" / name

    def _request(self, source):
        return {
            "stage": "compress",
            "input_sha256": payload_sha256(serialize_payload(source)),
            "system_sha256": digest(stages.PROMPTS["compress"]),
            "model": "stub-model",
            "command_sha256": "a" * 64,
            "reply_protocol": 1,
        }

    def test_added_field_changed_model_and_command_are_stale(self):
        source = {"s1": PLAN}
        with TemporaryDirectory(prefix="apr-round7-legacy-") as folder:
            root = Path(folder)
            case, entry, cache_path = self._workspace(root, self._request(source))
            self.assertEqual(runner.stage_result(root, case, "compress")["output"]["problem"]["text"], PLAN)
            reordered = {key: self._request(source)[key] for key in reversed(tuple(self._request(source)))}
            entry["request"] = reordered
            write(cache_path, entry)
            self.assertEqual(runner.stage_result(root, case, "compress")["attempts"], 1)
            for name, mutate in (
                ("added", lambda req: req.update(extra=True)),
                ("model", lambda req: req.update(model="other-model")),
                ("command", lambda req: req.update(command_sha256="b" * 64)),
                ("missing", lambda req: req.pop("stage")),
            ):
                changed = self._request(source)
                mutate(changed)
                entry["request"] = changed
                write(cache_path, entry)
                with self.assertRaisesRegex(ValueError, "stale"):
                    runner.stage_result(root, case, "compress")


class EnvelopeRound7Tests(TestCase):
    def test_missing_result_and_nested_failure_do_not_publish(self):
        judge = lambda _body: {"verdict": "clean"}
        missing = json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
        })
        inner = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "terminal_reason": "aborted",
            "errors": ["synthetic failure"],
            "result": "sorry I cannot complete that review",
        }
        nested = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(inner),
        })
        null_result = json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": None,
        })
        calls = (
            (claim_review, "forecast", {"c1": PLAN}),
            (panel, "forecast", {"s1": PLAN}),
            (clarification, "question", {"question": PLAN}),
        )
        for raw in (missing, nested, null_result):
            for module, stage, body in calls:
                with self.subTest(raw=raw[:24], stage=module.__name__):
                    with TemporaryDirectory(prefix="apr-round7-env-") as folder:
                        root = Path(folder)
                        with patch.object(module, "run_command", return_value=(raw, 0.01)):
                            with self.assertRaises(CommandTransportError):
                                if module is clarification:
                                    module.judgment(
                                        stage, body, root, cached_only=False, timeout=30, leak_judge=judge,
                                    )
                                else:
                                    module.request(stage, body, root, False, leak_judge=judge)
                        self.assertFalse(list(root.rglob("*.json")))


class PublishStressTests(TestCase):
    def test_twelve_writers_on_random_schedules_keep_outputs_and_inputs(self):
        random.seed(72)
        lost_outputs = lost_inputs = leftover = 0
        runs = 200
        for run in range(runs):
            with TemporaryDirectory(prefix="apr-round7-race-") as folder:
                root = Path(folder)
                destination = root / "report.json"
                payloads = [f"writer-{run}-{index}" for index in range(12)]
                protected = run % 5 == 0
                payload = b'{"files": {"synthetic": "abc"}}\n'
                if protected:
                    source = root / "external-sha256.json"
                    source.write_bytes(payload)
                    with file_io.protected_output(destination):
                        with file_io.open_input(source):
                            pass
                        source.replace(destination)
                else:
                    file_io.write_text(destination, "OLD")
                errors = []

                def publish(text, gate=run):
                    time.sleep(random.random() * 0.002)
                    try:
                        with file_io.protected_output(destination):
                            file_io.write_text(destination, text)
                    except ValueError:
                        errors.append(text)

                workers = [threading.Thread(target=publish, args=(text,)) for text in payloads]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(15)
                    if worker.is_alive():
                        leftover += 1
                temps = list(root.glob(".apr-output-*.tmp")) + list(root.glob(".apr-publish.lock"))
                if temps:
                    leftover += len(temps)
                if protected:
                    if destination.read_bytes() != payload:
                        lost_inputs += 1
                    if len(errors) != 12:
                        lost_inputs += 1
                else:
                    if destination.read_text(encoding="utf-8") not in set(payloads):
                        lost_outputs += 1
                    if errors:
                        lost_outputs += 1
        self.assertEqual((lost_outputs, lost_inputs, leftover), (0, 0, 0))
