"""Structural regressions for the seven blockers tracked in issue 72."""

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.command_transport import CommandTransportError, run_command
from autopaperreview.proposal import evaluation, merit, preference, priority, stages
from autopaperreview.proposal.data import (
    contiguous_quote, digest, filter_conclusion_sentences, read, reported_result, retained_label_text, write,
)
from autopaperreview.proposal.leak_audit import PROMPT, REFERENCE_PROMPT
from autopaperreview.proposal.prior_index import build, development_row, proposal_body, retrieve
from autopaperreview.proposal.sanitized import factual_leak, require_model_input, score_field, seal
from autopaperreview.cli import main


PLAN = "Could violet tags index archival baskets by location?"
CLEAN = '{"verdict":"clean"}'


def _command(stdout, stderr=""):
    script = (
        "import sys\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.stdout.write({stdout!r})\n"
    )
    return [sys.executable, "-c", script]


def _invoke(stdout, stderr=""):
    return run_command(
        _command(stdout, stderr), system="system", prompt="prompt", model="synthetic", timeout=30
    )


class TransportFailClosedTests(TestCase):
    def test_stderr_or_non_json_cannot_become_a_verdict(self):
        refusal = '{"verdict":"leak","stop_reason":"refusal"}'
        with self.assertRaises(CommandTransportError) as caught:
            _invoke(CLEAN, refusal)
        self.assertIn("stderr", str(caught.exception))
        self.assertNotIn("refusal", str(caught.exception))
        self.assertNotIn("leak", str(caught.exception))
        for stdout in (
            "Here is the judgment.\n```json\n" + CLEAN + "\n```\n",
            CLEAN + "\n" + CLEAN,
            "not json",
            "",
        ):
            with self.subTest(stdout=stdout[:24]), self.assertRaisesRegex(CommandTransportError, "JSON|nothing"):
                _invoke(stdout)
        stdout, _latency = _invoke(CLEAN, " \n\t")
        self.assertEqual(json.loads(stdout)["verdict"], "clean")

    def test_every_transport_helper_uses_the_same_command_runner(self):
        text = Path(stages.__file__).read_text(encoding="utf-8")
        self.assertIn("run_command(", text)
        for name in ("claim_review.py", "clarification.py", "reviewer_panel.py"):
            module = Path(stages.__file__).with_name(name).read_text(encoding="utf-8")
            self.assertIn("run_command(", module)


class ExplicitFactTests(TestCase):
    def test_explicit_facts_fail_in_body_text_and_nested_keys(self):
        for key in (
            "final_outcome", "submission_status", "rating_comments", "author_names", "email_address",
        ):
            with self.subTest(key=key):
                self.assertTrue(score_field(key))
                with self.assertRaisesRegex(ValueError, "leak"):
                    seal({"notes": {key: "synthetic"}})
        blocked = (
            "rating of 8 because the idea is clear.",
            'Decision: "Accept"',
            "Decision: (Accept)",
            "评分：8",
            "分数: 8",
            "attachment?id=synthetic",
            "By A. B. Carter",
            "A. B. Smith",
        )
        for text in blocked:
            with self.subTest(text=text):
                self.assertTrue(factual_leak(text), text)
        allowed = (
            "the similarity score is 0.2",
            "the acceptance rate of the planned method",
            "we accept the assumption",
            "the proposal should be accepted",
            "the attachment plan uses violet tags",
            "e.g. Figure labels stay prospective.",
        )
        for text in allowed:
            with self.subTest(text=text):
                self.assertFalse(factual_leak(text), text)
        self.assertFalse(score_field("problem"))
        self.assertFalse(score_field("contribution"))
        self.assertFalse(score_field("validation_plan"))

    def test_compress_blocks_nested_facts_and_reported_results_before_the_model(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "manifest.json", {"cases": [{"bundle": "bundle", "key": "synthetic-case", "split": "dev"}]})
            measured = "Our experiments demonstrate substantial improvements."
            self.assertTrue(reported_result(measured))
            candidates = (
                {"s1": {"note": "A planned comparison.", "final_outcome": "accept"}},
                {"s1": {"note": measured}},
            )
            for candidate in candidates:
                with self.subTest(candidate=sorted(candidate["s1"])):
                    write(root / "inputs" / "bundle" / "candidate.json", candidate)
                    with patch.object(stages, "run_command") as send, redirect_stderr(io.StringIO()):
                        self.assertEqual(
                            main(["proposal-review", "run", "--stage", "compress", "--work", str(root), "--jobs", "1"]),
                            2,
                        )
                    send.assert_not_called()


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
    }
    row.update(changes)
    return row


