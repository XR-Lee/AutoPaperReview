"""Round-4 blockers, exercised through the score CLI and the send boundary."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.command_transport import CommandTransportError, run_command
from autopaperreview.proposal import claim_review, clarification, priority, reviewer_panel as panel
from autopaperreview.proposal import runner, stages
from autopaperreview.proposal.data import archived_notes, digest, reported_result, write
from autopaperreview.proposal.leak_audit import payload_sha256, serialize_payload, strict_model_value
from autopaperreview.proposal.prior_index import (
    INDEX_CLOCK_VERSION, build, eligible_history, proposal_body, retrieve,
)
from autopaperreview.proposal.sanitized import factual_leak, sanitize_proposal, score_field, seal

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
        completed = subprocess.run(
            [
                sys.executable, "-m", "autopaperreview", "proposal-review", "score",
                str(proposal), "--work", str(work), "--command", command,
                "--output", str(output), "--timeout", "30",
            ],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        return completed

    def test_score_cli_blocks_explicit_facts_and_passes_scientific_text(self):
        leaks = (
            "The manuscript was rejected after review.",
            "Decision: accepted",
            "decision: acc",
            "El manuscrito fue aceptado.",
            "Estado: rechazo",
            "稿件已被拒稿",
            "决定：录用",
            "overall score is 8",
            "puntuacion: 8",
            "puntuacion entre 6 y 8",
            "总分为 8",
            "评分区间 6 到 8",
            "review status: major revision",
            "审稿状态：大修",
            "Authors: Ada Lovelace",
            "作者：艾达",
            "Affiliation: Example Laboratory",
            "单位：示例研究所",
            "phone: +1 202 555 0147",
            "Decision: <b>accepted</b>",
            "Ｄｅｃｉｓｉｏｎ：accepted",
        )
        allowed = (
            "the paper was accepted practice in this subfield",
            "the loss is 0.2 on the synthetic formula",
            "we plan a score between 1 and 5 for the layout",
            "an independent earlier preprint by Ada Lovelace reported a similarity of 0.2",
            "the proposal should be accepted",
            "we accept the assumption",
            "major revision of the formula is planned",
            "By Monte Carlo simulation the planned index stays bounded",
            "计划评分区间为 6 到 8",
        )
        with TemporaryDirectory(prefix="apr-round4-score-") as folder:
            root = Path(folder)
            log = root / "calls.log"
            command = _command_stub(
                root / "stub.py",
                log,
                "if kind == 'judge':\n"
                "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
                "else:\n"
                "    start = data.rfind('\\n{')\n"
                "    payload = json.loads(data[start + 1:] if start >= 0 else data)\n"
                "    basis = next(iter(payload))\n"
                "    row = {'score': 3, 'rationale': 'The plan is specific.', 'basis': [basis]}\n"
                f"    sys.stdout.write(json.dumps({{name: row for name in {RUBRIC_KEYS!r}}}))\n",
            )
            for index, text in enumerate(leaks):
                work = root / f"leak-{index}"
                proposal = root / f"leak-{index}.txt"
                proposal.write_text(text + "\n", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                completed = self._score(work, proposal, command, root / f"leak-{index}.json")
                self.assertEqual(completed.returncode, 2, (text, completed.stderr))
                self.assertEqual(log.read_text(encoding="utf-8"), "", text)
                self.assertFalse((root / f"leak-{index}.json").exists())
            for index, text in enumerate(allowed):
                work = root / f"ok-{index}"
                proposal = root / f"ok-{index}.txt"
                proposal.write_text(text + "\n", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                output = root / f"ok-{index}.json"
                completed = self._score(work, proposal, command, output)
                self.assertEqual(completed.returncode, 0, (text, completed.stderr))
                self.assertEqual(log.read_text(encoding="utf-8").split(), ["judge", "business"], text)
                self.assertIn("importance", output.read_text(encoding="utf-8"))

    def test_score_cli_blocks_a_fact_split_across_allowed_fields(self):
        with TemporaryDirectory(prefix="apr-round4-split-") as folder:
            root = Path(folder)
            stub = root / "stub.py"
            stub.write_text("import sys\nsys.stdin.read()\nsys.stdout.write('{}')\n", encoding="utf-8")
            sections = {key: "A synthetic plan for violet basket labels." for key in stages.SECTIONS}
            sections["problem"] = "submission status"
            sections["approach"] = "accepted"
            proposal = root / "split.json"
            proposal.write_text(json.dumps(sections), encoding="utf-8")
            completed = self._score(root / "work", proposal, f"{sys.executable} {stub}", root / "out.json")
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse((root / "out.json").exists())
            self.assertTrue(factual_leak("submission status\naccepted"))

    def test_score_cli_rejects_failure_envelopes_and_a_tampered_output(self):
        with TemporaryDirectory(prefix="apr-round4-envelope-") as folder:
            root = Path(folder)
            log = root / "calls.log"
            command = _command_stub(
                root / "stub.py",
                log,
                "if kind == 'judge':\n"
                "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
                "    raise SystemExit\n"
                "row = {'score': 3, 'rationale': 'The plan is specific.', 'basis': ['proposal']}\n"
                f"inner = {{name: row for name in {RUBRIC_KEYS!r}}}\n"
                "marker = data.rsplit('CASE_', 1)[-1].split()[0]\n"
                "envelopes = {\n"
                "    'terminal': {'terminal_reason': 'aborted'},\n"
                "    'api': {'api_error_status': 500},\n"
                "    'permission': {'permission_denials': ['denied']},\n"
                "    'safety': {'safety_stops': 1},\n"
                "    'stop': {'stop_reason': 'length'},\n"
                "    'boolstatus': {'status': True},\n"
                "    'boolsubtype': {'subtype': True},\n"
                "    'conflict': {'stop_reason': 'end_turn', 'finish_reason': 'length'},\n"
                "    'refusal': {'refusal': True},\n"
                "    'nullusage': {'modelUsage': None},\n"
                "}\n"
                "if marker == 'nan':\n"
                "    sys.stdout.write('{\"type\":\"result\",\"result\":' + json.dumps(json.dumps(inner)) + ',\"usage\":NaN}')\n"
                "else:\n"
                "    body = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': json.dumps(inner)}\n"
                "    body.update(envelopes[marker])\n"
                "    sys.stdout.write(json.dumps(body))\n",
            )
            for name in (
                "terminal", "api", "permission", "safety", "stop",
                "boolstatus", "boolsubtype", "conflict", "refusal", "nan",
            ):
                proposal = root / f"{name}.txt"
                proposal.write_text(f"CASE_{name} violet tags index baskets.\n", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                completed = self._score(root / name, proposal, command, root / f"{name}.json")
                self.assertEqual(completed.returncode, 2, (name, completed.stderr))
                self.assertIn("business", log.read_text(encoding="utf-8"), name)
                self.assertFalse((root / f"{name}.json").exists())
            proposal = root / "nullusage.txt"
            proposal.write_text("CASE_nullusage violet tags index baskets.\n", encoding="utf-8")
            log.write_text("", encoding="utf-8")
            kept = root / "nullusage.json"
            completed = self._score(root / "nullusage", proposal, command, kept)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").split(), ["judge", "business"])
            self.assertIn("importance", kept.read_text(encoding="utf-8"))
            proposal = root / "clean.txt"
            proposal.write_text(PLAN + "\n", encoding="utf-8")
            output = root / "clean.json"
            # A clean business reply, then a tampered stored output.
            command = _command_stub(
                root / "stub.py",
                log,
                "if kind == 'judge':\n"
                "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
                "else:\n"
                "    row = {'score': 3, 'rationale': 'The plan is specific.', 'basis': ['proposal']}\n"
                f"    sys.stdout.write(json.dumps({{name: row for name in {RUBRIC_KEYS!r}}}))\n",
            )
            first = self._score(root / "tamper", proposal, command, output)
            self.assertEqual(first.returncode, 0, first.stderr)
            receipt = next(
                path for path in (root / "tamper" / "cache").rglob("*.json")
                if path.parent.name == "review"
            )
            stored = json.loads(receipt.read_text(encoding="utf-8"))
            stored["output"] = {"illegal": True}
            receipt.write_text(json.dumps(stored), encoding="utf-8")
            second = self._score(root / "tamper", proposal, command, root / "tamper-out.json")
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertNotIn("illegal", second.stdout)
            self.assertFalse((root / "tamper-out.json").exists())


class StructureTests(TestCase):
    def test_nested_identity_keys_fail_closed_and_scientific_keys_stay(self):
        blocked = (
            "institution", "phone", "orcid", "affiliation", "university",
            "inst\u200bitution", "overall\\_{score}", "email_address",
        )
        allowed = (
            "mean_similarity_score", "threshold_score", "last_measurement_score",
            "contact_surface", "optical_contact_angle", "measurement_score", "address_space",
        )
        for key in blocked:
            self.assertTrue(score_field(key), key)
            with self.assertRaises(ValueError):
                seal({key: PLAN})
        for key in allowed:
            self.assertFalse(score_field(key), key)
            seal({key: PLAN})

    def test_renamed_fields_are_the_same_proposal(self):
        target = {
            "problem": "Alpha segment about violet tags.",
            "approach": "Beta segment about basket layout.",
            "validation_plan": "Gamma segment about the planned count.",
        }
        renamed = {
            "mechanism": "Gamma segment about the planned count.",
            "intended_value": "Alpha segment about violet tags.",
            "assumptions": "Beta segment about basket layout.",
        }
        self.assertEqual(proposal_body(target), proposal_body(renamed))
        self.assertNotEqual(proposal_body(target), proposal_body({"problem": "A different shelf."}))
        copy = _history(
            key="synthetic-copy",
            proposal=renamed,
            opinions=[{"text": "SELFHISTORYMARKER labels could be useful.", "review_ms": 4}],
        )
        self.assertFalse(eligible_history(copy, (2024, 20), exclude_text=proposal_body(target)))

    def test_prioritize_drops_a_renamed_self_history_opinion(self):
        target = {
            "problem": "Alpha segment about violet tags.",
            "approach": "Beta segment about basket layout.",
            "validation_plan": "Gamma segment about the planned count.",
        }
        peers = [
            _history(
                key=f"synthetic-peer-{index}",
                target=1 + index / 10,
                proposal={"problem": f"Index cloth archive number {index}."},
                excerpts=[{"text": "PEEROPINIONKEEP labels could be useful.", "review_ms": 4}],
            )
            for index in range(8)
        ]
        copied = _history(
            key="synthetic-copy",
            target=5,
            proposal={
                "mechanism": target["validation_plan"],
                "intended_value": target["problem"],
                "assumptions": target["approach"],
            },
            excerpts=[{"text": "SELFHISTORYMARKER labels could be useful.", "review_ms": 4}],
        )
        prompts = []

        def transport(_argv, *, system, prompt, **_kwargs):
            prompts.append(prompt)
            body = json.loads(prompt)
            reply = {
                "favorable": "Fewer labels", "unfavorable": "Scope", "estimate": 3.0,
                "confidence": 0.5, "wins": dict.fromkeys(body["examples"], 0.5),
            }
            return json.dumps(reply), 0

        with TemporaryDirectory(prefix="apr-round4-bank-") as folder:
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
                "--output", str(root / "out.json"), "--target-year", "2024", "--cutoff-ms", "20",
            ]
            with patch.object(priority, "model_leak_judge", return_value=lambda _body: {"verdict": "clean"}), \
                    patch.object(stages, "run_command", side_effect=transport):
                self.assertEqual(main(args), 0)
        self.assertTrue(prompts)
        self.assertIn("PEEROPINIONKEEP", prompts[0])
        self.assertNotIn("SELFHISTORYMARKER", prompts[0])

    def test_stale_index_is_refused_by_retrieval_and_the_cli(self):
        with TemporaryDirectory(prefix="apr-round4-index-") as folder:
            root = Path(folder)
            corpus = root / "corpus.jsonl"
            row = _history(title="Violet basket indexing", abstract="A synthetic index of violet baskets and labels.")
            corpus.write_text(json.dumps(row) + "\n", encoding="utf-8")
            database = root / "prior.sqlite"
            build(corpus, database)
            with sqlite3.connect(database) as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], INDEX_CLOCK_VERSION)
                conn.execute("PRAGMA user_version = 0")
            with self.assertRaisesRegex(ValueError, "stale"):
                retrieve(database, {"problem": PLAN}, target_year=2024, cutoff_ms=50)
            write(root / "profile.json", {
                "ranker": {"method": "mean", "names": ["field_priority"]},
                "prompts": {"field": digest(priority.PROMPTS["field"])},
            })
            proposal = root / "idea.txt"
            proposal.write_text(PLAN + "\n", encoding="utf-8")
            args = [
                "proposal-review", "prioritize", str(proposal),
                "--profile", str(root / "profile.json"), "--work", str(root / "work"),
                "--output", str(root / "out.json"), "--target-year", "2024", "--cutoff-ms", "50",
            ]
            with patch.object(priority, "model_leak_judge", return_value=lambda _body: {"verdict": "clean"}), \
                    patch.object(stages, "run_command", side_effect=AssertionError("business call")):
                self.assertEqual(main(args), 2)
            self.assertFalse((root / "out.json").exists())

    def test_identity_aliases_and_archive_tail_fail_closed(self):
        self.assertFalse(eligible_history(_history(forum="synthetic-other "), (2024, 50), exclude_forum="synthetic-other"))
        self.assertFalse(eligible_history(_history(key="Synthetic-Other"), (2024, 50), exclude_key="synthetic-other"))
        nested = _history(metadata={"arxiv": "9999.11111"})
        self.assertFalse(eligible_history(nested, (2024, 50), exclude_arxiv="9999.11111"))
        with TemporaryDirectory(prefix="apr-round4-archive-") as folder:
            root = Path(folder)
            escaped = root / "escaped.json"
            escaped.write_text('{"notes": [{"id": "a"}], "\\u006eotes": []}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                list(archived_notes(escaped))
            truncated = root / "truncated.json"
            truncated.write_text('{"notes": [{"id": "a"}]', encoding="utf-8")
            with self.assertRaises(ValueError):
                list(archived_notes(truncated))

    def test_strict_reply_rejects_nan_and_invalid_utf8(self):
        inner = json.dumps({"ok": True})
        with self.assertRaises(ValueError):
            strict_model_value('{"type":"result","subtype":"success","is_error":false,"result":' + json.dumps(inner) + ',"usage":NaN}')
        script = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"
        kwargs = {"system": "", "prompt": "ping", "model": "stub", "timeout": 30}
        with self.assertRaises(CommandTransportError):
            run_command([sys.executable, "-c", script], strict=True, **kwargs)
        text, _latency = run_command([sys.executable, "-c", script], strict=False, **kwargs)
        self.assertIn("\ufffd", text)

    def test_auxiliary_receipts_do_not_replay_a_folded_output(self):
        from autopaperreview.proposal.leak_audit import apply_leak_audit
        from autopaperreview.proposal.sanitized import require_model_input

        judge = lambda _body: {"verdict": "clean"}
        with TemporaryDirectory(prefix="apr-round4-receipt-") as folder:
            root = Path(folder)
            body = {"claim": PLAN}
            sealed = require_model_input(seal(body))
            message = apply_leak_audit(sealed, judge)
            descriptor = {
                "model": "claude-sonnet-5-5",
                "command": digest(claim_review.claude_argv()),
                "input": payload_sha256(message),
                "prompt": digest(claim_review.PROMPTS["ground"]),
            }
            path = root / "ground" / (digest(descriptor) + ".json")
            write(path, {"request": descriptor, "state": "response", "output": {"folded": True}})
            with patch.object(claim_review, "run_command", side_effect=AssertionError("business call")):
                with self.assertRaises(ValueError):
                    claim_review.request("ground", body, root, True, leak_judge=judge)
            panel_body = {"s1": PLAN}
            panel_message = apply_leak_audit(require_model_input(seal(panel_body)), judge)
            panel_descriptor = {
                "version": 1,
                "model": "claude-sonnet-5-5",
                "command": digest(panel.claude_argv()),
                "input": payload_sha256(panel_message),
                "prompt": digest(panel.FORECAST),
            }
            panel_path = root / "forecast" / (digest(panel_descriptor) + ".json")
            write(panel_path, {"request": panel_descriptor, "state": "response", "output": [{"folded": True}]})
            with patch.object(panel, "run_command", side_effect=AssertionError("business call")):
                with self.assertRaises(ValueError):
                    panel.request("forecast", panel_body, root, True, leak_judge=judge)
            question = {"question": PLAN}
            question_message = apply_leak_audit(require_model_input(seal(question)), judge)
            model, system, _abstention = clarification.STAGES["question"]
            question_request = {
                "stage": "question",
                "model": model,
                "prompt": digest(system),
                "command": digest(clarification.claude_argv()),
                "input": payload_sha256(question_message),
            }
            question_path = root / "question" / (digest(question_request) + ".json")
            write(question_path, {
                "request": question_request,
                "output": {"folded": True},
                "disposition": "response",
            })
            with patch.object(clarification, "run_command", side_effect=AssertionError("business call")):
                with self.assertRaises(ValueError):
                    clarification.judgment(
                        "question", question, root, cached_only=True, timeout=30, leak_judge=judge,
                    )

    def test_legacy_stage_result_rejects_a_receipt_without_a_strict_reply(self):
        source = {"problem": PLAN}
        with TemporaryDirectory(prefix="apr-round4-legacy-") as folder:
            root = Path(folder)
            case = {"key": "synthetic", "bundle": "bundle"}
            bundle = root / "inputs" / "bundle"
            bundle.mkdir(parents=True)
            write(bundle / "candidate.json", source)
            cache = root / "cache" / "compress" / "receipt.json"
            write(cache, {
                "request": {
                    "stage": "compress",
                    "input_sha256": payload_sha256(serialize_payload(source)),
                    "system_sha256": digest(stages.PROMPTS["compress"]),
                },
                "output": {"problem": {"text": PLAN, "evidence": []}, "folded": True},
            })
            write(root / "runs" / "synthetic" / "compress.json", {"bundle": "bundle", "cache": "cache/compress/receipt.json"})
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
