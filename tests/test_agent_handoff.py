from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from autopaperreview.adapters import digest_packet_text
from autopaperreview.cli import main, write_project
from autopaperreview.config import load_config
from autopaperreview.handoff import HandoffError, run_status, submit_drop
from autopaperreview.models import StageStatus
from autopaperreview.pipeline import (
    RunLockedError,
    run_directory,
    run_lock,
    run_pipeline,
    validate_config_paths,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "synthetic"
CALIBRATION = EXAMPLES / "calibration"


def agent_project(root: Path) -> Path:
    project = root / "paper"
    write_project(project, project_id="agent-review", title="Synthetic agent review", adapter="agent")
    (project / "manuscript.txt").write_text(
        (CALIBRATION / "manuscript.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return project / "review.toml"


def judgment_for(status: dict) -> dict:
    drop = json.loads((CALIBRATION / "judgment.json").read_text(encoding="utf-8"))
    drop["packet_sha256"] = status["awaiting"][0]["echo"]["packet_sha256"]
    return drop


def run_cli(*argv: str) -> tuple[int, dict]:
    stdout = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
        code = main(list(argv))
    return code, json.loads(stdout.getvalue())


class AwaitingInputTests(unittest.TestCase):
    def test_agent_project_validates_before_the_drop_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            self.assertEqual(validate_config_paths(load_config(config)), [])

    def test_fixture_adapter_still_needs_its_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            text = config.read_text(encoding="utf-8").replace("AgentJudgmentAdapter", "FixtureReviewAdapter")
            config.write_text(text, encoding="utf-8")
            errors = validate_config_paths(load_config(config))
            self.assertTrue(any("review fixture does not exist" in error for error in errors))

    def test_run_waits_for_the_judgment_and_runs_independent_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            manifest = run_pipeline(config)
            by_id = {stage.id: stage for stage in manifest.stages}
            self.assertEqual(by_id["review"].status, StageStatus.awaiting_input)
            self.assertEqual(by_id["integrity"].status, StageStatus.success)
            self.assertEqual(by_id["consensus"].status, StageStatus.pending)
            self.assertEqual(by_id["report"].metadata["blocked_by"], ["review"])
            run_dir = run_directory(load_config(config))
            self.assertFalse(any(run_dir.joinpath(".attempts").glob("review-*")))

            status = run_status(config)
            self.assertEqual(status["state"], "awaiting_input")
            request = status["awaiting"][0]
            self.assertEqual(request["kind"], "review_judgment")
            self.assertFalse(request["drop_exists"])
            self.assertTrue(request["verify_excerpts"])
            echo = request["echo"]["packet_sha256"]
            self.assertEqual(sorted(echo), sorted(item["id"] for item in request["reads"]))
            for item in request["reads"]:
                text = Path(item["absolute_path"]).read_text(encoding="utf-8")
                self.assertEqual(echo[item["id"]], digest_packet_text(text))

            detail = run_status(config, detail=True)["awaiting"][0]["template"]
            self.assertEqual(detail["drop"]["packet_sha256"], echo)
            self.assertEqual(len(detail["drop"]["dimension_scores"]), 7)
            self.assertIsNone(detail["drop"]["dimension_scores"][0]["clause_id"])

    def test_cli_run_exits_3_while_awaiting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            code, summary = run_cli("run", str(config))
            self.assertEqual(code, 3)
            self.assertEqual(summary["status"], "awaiting_input")
            self.assertEqual(summary["awaiting"][0]["stage_id"], "review")


class SubmitDropTests(unittest.TestCase):
    def test_submit_checks_the_drop_then_the_run_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_pipeline(config)
            drop = judgment_for(run_status(config))
            target = config.parent / "judgment.json"

            stale = json.loads(json.dumps(drop))
            stale["packet_sha256"]["M0"] = "0" * 64
            result = submit_drop(config, stale)
            self.assertEqual(result["status"], "rejected")
            self.assertIn("does not match the current prompt packets", result["errors"][0])
            self.assertFalse(target.exists())

            invented = json.loads(json.dumps(drop))
            invented["evidence"][0]["excerpt"] = "A sentence that the synthetic manuscript never contains."
            result = submit_drop(config, invented)
            self.assertEqual(result["status"], "rejected")
            self.assertIn("evidence E1 excerpt", result["errors"][0])

            missing = json.loads(json.dumps(drop))
            missing["dimension_scores"] = missing["dimension_scores"][:6]
            self.assertEqual(submit_drop(config, missing)["status"], "rejected")
            self.assertEqual(submit_drop(config, "[1, 2]")["status"], "rejected")

            result = submit_drop(config, drop)
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["excerpt_check"], {"status": "verified", "checked": 1})
            self.assertTrue(target.is_file())
            self.assertIn("is written; run again", " ".join(run_status(config)["changes"]))

            code, summary = run_cli("run", str(config))
            self.assertEqual(code, 0)
            self.assertEqual(summary["status"], "success")
            status = run_status(config)
            self.assertEqual(status["state"], "complete")
            self.assertTrue(any(item["path"].endswith("review_report.md") for item in status["outputs"]))
            self.assertTrue((config.parent / "review_report.md").is_file())

    def test_a_drop_that_fails_in_the_run_is_reported_with_its_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_pipeline(config)
            drop = judgment_for(run_status(config))
            drop["evidence"][0]["excerpt"] = "Not a sentence from the manuscript."
            (config.parent / "judgment.json").write_text(json.dumps(drop), encoding="utf-8")
            code, summary = run_cli("run", str(config))
            self.assertEqual(code, 2)
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["failed_stage"], "review")
            status = run_status(config)
            self.assertEqual(status["state"], "failed")
            failed = next(stage for stage in status["stages"] if stage["id"] == "review")
            self.assertEqual(failed["drop_path"], "judgment.json")
            self.assertIn("copy each excerpt verbatim", failed["error"])

    def test_submit_waits_for_a_running_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_pipeline(config)
            drop = judgment_for(run_status(config))
            with run_lock(run_directory(load_config(config))):
                with self.assertRaisesRegex(HandoffError, "in progress"):
                    submit_drop(config, drop)

    def test_an_unmet_dependency_is_a_state_error_not_a_rejected_drop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_pipeline(config)
            drop = judgment_for(run_status(config))
            manifest_path = run_directory(load_config(config)) / "run_manifest.json"
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            for stage in raw["stages"]:
                if stage["id"] == "prompt_packets":
                    stage["status"] = "failed"
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(HandoffError, "prompt_packets has not succeeded"):
                submit_drop(config, drop)

    def test_status_reports_running_while_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_pipeline(config)
            submit_drop(config, judgment_for(run_status(config)))
            run_pipeline(config)
            self.assertEqual(run_status(config)["state"], "complete")
            with run_lock(run_directory(load_config(config))):
                self.assertEqual(run_status(config)["state"], "running")

    def test_submit_before_any_run_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            with self.assertRaisesRegex(HandoffError, "has not run yet"):
                submit_drop(config, {"issues": []})


class RunLockTests(unittest.TestCase):
    def test_a_second_run_of_the_same_project_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_dir = run_directory(load_config(config))
            run_dir.mkdir(parents=True)
            with run_lock(run_dir):
                with self.assertRaises(RunLockedError):
                    run_pipeline(config)
                code, summary = run_cli("run", str(config))
                self.assertEqual(code, 2)
                self.assertEqual(summary["status"], "busy")
            self.assertEqual(run_pipeline(config).project_id, "agent-review")

    def test_a_stage_left_running_without_the_lock_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = agent_project(Path(temporary))
            run_pipeline(config)
            manifest_path = run_directory(load_config(config)) / "run_manifest.json"
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw["stages"][-1]["status"] = "running"
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(run_status(config)["state"], "interrupted")


class InitTests(unittest.TestCase):
    def test_title_is_escaped_in_review_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "paper"
            title = 'A "quoted" title\nnetwork_policy = "allow"'
            write_project(project, project_id="escaped", title=title)
            loaded = load_config(project / "review.toml")
            self.assertEqual(loaded.config.project.title, title)
            self.assertEqual(loaded.config.project.network_policy.value, "deny")

    def test_control_characters_and_backslashes_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            title = "tab\there, back\\slash, delete\x7f, bell\x07"
            write_project(Path(temporary) / "paper", project_id="controls", title=title)
            self.assertEqual(load_config(Path(temporary) / "paper" / "review.toml").config.project.title, title)
            with self.assertRaisesRegex(ValueError, "surrogate"):
                write_project(Path(temporary) / "other", project_id="surrogate", title="bad \ud800")

    def test_project_id_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "project id"):
                write_project(Path(temporary) / "paper", project_id="../escape", title="t")

    def test_init_prints_one_json_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code, result = run_cli("init", str(Path(temporary) / "paper"), "--adapter", "agent")
            self.assertEqual(code, 0)
            self.assertTrue(result["drop_path"].endswith("judgment.json"))


