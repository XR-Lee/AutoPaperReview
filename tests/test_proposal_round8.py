"""Round-8 blockers: structured facts, history judge, directory lock, old pointers, object results."""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.command_transport import CommandTransportError
from autopaperreview.proposal import claim_review, clarification, file_io, reviewer_panel as panel
from autopaperreview.proposal import priority, runner, stages
from autopaperreview.proposal.data import digest, write
from autopaperreview.proposal.leak_audit import payload_sha256, serialize_payload, strict_model_value
from autopaperreview.proposal.prior_index import HistoryJudgeError, eligible_history, proposal_body
from autopaperreview.proposal.sanitized import factual_leak
from autopaperreview.proposal.stages import SECTIONS
from scripts import reproduce_proposal_rank

ROOT = Path(__file__).resolve().parents[1]
PLAN = "Could violet tags index archival baskets by location?"


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


class FactShapeRound8Tests(TestCase):
    def test_label_table_and_layout_shapes_block_and_metrics_stay(self):
        blocked = (
            "| Disposition | Outcome |\n| --- | --- |\n| synthetic plan | rejected |",
            "Panel ruling: decline publication",
            "Referee state for the plan: revision requested",
            "Dictamen del estudio: cambios mayores",
            "终审结果：不予录用",
            "稿件状态=已收录",
            "本稿得分：8/10",
            "【通讯作者】桐野",
            r"\affiliation{North Warehouse Laboratory}",
            "Autoría del estudio — Nila Corv",
        )
        allowed = (
            "A reference cosine similarity score: 0.625 is a design parameter.",
            "the paper should be accepted",
            "we accept the assumption",
            "signature of the kernel stays Laplacian",
            "Use a reconstruction score between 0.1 and 0.3.",
        )
        for text in blocked:
            self.assertTrue(factual_leak(text), text)
        for text in allowed:
            self.assertFalse(factual_leak(text), text)

    def test_score_cli_blocks_a_disposition_table_and_keeps_a_metric(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round8-score-") as folder:
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
                (2, "| Disposition | Outcome |\n| --- | --- |\n| synthetic plan | rejected |\n"),
                (2, "【通讯作者】桐野\n"),
                (0, "A reference cosine similarity score: 0.625 is a design parameter.\n"),
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
                    self.assertTrue(output.is_file())
                else:
                    self.assertEqual(log.read_text(encoding="utf-8"), "")
                    self.assertFalse(output.exists())


class HistoryRound8Tests(TestCase):
    def test_operator_fullwidth_and_resegmented_fields_stay_in_code(self):
        unary = {"problem": "The offset is \u2212x when the dye curve falls."}
        plain = {"problem": "The offset is x when the dye curve falls."}
        self.assertTrue(_eligible(unary, plain))
        binary = {"problem": "The load equals a \u2212 b for violet tags."}
        ascii_minus = {"problem": "The load equals a - b for violet tags."}
        self.assertFalse(_eligible(binary, ascii_minus))
        fullwidth = {"problem": "用标签索引篮筐．染料记录在上层．"}
        stops = {"problem": "用标签索引篮筐。染料记录在上层。"}
        self.assertFalse(_eligible(fullwidth, stops))
        whole = {"problem": "Alpha violet tags index archival baskets by location on the cloth shelf."}
        split = {
            "left": "Alpha violet tags index archival",
            "right": "baskets by location on the cloth shelf.",
        }
        self.assertFalse(_eligible(split, whole))
        with self.assertRaises(HistoryJudgeError):
            _eligible(
                {"problem": "Violet tags index archival baskets by place on shelf zero."},
                {"problem": "Violet tags index archival baskets by location on shelf zero."},
            )

    def _claude(self, root: Path, mode_path: Path, log: Path) -> Path:
        path = root / "bin" / "claude"
        path.parent.mkdir()
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"MODE = {str(mode_path)!r}\n"
            f"LOG = {str(log)!r}\n"
            "args = sys.argv\n"
            "system = ''\n"
            "if '--system-prompt-file' in args:\n"
            "    system = open(args[args.index('--system-prompt-file') + 1], encoding='utf-8').read()\n"
            "prompt = sys.stdin.read()\n"
            "if system.startswith('You judge whether two research proposals'):\n"
            "    kind = 'history'\n"
            "elif 'verdict' in system:\n"
            "    kind = 'leak'\n"
            "else:\n"
            "    kind = 'business'\n"
            "open(LOG, 'a', encoding='utf-8').write(json.dumps({\n"
            "    'kind': kind,\n"
            "    'history': 'self_history' in prompt,\n"
            "    'marker': 'SHELFPLACECOPY' in prompt,\n"
            "}) + '\\n')\n"
            "mode = open(MODE, encoding='utf-8').read().strip()\n"
            "if kind == 'history':\n"
            "    if mode == 'clean':\n"
            "        sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
            "    else:\n"
            "        sys.stdout.write(json.dumps({'relation': mode}))\n"
            "elif kind == 'leak':\n"
            "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
            "else:\n"
            "    body = json.loads(prompt)\n"
            "    sys.stdout.write(json.dumps({\n"
            "        'favorable': 'Fewer labels', 'unfavorable': 'Scope', 'estimate': 3.0,\n"
            "        'confidence': 0.5, 'wins': dict.fromkeys(body['examples'], 0.5),\n"
            "    }))\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def test_prioritize_cli_sends_the_history_judge(self):
        target = {"problem": "Violet tags index archival baskets by location on cloth shelf zero."}
        copied = {"problem": "Violet tags index archival baskets by place on cloth shelf zero."}
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
        near = _history(
            key="synthetic-copy",
            forum="synthetic-copy",
            target=5,
            proposal=copied,
            excerpts=[{"text": "SHELFPLACECOPY labels could be useful.", "review_ms": 4}],
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round8-history-") as folder:
            root = Path(folder)
            log = root / "calls.log"
            mode = root / "mode.txt"
            self._claude(root, mode, log)
            env["PATH"] = str(root / "bin") + os.pathsep + env.get("PATH", "")
            write(root / "proposal.json", target)
            write(root / "bank.json", [*peers, near])
            write(root / "profile.json", {
                "ranker": {"method": "mean", "names": ["anchored"]},
                "prompts": {"match": digest(priority.PROMPTS["match"])},
            })

            def run(name):
                mode.write_text(name, encoding="utf-8")
                log.write_text("", encoding="utf-8")
                output = root / f"out-{name}.json"
                completed = subprocess.run(
                    [
                        sys.executable, "-m", "autopaperreview", "proposal-review", "prioritize",
                        str(root / "proposal.json"), "--profile", str(root / "profile.json"),
                        "--work", str(root / f"work-{name}"), "--output", str(output),
                        "--target-year", "2024", "--cutoff-ms", "50",
                    ],
                    cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=60,
                )
                rows = [
                    json.loads(line)
                    for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                return completed, rows, output

            distinct, rows, output = run("distinct")
            self.assertEqual(distinct.returncode, 0, distinct.stderr)
            history = [row for row in rows if row["kind"] == "history"]
            self.assertEqual(len(history), 1, rows)
            self.assertTrue(history[0]["history"])
            self.assertTrue(any(row["kind"] == "business" and row["marker"] for row in rows))
            self.assertTrue(output.is_file())
            same, rows, output = run("same")
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertEqual(sum(row["kind"] == "history" for row in rows), 1, rows)
            self.assertFalse(any(row["kind"] == "business" and row["marker"] for row in rows))
            self.assertTrue(output.is_file())
            failed, _rows, output = run("clean")
            self.assertEqual(failed.returncode, 2, failed.stdout)
            self.assertIn("history", failed.stderr)
            self.assertFalse(output.exists())


class PublishRound8Tests(TestCase):
    def test_two_processes_stay_serial_when_the_lock_name_is_replaced(self):
        script = (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, os.environ['APR_SRC'])\n"
            "from autopaperreview.proposal import file_io\n"
            "log = Path(os.environ['APR_LOG'])\n"
            "real = file_io._Output._publish_critical\n"
            "def wrapped(self, content, *, replace=False):\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(f'enter {os.getpid()} {time.time()}\\n')\n"
            "        handle.flush()\n"
            "    lock = Path(os.environ['APR_DIR']) / '.apr-publish.lock'\n"
            "    lock.write_text('replaced', encoding='utf-8')\n"
            "    lock.unlink(missing_ok=True)\n"
            "    time.sleep(0.15)\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(f'exit {os.getpid()} {time.time()}\\n')\n"
            "        handle.flush()\n"
            "    return real(self, content, replace=replace)\n"
            "file_io._Output._publish_critical = wrapped\n"
            "file_io.write_text(Path(os.environ['APR_DEST']), os.environ['APR_TEXT'])\n"
        )
        # The child is a test process, not the product. APR_SRC is only on this helper.
        overlaps = successes = 0
        rounds = 8
        for index in range(rounds):
            with TemporaryDirectory(prefix="apr-round8-lock-") as folder:
                root = Path(folder)
                dest = root / "report.json"
                file_io.write_text(dest, "OLD")
                log = root / "critical.log"
                helper = root / "publish.py"
                helper.write_text(script, encoding="utf-8")
                procs = []
                for name in ("AAA", "BBB"):
                    env = os.environ.copy()
                    env.update({
                        "APR_SRC": str(ROOT / "src"),
                        "APR_LOG": str(log),
                        "APR_DIR": str(root),
                        "APR_DEST": str(dest),
                        "APR_TEXT": f"{name}-{index}",
                        "PYTHONPATH": str(ROOT / "src"),
                    })
                    procs.append(subprocess.Popen(
                        [sys.executable, str(helper)], cwd=root, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    ))
                codes = []
                for proc in procs:
                    _out, err = proc.communicate(timeout=20)
                    codes.append((proc.returncode, err))
                self.assertTrue(all(code == 0 for code, _err in codes), codes)
                successes += 2
                open_count = max_open = 0
                for line in log.read_text(encoding="utf-8").splitlines():
                    kind = line.split()[0]
                    open_count += 1 if kind == "enter" else -1
                    max_open = max(max_open, open_count)
                overlaps += max_open > 1
                self.assertIn(dest.read_text(encoding="utf-8"), {f"AAA-{index}", f"BBB-{index}"})
                self.assertFalse(list(root.glob(".apr-output-*.tmp")))
        self.assertEqual((rounds, successes, overlaps), (8, 16, 0))

    def test_score_cli_waits_on_the_directory_lock(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round8-cli-lock-") as folder:
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
            out_dir = root / "out"
            out_dir.mkdir()
            output = out_dir / "score.json"
            holder = subprocess.Popen(
                [
                    sys.executable, "-c",
                    "import fcntl, os, time, sys\n"
                    "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n"
                    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                    "open(sys.argv[2], 'w').write('held')\n"
                    "while not os.path.exists(sys.argv[3]):\n"
                    "    time.sleep(0.02)\n"
                    "try:\n"
                    "    os.unlink(os.path.join(sys.argv[1], '.apr-publish.lock'))\n"
                    "except FileNotFoundError:\n"
                    "    pass\n"
                    "fcntl.flock(fd, fcntl.LOCK_UN)\n"
                    "os.close(fd)\n",
                    str(out_dir), str(root / "held"), str(root / "release"),
                ],
                cwd=root,
            )
            deadline = time.time() + 5
            while not (root / "held").exists():
                self.assertLess(time.time(), deadline)
                time.sleep(0.02)
            proposal = root / "idea.txt"
            proposal.write_text(PLAN + "\n", encoding="utf-8")
            cli = subprocess.Popen(
                [
                    sys.executable, "-m", "autopaperreview", "proposal-review", "score",
                    str(proposal), "--work", str(root / "work"),
                    "--command", f"{sys.executable} {stub}", "--timeout", "30",
                    "--output", str(output),
                ],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.time() + 15
                while "business" not in (log.read_text(encoding="utf-8") if log.exists() else ""):
                    self.assertIsNone(cli.poll(), "score exited before the directory lock was tested")
                    self.assertLess(time.time(), deadline)
                    time.sleep(0.05)
                held_until = time.time() + 1
                while time.time() < held_until:
                    self.assertIsNone(cli.poll(), "score published while the directory lock was held")
                    time.sleep(0.05)
                (root / "release").write_text("go", encoding="utf-8")
                stdout, stderr = cli.communicate(timeout=20)
            finally:
                (root / "release").write_text("go", encoding="utf-8")
                if cli.poll() is None:
                    cli.kill()
                    cli.communicate(timeout=5)
                if holder.poll() is None:
                    holder.kill()
                    holder.wait(timeout=5)
            self.assertEqual(cli.returncode, 0, stderr)
            self.assertTrue(output.is_file())
            self.assertFalse(stdout and "Traceback" in stdout)

    def test_a_swap_during_replace_keeps_the_protected_bytes(self):
        cases = ("input", "manifest")
        kept = 0
        for kind in cases:
            with TemporaryDirectory(prefix=f"apr-round8-{kind}-") as folder:
                root = Path(folder)
                payload = b'{"files": {"synthetic": "abc"}}\n'
                destination = root / "report.json"
                file_io.write_text(destination, '{"old": 1}\n')
                source = root / ("notes.txt" if kind == "input" else "external-hmac-sha256.json")
                if kind == "manifest":
                    folder_path = root / "checksums"
                    folder_path.mkdir()
                    source = folder_path / "external-hmac-sha256.json"
                source.write_bytes(payload)
                real = file_io._renameat2
                moved = {"done": False}

                def hooked(*args):
                    if args[-1] == file_io.RENAME_EXCHANGE and not moved["done"]:
                        moved["done"] = True
                        os.replace(source, destination)
                    return real(*args)

                with file_io.protected_output(destination):
                    if kind == "input":
                        with file_io.open_input(source):
                            pass
                    else:
                        with patch.object(reproduce_proposal_rank, "repo_repro_dir", return_value=source.parent):
                            reproduce_proposal_rank._pin_checksum_files()
                    with patch.object(file_io, "_renameat2", side_effect=hooked):
                        with self.assertRaisesRegex(ValueError, "distinct|disappeared|identity"):
                            file_io.write_text(destination, '{"summary": true}\n')
                self.assertEqual(destination.read_bytes(), payload)
                self.assertFalse(list(root.glob(".apr-output-*.tmp")))
                kept += 1
        self.assertEqual(kept, 2)


class LegacyPointerTests(TestCase):
    def test_a_pointer_without_model_and_command_is_stale(self):
        source = {"s1": PLAN}
        output = {section: {"text": None, "evidence": []} for section in SECTIONS}
        output["problem"] = {"text": PLAN, "evidence": ["s1"]}
        raw = json.dumps(output)
        request = {
            "stage": "compress",
            "input_sha256": payload_sha256(serialize_payload(source)),
            "system_sha256": digest(stages.PROMPTS["compress"]),
            "model": "stub-model",
            "command_sha256": "a" * 64,
            "reply_protocol": 1,
        }
        entry = {
            "request": request,
            "output": output,
            "reply": raw,
            "reply_sha256": payload_sha256(raw),
            "attempts": 1,
            "latency_sec": 0.1,
        }
        case = {"key": "synthetic", "bundle": "bundle"}
        with TemporaryDirectory(prefix="apr-round8-legacy-") as folder:
            root = Path(folder)
            bundle = root / "inputs" / "bundle"
            bundle.mkdir(parents=True)
            write(bundle / "candidate.json", source)
            write(root / "cache" / "compress" / "receipt.json", entry)
            write(root / "runs" / "synthetic" / "compress.json", {
                "bundle": "bundle",
                "cache": "cache/compress/receipt.json",
            })
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
            entry["request"] = {**request, "model": "other-model"}
            write(root / "cache" / "compress" / "receipt.json", entry)
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
            entry["request"] = {**request, "command_sha256": "b" * 64}
            write(root / "cache" / "compress" / "receipt.json", entry)
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.stage_result(root, case, "compress")
            self.assertFalse(list(root.glob("*.report")))


class EnvelopeObjectTests(TestCase):
    def test_object_result_recursion_and_a_business_object(self):
        failure = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "terminal_reason": "aborted",
                "errors": ["synthetic failure"],
            },
        }
        with self.assertRaises(CommandTransportError):
            strict_model_value(json.dumps(failure))
        null_text = {
            "type": "result", "subtype": "success", "is_error": False, "result": "null",
        }
        with self.assertRaises(CommandTransportError):
            strict_model_value(json.dumps(null_text))
        business = {"c1": [50, 30, 1]}
        wrapped = {
            "type": "result", "subtype": "success", "is_error": False, "result": business,
        }
        self.assertEqual(strict_model_value(json.dumps(wrapped)), business)
        judge = lambda _body: {"verdict": "clean"}
        calls = (
            (claim_review, "forecast", {"c1": PLAN}),
            (panel, "forecast", {"s1": PLAN}),
            (clarification, "question", {"question": PLAN}),
        )
        for module, stage, body in calls:
            with TemporaryDirectory(prefix="apr-round8-object-") as folder:
                root = Path(folder)
                with patch.object(module, "run_command", return_value=(json.dumps(failure), 0.01)) as command:
                    with self.assertRaises(CommandTransportError):
                        if module is clarification:
                            module.judgment(
                                stage, body, root, cached_only=False, timeout=30, leak_judge=judge,
                            )
                        else:
                            module.request(stage, body, root, False, leak_judge=judge)
                    self.assertEqual(command.call_count, 1)
                self.assertFalse(list(root.rglob("*.json")))
        with TemporaryDirectory(prefix="apr-round8-object-ok-") as folder:
            root = Path(folder)
            with patch.object(claim_review, "run_command", return_value=(json.dumps(wrapped), 0.01)) as command:
                key, result = claim_review.request(
                    "forecast", {"c1": PLAN}, root, False, leak_judge=judge,
                )
            self.assertEqual(command.call_count, 1)
            self.assertEqual(result["state"], "response")
            self.assertEqual(result["output"], business)
            self.assertTrue((root / "claims" / "forecast" / f"{key}.json").is_file())


    def test_claims_and_panel_cli_stop_on_an_object_failure(self):
        failure = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "terminal_reason": "aborted",
                "errors": ["synthetic failure"],
            },
        }
        plan = {"problem": PLAN, "approach": "Compare a manual log with the planned violet tags."}
        source = {"u1": "The violet tags would allow visitors to locate baskets."}
        row = {
            "id": "synthetic-candidate",
            "proposal": plan,
            "overall": 3,
            "outcome_screen": {"proposal": {}},
            "source_audit": {
                "source_units": source,
                "source_sha256": digest(source),
                "proposal_sha256": digest(plan),
            },
        }
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round8-cli-object-") as folder:
            root = Path(folder)
            log = root / "calls.log"
            binary = root / "bin" / "claude"
            binary.parent.mkdir()
            binary.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                f"LOG = {str(log)!r}\n"
                f"FAILURE = {json.dumps(failure)!r}\n"
                "args = sys.argv\n"
                "system = ''\n"
                "if '--system-prompt-file' in args:\n"
                "    system = open(args[args.index('--system-prompt-file') + 1], encoding='utf-8').read()\n"
                "sys.stdin.read()\n"
                "kind = 'leak' if 'verdict' in system else 'business'\n"
                "open(LOG, 'a', encoding='utf-8').write(kind + '\\n')\n"
                "sys.stdout.write(json.dumps({'verdict': 'clean'}) if kind == 'leak' else FAILURE)\n",
                encoding="utf-8",
            )
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
            env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
            write(root / "rows.json", [row])
            write(root / "claims-profile.json", {"simple_event": {"coef": [[0, 0]] * 14}})
            for name, extra in (
                ("claims", ["--model", str(root / "claims-profile.json")]),
                ("panel", []),
            ):
                log.write_text("", encoding="utf-8")
                output = root / f"{name}.json"
                completed = subprocess.run(
                    [
                        sys.executable, "-m", "autopaperreview", "proposal-review", name,
                        str(root / "rows.json"), "--work", str(root / name),
                        "--output", str(output), *extra,
                    ],
                    cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=30,
                )
                kinds = log.read_text(encoding="utf-8").split()
                self.assertEqual(completed.returncode, 2, (name, completed.stderr, kinds))
                self.assertEqual(kinds.count("business"), 1, (name, kinds))
                self.assertGreaterEqual(kinds.count("leak"), 1, (name, kinds))
                self.assertFalse(output.exists(), name)
                self.assertIn("command", completed.stderr, name)


