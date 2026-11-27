"""Round-5 blockers, exercised through the score CLI and the send boundary."""

import json
import os
import sqlite3
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
from autopaperreview.proposal.leak_audit import (
    payload_sha256, require_business_receipt, same_model_value, serialize_payload, strict_model_value,
)
from autopaperreview.proposal.prior_index import build, proposal_body, retrieve
from autopaperreview.proposal.sanitized import factual_leak, score_field, seal

ROOT = Path(__file__).resolve().parents[1]
RUBRIC_KEYS = ("importance", "novelty", "potential_value", "community_impact")
PLAN = "Could violet tags index archival baskets by location?"


def _command_stub(path: Path, log: Path, body: str) -> str:
    path.write_text(
        "import json, sys\n"
        f"LOG = {str(log)!r}\n"
        "data = sys.stdin.read()\n"
        "kind = 'judge' if 'You judge whether a research-proposal packet leaks' in data else 'business'\n"
        "with open(LOG, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(kind + '\\n')\n"
        + body,
        encoding="utf-8",
    )
    return f"{sys.executable} {path}"


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


class ScoreCliTests(TestCase):
    def _score(self, work, proposal, command, output):
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

    def _stub(self, root: Path, extra: str = "") -> tuple[Path, str]:
        log = root / "calls.log"
        command = _command_stub(
            root / "stub.py",
            log,
            "if kind == 'judge':\n"
            "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
            "    raise SystemExit\n"
            "row = {'score': 3, 'rationale': 'The plan is specific.', 'basis': ['proposal']}\n"
            f"inner = {{name: row for name in {RUBRIC_KEYS!r}}}\n"
            + extra
            + "sys.stdout.write(json.dumps(inner))\n",
        )
        return log, command

    def test_score_cli_blocks_tables_brackets_and_keeps_a_planned_metric(self):
        leaks = (
            "| Decision | accepted |",
            "<table><tr><th>Decision</th><td>accepted</td></tr></table>",
            "Decision | Score\naccepted | 8",
            "overall rating [6, 8]",
            "score (6-8)",
            "puntuacion [6, 8]",
            "| Estado | aceptado |",
            "| dictamen | rechazo |",
            "作者 | 艾达",
            "决定\t录用",
            "| Affiliation | Example Laboratory |",
            "| 单位 | 示例研究所 |",
            "review status | major",
            "总分 [8]",
            "| Decision |\n| --- |\n| accepted |",
            "Final decision: accepted",
            "Decision is: accepted",
            "Decision\n: accepted",
            "Decision & accepted \\\\",
            "\\item[Decision] accepted",
        )
        allowed = (
            "We intend, once the controls and the cohort are fixed, a reconstruction score between 0.1 and 0.3.",
            "The reconstruction error stays between 0.1 and 0.3 under the synthetic control.",
            "the similarity score (0.2) is the planned embedding target",
            "the paper should be accepted",
            "计划评分区间为 6 到 8",
            "we accept the assumption",
            "A reference cosine similarity score: 0.625 is a design parameter.",
        )
        with TemporaryDirectory(prefix="apr-round5-score-") as folder:
            root = Path(folder)
            log, command = self._stub(root)
            for index, text in enumerate(leaks):
                proposal = root / f"leak-{index}.txt"
                proposal.write_text(text + "\n", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                completed = self._score(root / f"work-{index}", proposal, command, root / f"out-{index}.json")
                self.assertEqual(completed.returncode, 2, (text, completed.stderr))
                self.assertEqual(log.read_text(encoding="utf-8"), "", text)
                self.assertFalse((root / f"out-{index}.json").exists(), text)
                self.assertTrue(factual_leak(text), text)
            for index, text in enumerate(allowed):
                proposal = root / f"ok-{index}.txt"
                proposal.write_text(text + "\n", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                output = root / f"ok-{index}.json"
                completed = self._score(root / f"okwork-{index}", proposal, command, output)
                self.assertEqual(completed.returncode, 0, (text, completed.stderr))
                self.assertEqual(log.read_text(encoding="utf-8").split(), ["judge", "business"], text)
                self.assertFalse(factual_leak(text), text)

    def test_identity_and_score_keys_fail_closed_except_scientific_measures(self):
        blocked = (
            r"panel\text{score}",
            "venue_status",
            "disposition",
            "afiliacion_del_autor",
            "dictamen_final",
            "puntuacion_oficial",
            "作者单位",
            "评审决定",
            "overall_score",
            "average_score",
            "review_state",
            "reviewState",
            "manuscript_state",
            "paper_status",
        )
        allowed = (
            "final_reconstruction_score",
            "average_calibration_metric",
            "mean_similarity_score",
            "threshold_score",
            "measurement_score",
            "estado_del_arte",
            "gradient_score",
            "slope_metric",
        )
        for key in blocked:
            self.assertTrue(score_field(key), key)
            with self.assertRaises(ValueError):
                seal({key: PLAN})
        for key in allowed:
            self.assertFalse(score_field(key), key)
            seal({key: PLAN})

    def test_errors_subagent_usage_and_overflow_do_not_score(self):
        with TemporaryDirectory(prefix="apr-round5-envelope-") as folder:
            root = Path(folder)
            log, command = self._stub(
                root,
                "marker = data.rsplit('CASE_', 1)[-1].split()[0]\n"
                "envelopes = {\n"
                "    'errors': {'errors': ['synthetic failure']},\n"
                "    'subagent': {'subagent_stats': {'failed': 1}},\n"
                "    'usage': {'usage': {'input_tokens': -1}},\n"
                "    'typed': {'usage': {'input_tokens': '3'}},\n"
                "    'overflow': {'usage': {'input_tokens': 1e9999}},\n"
                "    'nullusage': {'modelUsage': None},\n"
                "    'statusnull': {'status': None},\n"
                "    'negdur': {'duration_ms': -1},\n"
                "    'boolsub': {'subagent_stats': {'failed': False}},\n"
                "}\n"
                "if marker in envelopes:\n"
                "    sys.stdout.write(json.dumps({'type': 'result', 'subtype': 'success', "
                "'is_error': False, 'result': json.dumps(inner), **envelopes[marker]}))\n"
                "    raise SystemExit\n",
            )
            for marker in (
                "errors", "subagent", "usage", "typed", "overflow",
                "nullusage", "statusnull", "negdur", "boolsub",
            ):
                proposal = root / f"{marker}.txt"
                proposal.write_text(f"CASE_{marker} violet tags index baskets.\n", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                completed = self._score(root / marker, proposal, command, root / f"{marker}.json")
                self.assertEqual(completed.returncode, 2, (marker, completed.stderr, log.read_text()))
                self.assertIn("business", log.read_text(encoding="utf-8"), marker)
                self.assertFalse((root / f"{marker}.json").exists(), marker)

    def test_score_cli_rejects_an_equal_float_in_place_of_an_int(self):
        with TemporaryDirectory(prefix="apr-round5-type-") as folder:
            root = Path(folder)
            log, command = self._stub(root)
            proposal = root / "idea.txt"
            proposal.write_text(PLAN + "\n", encoding="utf-8")
            work = root / "work"
            first = self._score(work, proposal, command, root / "out.json")
            self.assertEqual(first.returncode, 0, first.stderr)
            receipts = list((work / "cache").rglob("*.json"))
            self.assertTrue(receipts)
            changed = False
            for path in receipts:
                stored = json.loads(path.read_text(encoding="utf-8"))
                output = stored.get("output")
                if not isinstance(output, dict):
                    continue
                for row in output.values():
                    if isinstance(row, dict) and type(row.get("score")) is int:
                        row["score"] = float(row["score"])
                        changed = True
                path.write_text(json.dumps(stored), encoding="utf-8")
            self.assertTrue(changed)
            log.write_text("", encoding="utf-8")
            second = self._score(work, proposal, command, root / "again.json")
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertNotIn("business", log.read_text(encoding="utf-8"))
            self.assertFalse((root / "again.json").exists())
            self.assertFalse(same_model_value(1, 1.0))
            self.assertFalse(same_model_value(1, True))


class HistoryTests(TestCase):
    def test_resegmented_self_history_is_not_a_match(self):
        target = {
            "problem": "Alpha violet tags index archival baskets by location.",
            "approach": "Beta baskets use a planned layout of cloth shelves.",
            "validation_plan": "Gamma counts the planned labels on each shelf.",
        }
        copies = {
            "duplicate": {
                "notes": target["problem"],
                "extra": target["problem"],
                "method": target["approach"],
                "check": target["validation_plan"],
            },
            "merge": {
                "combined": target["problem"] + " " + target["approach"],
                "check": target["validation_plan"],
            },
            "split": {
                "left": "Alpha violet tags index",
                "right": "archival baskets by location.",
                "method": target["approach"],
                "check": target["validation_plan"],
            },
            "reorder": {
                "gamma": target["validation_plan"],
                "alpha": target["problem"],
                "beta": target["approach"],
            },
        }
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
        for name, proposal in copies.items():
            with self.subTest(name=name):
                self.assertNotEqual(proposal_body(target), proposal_body(proposal)) if name != "reorder" else None
                if name == "reorder":
                    self.assertEqual(proposal_body(target), proposal_body(proposal))
                copied = _history(
                    key="synthetic-copy",
                    forum="synthetic-copy",
                    target=5,
                    proposal=proposal,
                    excerpts=[{"text": f"SELFHISTORYMARKER {name} labels could be useful.", "review_ms": 4}],
                )
                self.assertFalse(eligible_history_text(copied, target))
                prompts = []

                def transport(_argv, *, system, prompt, **_kwargs):
                    prompts.append(prompt)
                    body = json.loads(prompt)
                    reply = {
                        "favorable": "Fewer labels", "unfavorable": "Scope", "estimate": 3.0,
                        "confidence": 0.5, "wins": dict.fromkeys(body["examples"], 0.5),
                    }
                    return json.dumps(reply), 0

                with TemporaryDirectory(prefix=f"apr-round5-bank-{name}-") as folder:
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
                self.assertTrue(prompts, name)
                self.assertIn("PEEROPINIONKEEP", prompts[0], name)
                self.assertNotIn("SELFHISTORYMARKER", prompts[0], name)


def eligible_history_text(row, target):
    from autopaperreview.proposal.prior_index import eligible_history

    return eligible_history(row, (2024, 50), exclude_text=proposal_body(target))


class IndexTests(TestCase):
    def test_old_and_alias_indexes_are_refused_before_field(self):
        with TemporaryDirectory(prefix="apr-round5-index-") as folder:
            root = Path(folder)
            legacy = root / "prior.sqlite"
            with sqlite3.connect(legacy) as conn:
                conn.execute(
                    "CREATE VIRTUAL TABLE papers USING fts5(title, abstract, year UNINDEXED, "
                    "revision UNINDEXED, forum UNINDEXED, arxiv UNINDEXED, split UNINDEXED, "
                    "available_ms UNINDEXED)"
                )
                conn.execute(
                    "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?)",
                    ("Violet basket indexing", "A synthetic index of violet baskets and labels.",
                     2020, 5, "synthetic-late", "", "dev", 5),
                )
            with self.assertRaisesRegex(ValueError, "stale"):
                retrieve(legacy, {"problem": PLAN}, target_year=2024, cutoff_ms=50)
            alias = root / "alias.sqlite"
            with sqlite3.connect(alias) as conn:
                conn.execute(
                    "CREATE VIRTUAL TABLE papers USING fts5(title, abstract, year UNINDEXED, "
                    "revision_ms UNINDEXED, forum UNINDEXED, arxiv_id UNINDEXED, partition UNINDEXED, "
                    "cohort UNINDEXED, opinion_ms UNINDEXED)"
                )
                conn.execute("PRAGMA user_version = 2")
            with self.assertRaisesRegex(ValueError, "stale"):
                retrieve(alias, {"problem": PLAN}, target_year=2024, cutoff_ms=50)
            write(root / "profile.json", {
                "ranker": {"method": "mean", "names": ["field_priority"]},
                "prompts": {"field": digest(priority.PROMPTS["field"])},
            })
            proposal = root / "idea.txt"
            proposal.write_text(PLAN + "\n", encoding="utf-8")
            calls = []

            def transport(*_args, **_kwargs):
                calls.append("field")
                raise AssertionError("field was called")

            args = [
                "proposal-review", "prioritize", str(proposal),
                "--profile", str(root / "profile.json"), "--work", str(root / "work"),
                "--output", str(root / "out.json"), "--target-year", "2024", "--cutoff-ms", "50",
            ]
            with patch.object(priority, "model_leak_judge", return_value=lambda _body: {"verdict": "clean"}), \
                    patch.object(stages, "run_command", side_effect=transport):
                self.assertEqual(main(args), 2)
            self.assertEqual(calls, [])
            self.assertFalse((root / "out.json").exists())


class ReceiptTests(TestCase):
    def test_claims_and_panel_reject_an_equal_value_of_another_type(self):
        judge = lambda _body: {"verdict": "clean"}
        from autopaperreview.proposal.leak_audit import apply_leak_audit
        from autopaperreview.proposal.sanitized import require_model_input

        with TemporaryDirectory(prefix="apr-round5-receipt-") as folder:
            root = Path(folder)
            body = {"c1": PLAN}
            message = apply_leak_audit(require_model_input(seal(body)), judge)
            output = {"c1": [0, 5, 1]}
            raw = json.dumps(output)
            descriptor = {
                "model": "claude-sonnet-5-5",
                "command": digest(claim_review.claude_argv()),
                "input": payload_sha256(message),
                "prompt": digest(claim_review.PROMPTS["forecast"]),
            }
            descriptor.update(version=1, mode="forecast")
            path = root / "claims" / "forecast" / (digest(descriptor) + ".json")
            stored = {"c1": [0.0, 5, 1]}
            write(path, {
                "request": descriptor, "state": "response", "output": stored,
                "reply": raw, "reply_sha256": payload_sha256(raw),
            })
            with patch.object(claim_review, "run_command", side_effect=AssertionError("business call")):
                with self.assertRaises(ValueError):
                    claim_review.request("forecast", body, root, True, leak_judge=judge)
            panel_body = {"s1": PLAN}
            panel_message = apply_leak_audit(require_model_input(seal(panel_body)), judge)
            panel_output = [{"n": 20, "grade": 3, "pro": None, "con": None}]
            panel_raw = json.dumps(panel_output)
            panel_descriptor = {
                "version": 1,
                "model": "claude-sonnet-5-5",
                "command": digest(panel.claude_argv()),
                "input": payload_sha256(panel_message),
                "prompt": digest(panel.FORECAST),
            }
            panel_path = root / "panel" / "forecast" / (digest(panel_descriptor) + ".json")
            swapped = [{"n": True, "grade": 3, "pro": None, "con": None}]
            write(panel_path, {
                "request": panel_descriptor, "state": "response", "output": swapped,
                "reply": panel_raw, "reply_sha256": payload_sha256(panel_raw),
            })
            with patch.object(panel, "run_command", side_effect=AssertionError("business call")):
                with self.assertRaises(ValueError):
                    panel.request("forecast", panel_body, root, True, leak_judge=judge)

    def test_clarification_abstention_must_be_the_sentinel_and_failure_is_not_success(self):
        judge = lambda _body: {"verdict": "clean"}
        from autopaperreview.proposal.leak_audit import apply_leak_audit
        from autopaperreview.proposal.sanitized import require_model_input

        with TemporaryDirectory(prefix="apr-round5-clarify-") as folder:
            root = Path(folder)
            question = {"question": PLAN}
            message = apply_leak_audit(require_model_input(seal(question)), judge)
            model, system, abstention = clarification.STAGES["question"]
            request = {
                "stage": "question",
                "model": model,
                "prompt": digest(system),
                "command": digest(clarification.claude_argv()),
                "input": payload_sha256(message),
            }
            path = root / "question" / (digest(request) + ".json")
            write(path, {"request": request, "output": {"abstain": False}, "disposition": "invalid"})
            with patch.object(clarification, "run_command", side_effect=AssertionError("business call")):
                with self.assertRaisesRegex(ValueError, "sentinel"):
                    clarification.judgment(
                        "question", question, root, cached_only=True, timeout=30, leak_judge=judge,
                    )
            write(path, {"request": request, "output": abstention, "disposition": "refusal"})
            with patch.object(clarification, "run_command", side_effect=AssertionError("business call")):
                _key, value = clarification.judgment(
                    "question", question, root, cached_only=True, timeout=30, leak_judge=judge,
                )
            self.assertEqual(value, abstention)
            path.unlink()
            raw = json.dumps({
                "type": "result",
                "is_error": True,
                "result": "sorry I cannot answer that",
            })
            with patch.object(clarification, "run_command", return_value=(raw, 0.01)):
                with self.assertRaises(CommandTransportError):
                    clarification.judgment(
                        "question", question, root, cached_only=False, timeout=30, leak_judge=judge,
                    )
            self.assertFalse(path.exists())

    def test_legacy_receipt_metadata_is_type_checked(self):
        source = {"problem": PLAN}
        with TemporaryDirectory(prefix="apr-round5-legacy-") as folder:
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
                "attempts": True,
                "latency_sec": 0.1,
            }
            cache = root / "cache" / "compress" / "receipt.json"
            write(cache, entry)
            write(root / "runs" / "synthetic" / "compress.json", {
                "bundle": "bundle", "cache": "cache/compress/receipt.json",
            })
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
            entry["attempts"] = 1
            entry["latency_sec"] = ["slow"]
            write(cache, entry)
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
            entry["latency_sec"] = 0.1
            entry["request"]["reply_protocol"] = 0
            write(cache, entry)
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
        with self.assertRaises(ValueError):
            require_business_receipt({"request": {"reply_protocol": 1}, "attempts": -1, "latency_sec": 0}, "compress")


class PublishRaceTests(TestCase):
    def test_three_publisher_threads_leave_one_report(self):
        from autopaperreview.proposal import file_io

        for _ in range(30):
            with TemporaryDirectory(prefix="apr-round5-race-") as folder:
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