class RelatedWorkAwaitingTests(unittest.TestCase):
    def test_agent_search_waits_for_findings_and_lists_its_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manuscript.txt").write_text(
                (EXAMPLES / "related_work" / "manuscript.txt").read_text(encoding="utf-8"), encoding="utf-8"
            )
            (root / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "related-agent"
                    title = "Agent related work"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "related_work"
                    type = "related_work_search"
                    [stages.params]
                    mode = "agent"
                    findings_path = "related_findings.json"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            run_pipeline(root / "review.toml")
            request = run_status(root / "review.toml")["awaiting"][0]
            self.assertEqual(request["kind"], "related_work_findings")
            self.assertTrue(request["queries"])
            result = submit_drop(root / "review.toml", {"candidates": [{"title": "No payload"}]})
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["candidate_count"], 0)
            self.assertTrue(result["warnings"])
            manifest = run_pipeline(root / "review.toml")
            self.assertEqual(manifest.stage("related_work").status, StageStatus.success)


class VisualAwaitingTests(unittest.TestCase):
    def test_agent_inspector_waits_for_findings_and_lists_the_pages(self) -> None:
        try:
            import pypdfium2  # noqa: F401
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("visual and pdf extras are required")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = canvas.Canvas(str(root / "manuscript.pdf"), pagesize=(612, 792))
            document.drawString(72, 400, "Synthetic page")
            document.save()
            (root / "manuscript.txt").write_text("Synthetic page.\n", encoding="utf-8")
            (root / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "visual-agent"
                    title = "Agent visual"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "ingest"
                    type = "ingest"

                    [[stages]]
                    id = "pages"
                    type = "render_pages"
                    depends_on = ["ingest"]
                    [stages.params]
                    dpi = 72

                    [[stages]]
                    id = "inspect"
                    type = "visual_inspect"
                    depends_on = ["pages"]
                    [stages.params]
                    adapter = "autopaperreview.visual_agent:AgentFindingsInspector"
                    findings_path = "findings.json"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            config = root / "review.toml"
            manifest = run_pipeline(config)
            self.assertEqual(manifest.stage("inspect").status, StageStatus.awaiting_input)
            request = run_status(config, detail=True)["awaiting"][0]
            self.assertEqual(request["kind"], "visual_findings")
            page = request["reads"][0]
            self.assertTrue(Path(page["absolute_path"]).is_file())
            self.assertEqual(request["template"]["drop"]["pages_seen"], [page["id"]])
            self.assertEqual(submit_drop(config, {"pages_seen": [], "findings": []})["status"], "rejected")
            wrong_page = {
                "id": "V1",
                "page": 2,
                "page_id": page["id"],
                "bbox": [10, 10, 50, 50],
                "page_image_sha256": page["sha256"],
                "kind": "axis_legend",
                "visible_mark": "a line of text near the middle of the page",
            }
            result = submit_drop(config, {"pages_seen": [page["id"]], "findings": [wrong_page]})
            self.assertEqual(result["status"], "rejected")
            self.assertIn("page does not match the image hash", result["errors"][0])
            result = submit_drop(config, {"pages_seen": [page["id"]], "findings": []})
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(run_pipeline(config).stage("inspect").status, StageStatus.success)


if __name__ == "__main__":
    unittest.main()
