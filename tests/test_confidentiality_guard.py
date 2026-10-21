from __future__ import annotations

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


class ConfidentialityGuardTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
