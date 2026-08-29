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
            ["command", "consensus", "import_issues", "ingest", "literature_grounding", "prompt_packet", "report"],
        )

    def test_templates_command_exports_packaged_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "prompts"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = main(["templates", str(output_dir)])

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["templates"]), 6)
            self.assertIn("closed_book_v1.md", payload["templates"])
            self.assertIn("sar_grounded_review_v1.md", payload["templates"])
            self.assertTrue((output_dir / "visual_qa_v1.md").is_file())


if __name__ == "__main__":
    unittest.main()
