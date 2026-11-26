"""Round-9 blockers: review-fact shapes, history filter order, cache digest."""

import errno
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.proposal import claim_review, file_io, reviewer_panel as panel
from autopaperreview.proposal import priority, runner, stages
from autopaperreview.proposal.data import digest, reported_result, write
from autopaperreview.proposal.leak_audit import payload_sha256, serialize_payload
from autopaperreview.proposal.prior_index import (
    HistoryJudgeError, calibration_rows, eligible_history, proposal_body,
)
from autopaperreview.proposal.sanitized import factual_leak
from autopaperreview.proposal.stages import SECTIONS

ROOT = Path(__file__).resolve().parents[1]
PLAN = "Could violet tags index archival baskets by location?"
TARGET = "Violet tags index archival baskets by location on cloth shelf zero."
NEAR = "Violet tags index archival baskets by place on cloth shelf zero."

_CATEGORIES = ("decision", "score", "range", "status", "author", "affiliation")
_FORMATS = ("label", "markdown", "html", "layout", "bracket", "latex")
_LANGUAGES = ("en", "zh", "es")
_HEADS = {
    "decision": {"en": "Decision", "zh": "决定", "es": "Dictamen"},
    "score": {"en": "Overall rating", "zh": "本稿得分", "es": "Puntuacion"},
    "range": {"en": "Rating range", "zh": "评分区间", "es": "Intervalo"},
    "status": {"en": "Review status", "zh": "审稿状态", "es": "Estado de revision"},
    "author": {"en": "Author", "zh": "作者", "es": "Autor"},
    "affiliation": {"en": "Affiliation", "zh": "单位", "es": "Institucion"},
}
_VALUES = {
    "decision": {"en": "weak accept", "zh": "不予录用", "es": "rechazado"},
    "score": {"en": "8", "zh": "8", "es": "7"},
    "range": {"en": "6-8", "zh": "6到8", "es": "6-8"},
    "status": {
        "en": ("withdrawn", "under review", "desk-rejected", "camera-ready", "rebuttal", "meta-review"),
        "zh": ("已撤稿", "审稿中", "退修", "在审", "复审", "撤稿"),
        "es": ("retirado", "en revision", "bajo revision", "pendiente", "rechazo de mesa", "retirada"),
    },
    "author": {"en": "Ada North", "zh": "桐野", "es": "Nila Corv"},
    "affiliation": {"en": "North Warehouse Laboratory", "zh": "北仓实验室", "es": "Instituto Norte"},
}


def _value(category, language, format_index):
    values = _VALUES[category][language]
    if isinstance(values, tuple):
        return values[format_index]
    return values


def _render(category, fmt, language):
    """One synthetic fact. The wording is local to this fixture."""
    cat_index = _CATEGORIES.index(category)
    lang_index = _LANGUAGES.index(language)
    format_index = _FORMATS.index(fmt)
    head = _HEADS[category][language]
    value = _value(category, language, format_index)
    if fmt == "label":
        return f"{head}{(':', ' -', ' =')[cat_index % 3]} {value}"
    if fmt == "markdown":
        kind = lang_index % 3
        if kind == 0:
            return f"| {head}: {value} |"
        if kind == 1:
            return f"| {head} |\n| --- |\n| {value} |"
        return f"| **{head}** | {value} |"
    if fmt == "html":
        kind = cat_index % 3
        if kind == 0:
            return f"<table><tr><th>{head}</th><td>{value}</td></tr></table>"
        if kind == 1:
            return (
                f"<table><tr><th>{head}</th><th>Notes</th></tr>"
                f"<tr><td>{value}</td><td>shelf</td></tr></table>"
            )
        return f"<table><tr><td>{head}: {value}</td></tr></table>"
    if fmt == "layout":
        kind = lang_index % 3
        if kind == 0:
            return f"{head}: {value}\n"
        if kind == 1:
            return f"<dl><dt>{head}</dt><dd>{value}</dd></dl>"
        return f"{head}\n{value}"
    if fmt == "bracket":
        kind = (cat_index + lang_index) % 4
        if kind == 0:
            return f"[{head}: {value}]"
        if kind == 1:
            return f"[{head}] {value}"
        if kind == 2:
            return f"({head}: {value})"
        return f"（{head}：{value}）"
    if format_index % 3 == 0:
        return f"\\item[{head}] {value}"
    if format_index % 3 == 1:
        return f"\\textbf{{{head}}} {value}"
    return "\\begin{tabular}{ll}\n" + f"{head} & {value} \\\\\n" + "\\end{tabular}"


