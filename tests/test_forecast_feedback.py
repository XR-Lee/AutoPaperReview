"""Synthetic regressions for the optional forecast feedback artifact."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopaperreview.cli import build_parser
from autopaperreview.forecast_feedback import GROUNDED_WEAKNESSES, locate_weakness, write_forecast_feedback
from autopaperreview.model_exec import ForecastJudgment, OpenAICompatibleAdapter
from autopaperreview.public_calibration import MODEL_ADAPTER
from tests.test_model_exec import _chat, _forecast_content


class ForecastFeedbackTests(unittest.TestCase):
    def test_locator_requires_one_visible_contiguous_source_span(self):
        source = "Setup.\n18 clay markers are\ncounted. Setup.\nAppendix omitted."
        shown = source[:source.index("Appendix")]
        comment = "Describe the counting rule."
        located = locate_weakness(f"{comment} <quote>18 clay markers are counted</quote>", source, shown)
        self.assertIsNotNone(located)
        text, start, end = located
        self.assertEqual(text, comment)
        self.assertEqual(source[start:end], "18 clay markers are\ncounted")
        for excerpt in ("8 clay markers", "17 clay markers", "Setup.", "Appendix omitted.", " ", "are ... counted"):
            with self.subTest(excerpt=excerpt):
                self.assertIsNone(locate_weakness(f"{comment} <quote>{excerpt}</quote>", source, shown))
        self.assertIsNone(locate_weakness(comment, source, shown))

    def test_a_diagnosis_without_a_quote_is_kept_and_a_bad_quote_is_not(self):
        source = "Setup.\n18 clay markers are counted.\n"
        shown = source
        payload = json.loads(_forecast_content(
            ratings=(5, 5, 5),
            probabilities=([0, 0, 1, 0, 0, 0],) * 3,
            predicted_mean=5,
            rating=3,
        ))
        payload["reviews"][0]["weaknesses"] = [
            "The count is not defined.",
            "Name the counting rule. <quote>18 clay markers are counted</quote>",
        ]
        payload["reviews"][1]["weaknesses"] = [
            "This quote is not in the manuscript. <quote>a second tray is red</quote>",
        ]
        payload["reviews"][2]["weaknesses"] = ["The batch is a single tray."]
        panel = ForecastJudgment.model_validate(payload)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forecast-feedback.txt"
            dropped = write_forecast_feedback(path, [panel], source, shown)
            text = path.read_text(encoding="utf-8")
        self.assertIn("The count is not defined.", text)
        self.assertIn("The batch is a single tray.", text)
        self.assertIn("Name the counting rule.", text)
        self.assertIn("18 clay markers are counted", text)
        self.assertNotIn("a second tray is red", text)
        self.assertEqual(dropped, ["P1-R2-W1"])
        self.assertIn("Located 1 of 4 submitted comments in the manuscript.", text)
        self.assertIn("last weakness only", GROUNDED_WEAKNESSES)
        self.assertNotIn("End the same string with <quote>one short distinctive passage copied exactly from the manuscript</quote> so the author", GROUNDED_WEAKNESSES)

    def test_cli_publishes_located_comments_in_one_call_and_keeps_text_out_of_scores(self):
        calls = []
        valid = "Specify whether the bent tokens are counted separately."
        unlocated = "This comment carries an invented excerpt."
        visible = "Synthetic inventory\nWe count eighteen bent clay tokens on one tray.\n"
        source = visible + "At the back, the reference list names the supplier.\n"
        marker_only = "Discuss the supplier."
        excerpt = "eighteen bent clay tokens"

        def answer(request, timeout):
            calls.append(json.loads(request.data)["messages"])
            content = json.loads(_forecast_content(
                ratings=(5, 5, 5),
                probabilities=([0, 0, 1, 0, 0, 0],) * 3,
                predicted_mean=5,
                rating=3,
            ))
            content["reviews"][0]["weaknesses"] = [f"{valid} <quote>{excerpt}</quote>"]
            content["reviews"][1]["weaknesses"] = [f"{unlocated} <quote>A second tray is red.</quote>"]
            content["reviews"][2]["weaknesses"] = [f"{marker_only} <quote>the reference list</quote>"]
            return _chat(json.dumps(content))

        with tempfile.TemporaryDirectory() as tmp, patch.object(OpenAICompatibleAdapter, "transport", answer), patch(
            "autopaperreview.model_exec.FULL_TEXT_MAX_CHARS", len(visible)
        ):
            root = Path(tmp)
            paper = root / "papers" / "synthetic-feedback"
            paper.mkdir(parents=True)
            (paper / "manuscript.txt").write_text(source, encoding="utf-8")
            (paper / "status.json").write_text(
                json.dumps({"forum_id": paper.name, "state": "prepared"}), encoding="utf-8"
            )
            arguments = [
                "calibrate-eval", "run", "--cache", str(root), "--mode", "adapter",
                "--adapter", MODEL_ADAPTER, "--cheap", "--coefficient-set", "apr-fusion-v1",
                "--model", "synthetic-reviewer", "--base-url", "http://127.0.0.1:1/v1",
                "--api-key-env", "APR_SYNTHETIC_UNUSED",
            ]
            parser = build_parser()
            project = paper / "project"
            with patch("autopaperreview.cli._print_json") as printer:
                args = parser.parse_args(arguments)
                self.assertEqual(args.func(args), 0, printer.call_args)
                baseline = json.loads((project / "our_score.json").read_text(encoding="utf-8"))
                self.assertNotIn("grounded_weaknesses", baseline)
                self.assertEqual(list(project.rglob("forecast-feedback.txt")), [])
                args = parser.parse_args([
                    *arguments, "--grounded-weaknesses", "--fixed-set", "iclr-dev",
                    "--model-id", "synthetic-reviewer", "--effort", "xhigh",
                ])
                manifest = {"status": "locked", "content_sha256": "a" * 64}
                with patch("autopaperreview.fixed_eval.assert_manifest", return_value=manifest), patch(
                    "autopaperreview.fixed_eval.dev_ids", return_value=[paper.name]
                ):
                    self.assertEqual(args.func(args), 0, printer.call_args)
                stamp = json.loads((root / "fixed-eval-run.json").read_text(encoding="utf-8"))
                self.assertTrue(stamp["grounded_weaknesses"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][0], calls[1][0])
            self.assertNotIn(GROUNDED_WEAKNESSES, calls[0][1]["content"])
            self.assertIn(GROUNDED_WEAKNESSES, calls[1][1]["content"])
            self.assertIn("The manuscript text is cut here", calls[1][1]["content"])
            [report] = project.glob(".runs/*/stages/review/model/forecast-feedback.txt")
            feedback = report.read_text(encoding="utf-8")
            self.assertIn(valid, feedback)
            self.assertIn(excerpt, feedback)
            self.assertIn("line 2", feedback)
            self.assertNotIn(unlocated, feedback)
            self.assertNotIn(marker_only, feedback)
            self.assertIn("Located 1 of 3", feedback)
            serialized = (project / "our_score.json").read_text(encoding="utf-8")
            self.assertNotIn(valid, serialized)
            self.assertNotIn(excerpt, serialized)
            score = json.loads(serialized)
            self.assertTrue(score["grounded_weaknesses"])
            self.assertEqual(score["drop_counts"]["forecast_weakness_excerpt"], 2)
            self.assertEqual(score["rank_score"], baseline["rank_score"])