class CacheFallbackTests(TestCase):
    def test_claims_reads_a_matching_legacy_receipt(self):
        judge = lambda _body: {"verdict": "clean"}
        body = {"c1": PLAN}
        payload = {"c1": [50, 30, 1]}
        wrapped = json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": payload,
        })
        with TemporaryDirectory(prefix="apr-round8-cache-") as folder:
            root = Path(folder)
            with patch.object(claim_review, "run_command", return_value=(wrapped, 0.01)):
                key, first = claim_review.request("forecast", body, root, False, leak_judge=judge)
            new_path = root / "claims" / "forecast" / f"{key}.json"
            legacy = root / "forecast" / f"{key}.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(new_path.read_text(encoding="utf-8"), encoding="utf-8")
            new_path.unlink()
            with patch.object(claim_review, "run_command") as command:
                again_key, again = claim_review.request("forecast", body, root, True, leak_judge=judge)
            command.assert_not_called()
            self.assertEqual(again_key, key)
            self.assertEqual(again["output"], first["output"])
            stored = json.loads(legacy.read_text(encoding="utf-8"))
            stored["request"]["model"] = "other-model"
            legacy.write_text(json.dumps(stored), encoding="utf-8")
            with patch.object(claim_review, "run_command") as command:
                with self.assertRaises(ValueError):
                    claim_review.request("forecast", body, root, True, leak_judge=judge)
            command.assert_not_called()

    def test_panel_reads_a_matching_legacy_receipt(self):
        judge = lambda _body: {"verdict": "clean"}
        body = {"s1": PLAN}
        payload = [{"n": 20, "grade": 3, "pro": None, "con": None}]
        wrapped = json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "result": payload,
        })
        with TemporaryDirectory(prefix="apr-round8-panel-cache-") as folder:
            root = Path(folder)
            with patch.object(panel, "run_command", return_value=(wrapped, 0.01)):
                key, first = panel.request("forecast", body, root, False, leak_judge=judge)
            new_path = root / "panel" / "forecast" / f"{key}.json"
            legacy = root / "forecast" / f"{key}.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(new_path.read_text(encoding="utf-8"), encoding="utf-8")
            new_path.unlink()
            with patch.object(panel, "run_command") as command:
                again_key, again = panel.request("forecast", body, root, True, leak_judge=judge)
            command.assert_not_called()
            self.assertEqual(again_key, key)
            self.assertEqual(again["output"], first["output"])
