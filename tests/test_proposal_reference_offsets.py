import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from autopaperreview.proposal import data, reference_coding, stages
from autopaperreview.proposal.data import (
    neutralize_label_text,
    reported_result,
    retained_label_text,
)
from autopaperreview.proposal.leak_audit import REFERENCE_PROMPT, model_leak_judge


class ProposalReferenceOffsetTests(unittest.TestCase):
    def code(self, original, quotes, *, include_independent=False):
        row = {
            "forum": "synthetic-offsets",
            "reviews": [{"id": "synthetic-opinion", "units": {"u": original}}],
        }
        calls = []
        self.calls = calls

        def transport(command, *, system, prompt, model, timeout):
            payload = json.loads(prompt)
            stage = next(key for key, value in stages.PROMPTS.items() if value == system)
            calls.append((stage, payload))
            if stage == "ref_extract":
                result = {"r1": [{"id": "u", "quote": quote} for quote in quotes]}
            else:
                self.assertEqual(stage, "ref_grade")
                result = {"r1": {"score": 4, "used": list(reversed(range(len(quotes))))}}
            return json.dumps(result), 0.0

        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            with patch.object(stages, "run_command", side_effect=transport):
                return reference_coding.code_paper(
                    row,
                    Path(tmp),
                    ["synthetic-command"],
                    leak_judge=lambda _: {"verdict": "clean"},
                    include_independent_grades=include_independent,
                )

    def test_deleted_duplicate_cannot_supply_joint_or_independent_offsets(self):
        quote = "The proposed lattice test is precise."
        original = f"Decision: Reject, although {quote}\n- A simpler plan is plausible. {quote}"
        result = self.code(original, [quote], include_independent=True)
        self.assertNotIn("Decision", self.calls[0][1]["r1"]["u"])
        for review in (result["reviewers"][0], result["independent_reference"]["reviewers"][0]):
            evidence = review["evidence"][0]
            self.assertEqual(evidence["start"], original.rindex(quote))
            self.assertEqual(original[evidence["start"] : evidence["end"]], quote)

    def test_cross_deletion_and_normalized_spacing_rejected_before_grading(self):
        left = "The proposed switch is hard to verify."
        right = "The toy baseline is inexpensive."
        for gap in (" Decision: Reject. ", "\n", "  "):
            with self.subTest(gap=gap):
                with self.assertRaisesRegex(ValueError, "contiguous retained review span"):
                    self.code(left + gap + right, [left + " " + right])
                self.assertEqual([stage for stage, _ in self.calls], ["ref_extract"])

    def test_reverse_extraction_and_grade_order_keep_the_bound_spans(self):
        left = "The proposed switch is hard to verify."
        right = "The toy baseline is inexpensive."
        original = f"  *{left} {right}"
        result = self.code(original, [right, left], include_independent=True)
        expected = [(original.index(quote), quote) for quote in (left, right)]
        for review in (result["reviewers"][0], result["independent_reference"]["reviewers"][0]):
            self.assertEqual([(span["start"], span["quote"]) for span in review["evidence"]], expected)

        # A single unchanged source space is valid inside an exact excerpt.
        combined = left + " " + right
        result = self.code(original, [combined])
        span = result["reviewers"][0]["evidence"][0]
        self.assertEqual(original[span["start"] : span["end"]], combined)

    def test_mapped_cleaning_agrees_with_label_cleaning(self):
        original = "  * The cost model is testable.\nMeta Score: 7.\nA simpler plan is plausible."
        clean, offsets = retained_label_text(original)
        self.assertEqual(clean, neutralize_label_text(original))
        self.assertEqual(len(clean), len(offsets))
        self.assertTrue(all(pos is None or clean[i] == original[pos] for i, pos in enumerate(offsets)))

    def test_completed_experimental_review_units_cannot_supply_a_reference(self):
        reports = (
            "Our experiments demonstrate that violet bands speed up sorting.",
            "The pilot revealed fewer searches through the box ledger.",
            "The authors measured fewer errors with the new shelf tags.",
            "The violet tags made the boxes easier to find during the trial.",
            "试验显示紫色标记缩短了清点时间。",
        )
        for report in reports:
            with self.subTest(report=report):
                self.assertTrue(reported_result(report))
                coded = self.code(report, [report], include_independent=True)
                self.assertEqual(self.calls, [])
                self.assertIsNone(coded["prospective"])
                self.assertEqual(coded["reviewers"][0]["evidence"], [])
                self.assertIsNone(coded["independent_reference"]["prospective"])

    def test_reference_extract_and_grade_receive_only_retained_prospective_text(self):
        opinion = "The proposed layout makes the search assumptions testable."
        report = "The trial showed that violet tags sped up sorting."
        original = f"{report}\n{opinion}"
        coded = self.code(original, [opinion], include_independent=True)
        self.assertEqual([stage for stage, _ in self.calls], ["ref_extract", "ref_grade"])
        self.assertEqual(self.calls[0][1], {"r1": {"u": opinion}})
        self.assertEqual(self.calls[1][1], {"r1": [{"excerpt": opinion, "context": opinion}]})
        for result in (coded, coded["independent_reference"]):
            evidence = result["reviewers"][0]["evidence"][0]
            self.assertEqual(original[evidence["start"] : evidence["end"]], opinion)
            self.assertEqual(evidence["start"], original.index(opinion))

    def test_reference_semantic_audit_blocks_completed_result_paraphrase(self):
        text = "Once the baskets wore violet labels, the queue was gone before the kettle boiled."
        self.assertFalse(reported_result(text))
        row = {
            "forum": "synthetic-paraphrase",
            "reviews": [{"id": "synthetic-observation", "units": {"u": text}}],
        }
        calls = []

        def transport(command, *, system, prompt, model, timeout):
            calls.append(system)
            self.assertEqual(system, REFERENCE_PROMPT)
            self.assertIn("experiment", system.casefold())
            self.assertEqual(json.loads(prompt), {"r1": {"u": text}})
            return '{"verdict":"reported_result"}', 0.0

        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            cache = Path(tmp)
            command = ["synthetic-command"]
            judge = model_leak_judge(cache, "synthetic-judge", command, 10)
            with patch.object(stages, "run_command", side_effect=transport):
                with self.assertRaisesRegex(ValueError, "leak audit blocked"):
                    reference_coding.code_paper(row, cache, command, leak_judge=judge)
        self.assertEqual(calls, [REFERENCE_PROMPT])

    def test_same_quote_can_bind_distinct_overlapping_retained_spans(self):
        original = "violet tags violet tags violet tags"
        quote = "violet tags violet tags"
        units = reference_coding.coding_units({"u": original})
        item = {"id": "u", "quote": quote}
        spans = reference_coding.bind_evidence(units, [item, item])
        self.assertEqual([span["start"] for span in spans], [0, len("violet tags ")])
        self.assertTrue(all(original[span["start"] : span["end"]] == quote for span in spans))
        with self.assertRaisesRegex(ValueError, "contiguous retained review span"):
            reference_coding.bind_evidence(units, [item, item, item])

    def test_reference_protocol_binds_the_cleaning_algorithm_version(self):
        command = ["synthetic-command"]
        previous = reference_coding.protocol(command)
        with patch.object(data, "REVIEW_CLEANING_VERSION", data.REVIEW_CLEANING_VERSION + 1):
            current = reference_coding.protocol(command)
        self.assertNotEqual(previous["text_cleaning_sha256"], current["text_cleaning_sha256"])

    def test_reference_output_rejects_aliases_of_the_input(self):
        with TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp:
            root = Path(tmp)
            source = root / "reviews.json"
            data.write(source, [{
                "forum": "synthetic-input",
                "reviews": [{"id": "synthetic-note", "units": {"u": "The plan is clear."}}],
            }])
            original = source.read_bytes()
            symbolic = root / "symbolic.json"
            symbolic.symlink_to(source)
            linked = root / "linked.json"
            linked.hardlink_to(source)
            for output in (source, symbolic, linked):
                with self.subTest(output=output.name):
                    args = SimpleNamespace(jobs=1, timeout=1, inputs=source, output=output, work=root / "work")
                    with patch.object(reference_coding, "model_leak_judge") as judge:
                        with self.assertRaises(ValueError):
                            reference_coding.dispatch(args)
                    judge.assert_not_called()
                    self.assertEqual(source.read_bytes(), original)
