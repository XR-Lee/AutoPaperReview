from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from autopaperreview.hashing import hash_json, sha256_file
from autopaperreview.models import ReviewPackage, StageStatus
from autopaperreview.pipeline import PipelineError, run_pipeline

from tests.support import legacy_issue


class PipelineIntegrationTests(unittest.TestCase):
    def test_end_to_end_run_then_reuses_every_stage_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("Synthetic manuscript.\n", encoding="utf-8")
            checks_dir = workspace / "checks"
            checks_dir.mkdir()
            checker_path = checks_dir / "check.py"
            checker_path.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "Path(sys.argv[1]).write_text('ok\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            issue = legacy_issue(detected_by=[], sources=[])
            (workspace / "issues.json").write_text(
                json.dumps([issue], ensure_ascii=False),
                encoding="utf-8",
            )
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "cache-review"
                    title = "Cache review"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    default_language = "en"
                    network_policy = "deny"
                    copy_source = true

                    [[stages]]
                    id = "ingest"
                    type = "ingest"

                    [[stages]]
                    id = "deterministic_check"
                    type = "command"
                    depends_on = ["ingest"]
                    [stages.params]
                    command = ["python3", "check.py", "{stage_dir}/result.txt"]
                    cwd = "checks"
                    outputs = ["result.txt"]

                    [[stages]]
                    id = "issues"
                    type = "import_issues"
                    depends_on = ["deterministic_check"]
                    [stages.params]
                    path = "issues.json"

                    [[stages]]
                    id = "consensus"
                    type = "consensus"
                    depends_on = ["issues"]

                    [[stages]]
                    id = "report"
                    type = "report"
                    depends_on = ["consensus"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            first = run_pipeline(config_path)
            self.assertEqual([stage.status for stage in first.stages], [StageStatus.success] * 5)
            self.assertTrue(all(stage.metadata["cache_hit"] is False for stage in first.stages))
            self.assertTrue(all(stage.attempt == 1 for stage in first.stages))

            run_dir = workspace / ".runs" / first.run_id
            self.assertIsNotNone(first.config_snapshot)
            snapshot_path = run_dir / first.config_snapshot.path
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(first.config_snapshot.sha256, sha256_file(snapshot_path))
            self.assertEqual(first.config_sha256, hash_json(snapshot_payload))
            self.assertFalse(Path(first.config_snapshot.path).is_absolute())

            source_record = json.loads(
                (run_dir / "stages" / "ingest" / "source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(source_record["path"], "stages/ingest/manuscript.txt")
            self.assertEqual(source_record["metadata"]["path_base"], "run")
            source_record_text = json.dumps(source_record, ensure_ascii=False)
            self.assertNotIn(str(workspace), source_record_text)
            self.assertNotIn(".attempts", source_record_text)

            command_record = json.loads(
                (run_dir / "stages" / "deterministic_check" / "command.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(command_record["argv"][-1], "{stage_dir}/result.txt")
            self.assertEqual(
                command_record["input_file_hashes"],
                {"argument:1": sha256_file(checker_path)},
            )
            command_record_text = json.dumps(command_record, ensure_ascii=False)
            self.assertNotIn(str(workspace), command_record_text)
            self.assertNotIn(".attempts", command_record_text)

            release_path = run_dir / "stages" / "report" / "review_package.json"
            report_path = run_dir / "stages" / "report" / "review_report.md"
            package = ReviewPackage.model_validate_json(release_path.read_text(encoding="utf-8"))
            self.assertEqual(len(package.issues), 1)
            self.assertIn("I001", report_path.read_text(encoding="utf-8"))
            first_hashes = {
                stage.id: [(artifact.role, artifact.sha256) for artifact in stage.outputs]
                for stage in first.stages
            }
            first_command_signature = first.stage("deterministic_check").signature

            second = run_pipeline(config_path)
            self.assertEqual(second.run_id, first.run_id)
            self.assertEqual([stage.status for stage in second.stages], [StageStatus.skipped] * 5)
            self.assertTrue(all(stage.metadata["cache_hit"] is True for stage in second.stages))
            self.assertTrue(all(stage.attempt == 1 for stage in second.stages))
            self.assertEqual(second.stage("deterministic_check").signature, first_command_signature)
            second_hashes = {
                stage.id: [(artifact.role, artifact.sha256) for artifact in stage.outputs]
                for stage in second.stages
            }
            self.assertEqual(second_hashes, first_hashes)

    def test_deny_policy_blocks_declared_network_command_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("Synthetic manuscript.\n", encoding="utf-8")
            (workspace / "would_run.py").write_text(
                "from pathlib import Path\nPath('network-ran.txt').write_text('ran', encoding='utf-8')\n",
                encoding="utf-8",
            )
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "deny-network"
                    title = "Deny network"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "network_check"
                    type = "command"
                    [stages.params]
                    command = ["python3", "would_run.py"]
                    requires_network = true
                    network_scope = "fulltext"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PipelineError, "requests fulltext network access under policy deny"):
                run_pipeline(config_path)

            self.assertFalse((workspace / "network-ran.txt").exists())
            self.assertFalse((workspace / ".runs").exists())

    def test_prompt_packet_enforces_route_manuscript_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            secret_text = "PRIVATE-MANUSCRIPT-CONTENT"
            (workspace / "manuscript.txt").write_text(secret_text, encoding="utf-8")
            (workspace / "prompt.md").write_text("Review metadata only.\n", encoding="utf-8")
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "access-control"
                    title = "Access control"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M0"
                    name = "Metadata-only route"
                    kind = "prompt"
                    prompt = "prompt.md"
                    manuscript_access = false

                    [[stages]]
                    id = "packet"
                    type = "prompt_packet"
                    [stages.params]
                    route_ids = ["M0"]
                    include_source_text = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            manifest = run_pipeline(config_path)
            packet = (
                workspace / ".runs" / manifest.run_id / "stages" / "packet" / "M0.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn(secret_text, packet)
            self.assertNotIn("## Source Text", packet)
            self.assertIn("Manuscript access: `false`", packet)

    def test_command_failure_record_uses_portable_log_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("Synthetic.\n", encoding="utf-8")
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "failure-record"
                    title = "Failure record"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "failing"
                    type = "command"
                    [stages.params]
                    command = ["python3", "-c", "import sys; sys.exit(7)"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(PipelineError) as raised:
                run_pipeline(config_path)
            self.assertIn("see stderr.log", str(raised.exception))
            self.assertNotIn(str(workspace), str(raised.exception))

            manifests = list((workspace / ".runs").glob("*/run_manifest.json"))
            self.assertEqual(len(manifests), 1)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            failure = payload["stages"][0]
            self.assertIn("see stderr.log", failure["error"])
            self.assertNotIn(str(workspace), failure["error"])
            self.assertFalse(Path(failure["metadata"]["attempt_dir"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
