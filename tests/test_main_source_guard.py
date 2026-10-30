from __future__ import annotations

import importlib.util
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "main_source_guard.py"
THIS = "example/autopaperreview"
FORK = "example/autopaperreview-fork"


def load_guard():
    spec = importlib.util.spec_from_file_location("main_source_guard", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MainSourceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = load_guard()

    def _exit(self, args: list[str]) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return self.guard.main(args)

    def test_other_bases_pass(self) -> None:
        cases = [
            ("pre-release", "topic", FORK),
            ("pre-release", "pre-release", THIS),
            ("feature", "main", FORK),
            ("", "pre-release", ""),
        ]
        for base_ref, head_ref, head_repo in cases:
            with self.subTest(base_ref=base_ref, head_ref=head_ref, head_repo=head_repo):
                self.assertTrue(
                    self.guard.allows_pull_request(base_ref, head_ref, head_repo, THIS)
                )
                self.assertEqual(
                    self._exit(
                        [
                            "--base-ref",
                            base_ref,
                            "--head-ref",
                            head_ref,
                            "--head-repo",
                            head_repo,
                            "--this-repo",
                            THIS,
                        ]
                    ),
                    0,
                )

    def test_main_accepts_same_repo_pre_release(self) -> None:
        self.assertTrue(self.guard.allows_pull_request("main", "pre-release", THIS, THIS))
        self.assertEqual(
            self._exit(
                [
                    "--base-ref",
                    "main",
                    "--head-ref",
                    "pre-release",
                    "--head-repo",
                    THIS,
                    "--this-repo",
                    THIS,
                ]
            ),
            0,
        )

    def test_main_rejects_other_heads_and_forks(self) -> None:
        rejected = [
            ("topic", THIS, THIS),
            ("pre-release", FORK, THIS),
            ("Pre-release", THIS, THIS),
            ("refs/heads/pre-release", THIS, THIS),
            ("pre-release", "", THIS),
            ("pre-release", THIS, ""),
            ("main", THIS, THIS),
        ]
        for head_ref, head_repo, this_repo in rejected:
            with self.subTest(head_ref=head_ref, head_repo=head_repo, this_repo=this_repo):
                self.assertFalse(
                    self.guard.allows_pull_request("main", head_ref, head_repo, this_repo)
                )
                self.assertEqual(
                    self._exit(
                        [
                            "--base-ref",
                            "main",
                            "--head-ref",
                            head_ref,
                            "--head-repo",
                            head_repo,
                            "--this-repo",
                            this_repo,
                        ]
                    ),
                    1,
                )

    def test_workflow_job_and_existing_ci_triggers(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "main-source-guard.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^name: main-source-guard\s*$")
        self.assertRegex(workflow, r"(?m)^  main-source-guard:\s*$")
        self.assertRegex(workflow, r"(?m)^    name: main-source-guard\s*$")
        self.assertIn("pull_request:", workflow)
        self.assertIn("github.event.pull_request.base.ref", workflow)
        self.assertIn("github.event.pull_request.head.ref", workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name", workflow)
        self.assertIn("github.repository", workflow)
        self.assertIn("python3 scripts/main_source_guard.py", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("branches:", workflow)

        for relative in (".github/workflows/ci.yml", ".github/workflows/confidentiality.yml"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("\n  push:\n", text)
            self.assertIn("\n  pull_request:\n", text)
            self.assertNotIn("branches:", text)
            self.assertIn("pre-release", text)

        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertEqual(dependabot.count("target-branch: pre-release"), 2)

        policy = (ROOT / "docs" / "branching.md").read_text(encoding="utf-8")
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for text in (policy, contributing):
            self.assertIn("pre-release", text)
            self.assertIn("grok-4.7", text)
            self.assertIn("xhigh", text)
            self.assertIn("Cursor", text)
        self.assertIn("## Fixed benchmark", policy)
        self.assertIn("benchmarks/fixed_eval/manifest.json", policy)
