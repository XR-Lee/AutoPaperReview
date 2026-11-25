import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import clarification, clarification_protocol
from autopaperreview.proposal.data import digest


class ProposalClarificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        source = {"author-a": "The blue tag is chosen before the other tags are drawn."}
        plan = {"problem": "When should a tag be chosen in the proposed game?"}
        self.item = {
            "id": "toy", "proposal": plan, "overall": 3,
            "outcome_screen": {"proposal": {}},
            "source_audit": {"source_sha256": digest(source), "proposal_sha256": digest(plan), "source_units": source},
        }
        self.question = {
            "question": "Is a blue tag chosen before every other tag?",
            "basis": "s1", "axis": "mechanism", "yes_means": "The blue tag is chosen first.",
            "no_means": "The blue tag is chosen last.", "p_yes": .5, "yes_grade": 5, "no_grade": 1,
        }

    def test_source_reader_is_blind_and_verifier_sees_exact_quote(self):
        stages = []
        def fake(stage, data, *args, **kwargs):
            stages.append(stage)
            if stage == "question":
                self.assertEqual(set(data), {"s1"})
                value = self.question
            elif stage == "source-screen":
                value = {"keep": ["s1"]}
            elif stage == "resolve":
                self.assertNotIn("yes_grade", data["interpretations"])
                if data["text"]["s1"].startswith("The blue tag"):
                    value = {"answer": "yes", "unit": "s1", "quote": "chosen before the other tags"}
                else:
                    value = {"answer": "unknown", "unit": "", "quote": ""}
            else:
                self.assertEqual(set(data), {"interpretations", "source_unit", "excerpt"})
                self.assertIn(data["excerpt"], data["source_unit"])
                value = {"answer": "yes"}
            clarification_protocol.validate(stage, value, data)
            return stage, value
        with patch.object(clarification, "judgment", side_effect=fake):
            result = clarification.clarify(self.item, self.cache)
        self.assertEqual(stages, ["question", "source-screen", "resolve", "resolve", "verify"])
        self.assertEqual(result["source"]["evidence"]["original_passage_ids"], ["author-a"])
        self.assertEqual(result["state"], "resolved_from_source")
        self.assertEqual(result["deployed_overall"], 3)
        self.assertEqual(result["experimental_source_score"], 4)

    def test_disagreement_and_missing_evidence_remain_unknown(self):
        meanings = clarification_protocol.interpretations(self.question)
        text = {"s1": "The toy has four unmarked tags."}
        def fake(label, stage, data):
            if stage == "verify":
                return {"answer": "no"}
            return {"answer": "yes", "unit": "s1", "quote": "four unmarked tags"}
        self.assertEqual(clarification.cited_answer(text, meanings, "source", fake), {"answer": "unknown", "evidence": None})
        with self.assertRaises(ValueError):
            clarification_protocol.validate("resolve", {"answer": "yes", "unit": "s1", "quote": "invented details"}, {"text": text})
        with self.assertRaises(ValueError):
            clarification_protocol.validate("question", dict(self.question, p_yes=float("nan")), text)

    def test_missing_inputs_and_cache_guards(self):
        bad = copy.deepcopy(self.item)
        bad["proposal"]["problem"] = "A different toy question."
        with self.assertRaisesRegex(ValueError, "fingerprints"):
            clarification.clarify(bad, self.cache, cached_only=True)
        self.assertEqual(clarification.clarify({"proposal": {}}, self.cache)["state"], "insufficient_prospective_text")
        with self.assertRaisesRegex(ValueError, "missing cached question"):
            clarification.clarify(self.item, self.cache, cached_only=True)

    def test_single_object_and_empty_batch_preserve_input_shape(self):
        from types import SimpleNamespace
        from autopaperreview.proposal.data import read, write

        src, output = self.cache / "in.json", self.cache / "out.json"
        args = SimpleNamespace(input=src, output=output, work=self.cache, cached_only=True, timeout=240)
        write(src, {"id": "toy", "proposal": {}, "overall": None})
        self.assertEqual(clarification.dispatch(args), 0)
        self.assertEqual(read(output)["state"], "insufficient_prospective_text")
        write(src, [])
        clarification.dispatch(args)
        self.assertEqual(read(output), [])

    def test_refusal_is_cached_and_not_retried(self):
        with patch.object(clarification, "run_command", side_effect=RuntimeError('{"stop_reason":"refusal"}')) as run:
            first = clarification.clarify(self.item, self.cache)
            second = clarification.clarify(self.item, self.cache, cached_only=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "no_material_question")
