import json
import tempfile
import unittest
from pathlib import Path

from autopaperreview.integrity import build_integrity_records, collect_inventory
from autopaperreview.models import Artifact, IntegrityVerdict, ReviewPackage
from autopaperreview.reporting import render_markdown


class AccuracyIntegrityTests(unittest.TestCase):
    def record(self, manuscript, *result_values):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for index, value in enumerate(result_values):
                target = workspace / str(index) / "results.json"
                target.parent.mkdir()
                target.write_text(json.dumps({"accuracy": value}), encoding="utf-8")
            inventory = collect_inventory(["."], workspace=workspace, generated_by="test")
            records = build_integrity_records(
                manuscript_text=manuscript, snapshots=[], inventory=inventory,
                evidence=[], workspace=workspace,
            )
            return next((row for row in records if row.id == "INT-RES-accuracy"), None)

    def test_unrelated_percentages_do_not_establish_accuracy(self):
        for sentence in (
            "Paint covers 17% of each token.",
            "Accuracy improved by 12%.",
            "Our method achieved 8% accuracy improvement.",
            "The target accuracy was 81%.",
            "Accuracy: 75% confidence interval from 0.31 to 0.39.",
            "Accuracy was 14% higher than before.",
            "Background. Another system achieved 72% accuracy.",
            "The catalogue link ends in item7%2Fblue.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIsNone(self.record(sentence))
                self.assertIsNone(self.record(sentence, 0.25))

    def test_explicit_scalar_is_converted_from_percent_at_every_scale(self):
        for display, expected in (("0", 0), (".5", 0.005), ("1", 0.01), ("2", 0.02), ("100", 1)):
            with self.subTest(display=display):
                sentence = f"Blue tiles occupy 17% of the board. Our method achieves {display}% accuracy."
                record = self.record(sentence, expected)
                self.assertEqual(record.verdict, IntegrityVerdict.exact)
                self.assertEqual(float(record.expected), expected)
        self.assertEqual(self.record("Test accuracy: 62%.", 0.6).verdict, IntegrityVerdict.mismatch)
        self.assertEqual(self.record("We report an accuracy of 62%.").verdict, IntegrityVerdict.missing)

    def test_ambiguous_measurements_or_files_cannot_trigger_a_mismatch(self):
        for sentence in (
            "Test accuracy was 63%. Validation accuracy was 65%.",
            "We attained 54% accuracy. Another system reported 75% accuracy.",
            "Accuracy: 42% and 61% on the two partitions.",
        ):
            with self.subTest(sentence=sentence):
                record = self.record(sentence, 0.9)
                self.assertEqual(record.verdict, IntegrityVerdict.unclear)
                self.assertIsNone(record.expected)
        record = self.record("Our detector obtained 40% accuracy.", 0.4, 0.9)
        self.assertEqual(record.verdict, IntegrityVerdict.unclear)

    def test_non_scalar_results_do_not_assert_comparability(self):
        for invalid in (True, None, "0.40", -0.4, 40):
            with self.subTest(invalid=invalid):
                self.assertIsNone(self.record("Accuracy: 40%.", invalid))

    def test_unresolved_comparison_reaches_bilingual_author_report(self):
        record = self.record("Accuracy: 62% and 71% on the two panels.")
        package = ReviewPackage(
            project_id="reading-check",
            manuscript=Artifact(path="paper.txt", sha256="a" * 64, size_bytes=100, role="manuscript"),
            integrity_records=[record],
        )
        markdown = render_markdown(package, bilingual=True, fill_missing=False)
        self.assertIn("results.json / accuracy", markdown)
        self.assertIn(record.notes.primary, markdown)
        self.assertIn("无法将准确率字段唯一对应", markdown)
