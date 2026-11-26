import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import reference_coding, stages
from autopaperreview.proposal.data import neutralize_label_text, retained_label_text


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

        with TemporaryDirectory(prefix="apr-pr70-fix-r1-") as tmp:
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
