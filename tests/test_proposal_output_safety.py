"""Synthetic command-entry regressions for publication and verified snapshots."""

import errno
import hashlib
import json
import os
import tempfile
import threading
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal import file_io, repro_metrics
from autopaperreview.proposal.data import digest
from scripts import reproduce_proposal_rank


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ProposalOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r3-output-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.manifests = self.root / "manifests"
        self.rows = [
            {"forum": f"synthetic-candidate-{index}", "overall": index + 1}
            for index in range(4)
        ]
        for replacement in (
            patch.object(repro_metrics, "repo_repro_dir", return_value=self.manifests),
            patch.multiple(repro_metrics, TIER_DRAWS=40, NLP_DRAWS=40),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def _published_fixture(self):
        cases = [{"forum": row["forum"], "tier": str(index // 2)} for index, row in enumerate(self.rows)]
        lock = {"cases": cases, "ids_sha256": digest(sorted(row["forum"] for row in self.rows))}
        gold = [
            {
                "forum": row["forum"], "prospective": index + 1,
                "final_rating": 3 + index * 2, "accept": int(index >= 2),
            }
            for index, row in enumerate(self.rows)
        ]
        for folder, prediction in (("fixed100", "fixed-predictions"), ("confirm100", "fresh-predictions")):
            _json(self.source / folder / "lock.json", lock)
            _json(self.source / folder / f"{prediction}.json", self.rows)
            _json(self.source / folder / "references.json", gold)
        _json(self.source / "replications199" / "joined.json", [
            {
                "key": row["forum"], "base": row["overall"], "target": g["prospective"],
                "rating": g["final_rating"], "accept": g["accept"],
                "group": "replication_A", "tier": str(index // 2),
            }
            for index, (row, g) in enumerate(zip(self.rows, gold, strict=True))
        ])
        _json(self.source / "nlp84" / "deployed-external-rows.json", [
            {"topic": "synthetic-topic", "overall": row["overall"], "human": g["final_rating"]}
            for row, g in zip(self.rows, gold, strict=True)
        ])
        _json(self.manifests / "external-sha256.json", {"files": {
            path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.source.rglob("*.json")
        }})
        expected = repro_metrics.reproduce_headlines(self.source)
        _json(self.manifests / "aggregates.json", expected)
        return expected

    def test_reproduction_publishes_only_via_pinned_parent_in_both_modes(self):
        self._published_fixture()
        input_dir = self.source / "fixed100"
        source = input_dir / "fixed-predictions.json"
        before = source.read_bytes()
        real_rename = file_io.rename_exclusive
        modes = (
            ["--root", str(self.source)],
            ["--manifest", str(input_dir / "lock.json"), "--predictions", str(source),
             "--references", str(input_dir / "references.json"), "--stratified-draws", "0"],
        )
        for args in modes:
            with self.subTest(mode=args[0]):
                destination = self.root / "destination"
                parked = self.root / "parked"
                destination.mkdir()

                def swap_at_publish(src_dir, src_name, dst_dir, dst_name):
                    destination.rename(parked)
                    input_dir.rename(destination)
                    return real_rename(src_dir, src_name, dst_dir, dst_name)

                with patch.object(file_io, "rename_exclusive", side_effect=swap_at_publish) as publish:
                    with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as output:
                        rc = reproduce_proposal_rank.main([*args, "--output", str(destination / source.name)])
                self.assertEqual(rc, 2)
                self.assertIn("output directory changed", error.getvalue())
                self.assertFalse(output.getvalue())
                self.assertEqual((destination / source.name).read_bytes(), before)
                self.assertEqual([p.name for p in parked.iterdir()], [source.name])
                self.assertEqual(publish.call_count, 1)
                self.assertNotEqual(publish.call_args.args[0], publish.call_args.args[2])
                destination.rename(input_dir)
                (parked / source.name).unlink()
                parked.rmdir()

    def test_prioritize_refuses_implicit_bank_even_without_asset_manifest(self):
        proposal, profile, bank = (self.root / name for name in ("proposal.txt", "profile.json", "bank.json"))
        proposal.write_text("We propose to study a synthetic clustering rule.", encoding="utf-8")
        _json(profile, {"ranker": {"method": "mean", "names": ["anchored"]}})
        _json(bank, {"synthetic": "preserve these bytes"})
        before = bank.read_bytes()
        with patch("autopaperreview.proposal.priority.model_leak_judge") as judge, redirect_stderr(StringIO()) as error:
            code = main([
                "proposal-review", "prioritize", str(proposal), "--profile", str(profile),
                "--work", str(self.root / "work"), "--output", str(bank),
                "--target-year", "2025", "--cutoff-ms", "1000",
            ])
        self.assertEqual(code, 2)
        self.assertIn("output must be distinct", error.getvalue())
        self.assertEqual(bank.read_bytes(), before)
        judge.assert_not_called()

    def test_report_refuses_to_replace_the_calibration_it_read(self):
        calibration = self.root / "metrics.json"
        _json(calibration, {"config": {"review": {"stage": "review"}}})
        before = calibration.read_bytes()
        with patch("autopaperreview.proposal.cli.evaluation.evaluate") as evaluate:
            with redirect_stderr(StringIO()) as error:
                code = main([
                    "proposal-review", "report", "--work", str(self.root),
                    "--calibration", str(calibration),
                ])
        self.assertEqual(code, 2)
        self.assertIn("output must be distinct", error.getvalue())
        self.assertEqual(calibration.read_bytes(), before)
        self.assertFalse((self.root / "holdout.json").exists())
        evaluate.assert_not_called()

    def test_reproduction_refuses_symlinked_output_parent(self):
        self._published_fixture()
        link = self.root / "output-link"
        link.symlink_to(self.source / "fixed100", target_is_directory=True)
        source = self.source / "fixed100" / "fixed-predictions.json"
        before = source.read_bytes()
        with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as output:
            code = reproduce_proposal_rank.main(["--root", str(self.source), "--output", str(link / source.name)])
        self.assertEqual(code, 2)
        self.assertIn("symbolic links", error.getvalue())
        self.assertFalse(output.getvalue())
        self.assertEqual(source.read_bytes(), before)

    def test_reproduction_computes_from_exactly_the_bytes_it_verified(self):
        expected = self._published_fixture()
        prediction = self.source / "fixed100" / "fixed-predictions.json"
        loads = Counter()
        verify = repro_metrics.verify_external_files
        read_bytes = repro_metrics.read_bytes

        def read_once(path):
            loads[path] += 1
            return read_bytes(path)

        def mutate_after_verification(folder):
            validated = verify(folder)
            _json(prediction, [{**row, "overall": row["overall"] + 100} for row in self.rows])
            return validated

        with patch.object(repro_metrics, "read_bytes", side_effect=read_once):
            with patch.object(repro_metrics, "verify_external_files", side_effect=mutate_after_verification):
                with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as output:
                    rc = reproduce_proposal_rank.main(["--root", str(self.source)])
        self.assertEqual(rc, 0, error.getvalue())
        self.assertEqual(len(loads), 8)
        self.assertEqual(set(loads.values()), {1})
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_reproduction_refuses_replaced_input_identity_before_publishing(self):
        self._published_fixture()
        source = self.source / "fixed100" / "fixed-predictions.json"
        replacement = self.root / "replacement.json"
        replacement.write_bytes(source.read_bytes())
        real_stage = file_io.stage_output

        def swap_during_staging(*args):
            temporary = real_stage(*args)
            replacement.replace(source)
            return temporary

        destination = self.root / "destination" / "result.json"
        with patch.object(file_io, "stage_output", side_effect=swap_during_staging):
            with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as output:
                rc = reproduce_proposal_rank.main(["--root", str(self.source), "--output", str(destination)])
        self.assertEqual(rc, 2)
        self.assertIn("input file identity changed", error.getvalue())
        self.assertFalse(output.getvalue())
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_moving_an_input_onto_the_output_name_keeps_its_bytes(self):
        self._published_fixture()
        source = self.source / "fixed100" / "fixed-predictions.json"
        before = source.read_bytes()
        real_rename = file_io.rename_exclusive
        output = self.root / "destination" / "summary.json"
        output.parent.mkdir()

        def move_onto_destination(src_dir, src_name, dst_dir, dst_name):
            source.replace(output)
            return real_rename(src_dir, src_name, dst_dir, dst_name)

        for args in (
            ["--root", str(self.source)],
            ["--manifest", str(self.source / "fixed100" / "lock.json"),
             "--predictions", str(source),
             "--references", str(self.source / "fixed100" / "references.json"),
             "--stratified-draws", "0"],
        ):
            with self.subTest(mode=args[0]):
                if output.exists():
                    output.unlink()
                source.write_bytes(before)
                with patch.object(file_io, "rename_exclusive", side_effect=move_onto_destination):
                    with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as stdout:
                        rc = reproduce_proposal_rank.main([*args, "--output", str(output)])
                self.assertEqual(rc, 2, error.getvalue())
                self.assertFalse(stdout.getvalue())
                self.assertEqual(output.read_bytes(), before)
                self.assertIn("distinct", error.getvalue())

    def test_reproduction_refuses_output_overlapping_the_repo_or_input_directory(self):
        self._published_fixture()
        hmac_manifest = self.manifests / "external-hmac-sha256.json"
        hmac_manifest.write_text("{}\n", encoding="utf-8")
        before_hmac = hmac_manifest.read_bytes()
        before_raw = (self.manifests / "external-sha256.json").read_bytes()
        prediction = self.source / "fixed100" / "fixed-predictions.json"
        before_prediction = prediction.read_bytes()
        repo_file = Path(repro_metrics.__file__).resolve().parents[3] / "benchmarks/proposal/repro/aggregates.json"
        before_repo = repo_file.read_bytes()
        with self.assertRaisesRegex(ValueError, "overlap"):
            file_io.assert_output_isolated(repo_file)
        targets = (
            hmac_manifest,
            self.manifests / "external-sha256.json",
            self.manifests / "aggregates.json",
            self.source / "summary.json",
            self.source / "fixed100" / "summary.json",
            prediction,
        )
        for target in targets:
            with self.subTest(target=target.name):
                with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()) as stdout:
                    rc = reproduce_proposal_rank.main(["--root", str(self.source), "--output", str(target)])
                self.assertEqual(rc, 2, error.getvalue())
                self.assertIn("overlap", error.getvalue())
                self.assertFalse(stdout.getvalue())
        self.assertEqual(hmac_manifest.read_bytes(), before_hmac)
        self.assertEqual((self.manifests / "external-sha256.json").read_bytes(), before_raw)
        self.assertEqual(prediction.read_bytes(), before_prediction)
        self.assertEqual(repo_file.read_bytes(), before_repo)

    def test_a_short_overwrite_leaves_the_previous_report(self):
        destination = self.root / "report.json"
        original = '{"kept": true}\n'
        destination.write_text(original, encoding="utf-8")
        real_write = os.write

        def short_write(fd, data):
            chunk = data[:13] if len(data) > 13 else data
            real_write(fd, chunk)
            raise OSError(errno.ENOSPC, "No space left on device")

        with patch("os.write", side_effect=short_write):
            with self.assertRaises(OSError):
                file_io.write_text(destination, '{"replaced": true, "complete": true}\n')
        self.assertEqual(destination.read_text(encoding="utf-8"), original)
        self.assertFalse(list(destination.parent.glob(".apr-output-*.tmp")))

    def test_missing_rename_and_cross_device_leave_the_destination(self):
        fresh = self.root / "fresh.json"
        existing = self.root / "existing.json"
        existing.write_text('{"old": 1}\n', encoding="utf-8")
        before = existing.read_text(encoding="utf-8")
        for name, error in (
            ("renameat2", OSError(errno.ENOSYS, "renameat2 is not available")),
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
                self.assertFalse(list(self.root.glob(".apr-output-*.tmp")))

    def test_a_moved_checksum_is_not_replaced_by_the_report(self):
        folder = self.root / "checksums"
        folder.mkdir()
        payload = b'{"files": {"synthetic": "abc"}}\n'
        for name in ("external-sha256.json", "external-hmac-sha256.json", "aggregates.json"):
            (folder / name).write_bytes(payload)
        destination = self.root / "summary.json"
        destination.write_text('{"old": 1}\n', encoding="utf-8")
        hmac = folder / "external-hmac-sha256.json"
        with file_io.protected_output(destination):
            with patch.object(reproduce_proposal_rank, "repo_repro_dir", return_value=folder):
                reproduce_proposal_rank._pin_checksum_files()
                hmac.replace(destination)
                with self.assertRaisesRegex(ValueError, "distinct|disappeared|identity"):
                    file_io.write_text(destination, '{"summary": true}\n')
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse(hmac.exists())

    def test_a_symlinked_input_root_is_the_real_directory(self):
        source = self.root / "inputs"
        source.mkdir()
        link = self.root / "linked-inputs"
        link.symlink_to(source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "overlap"):
            file_io.assert_output_isolated(source / "summary.json", link)

    def _publish_all(self, path, texts):
        errors = []

        def publish(text, destination=path):
            try:
                with file_io.protected_output(destination):
                    file_io.write_text(destination, text)
            except ValueError as exc:
                errors.append(exc)

        workers = [threading.Thread(target=publish, args=(text,)) for text in texts]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        self.assertFalse(list(path.parent.glob(".apr-output-*.tmp")))
        self.assertFalse(list(path.parent.glob(".apr-publish.lock")))
        return errors

    def test_a_concurrent_publish_does_not_roll_back_the_winner(self):
        path = self.root / "report.json"
        with file_io.protected_output(path):
            file_io.write_text(path, "OLD")
        errors = self._publish_all(path, ("AAA", "BBB"))
        self.assertFalse(errors)
        self.assertIn(path.read_text(encoding="utf-8"), {"AAA", "BBB"})

    def test_three_writers_keep_the_latest_success(self):
        path = self.root / "report.json"
        with file_io.protected_output(path):
            file_io.write_text(path, "OLD")
        errors = self._publish_all(path, ("AAA", "BBB", "CCC"))
        self.assertFalse(errors)
        self.assertIn(path.read_text(encoding="utf-8"), {"AAA", "BBB", "CCC"})

    def test_a_writer_inside_the_rollback_keeps_the_newest_report(self):
        path = self.root / "report.json"
        file_io.write_text(path, "OLD")
        file_io.write_text(path, "BBB")
        errors = self._publish_all(path, ("AAA", "CCC"))
        self.assertFalse(errors)
        self.assertIn(path.read_text(encoding="utf-8"), {"AAA", "CCC"})

    def test_many_writers_keep_the_newest_report_and_a_late_input(self):
        for count in (4, 6, 12):
            with self.subTest(count=count):
                path = self.root / f"report-{count}.json"
                file_io.write_text(path, "OLD")
                texts = [chr(ord("A") + index) * 3 for index in range(count)]
                errors = self._publish_all(path, texts)
                self.assertFalse(errors, count)
                self.assertIn(path.read_text(encoding="utf-8"), set(texts), count)

        folder = self.root / "checksums"
        folder.mkdir()
        payload = b'{"files": {"synthetic": "abc"}}\n'
        destination = self.root / "late-summary.json"
        moved = folder / "external-sha256.json"
        moved.write_bytes(payload)
        with file_io.protected_output(destination):
            with patch.object(reproduce_proposal_rank, "repo_repro_dir", return_value=folder):
                reproduce_proposal_rank._pin_checksum_files()
            moved.replace(destination)
        errors = self._publish_all(destination, ("AAA", "BBB", "CCC"))
        self.assertEqual(len(errors), 3)
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse(moved.exists())

    def test_input_moved_after_the_destination_is_opened_is_restored(self):
        folder = self.root / "checksums"
        folder.mkdir()
        payload = b'{"files": {"synthetic": "abc"}}\n'
        names = ("external-sha256.json", "external-hmac-sha256.json", "aggregates.json")
        for name in names:
            (folder / name).write_bytes(payload)
        for name in names:
            with self.subTest(name=name):
                destination = self.root / f"{name}-summary.json"
                destination.write_text('{"old": 1}\n', encoding="utf-8")
                moved = folder / name
                moved.write_bytes(payload)
                with file_io.protected_output(destination):
                    with patch.object(reproduce_proposal_rank, "repo_repro_dir", return_value=folder):
                        reproduce_proposal_rank._pin_checksum_files()
                    moved.replace(destination)
                    with self.assertRaisesRegex(ValueError, "distinct|disappeared|identity"):
                        file_io.write_text(destination, '{"summary": true}\n')
                self.assertEqual(destination.read_bytes(), payload)
                self.assertFalse(moved.exists())
                self.assertFalse(list(self.root.glob(".apr-output-*.tmp")))

    def test_tilde_output_inside_the_input_root_is_refused(self):
        home = self.root / "home"
        source = home / "input"
        source.mkdir(parents=True)
        output = source / "report.json"
        with patch.dict(os.environ, {"HOME": str(home)}):
            with redirect_stderr(StringIO()) as error, redirect_stdout(StringIO()):
                code = reproduce_proposal_rank.main([
                    "--root", str(source),
                    "--output", "~/input/report.json",
                ])
        self.assertEqual(code, 2, error.getvalue())
        self.assertIn("overlap", error.getvalue())
        self.assertFalse(output.exists())

    def test_gitignored_workdir_is_writable_and_tracked_files_are_not(self):
        repo = Path(file_io.__file__).resolve().parents[3]
        file_io.assert_output_isolated(repo / "workdir" / "synthetic-round2" / "review.json")
        with self.assertRaisesRegex(ValueError, "overlap"):
            file_io.assert_output_isolated(repo / "benchmarks" / "proposal" / "repro" / "aggregates.json")
