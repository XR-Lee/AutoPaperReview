from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from autopaperreview.cli import main


class CliTests(unittest.TestCase):
    def test_schema_command_exports_all_canonical_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "schemas"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(["schema", str(output_dir)])

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            expected = {
                "harness-config.schema.json",
                "review-issue.schema.json",
                "review-package.schema.json",
                "run-manifest.schema.json",
                "batch-calibration.schema.json",
            }
            self.assertEqual(set(payload["schemas"]), expected)
            self.assertEqual({path.name for path in output_dir.iterdir()}, expected)

            for filename in expected:
                with self.subTest(schema=filename):
                    schema = json.loads((output_dir / filename).read_text(encoding="utf-8"))
                    self.assertEqual(schema["type"], "object")
                    self.assertFalse(schema["additionalProperties"])

            package_schema = json.loads(
                (output_dir / "review-package.schema.json").read_text(encoding="utf-8")
            )
            self.assertEqual(package_schema["properties"]["schema_version"]["default"], "1.0")
            self.assertIn("manuscript", package_schema["required"])
            self.assertIn("project_id", package_schema["required"])

    def test_plugins_command_lists_builtin_stage_types(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = main(["plugins"])

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            payload["stage_types"],
            [
                "agenda",
                "calibrate_batch",
                "command",
                "consensus",
                "execute_review",
                "format_check",
                "import_issues",
                "ingest",
                "integrity",
                "ledger",
                "literature_grounding",
                "novelty",
                "prompt_packet",
                "rate",
                "related_work_search",
                "render_pages",
                "report",
                "visual_inspect",
                "workspace_inspect",
            ],
        )

    def test_templates_command_exports_packaged_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "prompts"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(["templates", str(output_dir)])

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["templates"]), 11)
            self.assertIn("proposal_audit_v1.md", payload["templates"])
            self.assertIn("closed_book_v1.md", payload["templates"])
            self.assertIn("sar_grounded_review_v1.md", payload["templates"])
            self.assertIn("figure_grounded_v1.md", payload["templates"])
            self.assertIn("contribution_audit_v1.md", payload["templates"])
            self.assertIn("first_read_v1.md", payload["templates"])
            self.assertIn("sar_bands_v1.md", payload["templates"])
            self.assertTrue((output_dir / "visual_qa_v1.md").is_file())
            self.assertTrue((output_dir / "figure_grounded_v1.md").is_file())
            self.assertTrue((output_dir / "first_read_v1.md").is_file())

    def test_calibrate_eval_run_declares_the_holistic_mode(self) -> None:
        from contextlib import redirect_stderr

        from autopaperreview.cli import build_parser

        args = build_parser().parse_args(["calibrate-eval", "run", "--cache", "unused"])
        self.assertEqual(args.holistic_mode, "forecast")
        self.assertEqual(args.holistic_samples, 1)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "forecast",
                "--holistic-samples",
                "1",
            ]
        )
        self.assertEqual(args.holistic_mode, "forecast")
        self.assertEqual(args.holistic_samples, 1)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "rebuttal",
                "--holistic-samples",
                "2",
            ]
        )
        self.assertEqual(args.holistic_mode, "rebuttal")
        self.assertEqual(args.holistic_samples, 2)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "matched",
                "--holistic-samples",
                "2",
            ]
        )
        self.assertEqual(args.holistic_mode, "matched")
        self.assertEqual(args.holistic_samples, 2)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "panel",
                "--holistic-samples",
                "2",
            ]
        )
        self.assertEqual(args.holistic_mode, "panel")
        self.assertEqual(args.holistic_samples, 2)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "expert",
                "--holistic-samples",
                "2",
            ]
        )
        self.assertEqual(args.holistic_mode, "expert")
        self.assertEqual(args.holistic_samples, 2)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "mean",
                "--holistic-samples",
                "3",
            ]
        )
        self.assertEqual(args.holistic_mode, "mean")
        self.assertEqual(args.holistic_samples, 3)
        args = build_parser().parse_args(
            [
                "calibrate-eval",
                "run",
                "--cache",
                "unused",
                "--holistic-mode",
                "audited",
                "--holistic-samples",
                "1",
            ]
        )
        self.assertEqual(args.holistic_mode, "audited")
        self.assertEqual(args.holistic_samples, 1)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["calibrate-eval", "run", "--cache", "unused", "--holistic-mode", "other"]
            )


if __name__ == "__main__":
    unittest.main()
