import csv
import json
import os
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import repro_metrics
from autopaperreview.proposal.data import digest
from autopaperreview.proposal.fixed_benchmark import cohort_cases
from scripts import rekey_external_repro
from scripts.rekey_external_repro import rekey_tree


SALT = "synthetic-keying-fixture"


class ProposalRekeyTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="apr-pr70-fix-r1-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "input"
        self.source.mkdir()
        self.output = self.root / "output"
        self.payload = '{"forum":"synthetic-row"}\n'
        self.original = self.source / "a.json"
        self.original.write_text(self.payload, encoding="utf-8")

    def test_rekey_rejects_same_child_and_parent_outputs(self):
        for output in (self.source, self.source / "child", self.root):
            with self.subTest(output=output.name):
                with self.assertRaisesRegex(ValueError, "must not overlap"):
                    rekey_tree(self.source, output, SALT)
        self.assertEqual(list(self.source.iterdir()), [self.original])
        self.assertEqual(self.original.read_text(encoding="utf-8"), self.payload)

    def test_rekey_checks_all_destinations_before_writing_any(self):
        (self.source / "z.json").write_text(self.payload, encoding="utf-8")
        self.output.mkdir()
        bad = self.output / "z.json"
        bad.symlink_to(self.original)
        with self.assertRaisesRegex(ValueError, "without other links"):
            rekey_tree(self.source, self.output, SALT)
        self.assertFalse((self.output / "a.json").exists())
        self.assertTrue(bad.is_symlink())
        self.assertEqual(self.original.read_text(encoding="utf-8"), self.payload)

    def test_rekey_rejects_dangling_symlinks_and_hardlinks(self):
        self.output.mkdir()
        dest = self.output / "a.json"
        missing = self.root / "absent.json"
        dest.symlink_to(missing)
        with self.assertRaisesRegex(ValueError, "without other links"):
            rekey_tree(self.source, self.output, SALT)
        self.assertFalse(missing.exists())
        dest.unlink()
        os.link(self.original, dest)
        with self.assertRaisesRegex(ValueError, "without other links"):
            rekey_tree(self.source, self.output, SALT)
        self.assertEqual(self.original.read_text(encoding="utf-8"), self.payload)

    def test_rekey_rejects_symlinked_output_root_and_intermediate_directory(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.output.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directories"):
            rekey_tree(self.source, self.output, SALT)
        self.output.unlink()
        (self.source / "nested").mkdir()
        (self.source / "nested" / "b.json").write_text(self.payload, encoding="utf-8")
        self.output.mkdir()
        (self.output / "nested").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directories"):
            rekey_tree(self.source, self.output, SALT)
        self.assertFalse((self.output / "a.json").exists())
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_rekey_detects_output_directory_replaced_by_source_after_reading(self):
        self.output.mkdir()
        displaced = self.root / "displaced-output"
        convert = rekey_external_repro._rekey_text

        def move_after_read(*args):
            result = convert(*args)
            self.output.rename(displaced)
            self.source.rename(self.output)
            return result

        with patch.object(rekey_external_repro, "_rekey_text", side_effect=move_after_read):
            with self.assertRaisesRegex(ValueError, "directory changed"):
                rekey_tree(self.source, self.output, SALT)
        self.assertEqual((self.output / self.original.name).read_text(encoding="utf-8"), self.payload)
        self.assertEqual(list(displaced.iterdir()), [])

    def test_rekey_rejects_directory_swap_during_output_preflight(self):
        self.output.mkdir()
        displaced = self.root / "displaced-output"
        check_directory = rekey_external_repro._check_directory
        swapped = False

        def swap_on_first_check(path):
            nonlocal swapped
            if not swapped:
                swapped = True
                self.output.rename(displaced)
                self.source.rename(self.output)
                displaced.rename(self.source)
            return check_directory(path)

        with patch.object(rekey_external_repro, "_check_directory", side_effect=swap_on_first_check):
            with self.assertRaisesRegex(ValueError, "aliases an input directory"):
                rekey_tree(self.source, self.output, SALT)
        self.assertEqual((self.output / self.original.name).read_text(encoding="utf-8"), self.payload)
        self.assertEqual(list(self.source.iterdir()), [])

    def test_rekey_staging_failure_leaves_all_existing_destinations_intact(self):
        (self.source / "z.json").write_text(self.payload, encoding="utf-8")
        self.output.mkdir()
        saved = self.output / "a.json"
        saved.write_text("previous output", encoding="utf-8")
        stage = rekey_external_repro._stage_output
        attempts = 0

        def fail_second(*args):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError("synthetic staging failure")
            return stage(*args)

        with patch.object(rekey_external_repro, "_stage_output", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "synthetic staging failure"):
                rekey_tree(self.source, self.output, SALT)
        self.assertEqual(saved.read_text(encoding="utf-8"), "previous output")
        self.assertEqual(list(self.output.iterdir()), [saved])
        self.assertEqual(self.original.read_text(encoding="utf-8"), self.payload)

    def test_rekey_publish_uses_pinned_directory_after_check(self):
        self.output.mkdir()
        displaced = self.root / "displaced-output"
        replace = os.replace

        def move_on_publish(*args, **kwargs):
            self.output.rename(displaced)
            self.source.rename(self.output)
            return replace(*args, **kwargs)

        with patch.object(rekey_external_repro.os, "replace", side_effect=move_on_publish):
            with self.assertRaisesRegex(ValueError, "directory changed"):
                rekey_tree(self.source, self.output, SALT)
        self.assertEqual((self.output / self.original.name).read_text(encoding="utf-8"), self.payload)
        self.assertTrue((displaced / self.original.name).is_file())

    def test_rekey_updates_cohort_digest_and_verifies_complete_manifests(self):
        lock = {
            "ids_sha256": digest(["synthetic-row"]),
            "cases": [
                {"forum": "synthetic-row", "split": "test"},
                {"forum": "synthetic-developed", "split": "dev"},
            ],
        }
        (self.source / "lock.json").write_text(json.dumps(lock), encoding="utf-8")
        (self.source / "unneeded.json").write_text("unreadable as JSON", encoding="utf-8")
        manifests = self.root / "manifests"
        manifests.mkdir()

        def manifest(folder, name):
            files = {part: sha256((folder / part).read_bytes()).hexdigest() for part in ("a.json", "lock.json")}
            (manifests / name).write_text(json.dumps({"files": files}), encoding="utf-8")

        manifest(self.source, "external-sha256.json")
        with patch.object(repro_metrics, "repo_repro_dir", return_value=manifests):
            self.assertEqual(rekey_tree(self.source, self.output, SALT, published_only=True), 2)
            self.assertFalse((self.output / "unneeded.json").exists())
            keyed = repro_metrics.identifier_key("synthetic-row", SALT)
            updated = json.loads((self.output / "lock.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["ids_sha256"], digest([keyed]))
            self.assertEqual(set(cohort_cases(updated)), {keyed})
            manifest(self.output, "external-hmac-sha256.json")
            repro_metrics.verify_external_files(self.source)
            repro_metrics.verify_external_files(self.output)
            (self.output / "a.json").write_text(self.payload, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "either committed SHA-256 manifest"):
                repro_metrics.verify_external_files(self.output)
            self.assertEqual(rekey_tree(self.source, self.output, SALT, published_only=True), 2)
            repro_metrics.verify_external_files(self.output)
        self.assertEqual(json.loads((self.source / "lock.json").read_text(encoding="utf-8")), lock)

    def test_rekey_handles_jsonl_wrappers_and_quoted_tsv_headers(self):
        row = {"forum": {"value": "synthetic-row"}, "review_ids": [{"id": "synthetic-note"}]}
        (self.source / "rows.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        (self.source / "rows.tsv").write_text('\ufeff\n"names, with commas"\tnote_id\r\nA\tsynthetic-note\r\n', encoding="utf-8")
        self.assertEqual(rekey_tree(self.source, self.output, SALT), 3)
        keyed_row = repro_metrics.identifier_key("synthetic-row", SALT)
        keyed_note = repro_metrics.identifier_key("synthetic-note", SALT)
        updated = json.loads((self.output / "rows.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(updated, {"forum": {"value": keyed_row}, "review_ids": [{"id": keyed_note}]})
        with (self.output / "rows.tsv").open(encoding="utf-8") as handle:
            rows = [row for row in csv.reader(handle, delimiter="\t") if row]
        self.assertEqual(rows, [["names, with commas", "note_id"], ["A", keyed_note]])

    def test_normalized_table_ignores_quoted_commas_and_leading_blank_lines(self):
        self.assertEqual(
            repro_metrics.normalize_table('\ufeff\n"names, with commas"\tfield\r\nA\tB\r\n'),
            [["names, with commas", "field"], ["A", "B"]],
        )

    def test_headlines_match_rejects_missing_values_malformed_intervals_and_overflow(self):
        committed = repro_metrics.committed_aggregates()
        for key, value in (("rho", None), ("rho", 10 ** 400), ("rho_ci95", []), ("rho_ci95", "12")):
            with self.subTest(key=key, value_type=type(value).__name__):
                got = repro_metrics.committed_aggregates()
                got["fixed100"][key] = value
                self.assertFalse(repro_metrics.headlines_match(got, committed))
        got = repro_metrics.committed_aggregates()
        del got["fixed100"]["rho"]
        self.assertFalse(repro_metrics.headlines_match(got, committed))


if __name__ == "__main__":
    unittest.main()
