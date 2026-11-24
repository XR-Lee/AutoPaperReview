import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.feedback_quality.__main__ import build
from benchmarks.feedback_quality.cases import SCENARIOS
from benchmarks.feedback_quality.grading import Grade, exact_interval, paired_delta, prepare, read_jsonl, summarize, write_jsonl


class FeedbackQualityTest(unittest.TestCase):
    def test_only_one_section_changes_and_ids_are_blind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            build(path)
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertEqual(len(set(manifest["input_order"])), 2 * len(SCENARIOS))
            self.assertEqual(len({case.name for case in SCENARIOS}), len(SCENARIOS))
            for case in manifest["scenarios"]:
                texts = []
                for variant in ("clean", "defect"):
                    item = case["variants"][variant]["item_id"]
                    self.assertRegex(item, r"^[a-f0-9]{16}$")
                    text = (path / "inputs" / f"{item}.txt").read_text()
                    self.assertNotIn(item, text)
                    texts.append(text.split("\n\n"))
                self.assertEqual(len(texts[0]), 5)
                self.assertEqual(len(texts[1]), 5)
                self.assertEqual(sum(a != b for a, b in zip(*texts)), 1)

    def test_confidence_intervals_at_boundaries_and_matched_deltas(self):
        self.assertEqual(exact_interval(0, 0), [None, None])
        self.assertAlmostEqual(exact_interval(1, 1)[0], .025)
        self.assertAlmostEqual(exact_interval(0, 1)[1], .975)
        contrast = paired_delta([True, False, True, None], [True, True, False, True])
        self.assertEqual(contrast["n"], 3)
        self.assertEqual((contrast["improved"], contrast["worsened"], contrast["tied"]), (1, 1, 1))
        self.assertEqual(contrast["estimate"], 0)
        tied = paired_delta([True]*4, [True]*4)["ci95"]
        self.assertLess(tied[0], 0)
        self.assertGreater(tied[1], 0)

    def test_invalid_and_unfinished_judgments_are_rejected(self):
        row = {"grade_id": "G0001", "target_raised": True, "localized": False,
               "actionable": True, "support": ["outside review"]}
        with self.assertRaisesRegex(ValueError, "Unmatched support"):
            Grade.from_label(row, "sample review")
        with self.assertRaisesRegex(ValueError, "exact review support"):
            Grade.from_label({**row, "support": []}, "sample review")
        with self.assertRaisesRegex(ValueError, "require a target"):
            Grade.from_label({**row, "target_raised": False}, "sample review")
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            Grade.from_label({**row, "localized": None}, "sample review")

    def test_cli_round_trip_missing_output_counts_against_scheduled_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset, packet = root / "data", root / "packet"
            build(dataset, 2)
            cases = json.loads((dataset / "manifest.json").read_text())["scenarios"]
            arms = {name: root / f"{name}.jsonl" for name in ("reference", "new")}
            for name, path in arms.items():
                rows = []
                for num, case in enumerate(cases):
                    for variant, info in case["variants"].items():
                        if (name, num, variant) == ("new", 1, "clean"):
                            rows.append({"item_id": info["item_id"], "error": "synthetic timeout"})
                            continue
                        raised = variant == "defect" or (name == "reference" and num == 0)
                        rows.append({"item_id": info["item_id"], "review": "Target finding." if raised else "No target."})
                write_jsonl(path, rows)
            prepare(dataset, arms, packet, seed=18)
            outputs = read_jsonl(packet / "packet.jsonl")
            for item in outputs:
                self.assertFalse({"arm", "variant", "scenario", "item_id"} & item.keys())
            labels = []
            for item in outputs:
                raised = item["review"] == "Target finding."
                labels.append({"grade_id": item["grade_id"], "target_raised": raised,
                               "localized": raised, "actionable": raised,
                               "support": [item["review"]] if raised else []})
            path = root / "decisions.jsonl"
            write_jsonl(path, labels)
            score = summarize(packet, path, "reference")
            self.assertEqual(len(score["missing"]), 1)
            new = score["rates"]["new"]
            self.assertEqual((new["targeted_specificity"]["k"], new["targeted_specificity"]["n"]), (1, 1))
            self.assertEqual((new["scheduled_grounded_actionable_discrimination"]["k"],
                              new["scheduled_grounded_actionable_discrimination"]["n"]), (1, 2))
            self.assertEqual(new["grounded_actionable_discrimination"]["n"], 1)
            diff = score["paired_difference_from_baseline"]["new"]["scheduled_grounded_actionable_discrimination"]
            self.assertEqual((diff["n"], diff["improved"], diff["worsened"]), (2, 1, 1))
            with self.assertRaisesRegex(ValueError, "already exists"):
                prepare(dataset, arms, packet, seed=18)
            write_jsonl(path, labels[:-1])
            with self.assertRaisesRegex(ValueError, "exactly one label"):
                summarize(packet, path, "reference")


if __name__ == "__main__":
    unittest.main()