def _cross_product():
    samples = [
        (category, fmt, language, _render(category, fmt, language))
        for category in _CATEGORIES
        for fmt in _FORMATS
        for language in _LANGUAGES
    ]
    if len(samples) != 108 or len({text for _, _, _, text in samples}) != 108:
        raise AssertionError("cross product is not 108 distinct texts")
    return samples


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


class FactShapeRound9Tests(TestCase):
    def test_cross_product_blocks_and_scientific_controls_stay(self):
        missed = [item for item in _cross_product() if not factual_leak(item[3])]
        self.assertEqual(missed, [])
        controls = (
            "| metric | value |\n| --- | --- |\n| accuracy | 0.91 |\n| loss | 0.20 |\n",
            "<table><tr><th>accuracy</th><td>0.91</td></tr><tr><th>loss</th><td>0.20</td></tr></table>",
            "The update is $x_{t+1} = x_t - \\eta g_t$.",
            "See the earlier note [Smith 2020] and (see Figure 2).",
            "Use a reconstruction score between 0.1 and 0.3.",
            "Configuration\n============\n0.625\n",
            "Model setup\n128\n",
            "The reference loss (0.625) is a design parameter.",
            "accuracy: 0.91",
            "loss = 0.2",
            "准确率: 0.91",
            "准确率：0.91",
            "重建误差: 0.2",
            "A reference cosine similarity score: 0.625 is a design parameter.",
            "the similarity score is 0.2",
            "we accept the assumption",
            "the proposal should be accepted",
            "signature of the kernel stays Laplacian",
        )
        blocked = [text for text in controls if factual_leak(text)]
        self.assertEqual(blocked, [])
        for text in ("accuracy: 0.91", "loss = 0.2", "准确率: 0.91", "准确率：0.91", "重建误差: 0.2"):
            self.assertFalse(reported_result(text), text)
        self.assertTrue(reported_result("Our search accuracy was 91%."))
        self.assertTrue(reported_result("Our experiments demonstrate substantial improvements."))
        self.assertTrue(factual_leak("(8)"))
        self.assertTrue(factual_leak("Overall rating\n8"))

    def test_score_cli_blocks_a_status_table_and_keeps_a_metric(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round9-score-") as folder:
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
                (2, "| Review status | Value |\n| --- | --- |\n| shelf plan | withdrawn |\n"),
                (2, "[Review status] under review\n"),
                (2, "\\textbf{审稿状态} 已撤稿\n"),
                (0, "accuracy: 0.91\n"),
                (0, "Configuration\n============\n0.625\n"),
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
                self.assertEqual(completed.returncode, code, (text, completed.stderr))
                if code == 0:
                    self.assertEqual(log.read_text(encoding="utf-8").split(), ["judge", "business"])
                    self.assertTrue(output.is_file())
                else:
                    self.assertEqual(log.read_text(encoding="utf-8"), "")
                    self.assertFalse(output.exists())


class HistoryOrderRound9Tests(TestCase):
    def test_local_filters_drop_a_near_copy_before_the_judge(self):
        calls = []

        def judge(_body):
            calls.append("history")
            raise AssertionError("history judge")

        target = {"problem": TARGET}
        near = {"problem": NEAR}
        exclude = proposal_body(target)
        late_year = _history(year=2024, proposal=near, key="synthetic-year", forum="synthetic-year")
        late_revision = _history(
            revision_ms=60, proposal=near, key="synthetic-revision", forum="synthetic-revision",
        )
        same_key = _history(
            key="synthetic-target ", proposal=near, forum="synthetic-key",
        )
        for row in (late_year, late_revision):
            calls.clear()
            self.assertFalse(eligible_history(
                row, (2024, 50), exclude_text=exclude, history_judge=judge,
            ))
            self.assertEqual(calls, [])
        calls.clear()
        self.assertFalse(eligible_history(
            same_key, (2024, 50), exclude_key="synthetic-target", exclude_text=exclude,
            history_judge=judge,
        ))
        self.assertEqual(calls, [])
        missing = _history(proposal=near, key="synthetic-missing", forum="synthetic-missing")
        del missing["target"]
        calls.clear()
        rows = list(calibration_rows(
            [missing], (2024, 50), excerpt_field="excerpts", exclude_text=exclude, history_judge=judge,
        ))
        self.assertEqual(rows, [])
        self.assertEqual(calls, [])
        with self.assertRaises(HistoryJudgeError):
            eligible_history(
                _history(proposal=near, key="synthetic-open", forum="synthetic-open"),
                (2024, 50), exclude_text=exclude,
            )

    def test_prioritize_cli_drops_out_of_window_near_copies_without_a_history_call(self):
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
        dropped = [
            _history(year=2024, key="synthetic-year", forum="synthetic-year", target=4, proposal={"problem": NEAR}),
            _history(
                revision_ms=60, key="synthetic-revision", forum="synthetic-revision",
                target=4, proposal={"problem": NEAR},
            ),
        ]
        missing = _history(key="synthetic-missing", forum="synthetic-missing", proposal={"problem": NEAR})
        del missing["target"]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with TemporaryDirectory(prefix="apr-round9-history-") as folder:
            root = Path(folder)
            log = root / "calls.log"
            binary = root / "bin" / "claude"
            binary.parent.mkdir()
            binary.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
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
                "open(LOG, 'a', encoding='utf-8').write(json.dumps({'kind': kind, 'history': 'self_history' in prompt}) + '\\n')\n"
                "if kind == 'history':\n"
                "    sys.stdout.write(json.dumps({'verdict': 'clean'}))\n"
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
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
            env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
            write(root / "proposal.json", {"problem": TARGET})
            write(root / "bank.json", [*peers, *dropped, missing])
            write(root / "profile.json", {
                "ranker": {"method": "mean", "names": ["anchored"]},
                "prompts": {"match": digest(priority.PROMPTS["match"])},
            })
            output = root / "out.json"
            completed = subprocess.run(
                [
                    sys.executable, "-m", "autopaperreview", "proposal-review", "prioritize",
                    str(root / "proposal.json"), "--profile", str(root / "profile.json"),
                    "--work", str(root / "work"), "--output", str(output),
                    "--target-year", "2024", "--cutoff-ms", "50",
                ],
                cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=60,
            )
            rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual([row for row in rows if row["kind"] == "history"], [])
            self.assertTrue(any(row["kind"] == "business" for row in rows))
            self.assertTrue(output.is_file())


class CacheDigestRound9Tests(TestCase):
    def _entry(self, source, request):
        output = {section: {"text": None, "evidence": []} for section in SECTIONS}
        output["problem"] = {"text": PLAN, "evidence": ["s1"]}
        raw = json.dumps(output)
        return {
            "request": request,
            "output": output,
            "reply": raw,
            "reply_sha256": payload_sha256(raw),
            "attempts": 1,
            "latency_sec": 0.1,
        }

    def test_a_rewritten_pair_under_the_old_filename_is_stale(self):
        source = {"s1": PLAN}
        request = {
            "stage": "compress",
            "input_sha256": payload_sha256(serialize_payload(source)),
            "system_sha256": digest(stages.PROMPTS["compress"]),
            "model": "stub-model",
            "command_sha256": "a" * 64,
            "reply_protocol": 1,
        }
        with TemporaryDirectory(prefix="apr-round9-cache-") as folder:
            root = Path(folder)
            bundle = root / "inputs" / "bundle"
            bundle.mkdir(parents=True)
            write(bundle / "candidate.json", source)
            case = {"key": "synthetic", "bundle": "bundle"}
            old_name = digest(request) + ".json"
            write(root / "cache" / "compress" / old_name, self._entry(source, request))
            pointer = root / "runs" / "synthetic" / "compress.json"
            write(pointer, {
                "bundle": "bundle",
                "cache": f"cache/compress/{old_name}",
                "model": "stub-model",
                "command_sha256": "a" * 64,
            })
            self.assertEqual(runner.stage_result(root, case, "compress")["attempts"], 1)
            rewrites = (
                {"model": "other-model"},
                {"command_sha256": "b" * 64},
                {"model": "other-model", "command_sha256": "c" * 64},
                {"model": "paired-model", "command_sha256": "d" * 64},
            )
            for index, changes in enumerate(rewrites):
                updated = {**request, **changes}
                write(root / "cache" / "compress" / old_name, self._entry(source, updated))
                write(pointer, {
                    "bundle": "bundle",
                    "cache": f"cache/compress/{old_name}",
                    "model": updated["model"],
                    "command_sha256": updated["command_sha256"],
                })
                with self.assertRaisesRegex(ValueError, "stale"):
                    runner.stage_result(root, case, "compress")
                if index < 3:
                    continue
                new_name = digest(updated) + ".json"
                write(root / "cache" / "compress" / new_name, self._entry(source, updated))
                write(pointer, {
                    "bundle": "bundle",
                    "cache": f"cache/compress/{new_name}",
                    "model": updated["model"],
                    "command_sha256": updated["command_sha256"],
                })
                self.assertEqual(runner.stage_result(root, case, "compress")["request"]["model"], "paired-model")

    def test_a_legacy_array_does_not_crash_the_cache_fallback(self):
        judge = lambda _body: {"verdict": "clean"}
        cases = (
            (claim_review, "forecast", {"c1": PLAN}, {"c1": [50, 30, 1]}),
            (panel, "forecast", {"s1": PLAN}, [{"n": 20, "grade": 3, "pro": None, "con": None}]),
        )
        for module, stage, body, payload in cases:
            wrapped = json.dumps({
                "type": "result", "subtype": "success", "is_error": False, "result": payload,
            })
            with TemporaryDirectory(prefix="apr-round9-array-") as folder:
                root = Path(folder)
                with patch.object(module, "run_command", return_value=(wrapped, 0.01)):
                    key, _first = module.request(stage, body, root, False, leak_judge=judge)
                legacy = root / stage / f"{key}.json"
                legacy.parent.mkdir(parents=True)
                legacy.write_text("[1, 2, 3]\n", encoding="utf-8")
                new_path = root / ("claims" if module is claim_review else "panel") / stage / f"{key}.json"
                new_path.unlink()
                with patch.object(module, "run_command") as command:
                    with self.assertRaises(ValueError):
                        module.request(stage, body, root, True, leak_judge=judge)
                command.assert_not_called()


class PublishRound9Tests(TestCase):
    def test_twelve_processes_stay_serial_when_the_lock_file_is_removed(self):
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
            "    time.sleep(0.05)\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(f'exit {os.getpid()} {time.time()}\\n')\n"
            "        handle.flush()\n"
            "    return real(self, content, replace=replace)\n"
            "file_io._Output._publish_critical = wrapped\n"
            "file_io.write_text(Path(os.environ['APR_DEST']), os.environ['APR_TEXT'])\n"
        )
        names = [f"P{index:02d}" for index in range(12)]
        for round_index in range(2):
            with TemporaryDirectory(prefix="apr-round9-lock-") as folder:
                root = Path(folder)
                dest = root / "report.json"
                file_io.write_text(dest, "OLD")
                log = root / "critical.log"
                helper = root / "publish.py"
                helper.write_text(script, encoding="utf-8")
                procs = []
                for name in names:
                    env = os.environ.copy()
                    env.update({
                        "APR_SRC": str(ROOT / "src"),
                        "APR_LOG": str(log),
                        "APR_DIR": str(root),
                        "APR_DEST": str(dest),
                        "APR_TEXT": f"{name}-{round_index}",
                        "PYTHONPATH": str(ROOT / "src"),
                    })
                    procs.append(subprocess.Popen(
                        [sys.executable, str(helper)], cwd=root, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    ))
                codes = []
                for proc in procs:
                    _out, err = proc.communicate(timeout=30)
                    codes.append((proc.returncode, err))
                self.assertTrue(all(code == 0 for code, _err in codes), codes)
                open_count = max_open = 0
                for line in log.read_text(encoding="utf-8").splitlines():
                    kind = line.split()[0]
                    open_count += 1 if kind == "enter" else -1
                    max_open = max(max_open, open_count)
                self.assertEqual(max_open, 1)
                self.assertIn(dest.read_text(encoding="utf-8"), {f"{name}-{round_index}" for name in names})
                self.assertFalse(list(root.glob(".apr-output-*.tmp")))
                self.assertFalse((root / ".apr-publish.lock").exists())

    def test_missing_rename_and_cross_device_leave_the_previous_bytes(self):
        with TemporaryDirectory(prefix="apr-round9-rename-") as folder:
            root = Path(folder)
            fresh = root / "fresh.json"
            existing = root / "existing.json"
            file_io.write_text(existing, '{"old": 1}\n')
            before = existing.read_text(encoding="utf-8")
            for name, error in (
                ("enosys", OSError(errno.ENOSYS, "renameat2 is not available")),
                ("exdev", OSError(errno.EXDEV, "Invalid cross-device link")),
            ):
                with self.subTest(name=name):
                    fresh.unlink(missing_ok=True)
                    with patch.object(file_io, "_renameat2", side_effect=error):
                        with self.assertRaises(OSError):
                            file_io.write_text(fresh, "{}\n")
                        with self.assertRaises(OSError):
                            file_io.write_text(existing, '{"new": 1}\n')
                    self.assertFalse(fresh.exists())
                    self.assertEqual(existing.read_text(encoding="utf-8"), before)
                    self.assertFalse(list(root.glob(".apr-output-*.tmp")))
