"""Exercise the proposal CLI's model boundary with synthetic packets and stub transports."""

import io
import json
from hashlib import sha256
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal import (
    claim_review, clarification, clarification_protocol, cli, evaluation, fixed_benchmark, merit,
    pair_compare, priority, reference_coding, reviewer_panel, stages,
)
from autopaperreview.proposal.data import digest, reported_result, write
from autopaperreview.proposal.leak_audit import (
    OUTCOME_SCREEN_PROMPT, PROMPT, REFERENCE_PROMPT, apply_leak_audit,
)
from autopaperreview.proposal.sanitized import require_model_input, sanitize_proposal

PLAN = "Could violet tags index archival baskets by location?"
SIGNAL = "The invitation to the final program arrived at dawn."
CLEAN = '{"verdict":"clean"}'
LEAK = '{"verdict":"leak"}'
RESULT_LEAK = '{"verdict":"reported_result"}'


class ProposalLeakBoundaryTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = self.document("profile.json", {"ranker": {"names": ["novelty"]}})
        self.output = self.root / "result.json"

    def document(self, filename, value):
        path = self.root / filename
        write(path, value)
        return path

    def cli(self, *args, expected=2):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["proposal-review", *map(str, args)]), expected)

    def rank_args(self, source, *, predict=False):
        base = ["--profile", self.profile, "--work", self.root / "work", "--output", self.output]
        if predict:
            ids = [f"synthetic-{i}" for i in range(100)]
            manifest = self.document("cohort.json", {
                "cases": [{"forum": key, "split": "test"} for key in ids],
                "ids_sha256": digest(sorted(ids)),
            })
            rows = self.document("blind.json", [{"forum": key, "proposal": source} for key in ids])
            return ["predict-rank", "--manifest", manifest, "--proposals", rows, "--jobs", "1", *base]
        return ["prioritize", source, *base]

    def reference_args(self, *, independent=False):
        path = self.document("reviewers.json", [{
            "forum": "synthetic-forum",
            "reviews": [
                {"id": "synthetic-note-one", "units": {"u1": "The planned index gives useful catalogue access."}},
                {"id": "synthetic-note-two", "units": {"u1": "The groups would be difficult to compare."}},
            ],
        }])
        options = ["--include-independent-grades"] if independent else []
        return ["code-idea-references", "--inputs", path, "--work", self.root / "refs",
                "--output", self.output, "--jobs", "1", *options]

    def audited_row(self, *, source_text=None):
        plan = {"problem": PLAN, "approach": "Compare a manual log with the planned violet tags."}
        source = {"u1": source_text or "The violet tags would allow visitors to locate baskets."}
        return {
            "id": "synthetic-candidate", "proposal": plan, "overall": 3,
            "outcome_screen": {"proposal": {}},
            "source_audit": {
                "source_units": source, "source_sha256": digest(source),
                "proposal_sha256": digest(plan),
            },
        }

    def auxiliary_args(self, name, row):
        args = [
            name, self.document("candidate.json", [row]), "--work", self.root / name,
            "--output", self.output,
        ]
        if name == "claims":
            args.extend(["--model", self.document("claims-profile.json", {"simple_event": {"coef": [[0, 0]] * 14}})])
        return args

    def test_score_rejects_malformed_judge_transport_and_nonexact_verdict(self):
        source = self.root / "plan.txt"
        source.write_text(PLAN, encoding="utf-8")
        invalid = [
            json.dumps({"type": "result", "stop_reason": "refusal", "result": CLEAN}),
            json.dumps({"type": "result", "status": "refused", "result": CLEAN}),
            json.dumps({"type": "result", "subtype": "error", "result": CLEAN}),
            json.dumps({"type": "result", "is_error": True, "result": CLEAN}),
            json.dumps({"type": "result", "terminal_reason": "aborted_streaming", "result": CLEAN}),
            json.dumps({"type": "result", "safety_stops": 1, "result": CLEAN}),
            json.dumps({"type": "result", "permission_denials": ["refusal"], "result": CLEAN}),
            json.dumps({"type": "result", "usage": {"content": "refusal"}, "result": CLEAN}),
            json.dumps({"type": "result", "usage": {"input_tokens": "refusal"}, "result": CLEAN}),
            json.dumps({"type": "result", "structured_output": {"verdict": "leak"}, "result": CLEAN}),
            json.dumps({"type": "result", "result": "Sorry, " + CLEAN}),
            '{"type":"result","stop_reason":"refusal","stop_reason":"stop","result":' + json.dumps(CLEAN) + '}',
            "I cannot comply. A hypothetical reply would be " + CLEAN,
            LEAK + "\n" + CLEAN,
            '{"verdict":"leak","verdict":"clean"}',
            '{"verdict":"CLEAN"}', '{"verdict":" clean "}', '{"verdict":true}',
            '{"verdict":"clean","refusal":"unavailable"}',
            '"clean"', "[" + CLEAN + "]", CLEAN + " trailing text", "",
        ]
        invalid.extend(json.dumps({"type": "result", "result": CLEAN, field: blocked}) for field, blocked in (
            ("content", "I cannot comply."), ("message", {"refusal": "unavailable"}),
            ("errors", ["request refused"]), ("refusal", None), ("refusal", False),
            ("additional_text", "I cannot comply."),
        ))
        for raw in invalid:
            with self.subTest(raw=raw), patch.object(stages, "run_command", return_value=(raw, 0)) as transport:
                self.cli("score", source, "--work", self.root / "score")
                self.assertEqual([c.kwargs["system"] for c in transport.call_args_list], [PROMPT])

    def test_score_accepts_full_clean_object_and_compressor_export(self):
        fields = {k: {"text": None, "evidence": []} for k in stages.SECTIONS}
        fields["problem"] = {"text": PLAN, "evidence": ["s1"]}
        source = self.document("plan.json", {"proposal": fields})
        fence = chr(96) * 3
        for i, raw in enumerate((CLEAN, json.dumps({
            "type": "result", "subtype": "success", "stop_reason": "end_turn",
            "is_error": False, "result": f"{fence}json\n{CLEAN}\n{fence}",
            "terminal_reason": "completed", "safety_stops": 0, "permission_denials": [],
            "duration_ms": 120, "uuid": "00000000-0000-0000-0000-000000000001",
            "usage": {"input_tokens": 4, "cache_creation": {"ephemeral_5m_input_tokens": 0}},
            "modelUsage": {"synthetic-model": {"inputTokens": 4, "costUSD": 0}},
        }))):
            seen = []

            def transport(_command, *, system, prompt, **_options):
                seen.append((system, json.loads(prompt)))
                if system == PROMPT:
                    return raw, 0
                return json.dumps({key: {"score": None, "rationale": "No evidence.", "basis": []}
                                   for key in stages.RUBRIC}), 0

            with self.subTest(i=i), patch.object(stages, "run_command", side_effect=transport):
                self.cli("score", source, "--work", self.root / f"score-{i}", expected=0)
            self.assertEqual([s for s, _ in seen], [PROMPT, stages.JUDGE])
            self.assertEqual(seen[0][1], seen[1][1])
            self.assertEqual(seen[0][1]["problem"], PLAN)

    def test_default_score_and_prioritize_reject_measured_results_but_allow_plans(self):
        plans = (
            "We will measure whether the tags reach 91% accuracy.",
            "A planned test compares whether the tags outperform a manual log.",
            "将会测量检索速度，并计划比较准确率是否达到 91%。",
        )
        reports = (
            "The violet tags made the boxes easier to find during the trial.",
            "Our search accuracy was 91%.",
            "The violet tags outperformed the control slips.",
            "The storage trays were 3x faster to locate with the violet tags.",
            "试验显示紫色标记缩短了清点时间。",
        )
        source = self.root / "plan.txt"
        self.profile = self.document("valid-profile.json", {
            "ranker": {"method": "weighted", "names": ["novelty"], "weights": [1]},
            "prompts": {"merit": digest(stages.PROMPTS["merit"])},
        })
        for text in reports:
            self.assertTrue(reported_result(text))
            source.write_text(text, encoding="utf-8")
            with self.subTest(report=text), patch.object(stages, "run_command") as send:
                self.cli("score", source, "--work", self.root / "result-score")
                self.cli(*self.rank_args(source))
                send.assert_not_called()

        for text in plans:
            self.assertFalse(reported_result(text))
            source.write_text(text, encoding="utf-8")
            sent = []

            def transport(_command, *, system, prompt, **_options):
                sent.append((system, prompt))
                if system == PROMPT:
                    return CLEAN, 0
                if system == stages.JUDGE:
                    result = {k: {"score": None, "rationale": "No evidence.", "basis": []} for k in stages.RUBRIC}
                else:
                    self.assertEqual(system, merit.MERIT)
                    result = {
                        "dimensions": {k: {"score": 3, "why": "A planned comparison."} for k in merit.AXES},
                        "panel": [[0, 0, 1, 0, 0]] * 3, "strength": "Specific scope.", "objection": "Unclear sampling.",
                    }
                return json.dumps(result), 0

            with self.subTest(plan=text), patch.object(stages, "run_command", side_effect=transport):
                self.cli("score", source, "--work", self.root / "plan-score", expected=0)
                self.cli(*self.rank_args(source), expected=0)
            self.assertEqual([system for system, _ in sent], [PROMPT, stages.JUDGE, PROMPT, merit.MERIT])
            self.assertEqual({json.loads(payload)["proposal"] for _, payload in sent}, {text})

    def test_completed_result_verdict_blocks_indirect_draft_before_default_scoring(self):
        draft = "Once the baskets wore violet labels, the queue was gone before the kettle boiled."
        self.assertFalse(reported_result(draft))
        source = self.root / "draft.txt"
        source.write_text(draft, encoding="utf-8")
        for command in (("score", source, "--work", self.root / "score"), self.rank_args(source)):
            with self.subTest(command=command[0]), patch.object(stages, "run_command", return_value=(RESULT_LEAK, 0)) as send:
                self.cli(*command)
                send.assert_called_once()
                self.assertEqual(send.call_args.kwargs["system"], PROMPT)
                self.assertIn('Use "reported_result" for completed experiments', PROMPT)

    def test_legacy_references_remove_results_before_label_and_audit(self):
        root = self.root / "legacy"
        case = {"bundle": "bundle", "key": "synthetic-case", "split": "dev"}
        write(root / "manifest.json", {"cases": [case]})
        bundle = root / "inputs" / case["bundle"]
        write(bundle / "candidate.json", {"s1": PLAN})
        write(bundle / "provenance.json", {
            "forum": "synthetic-paper", "review_ids": {"r1": "synthetic-review"}, "pre_review": True,
        })
        report = "The violet tags made the boxes easier to find during the trial."
        opinion = "The proposed index offers a distinct way to trace lost baskets."
        write(bundle / "comments.json", {"r1": {"u1": report, "u2": opinion}})
        sent = []

        def transport(_command, *, system, prompt, **_options):
            payload = json.loads(prompt)
            sent.append((system, payload))
            self.assertNotIn(report, prompt)
            if system in {PROMPT, REFERENCE_PROMPT}:
                return CLEAN, 0
            if system == stages.COMPRESS:
                result = {key: {"text": None, "evidence": []} for key in stages.SECTIONS}
                result["problem"] = {"text": PLAN, "evidence": ["s1"]}
            elif system == stages.JUDGE:
                result = {key: {"score": None, "rationale": "No evidence.", "basis": []} for key in stages.RUBRIC}
            elif system == stages.LABEL:
                self.assertEqual(payload, {"r1": {"u2": opinion}})
                result = {"r1": [{"dimension": "novelty", "score": 4, "evidence": ["u2"]}]}
            else:
                self.assertEqual(system, stages.AUDIT)
                self.assertEqual(payload["r1.novelty"]["units"], [opinion])
                result = {"r1.novelty": [opinion]}
            return json.dumps(result), 0

        with patch.object(stages, "run_command", side_effect=transport):
            self.cli("run", "--stage", "ideas", "--work", root, "--jobs", 1, expected=0)
            self.cli("run", "--stage", "references", "--work", root, "--jobs", 1, expected=0)
        self.assertEqual([system for system, _ in sent[-4:]], [REFERENCE_PROMPT, stages.LABEL, REFERENCE_PROMPT, stages.AUDIT])
        self.assertEqual(sent[-4][1], sent[-3][1])
        self.assertEqual(sent[-2][1], sent[-1][1])
        self.assertEqual(evaluation.collect(root, "dev")[0]["labels"]["novelty"][0]["quotes"], [opinion])

    def test_legacy_references_stop_an_indirect_completed_result_before_label(self):
        root = self.root / "legacy-indirect"
        write(root / "manifest.json", {"cases": [{"bundle": "bundle", "key": "synthetic-case", "split": "dev"}]})
        report = "Once the baskets wore violet labels, the queue was gone before the kettle boiled."
        self.assertFalse(reported_result(report))
        write(root / "inputs" / "bundle" / "comments.json", {"r1": {"u1": report}})
        with patch.object(stages, "run_command", return_value=(RESULT_LEAK, 0)) as send:
            self.cli("run", "--stage", "references", "--work", root, "--jobs", 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["system"], REFERENCE_PROMPT)
        self.assertEqual(json.loads(send.call_args.kwargs["prompt"]), {"r1": {"u1": report}})

    def test_legacy_audit_rejects_a_partial_quote_from_a_completed_result_unit(self):
        unit = "The catalogue is simple, and the tray trial showed that it saves time."
        source = {"r1.novelty": {"dimension": "novelty", "score": 4, "units": [unit]}}
        with self.assertRaisesRegex(ValueError, "drop measured outcomes"):
            stages.validate("audit", {"r1.novelty": ["The catalogue is simple"]}, source)
            self.assertNotIn("evidence", str(seen[0][1]))

    def test_tampered_judge_receipts_block_new_stage_and_cannot_be_reused(self):
        source = self.root / "plan.txt"
        source.write_text(PLAN, encoding="utf-8")
        score = json.dumps({key: {"score": None, "rationale": "No evidence.", "basis": []} for key in stages.RUBRIC})
        for name, first, reason in (
            ("duplicate", CLEAN, "duplicate JSON key"),
            ("verdict", LEAK, "cached verdict mismatch"),
            ("reply-and-verdict", LEAK, "invalid cached reply digest"),
        ):
            with self.subTest(name=name):
                work = self.root / name
                with patch.object(stages, "run_command", side_effect=[(first, 0), (score, 0)]):
                    self.cli("score", source, "--work", work, expected=0 if first == CLEAN else 2)
                receipt, = (work / "cache" / "leak_audit").glob("*.json")
                original = receipt.read_text(encoding="utf-8")
                if name == "duplicate":
                    altered = original.replace('"verdict": "clean"', '"verdict": "leak", "verdict": "clean"')
                else:
                    altered = original.replace('"verdict": "leak"', '"verdict": "clean"')
                    if name == "reply-and-verdict":
                        altered = altered.replace(r'\"leak\"', r'\"clean\"')
                self.assertNotEqual(altered, original)
                receipt.write_text(altered, encoding="utf-8")
                for cached_only in (False, True):
                    judge = cli.model_leak_judge(work / "cache", "claude-opus-5-5", stages.claude_argv(), 240,
                                                 cached_only=cached_only)
                    with (patch.object(stages, "run_command") as transport,
                          self.assertRaisesRegex(ValueError, "leak audit blocked") as error):
                        stages.invoke("forecast", sanitize_proposal(PLAN), work / "cache", "synthetic", ["stub"], 1,
                                      leak_judge=judge, cached_only=cached_only)
                    self.assertEqual(str(error.exception.__cause__), reason)
                    transport.assert_not_called()

    def test_reordered_score_packet_gets_a_new_exact_byte_audit(self):
        source = self.root / "plan.json"
        work = self.root / "ordered"
        fields = {key: None for key in stages.SECTIONS}
        fields.update(problem=PLAN, approach="Index the café catalogue by location.")
        sent = []

        def transport(_argv, *, system, prompt, **_options):
            sent.append((system, prompt.encode("utf-8")))
            if system == PROMPT:
                return CLEAN, 0
            key = "score" if system == stages.JUDGE else "panel"
            row = {"rationale": "No evidence.", "basis": [], key: None}
            return json.dumps(dict.fromkeys(stages.RUBRIC, row)), 0

        with patch.object(stages, "run_command", side_effect=transport):
            args = ["score", source, "--work", work]
            source.write_text(json.dumps(fields), encoding="utf-8")
            self.cli(*args, expected=0)
            self.cli(*args, expected=0)
            source.write_text(json.dumps(dict(reversed(list(fields.items())))), encoding="utf-8")
            self.cli(*args, "--reviewer", "forecast", expected=0)
        self.assertEqual([system for system, _ in sent], [PROMPT, stages.JUDGE, PROMPT, stages.FORECAST])
        first, second = sent[0][1], sent[2][1]
        self.assertEqual(first, sent[1][1])
        self.assertEqual(second, sent[3][1])
        self.assertNotEqual(first, second)
        receipts = (work / "cache" / "leak_audit").glob("*.json")
        hashes = {json.loads(path.read_text(encoding="utf-8"))["request"]["input_sha256"] for path in receipts}
        self.assertEqual(hashes, {sha256(payload).hexdigest() for payload in (first, second)})

    def test_missing_judge_factories_stop_each_cli_entry_and_run_stage(self):
        source = self.document("plan.json", {"proposal": PLAN})
        score = self.root / "plan.txt"
        score.write_text(PLAN, encoding="utf-8")
        cases = [
            (cli, ["score", score, "--work", self.root / "score"]),
            (priority, self.rank_args(source)),
            (fixed_benchmark, self.rank_args(PLAN, predict=True)),
            (reference_coding, self.reference_args()),
            (claim_review, self.auxiliary_args("claims", self.audited_row())),
            (reviewer_panel, self.auxiliary_args("panel", self.audited_row())),
            (clarification, self.auxiliary_args("clarify", self.audited_row())),
            (pair_compare, [
                "compare", self.document("compare.json", [{"id": "toy-a"}, {"id": "toy-b"}]),
                "--left", "toy-a", "--right", "toy-b", "--model", self.root / "local.zip",
                "--work", self.root / "pair",
            ]),
        ]
        cases.extend((cli, ["run", "--stage", stage, "--work", self.root / "run"]) for stage in
                     ("ideas", "compress", "review", "label", "audit", "references", "forecast"))
        for module, args in cases:
            for unavailable in (None, False, ""):
                with (self.subTest(action=args[0], missing=unavailable),
                      patch.object(module, "model_leak_judge", return_value=unavailable),
                      patch.object(stages, "run_command") as command):
                    self.cli(*args)
                    command.assert_not_called()
        for value in ("clean", {"verdict": " clean"}, {"verdict": "UNSURE"}, {"verdict": "clean", "error": True}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "blocked"):
                apply_leak_audit(sanitize_proposal(PLAN), lambda _: value)

    def test_screen_judge_precedes_prioritize_predict_and_apd_transports(self):
        # The empirical wording selects APD's screening route. The separate
        # program invitation can only be understood by the semantic judge.
        apd = self.document("apd.json", {"candidates": [{
            "id": "synthetic-candidate",
            "contribution": f"We observed a gain when we outperform the baseline. {SIGNAL}",
        }]})
        source = self.document("plan.json", {"proposal": f"{PLAN} {SIGNAL}"})
        for i, args in enumerate((self.rank_args(source), self.rank_args(f"{PLAN} {SIGNAL}", predict=True), self.rank_args(apd))):
            args[args.index("--work") + 1] = self.root / f"screen-{i}"
            seen = []

            def transport(_argv, *, system, prompt, **_options):
                self.assertEqual(system, OUTCOME_SCREEN_PROMPT)
                self.assertIn(SIGNAL, prompt)
                seen.append(json.loads(prompt))
                return LEAK, 0

            with self.subTest(action=args[0]), patch.object(stages, "run_command", side_effect=transport):
                self.cli(*args, "--screen-outcomes")
            self.assertTrue(seen)
            self.assertTrue(all("u1" in body for body in seen))

    def test_source_evidence_reaudits_combined_source_and_proposal(self):
        source = self.document("paired.json", {"source": {"problem": SIGNAL}, "proposal": {"problem": PLAN}})
        seen = []

        def transport(_argv, *, system, prompt, **_options):
            payload = json.loads(prompt)
            seen.append((system, payload))
            if system in {PROMPT, OUTCOME_SCREEN_PROMPT}:
                if set(payload) == {"source", "claims"}:
                    self.assertIn(SIGNAL, str(payload["source"]))
                    self.assertIn(PLAN, str(payload["claims"]))
                    return LEAK, 0
                return CLEAN, 0
            self.assertEqual(system, stages.PROMPTS["outcome_screen"])
            return '{"exclude":[]}', 0

        with patch.object(stages, "run_command", side_effect=transport):
            self.cli(*self.rank_args(source), "--include-source-audit")
        self.assertEqual(seen[-1][0], PROMPT)
        self.assertTrue(any(SIGNAL in str(body) for system, body in seen[:-1] if system == OUTCOME_SCREEN_PROMPT))

    def test_outcome_screen_judges_attribution_and_context_before_sending(self):
        for i, (text, stage) in enumerate((
            ("Could violet tags be faster than a manual log?", "outcome_attribution"),
            ("A prior catalogue outperforms the manual log.", "outcome_context"),
        )):
            seen = []

            def transport(_argv, *, system, prompt, **_options):
                body = json.loads(prompt)
                seen.append(system)
                if system == OUTCOME_SCREEN_PROMPT:
                    if "context" in body:
                        self.assertEqual(body["context"]["u1"]["text"], text)
                        self.assertEqual(body["candidates"], ["u1"])
                        return LEAK, 0
                    return CLEAN, 0
                self.assertEqual(system, stages.PROMPTS["outcome_screen"])
                return '{"exclude":[]}', 0

            cache = self.root / f"secondary-{i}"
            judge = cli.model_leak_judge(cache / "cache", "synthetic", ["stub"], 1)
            with (self.subTest(stage=stage), patch.object(stages, "run_command", side_effect=transport),
                  self.assertRaisesRegex(ValueError, "leak audit blocked")):
                priority.screen_packet({"proposal": text}, cache, leak_judge=judge)
            self.assertEqual(seen, [OUTCOME_SCREEN_PROMPT, stages.PROMPTS["outcome_screen"], OUTCOME_SCREEN_PROMPT])

    def test_run_compress_judges_exact_candidate_units_before_sending(self):
        root = self.root / "batch"
        write(root / "manifest.json", {"cases": [{"bundle": "bundle", "key": "synthetic-case", "split": "dev"}]})
        units = {"s1": PLAN, "s2": SIGNAL}
        write(root / "inputs" / "bundle" / "candidate.json", units)
        seen = []

        def transport(_argv, *, system, prompt, **_options):
            self.assertEqual(system, PROMPT)
            seen.append(json.loads(prompt))
            return LEAK, 0

        with patch.object(stages, "run_command", side_effect=transport):
            self.cli("run", "--stage", "compress", "--work", root, "--jobs", "1")
        self.assertEqual(seen, [units])

    def test_rank_judges_assembled_learned_slate_contrast_field_peer_and_opinion(self):
        root = self.root
        bank = [{
            "key": f"synthetic-{i}", "split": "dev", "year": 2020, "revision_ms": i + 1,
            "target": 1 + (i % 5), "proposal": {"problem": f"{PLAN} {SIGNAL}"},
            "opinions": [SIGNAL], "opinion_ms": 30,
        } for i in range(15)]
        write(root / "bank.json", bank)
        write(root / "contrast-bank.json", bank)
        write(root / "lessons.json", {"criteria": [SIGNAL], "split": "dev", "year": 2020, "revision_ms": 30})
        write(root / "prior-opinions.json", {"synthetic-paper": [{"opinion": SIGNAL, "opinion_ms": 30}]})
        prior = [{
            "title": "A synthetic archival method", "abstract": SIGNAL,
            "year": 2020, "forum": "synthetic-paper",
        }]
        for feature, marker in (
            ("learned_priority", "calibration_questions"),
            ("relative_priority", "A"), ("pair_priority", "examples"),
            ("field_priority", "earlier_research"), ("peer_priority", "prior_work"),
            ("peer_priority", "r1"),
        ):
            receipts = []

            def judge(body):
                if marker in body:
                    receipts.append(body)
                    self.assertIn(SIGNAL, str(require_model_input(body, stage=stage)))
                    return {"verdict": "leak"}
                return {"verdict": "clean"}

            def transport(_argv, *, system, prompt, **_options):
                self.assertEqual(marker, "r1")
                self.assertEqual(system, stages.PROMPTS["peer"])
                self.assertIn(SIGNAL, prompt)
                return json.dumps({f"r{i}": [SIGNAL] for i in range(1, 5)}), 0

            stage = priority.stage_for_feature(feature)
            profile = {"ranker": {"names": [feature]}, "prompts": {stage: digest(stages.PROMPTS[stage])}}
            with (self.subTest(marker=marker), patch.object(priority, "retrieve", return_value=prior),
                  patch.object(stages, "run_command", side_effect=transport) as calls,
                  self.assertRaisesRegex(ValueError, "leak audit blocked")):
                priority.rank(PLAN, root, profile, root, year=2024, cutoff_ms=50, leak_judge=judge)
            self.assertTrue(receipts)
            self.assertEqual(calls.call_count, int(marker == "r1"))

    def test_reference_cli_judges_extraction_and_each_grading_payload(self):
        for reject in ("extract", "joint", "independent"):
            sent = []

            def transport(_argv, *, system, prompt, **_options):
                data = json.loads(prompt)
                if system == REFERENCE_PROMPT:
                    stage = "extract" if isinstance(data["r1"], dict) else "joint" if len(data) == 2 else "independent"
                    sent.append(("judge", stage))
                    return (LEAK if stage == reject else CLEAN), 0
                if system == merit.EXTRACT:
                    sent.append(("model", "extract"))
                    return json.dumps({key: [{"id": "u1", "quote": row["u1"]}] for key, row in data.items()}), 0
                self.assertEqual(system, merit.GRADE)
                self.assertEqual(len(data), 2)
                sent.append(("model", "joint"))
                return json.dumps({key: {"score": 3, "used": [0]} for key in data}), 0

            with self.subTest(reject=reject), patch.object(stages, "run_command", side_effect=transport):
                args = self.reference_args(independent=True)
                args[args.index("--work") + 1] = self.root / reject
                self.cli(*args)
            self.assertEqual(sent[-1], ("judge", reject))
            self.assertNotIn(("model", reject), sent)
            self.assertEqual(len(sent), {"extract": 1, "joint": 3, "independent": 5}[reject])

    def test_claims_and_panel_source_material_are_gated_by_hard_and_semantic_audits(self):
        for module, action, source_key in ((claim_review, "claims", "sources"), (reviewer_panel, "panel", "sources")):
            for source in ("Contact synthetic.person@invalid.example for details.", SIGNAL):
                handled = []

                def judge(body):
                    self.assertIn(source, str(body[source_key]))
                    handled.append(body)
                    return {"verdict": "unsure"}

                with (self.subTest(action=action, source=source),
                      patch.object(module, "model_leak_judge", return_value=judge),
                      patch.object(module, "run_command") as model):
                    self.cli(*self.auxiliary_args(action, self.audited_row(source_text=source)))
                    model.assert_not_called()
                self.assertEqual(len(handled), int(source == SIGNAL))

    def test_clarify_judges_model_generated_interpretations_before_resolution(self):
        row = self.audited_row()
        question = {
            "question": SIGNAL, "axis": "mechanism", "basis": "s1",
            "yes_means": "Tags mark each basket.", "no_means": "Tags mark each aisle.",
            "p_yes": .5, "yes_grade": 4, "no_grade": 2,
        }
        handled = []

        def judge(body):
            if "interpretations" in body:
                self.assertEqual(body["interpretations"]["question"], SIGNAL)
                handled.append(body)
                return {"verdict": "leak"}
            return {"verdict": "clean"}

        def transport(_argv, *, system, **_options):
            if system == clarification_protocol.QUEST:
                return json.dumps(question), 0
            self.assertEqual(system, clarification_protocol.SCREEN)
            return '{"keep":["s1"]}', 0

        with (patch.object(clarification, "model_leak_judge", return_value=judge),
              patch.object(clarification, "run_command", side_effect=transport) as command):
            self.cli(*self.auxiliary_args("clarify", row))
        self.assertEqual(len(handled), 1)
        self.assertEqual(command.call_count, 2)

    def test_retries_with_added_instructions_are_audited_before_resending(self):
        recorded = []

        def judge(body):
            recorded.append(body)
            return {"verdict": "leak" if "validation_feedback" in body else "clean"}

        with patch.object(stages, "run_command", return_value=("malformed", 0)) as command:
            with self.assertRaisesRegex(ValueError, "leak audit blocked"):
                stages.invoke("review", sanitize_proposal(PLAN), self.root, "synthetic", ["stub"], 1,
                              leak_judge=judge)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(recorded[1]["payload"], recorded[0])
        self.assertTrue(recorded[1]["validation_feedback"])

    def test_model_judge_and_review_retry_receive_identical_bytes(self):
        sent = []

        def transport(_argv, *, system, prompt, **_options):
            sent.append((system, prompt.encode("utf-8")))
            if system == PROMPT:
                return CLEAN, 0
            if len(sent) == 2:
                return "malformed", 0
            return json.dumps({key: {"score": None, "rationale": "No evidence.", "basis": []}
                               for key in stages.RUBRIC}), 0

        with patch.object(stages, "run_command", side_effect=transport):
            judge = cli.model_leak_judge(self.root, "synthetic", ["stub"], 1)
            stages.invoke("review", sanitize_proposal(PLAN), self.root, "synthetic", ["stub"], 1,
                          leak_judge=judge)
        self.assertEqual([system for system, _ in sent], [PROMPT, stages.JUDGE, PROMPT, stages.JUDGE])
        self.assertEqual(sent[0][1], sent[1][1])
        self.assertEqual(sent[2][1], sent[3][1])
        self.assertNotEqual(sent[0][1], sent[2][1])