class RetrievalIsolationTests(TestCase):
    def test_the_index_keeps_keys_and_retrieval_drops_the_target(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            target_key = "synthetic-target"
            self_title = "Target violet basket index"
            self_opinion = "The target baskets could use a second planned label."
            other_opinion = "The other baskets could use a second planned label."
            rows = [_history(
                key=target_key, title=self_title, forum="synthetic-self",
                abstract="violet archive indexing baskets labels for the target shelf.",
            )]
            # Two remaining rows keep the shared term above the frequency floor after exclusion.
            rows.append(_history())
            rows.append(_history(
                key="synthetic-third", title="Third violet basket index", forum="synthetic-third",
                abstract="violet archive indexing baskets labels for a third shelf.",
            ))
            rows.extend(
                _history(
                    key=f"synthetic-filler-{index}",
                    title=f"Filler catalogue {index}",
                    abstract="Unrelated painted symbols and cloth samples.",
                    forum=f"synthetic-filler-{index}",
                )
                for index in range(10)
            )
            corpus = root / "corpus.jsonl"
            corpus.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            database = root / "prior.sqlite"
            build(corpus, database)
            proposal = {"problem": "violet archive indexing baskets labels"}
            hits = retrieve(
                database, proposal, exclude_key=target_key, limit=4,
                target_year=2024, cutoff_ms=50, identity=proposal,
            )
            self.assertTrue(hits)
            self.assertNotIn(self_title, [hit["title"] for hit in hits])
            self.assertIn("Other violet basket index", [hit["title"] for hit in hits])
            opinions = {
                "synthetic-self": [{"opinion": self_opinion, "opinion_ms": 5}],
                "synthetic-other": [{"opinion": other_opinion, "opinion_ms": 5}],
            }
            packet = merit.field_packet(proposal, hits, opinions, cutoff_ms=50, target_year=2024)
            rendered = json.dumps(require_model_input(seal(packet, stage="field"), stage="field"))
            self.assertNotIn(self_opinion, rendered)
            self.assertNotIn(self_title, rendered)
            self.assertIn(other_opinion, rendered)

            copy = _history(
                key="synthetic-copy", title=proposal["problem"], abstract=proposal["problem"],
                forum="synthetic-copy",
            )
            corpus.write_text("\n".join(json.dumps(row) for row in [*rows, copy]), encoding="utf-8")
            database.unlink()
            build(corpus, database)
            copied = retrieve(database, proposal, limit=4, target_year=2024, cutoff_ms=50, identity=proposal)
            self.assertNotIn(proposal["problem"], [hit["title"] for hit in copied])

    def test_match_and_slate_drop_an_anchor_with_the_same_proposal_text(self):
        target = {"problem": "Index violet baskets by planned tags."}
        bank = []
        for index in range(16):
            bank.append({
                "key": f"synthetic-{index}",
                "split": "dev",
                "year": 2020,
                "revision_ms": index + 1,
                "opinion_ms": 5,
                "target": 1 + (index % 5),
                "proposal": {"problem": f"Index cloth sample {index} by planned tags."},
                "excerpts": ["The archival labels could be useful."],
                "opinions": ["The archival labels could be useful."],
            })
        bank.append({
            **bank[0],
            "key": "synthetic-self",
            "target": 5,
            "proposal": dict(target),
        })
        self.assertEqual(proposal_body(target), proposal_body(bank[-1]["proposal"]))
        examples = priority.example_packet(target, bank, target_year=2024, cutoff_ms=50)
        ideas = [row["idea"]["problem"] for row in examples["examples"].values()]
        self.assertNotIn(target["problem"], ideas)
        panel, _scores = preference.panel(target, bank, 0, target_year=2024, cutoff_ms=50)
        bodies = [item.get("problem") for item in panel.values()]
        self.assertEqual(bodies.count(target["problem"]), 1)


class AmbiguousSplitTests(TestCase):
    def test_duplicate_and_nested_split_keys_never_enter_a_model(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            raw = '{"split":"test","year":2020,"revision_ms":1,"target":3,"key":"synthetic","proposal":{"problem":"Index baskets."},"split":"dev"}'
            path = root / "bank.json"
            path.write_text("[" + raw + "]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                read(path)
            nested = '{"outer":{"split":"dev","split":"test"}}'
            (root / "nested.json").write_text(nested, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                read(root / "nested.json")
            line = (
                '{"title":"T","abstract":"violet archive indexing baskets labels",'
                '"year":2020,"revision_ms":1,"forum":"synthetic","available_ms":1,'
                '"split":"test","split":"dev"}\n'
            )
            corpus = root / "rows.jsonl"
            corpus.write_text(line, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                build(corpus, root / "prior.sqlite")
            lessons = {
                "split": "dev",
                "year": 2020,
                "revision_ms": 5,
                "criteria": [{"partition": "test", "text": "Would the labels help visitors?"}],
            }
            self.assertFalse(development_row(lessons))
            write(root / "lessons.json", lessons)
            profile = {
                "ranker": {"method": "mean", "names": ["learned_chair"]},
                "prompts": {"learned": digest(priority.PROMPTS["learned"])},
            }
            with patch.object(stages, "run_command") as send:
                with self.assertRaisesRegex(ValueError, "lack eligible development provenance"):
                    priority.rank(
                        {"problem": PLAN}, root, profile, root, year=2024, cutoff_ms=50,
                        leak_judge=lambda _body: {"verdict": "clean"},
                    )
            send.assert_not_called()


class LegacyEvidenceTests(TestCase):
    def test_a_splice_across_a_deleted_sentence_is_not_a_reference(self):
        original = (
            "The catalogue is simple. "
            "The tray trial showed that it saves time. "
            "The index would help later visitors."
        )
        cleaned = filter_conclusion_sentences(original)
        self.assertNotIn("saves time", cleaned)
        self.assertIn("The catalogue is simple.", cleaned)
        self.assertIn("The index would help later visitors.", cleaned)
        _text, offsets = retained_label_text(original, screen_results=True)
        self.assertIsNone(contiguous_quote(cleaned, offsets, cleaned))
        self.assertIsNotNone(contiguous_quote(cleaned, offsets, "The catalogue is simple."))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "manifest.json", {"cases": [{"bundle": "bundle", "key": "synthetic-case", "split": "dev"}]})
            bundle = root / "inputs" / "bundle"
            write(bundle / "candidate.json", {"s1": PLAN})
            write(bundle / "provenance.json", {
                "forum": "synthetic-paper", "review_ids": {"r1": "synthetic-review"}, "pre_review": True,
            })
            write(bundle / "comments.json", {"r1": {"u1": original}})
            sent = []

            def transport(_command, *, system, prompt, **_options):
                payload = json.loads(prompt)
                sent.append((system, prompt))
                self.assertNotIn("saves time", prompt)
                self.assertNotIn("unit_offsets", prompt)
                if system in {PROMPT, REFERENCE_PROMPT}:
                    return CLEAN, 0
                if system == stages.COMPRESS:
                    result = {key: {"text": None, "evidence": []} for key in stages.SECTIONS}
                    result["problem"] = {"text": PLAN, "evidence": ["s1"]}
                elif system == stages.JUDGE:
                    result = {key: {"score": None, "rationale": "No evidence.", "basis": []} for key in stages.RUBRIC}
                elif system == stages.LABEL:
                    result = {"r1": [{"dimension": "novelty", "score": 4, "evidence": ["u1"]}]}
                else:
                    self.assertEqual(system, stages.AUDIT)
                    self.assertEqual(payload["r1.novelty"]["units"], [cleaned])
                    result = {"r1.novelty": [cleaned]}
                return json.dumps(result), 0

            with patch.object(stages, "run_command", side_effect=transport), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(main(["proposal-review", "run", "--stage", "ideas", "--work", str(root), "--jobs", "1"]), 0)
                self.assertEqual(main(["proposal-review", "run", "--stage", "references", "--work", str(root), "--jobs", "1"]), 2)
            self.assertTrue(any(system == stages.LABEL for system, _prompt in sent))
            self.assertFalse((root / "runs" / "synthetic-case" / "audit.json").exists())

            def accept_one_side(_command, *, system, prompt, **_options):
                if system in {PROMPT, REFERENCE_PROMPT}:
                    return CLEAN, 0
                if system == stages.LABEL:
                    return json.dumps({"r1": [{"dimension": "novelty", "score": 4, "evidence": ["u1"]}]}), 0
                if system == stages.AUDIT:
                    return json.dumps({"r1.novelty": ["The catalogue is simple."]}), 0
                raise AssertionError(system)

            # The failed audit did not leave a reusable splice. A kept sentence still resolves.
            audit_cache = root / "cache" / "audit"
            if audit_cache.exists():
                for path in audit_cache.glob("*.json"):
                    path.unlink()
            with patch.object(stages, "run_command", side_effect=accept_one_side), redirect_stderr(io.StringIO()) as error, redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["proposal-review", "run", "--stage", "references", "--work", str(root), "--jobs", "1"]),
                    0,
                    error.getvalue(),
                )
            quotes = evaluation.collect(root, "dev")[0]["labels"]["novelty"][0]["quotes"]
            self.assertEqual(quotes, ["The catalogue is simple."])
