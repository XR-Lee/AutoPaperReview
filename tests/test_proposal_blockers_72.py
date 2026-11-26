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
from autopaperreview.proposal.leak_audit import REPLY_PROTOCOL, payload_sha256
from autopaperreview.proposal.prior_index import (
    build, canonical_key, development_row, historical_opinions, proposal_body, retrieve, same_document,
)
from autopaperreview.proposal.sanitized import factual_leak, require_model_input, sanitize_proposal, score_field, seal
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
        _command(stdout, stderr), system="system", prompt="prompt", model="synthetic", timeout=30, strict=True,
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
        self.assertIn("strict=True", text)
        for name in ("claim_review.py", "clarification.py", "reviewer_panel.py"):
            module = Path(stages.__file__).with_name(name).read_text(encoding="utf-8")
            self.assertIn("strict=True", module)


class ExplicitFactTests(TestCase):
    def test_explicit_facts_fail_in_body_text_and_nested_keys(self):
        for key in (
            "final_outcome", "submission_status", "rating_comments", "author_names", "email_address",
            "contact_address", "previous_reviewer_average_score",
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
            "The submission was accepted.",
            "Rating: 8\nbecause the problem is specific.",
            "**Rating**: 8",
            "Rating — 8 — a strong reading.",
            "投稿已录取",
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
            "Measure the average score of six planned baskets.",
            "Use a score of 8 units in the planned layout.",
            "the author of the method assumes a bounded noise term",
            "the paper was accepted practice in this subfield",
            "cosine similarity score is 0.2 for the planned embedding",
            "equation 8 defines the planned update",
            "the loss is 0.2 on the synthetic formula",
            "bleu score is 0.2 for the reference sentence",
            "an f1 score of 0.2 is the target of the formula",
            "the objective function score is 0.2",
            "jaccard score is 0.2",
            "the distance score is 0.2",
            "section 4 describes the planned protocol",
            "table 2 lists the planned factors",
            "a probability of 0.2 under the model",
            "the ratio is 8 to 1 in the formula",
            "we will measure accuracy after the study",
            "the method could reach a higher score if the plan works",
            "the acceptance region of the hypothesis test",
            "earlier research reported a similarity of 0.2 and we plan a new test",
            "the paper proposes a violet index",
        )
        for text in allowed:
            with self.subTest(text=text):
                self.assertFalse(factual_leak(text), text)
        self.assertFalse(score_field("problem"))
        self.assertFalse(score_field("contribution"))
        self.assertFalse(score_field("validation_plan"))
        self.assertFalse(score_field("acceptable_error"))
        self.assertFalse(score_field("scoreboard"))
        self.assertFalse(score_field("authority"))

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

    def test_a_quote_must_be_contiguous_in_the_cited_unit(self):
        original = (
            "The catalogue is simple. "
            "The tray trial showed that tray saves time. "
            "The index would help later visitors."
        )
        cleaned = filter_conclusion_sentences(original)
        labels = {"r1": [{"dimension": "novelty", "score": 4, "evidence": ["u1"]}]}
        originals = {"r1": {"u1": original, "u2": cleaned}}
        packet = stages.audit_packet(labels, {"r1": {"u1": cleaned, "u2": cleaned}})
        spans = stages.audit_spans(originals, packet, labels)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            stages.validate("audit", {"r1.novelty": [cleaned]}, packet, spans=spans)
        stages.validate("audit", {"r1.novelty": ["The catalogue is simple."]}, packet, spans=spans)
        cited_second = {"r1": [{"dimension": "novelty", "score": 4, "evidence": ["u2"]}]}
        second = stages.audit_packet(cited_second, {"r1": {"u2": cleaned}})
        second_spans = stages.audit_spans(originals, second, cited_second)
        stages.validate("audit", {"r1.novelty": [cleaned]}, second, spans=second_spans)


