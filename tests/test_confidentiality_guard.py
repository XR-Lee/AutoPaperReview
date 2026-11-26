from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check_confidentiality.py"


def run_check(root: Path, hashes: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CHECK), "--root", str(root)]
    if hashes is not None:
        command.extend(["--hashes", str(hashes)])
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def _git_checkout() -> bool:
    if shutil.which("git") is None:
        return False
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    return probe.returncode == 0 and probe.stdout.strip() == "true"


class ConfidentialityGuardTests(unittest.TestCase):
    @unittest.skipUnless(_git_checkout(), "needs git and a git checkout (tracked files come from git ls-files)")
    def test_repository_tree_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECK)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passed", result.stdout)

    def test_submission_id_pattern_fails_without_storing_an_example(self) -> None:
        token = "sub" + "12345"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notes.txt").write_text(f"see {token}\n", encoding="utf-8")
            result = run_check(root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("denylist pattern submission-id", result.stderr)
        self.assertNotIn(token, result.stderr)

    def test_forum_url_pattern_fails(self) -> None:
        url = "https://openreview.net/" + "forum?id=" + "ExampleForumId"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notes.txt").write_text(url + "\n", encoding="utf-8")
            result = run_check(root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("denylist pattern openreview-forum", result.stderr)
        self.assertNotIn("ExampleForumId", result.stderr)

    def test_work_directory_and_binaries_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploads = root / "uploads"
            uploads.mkdir()
            (uploads / "note.txt").write_text("local only\n", encoding="utf-8")
            (root / "scan.pdf").write_bytes(b"%PDF-1.4 synthetic\n")
            (root / "page-3.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
            result = run_check(root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("uploads/note.txt: forbidden review path", result.stderr)
        self.assertIn("scan.pdf: forbidden document or image", result.stderr)
        self.assertIn("page-3.png:", result.stderr)

    def test_allowlisted_binary_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nlogo")
            allow = root / "allow.txt"
            allow.write_text("logo.png\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(CHECK),
                    "--root",
                    str(root),
                    "--allowlist",
                    str(allow),
                    "--patterns",
                    str(ROOT / "scripts" / "confidentiality_patterns.txt"),
                    "--hashes",
                    str(ROOT / "scripts" / "confidentiality_hashes.txt"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hash_denylist_blocks_a_sentence_without_echoing_it(self) -> None:
        sentence = "We introduce a synthetic blocked fixture sentence for this guard test only."
        digest = __import__("hashlib").sha256(sentence.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hashes = root / "hashes.txt"
            hashes.write_text(digest + "\n", encoding="utf-8")
            (root / "fixture.txt").write_text(sentence + "\n", encoding="utf-8")
            result = run_check(root, hashes)
        self.assertEqual(result.returncode, 1)
        self.assertIn("fixture.txt:1: blocked content hash", result.stderr)
        self.assertNotIn("synthetic blocked fixture", result.stderr)

    def test_openreview_id_and_rating_fields_fail_without_echoing_the_id(self) -> None:
        token = "AbCdEf" + "Gh12"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rows.json").write_text(
                json.dumps(
                    [
                        {"forum": token, "final_rating": 8, "decision": "accept"},
                        {"note_ids": [token]},
                    ]
                ),
                encoding="utf-8",
            )
            result = run_check(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("rows.json: openreview id field", result.stderr)
        self.assertIn("rows.json: per-paper rating", result.stderr)
        self.assertNotIn(token, result.stderr)

    def test_synthetic_rows_with_ratings_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "rows.json").write_text(
                json.dumps(
                    [
                        {
                            "forum": "synthetic-a",
                            "final_rating": 6,
                            "accept": 1,
                            "decision": "not-a-venue-token",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            result = run_check(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsalted_title_digest_fails(self) -> None:
        title = "Synthetic title written only for this guard"
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mapping.json").write_text(
                json.dumps([{"title": title, "title_sha256": digest}]),
                encoding="utf-8",
            )
            (root / "sheet.csv").write_text(
                "label,title_sha256\nexample," + digest + "\n",
                encoding="utf-8",
            )
            result = run_check(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("mapping.json: unsalted title digest", result.stderr)
        self.assertIn("sheet.csv: unsalted title digest", result.stderr)
        self.assertNotIn(title, result.stderr)

    def test_hmac_digest_and_file_hash_pass(self) -> None:
        title = "Synthetic title written only for this guard"
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "keys.json").write_text(
                json.dumps(
                    {
                        "files": {"rows.json": digest},
                        "rows": [{"title": title, "hmac_sha256": digest, "salt_env": "APR_PROPOSAL_REPRO_SALT"}],
                    }
                ),
                encoding="utf-8",
            )
            result = run_check(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_home_directory_path_fails(self) -> None:
        home = "/" + "Users" + "/" + "example" + "/notes"
        other = "/" + "home" + "/" + "example" + "/notes"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notes.txt").write_text(home + "\n" + other + "\n", encoding="utf-8")
            result = run_check(root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("notes.txt: home-directory path", result.stderr)
        self.assertNotIn("example", result.stderr)


if __name__ == "__main__":
    unittest.main()
