"""Offline CVPR accepted-tier calibration. Fixtures are synthetic."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pydantic import ValidationError

from autopaperreview.anchors import forecast_mean, panel_mean
from autopaperreview.cli import main
from autopaperreview.cvpr_tier import (
    CvprTierError,
    OpenAIChatCompletion,
    SamplePaper,
    build_prompt,
    build_year_catalog,
    compare_models,
    fetch_sample,
    list_catalog,
    model_metrics,
    parse_model_id,
    parse_verdict,
    prepare_manuscripts,
    read_judgment_bytes,
    read_judgment_text,
    render_command,
    run_model,
    stratified_sample,
    strip_cvpr_banner,
    write_report,
)
from autopaperreview.model_exec import HOLISTIC_TASK
from autopaperreview.venues import known_venue_keys, load_venue_deadlines, load_venue_profile

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "cvpr_tier"
CANARY = "CANARY-CVPR-TIER-HELD-OUT-9f3a"
BASES = {
    "cvpr2024-syn-oral-a": 8.0,
    "cvpr2024-syn-oral-b": 7.6,
    "cvpr2025-syn-oral-a": 8.4,
    "cvpr2025-syn-oral-b": 7.4,
    "cvpr2024-syn-high-a": 6.0,
    "cvpr2024-syn-high-b": 6.2,
    "cvpr2024-syn-post-a": 3.0,
    "cvpr2024-syn-post-b": 2.4,
}
LABEL_FILES = {"catalog.json", "heldout/tiers.json"}


def score_for(model: str, paper_id: str) -> tuple[float, str]:
    score = BASES[paper_id]
    if model == "opus-local":
        score = round(10.5 - score, 4)
    elif model == "astra-local":
        score = round(score - 0.2, 4)
    elif model == "grok-4.7-cursor":
        score = round(score, 4)
    else:
        raise AssertionError(model)
    if score >= 7:
        tier = "oral"
    elif score >= 5:
        tier = "highlight"
    else:
        tier = "poster"
    return score, tier


def mean_answer(model: str, paper_id: str) -> str:
    score, tier = score_for(model, paper_id)
    return json.dumps(
        {
            "rationale": "synthetic lattice reading",
            "rating": 4,
            "predicted_mean": score,
            "tier": tier,
        }
    )


def _forecast_review(reviewer: str, rating: int, probabilities: list[float]) -> dict:
    return {
        "reviewer": reviewer,
        "summary": "Synthetic reading of a lattice counter.",
        "strengths": ["The grid is defined."],
        "weaknesses": ["The grid is small."],
        "rating": rating,
        "confidence": 3,
        "probabilities": probabilities,
    }


def _panel_review(reviewer: str, rating: int) -> dict:
    return {
        "reviewer": reviewer,
        "summary": "Synthetic panel note.",
        "strengths": ["Clear grid."],
        "weaknesses": ["Narrow grid."],
        "rating": rating,
        "confidence": 4,
    }


class CatalogTests(unittest.TestCase):
    def test_virtual_json_collapses_events_and_joins_open_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            catalog = list_catalog(
                cache,
                years=(2024, 2025, 2023, 2026),
                fixture_dir=FIXTURE,
            )
            stored = (cache / "catalog.json").read_text(encoding="utf-8")
        by_id = {paper.paper_id: paper for paper in catalog.papers}
        years = {summary.year: summary for summary in catalog.years}

        self.assertEqual(years[2024].label_field, "decision")
        self.assertTrue(years[2024].complete)
        self.assertEqual(years[2024].tiers, {"oral": 2, "highlight": 2, "poster": 2})
        self.assertEqual(years[2024].unlabeled, 3)
        self.assertEqual(by_id["cvpr2024-syn-oral-a"].tier, "oral")
        self.assertEqual(by_id["cvpr2024-syn-high-a"].tier, "highlight")
        self.assertIsNone(by_id["cvpr2024-syn-spot"].tier)
        joined = by_id["cvpr2024-syn-join"]
        self.assertIsNone(joined.tier)
        self.assertEqual(
            joined.pdf_url,
            "https://openaccess.thecvf.com/content/CVPR2024/papers/synthetic_lattice_counter_alpha.pdf",
        )
        self.assertEqual(by_id["cvpr2024-syn-amb"].pdf_url, "")

        self.assertEqual(years[2025].label_field, "eventtype-oral-only")
        self.assertEqual(years[2025].tiers, {"oral": 2, "highlight": 0, "poster": 0})
        self.assertEqual(years[2025].unlabeled, 2)
        self.assertEqual(by_id["cvpr2025-syn-oral-a"].tier, "oral")
        self.assertIsNone(by_id["cvpr2025-syn-post-a"].tier)

        self.assertEqual(years[2023].label_field, "none")
        self.assertEqual(years[2023].unlabeled, 2)
        self.assertEqual(years[2023].tiers["poster"], 0)
        self.assertFalse(years[2026].complete)
        self.assertFalse(any(paper.year == 2026 for paper in catalog.papers))
        self.assertNotIn("Synthetic", stored)
        self.assertNotIn("Inventor", stored)

    def test_a_json_pdf_is_kept_when_the_title_also_joins(self) -> None:
        payload = {
            "count": 1,
            "results": [
                {
                    "uid": "keep",
                    "name": "Synthetic Lattice Counter Alpha",
                    "decision": "Accept: Poster",
                    "eventtype": "Poster",
                    "paper_pdf_url": "https://openaccess.thecvf.com/content/CVPR2024/papers/already_listed.pdf",
                }
            ],
        }
        html = (FIXTURE / "CVPR2024.html").read_text(encoding="utf-8")
        papers, _summary = build_year_catalog(2024, payload, html)
        self.assertEqual(
            papers[0].pdf_url,
            "https://openaccess.thecvf.com/content/CVPR2024/papers/already_listed.pdf",
        )

    def test_stratified_sample_is_stable_and_omits_per_paper_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            catalog = list_catalog(cache, years=(2024, 2025), fixture_dir=FIXTURE)
            first, first_draws = stratified_sample(catalog.papers, {"oral": 1, "highlight": 1, "poster": 1}, 20261029)
            second, _second_draws = stratified_sample(catalog.papers, {"oral": 1, "highlight": 1, "poster": 1}, 20261029)
            self.assertEqual([paper.paper_id for paper in first], [paper.paper_id for paper in second])
            wide, wide_draws = stratified_sample(catalog.papers, {"oral": 20, "highlight": 20, "poster": 20}, 7)
            self.assertEqual(wide_draws["oral"], {"available": 4, "requested": 20, "drawn": 4})
            self.assertEqual(wide_draws["highlight"]["drawn"], 2)
            self.assertEqual(wide_draws["poster"]["drawn"], 2)
            self.assertEqual(first_draws["oral"]["drawn"], 1)
            manifest = fetch_sample(cache, n_per_tier=1, seed=20261029, fixture_dir=FIXTURE)
            dumped = json.loads((cache / "sample_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest.seed, 20261029)
            for paper in dumped["papers"]:
                self.assertNotIn("tier", paper)
            with self.assertRaises(ValidationError):
                SamplePaper.model_validate(
                    {"paper_id": "cvpr2024-syn-oral-a", "year": 2024, "pdf_url": "fixture://x", "tier": "oral"}
                )


class IsolationTests(unittest.TestCase):
    def test_prepare_and_run_do_not_read_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            list_catalog(cache, years=(2024, 2025), fixture_dir=FIXTURE)
            fetch_sample(cache, n_per_tier=1, seed=20261029, fixture_dir=FIXTURE)
            self._plant(cache)
            self._guard(cache, lambda: prepare_manuscripts(cache))
            for path in (cache / "manuscripts").glob("*.txt"):
                text = path.read_text(encoding="utf-8")
                self.assertIn("poster child", text)
                self.assertNotIn(CANARY, text)
                self.assertNotIn("Oral presentation", text)
                self.assertNotIn("open access version", text.lower())
            result = self._guard(
                cache,
                lambda: run_model(cache, model_id="grok-4.7-cursor", backend="replay", holistic_mode="mean"),
            )
            self.assertEqual(result["backend"], "replay")
            self.assertTrue(result["awaiting_judgment"])
            handoff = Path(result["handoff_dir"])
            prompts = list(handoff.glob("*/prompt.txt"))
            self.assertTrue(prompts)
            for path in prompts:
                self.assertRegex(path.parent.name, r"^[0-9a-f]{12}$")
                text = path.read_text(encoding="utf-8")
                self.assertIn("Do not add a second score", text)
                self.assertIn("poster child", text)
                self.assertNotIn(CANARY, text)
                packet = json.loads((path.parent / "packet.json").read_text(encoding="utf-8"))
                self.assertNotIn("paper_id", packet)
                self.assertNotIn("cvpr", json.dumps(packet).lower())
            with self.assertRaises(CvprTierError):
                read_judgment_text(cache, cache / "heldout" / "tiers.json")
            with self.assertRaises(CvprTierError):
                read_judgment_bytes(cache, cache / "catalog.json")

    def _plant(self, cache: Path) -> None:
        catalog_path = cache / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["years"][0]["notes"] += " " + CANARY
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        held_path = cache / "heldout" / "tiers.json"
        held = json.loads(held_path.read_text(encoding="utf-8"))
        held["papers"]["canary-row"] = CANARY
        held_path.write_text(json.dumps(held), encoding="utf-8")
        self.assertIn(CANARY, catalog_path.read_text(encoding="utf-8"))
        self.assertIn(CANARY, held_path.read_text(encoding="utf-8"))

    def _guard(self, cache: Path, fn):
        opened: list[Path] = []
        original_text = Path.read_text
        original_bytes = Path.read_bytes

        def wrap_text(path: Path, *args, **kwargs):
            opened.append(Path(path))
            return original_text(path, *args, **kwargs)

        def wrap_bytes(path: Path, *args, **kwargs):
            opened.append(Path(path))
            return original_bytes(path, *args, **kwargs)

        locked: list[Path] = []
        if os.geteuid() != 0:
            for relative in LABEL_FILES:
                path = cache / relative
                os.chmod(path, 0)
                locked.append(path)
        Path.read_text = wrap_text  # type: ignore[method-assign]
        Path.read_bytes = wrap_bytes  # type: ignore[method-assign]
        try:
            value = fn()
        finally:
            Path.read_text = original_text  # type: ignore[method-assign]
            Path.read_bytes = original_bytes  # type: ignore[method-assign]
            for path in locked:
                os.chmod(path, 0o644)
        root = cache.resolve()
        for path in opened:
            try:
                relative = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            self.assertNotIn(relative, LABEL_FILES)
        return value


class VerdictTests(unittest.TestCase):
    def test_forecast_and_panel_use_their_own_ranks(self) -> None:
        forecast = {
            "tier": "highlight",
            "reviews": [
                _forecast_review("specialist", 10, [0, 0, 0, 0, 0, 1]),
                _forecast_review("generalist", 1, [1, 0, 0, 0, 0, 0]),
                _forecast_review("senior", 5, [0, 0, 1, 0, 0, 0]),
            ],
            "discussion": "Synthetic disagreement.",
            "predicted_mean": 9.0,
            "rating": 5,
        }
        other = {
            "reviews": [
                _forecast_review("specialist", 10, [0, 0, 0, 0, 0, 1]),
                _forecast_review("generalist", 10, [0, 0, 0, 0, 0, 1]),
                _forecast_review("senior", 10, [0, 0, 0, 0, 0, 1]),
            ],
            "discussion": "Synthetic agreement.",
            "predicted_mean": 1.0,
            "rating": 6,
        }
        parsed = parse_verdict(
            "forecast",
            json.dumps({"tier": "oral", "samples": [forecast, other]}),
            samples=2,
        )
        expected = forecast_mean([10.0, 1.0, 5.0])
        self.assertEqual(parsed["rank_source"], "forecast_mean")
        self.assertEqual(parsed["score"], expected)
        self.assertNotEqual(parsed["score"], 9.0)
        self.assertEqual(parsed["rank_samples_used"], 1)
        self.assertEqual(parsed["predicted_tier"], "oral")

        panel = {
            "tier": "poster",
            "reviews": [
                _panel_review("specialist", 10),
                _panel_review("generalist", 8),
                _panel_review("methodologist", 6),
                _panel_review("senior", 3),
            ],
            "discussion": "Synthetic panel.",
            "predicted_mean": 2.0,
            "rating": 3,
        }
        panel_parsed = parse_verdict("panel", json.dumps(panel))
        self.assertEqual(panel_parsed["rank_source"], "panel_mean")
        self.assertEqual(panel_parsed["score"], panel_mean([10, 8, 6, 3]))
        self.assertNotEqual(panel_parsed["score"], 2.0)

        rebuttal = {
            "tier": "Highlight",
            "reviews": [
                _panel_review("specialist", 5),
                _panel_review("generalist", 6),
                _panel_review("senior", 8),
            ],
            "response": "The authors add a second grid.",
            "updates": [
                {"reviewer": "specialist", "change": "kept", "final_rating": 8},
                {"reviewer": "generalist", "change": "kept", "final_rating": 6},
                {"reviewer": "senior", "change": "lowered", "final_rating": 3},
            ],
            "discussion": "Synthetic discussion.",
            "predicted_mean": 9.0,
            "rating": 5,
        }
        rebuttal_parsed = parse_verdict("rebuttal", json.dumps(rebuttal))
        self.assertEqual(rebuttal_parsed["rank_source"], "final_rating_mean")
        self.assertEqual(rebuttal_parsed["score"], panel_mean([8, 6, 3], size=3))
        self.assertEqual(rebuttal_parsed["predicted_tier"], "highlight")
        with self.assertRaises(CvprTierError):
            parse_verdict("mean", json.dumps({"tier": "spotlight", "rationale": "x", "rating": 4, "predicted_mean": 5}))
        with self.assertRaises(CvprTierError):
            parse_verdict("mean", json.dumps({"tier": "weak_accept", "rationale": "x", "rating": 4, "predicted_mean": 5}))

    def test_banner_strip_keeps_a_scientific_sentence(self) -> None:
        text = strip_cvpr_banner(
            "\n".join(
                [
                    "Accepted to CVPR",
                    "CVPR 2024",
                    "Oral presentation",
                    "Highlight paper",
                    "This is the open access version of a synthetic note.",
                    "The method is not a poster child for the claim.",
                    "A poster of the lattice still describes the method.",
                ]
            )
        )
        self.assertIn("poster child", text)
        self.assertIn("A poster of the lattice still describes the method.", text)
        self.assertNotIn("Accepted to CVPR", text)
        self.assertNotIn("CVPR 2024", text)
        self.assertNotIn("Oral presentation", text)
        self.assertNotIn("open access version", text.lower())

    def test_prompt_uses_the_mode_task_and_leaves_the_holistic_task_alone(self) -> None:
        prompt = build_prompt("Synthetic lattice counter crosses eight cells.\n", "forecast", samples=1)
        self.assertIn("probabilities", prompt)
        self.assertIn("reject < poster < highlight < oral", prompt)
        self.assertNotIn("presentation tier", HOLISTIC_TASK.lower())


class CommandTests(unittest.TestCase):
    def test_template_is_argv_and_free_form_labels_are_path_safe(self) -> None:
        values = {
            "prompt_file": "/tmp/my paper.txt",
            "answer_file": "/tmp/out.json",
            "model": "opus-local",
            "paper_id": "cvpr2024-syn-oral-a",
            "mode": "forecast",
        }
        argv = render_command(
            "echo hi; touch {prompt_file} {answer_file} {model} {paper_id} {mode}",
            values,
        )
        self.assertEqual(argv[:2], ["echo", "hi;"])
        self.assertIn("/tmp/my paper.txt", argv)
        self.assertIn("opus-local", argv)
        for label in ("grok-4.7-cursor", "opus-local", "astra-local"):
            self.assertEqual(parse_model_id(label), label)
        with self.assertRaises(CvprTierError):
            parse_model_id("Astra Local")
        with self.assertRaises(CvprTierError):
            render_command("curl --header Authorization: Bearer sk-local {prompt_file}", values)
        with self.assertRaises(CvprTierError):
            render_command("tool --api_key {prompt_file}", values)
        with self.assertRaises(CvprTierError):
            render_command("", values)

    def test_command_timeout_names_the_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            list_catalog(cache, years=(2024,), fixture_dir=FIXTURE)
            fetch_sample(cache, n_per_tier={"oral": 1, "highlight": 0, "poster": 0}, seed=1, fixture_dir=FIXTURE)
            prepare_manuscripts(cache)
            with self.assertRaises(CvprTierError) as caught:
                run_model(
                    cache,
                    model_id="opus-local",
                    backend="command",
                    holistic_mode="mean",
                    command_template=f"{sys.executable} -c \"import time; time.sleep(5)\"",
                    command_timeout=0.2,
                )
        self.assertIn("timed out", str(caught.exception))


class ReportTests(unittest.TestCase):
    def test_metrics_and_two_model_agreement(self) -> None:
        rows = [
            {"paper_id": "p1", "score": 1.5, "predicted_tier": "reject", "true_tier": "poster"},
            {"paper_id": "p2", "score": 3.0, "predicted_tier": "poster", "true_tier": "poster"},
            {"paper_id": "h1", "score": 6.0, "predicted_tier": "highlight", "true_tier": "highlight"},
            {"paper_id": "o1", "score": 8.0, "predicted_tier": "oral", "true_tier": "oral"},
        ]
        metrics = model_metrics(rows, resamples=32, seed=20261029)
        self.assertEqual(metrics["n"], 4)
        self.assertEqual(metrics["confusion"]["poster"]["reject"], 1)
        self.assertEqual(metrics["confusion"]["poster"]["poster"], 1)
        self.assertEqual(metrics["pairwise_auc"]["oral_vs_poster"]["point"], 1.0)
        self.assertGreater(metrics["spearman"]["point"], 0.9)
        self.assertFalse(metrics["spread"]["clustered"])
        flat = [{**row, "score": 5.0} for row in rows]
        self.assertTrue(model_metrics(flat, resamples=8, seed=1)["spread"]["clustered"])
        same = compare_models(rows, rows, resamples=16, seed=1)
        self.assertEqual(same["tier_agreement"]["point"], 1.0)
        self.assertAlmostEqual(same["score_spearman"]["point"], 1.0)
        flipped = [{**row, "score": 10.0 - row["score"]} for row in rows]
        opposite = compare_models(rows, flipped, resamples=16, seed=1)
        self.assertAlmostEqual(opposite["score_spearman"]["point"], -1.0)

    def test_three_labels_share_a_sample_and_keep_their_own_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            list_catalog(cache, years=(2024, 2025), fixture_dir=FIXTURE)
            fetch_sample(cache, n_per_tier=1, seed=20261029, fixture_dir=FIXTURE)
            prepare_manuscripts(cache)
            catalog = json.loads((cache / "catalog.json").read_text(encoding="utf-8"))
            catalog["years"][0]["notes"] += " " + CANARY
            (cache / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
            held_path = cache / "heldout" / "tiers.json"
            held = json.loads(held_path.read_text(encoding="utf-8"))
            held["papers"]["canary-row"] = CANARY
            held_path.write_text(json.dumps(held), encoding="utf-8")
            script = cache / "answer_cli.py"
            script.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                f"BASES = {json.dumps(BASES)}\n"
                "prompt, answer, model, paper, mode = sys.argv[1:6]\n"
                "joined = ' '.join(sys.argv)\n"
                "if 'cvpr20' in joined.lower():\n"
                "    raise SystemExit('catalog id in argv')\n"
                "text = Path(prompt).read_text(encoding='utf-8')\n"
                f"if {CANARY!r} in text:\n"
                "    raise SystemExit('label leaked')\n"
                "matched = [key for key in BASES if key in text]\n"
                "if len(matched) != 1:\n"
                "    raise SystemExit('paper id not in prompt')\n"
                "paper = matched[0]\n"
                "score = BASES[paper]\n"
                "if model == 'opus-local':\n"
                "    score = round(10.5 - score, 4)\n"
                "elif model == 'astra-local':\n"
                "    score = round(score - 0.2, 4)\n"
                "elif model != 'grok-4.7-cursor':\n"
                "    raise SystemExit('unexpected model ' + model)\n"
                "tier = 'oral' if score >= 7 else 'highlight' if score >= 5 else 'poster'\n"
                "Path(answer).write_text(json.dumps({\n"
                "    'rationale': 'synthetic lattice reading',\n"
                "    'rating': 4,\n"
                "    'predicted_mean': score,\n"
                "    'tier': tier,\n"
                "}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            opus_template = f"{sys.executable} {script} {{prompt_file}} {{answer_file}} {{model}} {{paper_id}} {{mode}}"
            astra_template = (
                f"{sys.executable} -I {script} {{prompt_file}} {{answer_file}} {{model}} {{paper_id}} {{mode}}"
            )
            self.assertNotEqual(opus_template, astra_template)
            run_model(
                cache,
                model_id="opus-local",
                backend="command",
                holistic_mode="mean",
                command_template=opus_template,
            )
            run_model(
                cache,
                model_id="astra-local",
                backend="command",
                holistic_mode="mean",
                command_template=astra_template,
            )
            waiting = run_model(cache, model_id="grok-4.7-cursor", backend="replay", holistic_mode="mean")
            self.assertTrue(waiting["awaiting_judgment"])
            manifest = json.loads((cache / "sample_manifest.json").read_text(encoding="utf-8"))
            handoff = Path(waiting["handoff_dir"])
            grok_manifest = json.loads(
                (cache / "runs" / "grok-4.7-cursor" / "manifest.json").read_text(encoding="utf-8")
            )
            for paper in manifest["papers"]:
                paper_id = paper["paper_id"]
                hid = grok_manifest["handoff_ids"][paper_id]
                self.assertRegex(hid, r"^[0-9a-f]{12}$")
                prompt = (handoff / hid / "prompt.txt").read_text(encoding="utf-8")
                self.assertNotIn(CANARY, prompt)
                (handoff / hid / "answer.json").write_text(
                    mean_answer("grok-4.7-cursor", paper_id), encoding="utf-8"
                )
            finished = run_model(cache, model_id="grok-4.7-cursor", backend="replay", holistic_mode="mean")
            self.assertEqual(finished["awaiting_judgment"], [])
            self.assertEqual(finished["errors"], [])
            for label, template in (("opus-local", opus_template), ("astra-local", astra_template)):
                stored = json.loads((cache / "runs" / label / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["model_id"], label)
                self.assertEqual(stored["backend"], "command")
                self.assertEqual(stored["params"]["command_template"], template)
                self.assertIsNone(stored["params"]["api_key_env"])
            grok = json.loads((cache / "runs" / "grok-4.7-cursor" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(grok["backend"], "replay")
            self.assertIsNone(grok["params"]["command_template"])
            for label in ("grok-4.7-cursor", "opus-local", "astra-local"):
                for path in (cache / "runs" / label / "parsed").glob("*.json"):
                    row = json.loads(path.read_text(encoding="utf-8"))
                    self.assertNotIn("true_tier", row)
                    self.assertNotIn("tier", row)
                    self.assertEqual(row["rank_source"], "predicted_mean")
                    score, tier = score_for(label, row["paper_id"])
                    self.assertEqual(row["score"], score)
                    self.assertEqual(row["predicted_tier"], tier)
            output = cache / "cvpr_tier_report.md"
            payload = write_report(cache, output, resamples=40, seed=20261029)
            text = output.read_text(encoding="utf-8")
            self.assertIn("grok-4.7-cursor", text)
            self.assertIn("opus-local", text)
            self.assertIn("astra-local", text)
            self.assertIn("Report only", text)
            self.assertIn("Accepted papers only", text)
            sibling = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(sibling["models"].keys(), payload["models"].keys())
            grok_metrics = payload["models"]["grok-4.7-cursor"]["metrics"]
            opus_metrics = payload["models"]["opus-local"]["metrics"]
            self.assertEqual(grok_metrics["n"], 3)
            self.assertAlmostEqual(grok_metrics["spearman"]["point"], 1.0)
            self.assertAlmostEqual(opus_metrics["spearman"]["point"], -1.0)
            self.assertEqual(grok_metrics["confusion"]["oral"]["oral"], 1)
            self.assertEqual(grok_metrics["confusion"]["poster"]["poster"], 1)
            pairs = list(payload["comparisons"].values())

            def pair(left: str, right: str) -> dict:
                for body in pairs:
                    if {body["left"], body["right"]} == {left, right}:
                        return body
                self.fail(f"missing comparison {left} {right}")

            grok_astra = pair("grok-4.7-cursor", "astra-local")
            grok_opus = pair("grok-4.7-cursor", "opus-local")
            self.assertAlmostEqual(grok_astra["score_spearman"]["point"], 1.0)
            self.assertAlmostEqual(grok_astra["tier_agreement"]["point"], 1.0)
            self.assertAlmostEqual(grok_opus["score_spearman"]["point"], -1.0)
            self.assertAlmostEqual(grok_opus["tier_agreement"]["point"], 0.0)


class AdapterTests(unittest.TestCase):
    def test_completion_does_not_store_the_api_key(self) -> None:
        token = "fixture-token-9f3ac0"
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            list_catalog(cache, years=(2024,), fixture_dir=FIXTURE)
            fetch_sample(
                cache,
                n_per_tier={"oral": 1, "highlight": 0, "poster": 0},
                seed=3,
                fixture_dir=FIXTURE,
            )
            prepare_manuscripts(cache)
            captured: list = []

            def transport(request, timeout):
                captured.append(request)
                content = json.dumps(
                    {
                        "rationale": "synthetic lattice reading",
                        "rating": 4,
                        "predicted_mean": 6.5,
                        "tier": "highlight",
                    }
                )
                return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

            completion = OpenAIChatCompletion(
                base_url="http://127.0.0.1:9/v1",
                model="fixture-endpoint",
                api_key_env="APR_MODEL_API_KEY",
                temperature=0.25,
                transport=transport,
            )
            previous = os.environ.get("APR_MODEL_API_KEY")
            os.environ["APR_MODEL_API_KEY"] = token
            try:
                result = run_model(
                    cache,
                    model_id="grok-4.7-cursor",
                    backend="adapter",
                    holistic_mode="mean",
                    endpoint_model="fixture-endpoint",
                    base_url="http://127.0.0.1:9/v1",
                    temperature=0.25,
                    completion=completion,
                )
            finally:
                if previous is None:
                    os.environ.pop("APR_MODEL_API_KEY", None)
                else:
                    os.environ["APR_MODEL_API_KEY"] = previous
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["parsed"], result["parsed"])
            self.assertTrue(captured)
            self.assertIn(token, captured[0].get_header("Authorization"))
            self.assertIn(b"0.25", captured[0].data)
            run_dir = cache / "runs" / "grok-4.7-cursor"
            blob = b""
            for path in run_dir.rglob("*"):
                if path.is_file():
                    blob += path.read_bytes()
            self.assertNotIn(token.encode("utf-8"), blob)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["params"]["api_key_env"], "APR_MODEL_API_KEY")
            self.assertEqual(manifest["params"]["temperature"], 0.25)
            self.assertNotIn(token, json.dumps(manifest))


class VenueTests(unittest.TestCase):
    def test_cvpr_profiles_pin_dates_and_eight_content_pages(self) -> None:
        self.assertIn("cvpr2025", known_venue_keys())
        self.assertIn("cvpr2026", known_venue_keys())
        cvpr2025 = load_venue_deadlines("cvpr2025")
        self.assertEqual(cvpr2025.abstract_deadline.isoformat(), "2024-11-08")
        self.assertEqual(cvpr2025.submission_deadline.isoformat(), "2024-11-15")
        self.assertEqual(cvpr2025.deadline_timezone, "UTC")
        self.assertIn("thecvf.com", cvpr2025.source)
        profile = load_venue_profile("cvpr2025")
        self.assertEqual(profile["max_content_pages"], 8)
        self.assertNotIn("max_total_pages", profile)
        self.assertEqual(profile["submission_deadline"], "2024-11-15")
        self.assertEqual(profile["template_markers"], ["cvpr"])
        self.assertTrue(profile["page_limit_fatal"])
        self.assertTrue(profile["anonymity_metadata_fatal"])
        cvpr2026 = load_venue_deadlines("cvpr2026")
        self.assertEqual(cvpr2026.abstract_deadline.isoformat(), "2025-11-07")
        self.assertEqual(cvpr2026.submission_deadline.isoformat(), "2025-11-13")
        self.assertEqual(cvpr2026.deadline_timezone, "Anywhere on Earth")


class CliTests(unittest.TestCase):
    def test_cvpr_tier_commands_print_counts_and_replay_waits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "list",
                        "--cache",
                        str(cache),
                        "--fixture",
                        str(FIXTURE),
                        "--years",
                        "2024,2025,2023,2026",
                    ]
                )
            self.assertEqual(code, 0)
            listed = stdout.getvalue()
            self.assertNotIn("Synthetic", listed)
            self.assertNotIn("fixture://", listed)
            self.assertIn("\"oral\": 2", listed)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "fetch",
                        "--cache",
                        str(cache),
                        "--fixture",
                        str(FIXTURE),
                        "--n-per-tier",
                        "1",
                        "--seed",
                        "20261029",
                    ]
                )
            self.assertEqual(code, 0)
            fetched = stdout.getvalue()
            self.assertNotIn("syn-oral", fetched)
            self.assertNotIn("fixture://", fetched)
            self.assertIn("paper_count", fetched)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                prepared = main(["calibrate-eval", "cvpr-tier", "prepare", "--cache", str(cache)])
            self.assertEqual(prepared, 0)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                bad = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "run",
                        "--cache",
                        str(cache),
                        "--model-id",
                        "Astra Local",
                        "--backend",
                        "replay",
                    ]
                )
            self.assertEqual(bad, 2)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                waiting = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "run",
                        "--cache",
                        str(cache),
                        "--model-id",
                        "grok-4.7-cursor",
                        "--backend",
                        "replay",
                        "--holistic-mode",
                        "mean",
                    ]
                )
            self.assertEqual(waiting, 3)
            waiting_body = json.loads(stdout.getvalue())
            self.assertTrue(waiting_body["awaiting_judgment"])
            hid = waiting_body["awaiting_judgment"][0]
            self.assertRegex(hid, r"^[0-9a-f]{12}$")
            answer = Path(waiting_body["handoff_dir"]) / hid / "answer.json"
            answer.write_text("{not json", encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                mixed = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "run",
                        "--cache",
                        str(cache),
                        "--model-id",
                        "grok-4.7-cursor",
                        "--backend",
                        "replay",
                        "--holistic-mode",
                        "mean",
                    ]
                )
            self.assertEqual(mixed, 3)
            body = json.loads(stdout.getvalue())
            self.assertTrue(body["awaiting_judgment"])
            self.assertTrue(body["errors"])
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                missing = main(
                    [
                        "calibrate-eval",
                        "cvpr-tier",
                        "run",
                        "--cache",
                        str(cache),
                        "--model-id",
                        "opus-local",
                        "--backend",
                        "command",
                        "--holistic-mode",
                        "mean",
                    ]
                )
            self.assertEqual(missing, 2)
            self.assertIn("command template", stderr.getvalue())