class ReceiptAndReplyTests(TestCase):
    def test_an_older_leak_receipt_is_not_replayed(self):
        self.assertGreaterEqual(REPLY_PROTOCOL, 4)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            prompt, system, command, model = "synthetic prompt", "synthetic system", ["synthetic"], "synthetic"
            old = {
                "stage": "leak_audit",
                "system_sha256": digest(system),
                "input_sha256": payload_sha256(prompt),
                "model": model,
                "command_sha256": digest(command),
                "reply_protocol": REPLY_PROTOCOL - 1,
            }
            path = root / "leak_audit" / (digest(old) + ".json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "request": old,
                "output": {"verdict": "clean"},
                "reply": CLEAN,
                "reply_sha256": payload_sha256(CLEAN),
            }), encoding="utf-8")
            calls = []

            def transport(*_args, **_kwargs):
                calls.append(1)
                return CLEAN, 0

            with patch.object(stages, "run_command", side_effect=transport):
                result = stages._invoke_leak_judge(
                    prompt, system, root, model, command, 30, cached_only=False,
                )
            self.assertEqual(result, {"verdict": "clean"})
            self.assertEqual(calls, [1])
            calls.clear()
            with patch.object(stages, "run_command", side_effect=transport):
                again = stages._invoke_leak_judge(
                    prompt, system, root, model, command, 30, cached_only=False,
                )
            self.assertEqual(again, {"verdict": "clean"})
            self.assertEqual(calls, [])

    def test_a_cached_reply_is_parsed_again(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            prompt, system, command, model = "synthetic prompt", "synthetic system", ["synthetic"], "synthetic"

            def transport(*_args, **_kwargs):
                return CLEAN, 0

            with patch.object(stages, "run_command", side_effect=transport):
                stages._invoke_leak_judge(prompt, system, root, model, command, 30, cached_only=False)
            receipt = next((root / "leak_audit").glob("*.json"))
            stored = json.loads(receipt.read_text(encoding="utf-8"))
            stored["reply"] = '{"verdict":"clean","verdict":"leak"}'
            stored["reply_sha256"] = payload_sha256(stored["reply"])
            receipt.write_text(json.dumps(stored), encoding="utf-8")
            with patch.object(stages, "run_command", side_effect=AssertionError):
                with self.assertRaisesRegex(ValueError, "duplicate JSON key|invalid leak"):
                    stages._invoke_leak_judge(prompt, system, root, model, command, 30, cached_only=False)

    def test_duplicate_business_keys_fail_before_merge(self):
        packet = {"problem": PLAN}
        duplicated = (
            '{"importance":{"score":3,"rationale":"The plan is specific.","basis":["problem"]},'
            '"novelty":{"score":3,"rationale":"The plan is specific.","basis":["problem"]},'
            '"potential_value":{"score":3,"rationale":"The plan is specific.","basis":["problem"]},'
            '"community_impact":{"score":3,"rationale":"The plan is specific.","basis":["problem"]},'
            '"importance":{"score":1,"rationale":"A different claim.","basis":["problem"]}}'
        )
        inner = json.dumps({
            key: {"score": 3, "rationale": "The plan is specific.", "basis": ["problem"]}
            for key in stages.RUBRIC
        })
        cases = (
            duplicated,
            '{"type":"result","result":' + json.dumps(inner) + ',"type":"result"}',
            json.dumps({"type": "result", "result": duplicated}),
        )
        for raw in cases:
            with self.subTest(raw=raw[:48]):
                with TemporaryDirectory() as folder:
                    root = Path(folder)

                    def transport(*_args, **_kwargs):
                        return raw, 0

                    with patch.object(stages, "run_command", side_effect=transport):
                        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                            stages.invoke(
                                "review", seal(packet), root / "cache", "synthetic", ["synthetic"], 30,
                                leak_judge=lambda _body: {"verdict": "clean"},
                            )


class NormalizationTests(TestCase):
    def test_whitespace_and_unicode_keys_use_one_normalizer(self):
        self.assertEqual(canonical_key(" synthetic-target \t"), canonical_key("synthetic-target"))
        self.assertEqual(canonical_key("cafe\u0301"), canonical_key("caf\u00e9"))
        with TemporaryDirectory() as folder:
            root = Path(folder)
            spaced = "synthetic-target "
            rows = [_history(key=spaced, title="Target violet basket index", forum="synthetic-self")]
            rows.append(_history())
            rows.append(_history(key="synthetic-third", title="Third violet basket index", forum="synthetic-third"))
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
            for exclude in (spaced, canonical_key(spaced)):
                hits = retrieve(
                    database, proposal, exclude_key=exclude, limit=4,
                    target_year=2024, cutoff_ms=50, identity=proposal,
                )
                self.assertNotIn("Target violet basket index", [hit["title"] for hit in hits])
        composed = "A caf\u00e9 index for violet baskets."
        decomposed = "A cafe\u0301 index for violet baskets."
        self.assertTrue(same_document({"problem": decomposed}, "", composed))
        self.assertTrue(same_document(
            {"m": "gamma beta", "a": "alpha delta"},
            "alpha beta",
            "gamma delta",
        ))

    def test_nested_opinion_metadata_participates_in_eligibility(self):
        kept = "The labels could help later visitors."
        opinions = [
            {"opinion": kept, "opinion_ms": 5},
            {"opinion": "Hidden test partition.", "opinion_ms": 5, "metadata": {"partition": "test"}},
            {"opinion": "Late nested snapshot.", "opinion_ms": 5, "metadata": {"snapshot_ms": 80}},
            {"opinion": "Nested development stays.", "opinion_ms": 5, "metadata": {"split": "dev"}},
            {"opinion": "A non-text split is not eligible.", "opinion_ms": 5, "metadata": {"split": 1}},
            {"opinion": "Top-level test stays out.", "split": "test", "opinion_ms": 5},
        ]
        self.assertEqual(
            historical_opinions(opinions, 50),
            [kept, "Nested development stays."],
        )

    def test_screened_prior_findings_can_reach_forward_scoring(self):
        finding = (
            "Earlier experiments showed that the violet index outperformed the baseline. "
            "We plan to test a new label."
        )
        self.assertTrue(reported_result(finding))
        packet = {"problem": finding}
        profile = {
            "ranker": {"method": "mean", "names": ["novelty"]},
            "prompts": {"merit": digest(priority.PROMPTS["merit"])},
        }
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(stages, "run_command") as send:
                with self.assertRaisesRegex(ValueError, "remove reported outcomes"):
                    priority.rank(
                        packet, root, profile, root, year=2024, cutoff_ms=50,
                        leak_judge=lambda _body: {"verdict": "clean"},
                    )
            send.assert_not_called()
            receipt = {"cleared_packet": digest(sanitize_proposal(packet))}
            calls = []

            def transport(*_args, **_kwargs):
                calls.append(1)
                return json.dumps({
                    "dimensions": {
                        axis: {"score": 3, "why": "The plan names a concrete comparison."}
                        for axis in merit.AXES
                    },
                    "panel": [[0.2, 0.2, 0.2, 0.2, 0.2] for _ in range(3)],
                    "strength": "The comparison is specific.",
                    "objection": "The label set is narrow.",
                }), 0

            with patch.object(stages, "run_command", side_effect=transport):
                result = priority.rank(
                    packet, root, profile, root, year=2024, cutoff_ms=50,
                    leak_judge=lambda _body: {"verdict": "clean"},
                    screen_receipt=receipt,
                )
            self.assertEqual(calls, [1])
            self.assertIn("novelty", result["features"])

    def test_duplicate_note_keys_fail_closed(self):
        from autopaperreview.proposal.data import archived_notes

        with TemporaryDirectory() as folder:
            path = Path(folder) / "notes.json"
            path.write_text(
                '{"notes":[{"id":"synthetic","id":"other","tmdate":1}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                list(archived_notes(path))
            path.write_text('{"notes":[{"id":"synthetic","tmdate":1}]}', encoding="utf-8")
            self.assertEqual(list(archived_notes(path)), [{"id": "synthetic", "tmdate": 1}])
