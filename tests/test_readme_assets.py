"""The README quotes the holdout aggregate, and the showcase images are synthetic files."""

from __future__ import annotations

import json
import re
import unittest
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = ROOT / "docs" / "assets" / "holdout15-aggregate.json"
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
HAN = re.compile(r"[\u4e00-\u9fff]")
PNGS = (
    "docs/assets/hero.png",
    "docs/assets/holdout-ranking.png",
    "docs/assets/annotated-preview.png",
    "docs/assets/forecast-panel.png",
    "docs/assets/holdout-glance.png",
)
PNGS_ZH = (
    "docs/assets/hero.zh-CN.png",
    "docs/assets/holdout-ranking.zh-CN.png",
    "docs/assets/forecast-panel.zh-CN.png",
    "docs/assets/holdout-glance.zh-CN.png",
)


def r3(value: float) -> str:
    quantized = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return f"{quantized:.3f}"


class ReadmeAssetTests(unittest.TestCase):
    def test_aggregate_has_no_paper_identifiers(self) -> None:
        text = AGGREGATE.read_text(encoding="utf-8")
        self.assertNotIn("openreview.net", text.lower())
        self.assertNotIn("forum", text.lower())
        payload = json.loads(text)
        self.assertNotIn("r13_twin_k2_holdout_opus55_not_shipped", json.dumps(payload))
        opus = payload["APR_main_r11_forecast_k1"]["holdout_opus55"]
        self.assertEqual(opus["n"], 14)
        self.assertAlmostEqual(opus["spearman"], 0.8505)

    def test_readme_quotes_rounded_aggregate(self) -> None:
        payload = json.loads(AGGREGATE.read_text(encoding="utf-8"))
        readme = README.read_text(encoding="utf-8").replace("−", "-")
        opus = payload["APR_main_r11_forecast_k1"]["holdout_opus55"]
        grok = payload["APR_main_r11_forecast_k1"]["holdout_grok47"]
        judge = payload["openjudge_opus55"]["point"]
        self.assertIn(r3(opus["spearman"]), readme)
        self.assertIn(r3(opus["spearman_ci"][0]), readme)
        self.assertIn(r3(opus["spearman_ci"][1]), readme)
        self.assertIn(r3(grok["spearman"]), readme)
        self.assertIn(r3(judge["spearman"]), readme)
        self.assertIn(r3(judge["auc"]), readme)
        self.assertIn("forecast-k1", readme)
        self.assertIn("| 14 |", readme)
        self.assertNotIn("openreview.net/forum", readme.lower())

    def test_astra_full_holdout_is_quoted_and_matched14_is_not(self) -> None:
        payload = json.loads(AGGREGATE.read_text(encoding="utf-8"))
        apr = payload["APR_main_r11_forecast_k1"]["holdout_astra"]
        judge = payload["openjudge_astra"]
        self.assertEqual(apr["n"], 15)
        self.assertEqual(judge["n"], 15)
        self.assertEqual(apr["model_as_recorded"], "GPT-6 Astra")
        text = AGGREGATE.read_text(encoding="utf-8").lower()
        self.assertNotIn("forum", text)
        self.assertNotIn("openreview.net", text)
        for note in (apr["note"], judge["note"], payload["matched14_not_plotted"]["note"]):
            lowered = note.lower()
            self.assertNotIn("forum", lowered)
            self.assertNotIn("openreview.net", lowered)
        matched = payload["matched14_not_plotted"]
        self.assertEqual(matched["apr_gpt6_astra"]["n"], 14)
        english = README.read_text(encoding="utf-8")
        chinese = README_ZH.read_text(encoding="utf-8")
        quoted = [
            r3(apr["spearman"]),
            r3(apr["spearman_ci"][0]),
            r3(apr["spearman_ci"][1]),
            r3(apr["auc"]),
            r3(apr["auc_ci"][0]),
            r3(apr["auc_ci"][1]),
            r3(judge["point"]["spearman"]),
            r3(judge["ci95"]["spearman"][0]),
            r3(judge["ci95"]["spearman"][1]),
            r3(judge["point"]["auc"]),
            r3(judge["ci95"]["auc"][0]),
            r3(judge["ci95"]["auc"][1]),
            "GPT-6 Astra",
        ]
        for token in quoted:
            self.assertIn(token, english)
            self.assertIn(token, chinese)
        # The shared 14-paper subset is archived, not substituted for the full holdout.
        for hidden in (r3(matched["apr_gpt6_astra"]["spearman"]), r3(matched["openjudge_opus55"]["spearman"])):
            self.assertNotIn(hidden, english)
            self.assertNotIn(hidden, chinese)

    def test_showcase_pngs_are_allowlisted_images(self) -> None:
        allow = (ROOT / "scripts" / "confidentiality_allowlist.txt").read_text(encoding="utf-8")
        for relative in PNGS:
            self.assertIn(relative, allow)
            blob = (ROOT / relative).read_bytes()
            self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"), relative)
            self.assertGreater(len(blob), 10_000, relative)
        svg = (ROOT / "docs" / "assets" / "holdout-ranking.svg").read_text(encoding="utf-8")
        self.assertIn(r3(0.8505), svg)
        self.assertIn("GPT-6 Astra", svg)
        self.assertIn(r3(0.706), svg)
        self.assertNotIn("openreview.net", svg.lower())
        self.assertIsNone(HAN.search(svg))
        renderer = (ROOT / "scripts" / "render_readme_figures.py").read_text(encoding="utf-8")
        self.assertNotIn("ellipse", renderer)

    def test_english_readme_has_no_han_and_links_to_chinese(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIsNone(HAN.search(readme))
        self.assertTrue(readme.startswith("English | [Simplified Chinese](README.zh-CN.md)\n"))
        for relative in PNGS_ZH:
            self.assertNotIn(relative, readme)

    def test_chinese_readme_keeps_the_same_numbers(self) -> None:
        english = README.read_text(encoding="utf-8")
        chinese = README_ZH.read_text(encoding="utf-8")
        self.assertIsNotNone(HAN.search(chinese))
        self.assertTrue(chinese.startswith("[English](README.md) | 简体中文\n"))
        numerals = re.compile(r"\d+(?:\.\d+)?")
        self.assertEqual(Counter(numerals.findall(english)), Counter(numerals.findall(chinese)))
        for token in ("0.851", "0.883", "1.000", "−0.513", "−0.848", "| 14 |", "forecast-k1", "5.53", "--cheap", "decision_bin", "snap_down"):
            self.assertIn(token, english)
            self.assertIn(token, chinese)
        self.assertNotIn("openreview.net/forum", chinese.lower())
        for relative in ("docs/assets/hero.zh-CN.png", "docs/assets/holdout-ranking.zh-CN.png", "docs/assets/forecast-panel.zh-CN.png", "docs/assets/holdout-glance.zh-CN.png", "docs/assets/annotated-preview.png"):
            self.assertIn(relative, chinese)
        self.assertNotIn("ablation-holdout", english)
        self.assertNotIn("ablation-holdout", chinese)

    def test_chinese_figure_labels_are_allowlisted(self) -> None:
        allow = (ROOT / "scripts" / "confidentiality_allowlist.txt").read_text(encoding="utf-8")
        for relative in PNGS_ZH:
            self.assertIn(relative, allow)
            blob = (ROOT / relative).read_bytes()
            self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"), relative)
            self.assertGreater(len(blob), 10_000, relative)
        svg = (ROOT / "docs" / "assets" / "holdout-ranking.zh-CN.svg").read_text(encoding="utf-8")
        self.assertIsNotNone(HAN.search(svg))
        self.assertIn(r3(0.8505), svg)
        self.assertNotIn("openreview.net", svg.lower())
        glance = (ROOT / "docs" / "assets" / "holdout-glance.svg").read_text(encoding="utf-8")
        self.assertIsNone(HAN.search(glance))
        self.assertIn("degenerate", glance)
        self.assertIn("GPT-6 Astra", glance)
        self.assertNotIn("is a point", glance)
        glance_zh = (ROOT / "docs" / "assets" / "holdout-glance.zh-CN.svg").read_text(encoding="utf-8")
        self.assertIsNotNone(HAN.search(glance_zh))
        self.assertIn("退化", glance_zh)
        renderer = (ROOT / "scripts" / "render_holdout_glance.py").read_text(encoding="utf-8")
        self.assertNotIn("scatter", renderer)

    def test_apr_auc_intervals_match_the_anonymized_scores(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "src"))
        from autopaperreview.public_calibration import roc_auc
        from autopaperreview.rank_metrics import bootstrap_interval

        scores = json.loads((ROOT / "docs" / "assets" / "holdout15-apr-scores.json").read_text(encoding="utf-8"))
        aggregate = json.loads(AGGREGATE.read_text(encoding="utf-8"))
        payload = json.dumps(scores["apr_opus55"] + scores["apr_grok47"]).lower()
        self.assertNotIn("forum", payload)
        self.assertNotIn("openreview", payload)
        self.assertNotIn("title", payload)
        expected = {"apr_opus55": (14, 1.0), "apr_grok47": (15, 0.8)}
        stored = aggregate["APR_main_r11_forecast_k1"]
        for key, (count, auc) in expected.items():
            rows = scores[key]
            self.assertEqual(len(rows), count)
            values = [float(row["rank_score"]) for row in rows]
            labels = [int(row["accept"]) for row in rows]
            self.assertEqual(roc_auc(values, labels), auc)
            interval = bootstrap_interval(values, [0.0] * count, labels, "auc")
            field = "holdout_opus55" if key.endswith("opus55") else "holdout_grok47"
            self.assertEqual(stored[field]["auc_ci"], [interval["low"], interval["high"]])

    def test_ablation_aggregate_is_quoted_and_has_no_ids(self) -> None:
        path = ROOT / "docs" / "assets" / "ablation-aggregate.json"
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertNotIn("forum", lowered)
        self.assertNotIn("openreview.net", lowered)
        self.assertNotIn("title", lowered)
        payload = json.loads(text)
        order = (
            "pre-fusion 7-dim weighted",
            "pre-fusion 7-dim bin",
            "pre-fusion 7-dim capped",
            "r2 mean-k1",
            "r8 panel-k2",
            "r11 forecast-k1",
        )
        self.assertEqual(list(payload["holdout15_grok47"]), list(order))
        readme = README.read_text(encoding="utf-8").replace("−", "-")
        chinese = README_ZH.read_text(encoding="utf-8").replace("−", "-")
        for token in ("0.452", "0.667", "0.761", "0.705", "0.601/0.780", "0.485/0.658"):
            self.assertIn(token, readme)
            self.assertIn(token, chinese)
        for banned in ("twin-k2", "r13"):
            self.assertNotIn(banned, readme)
            self.assertNotIn(banned, chinese)
        self.assertIn("r13-opus55 twin-k2", text)

    def test_tune30_ablation_conclusions_match_the_aggregate(self) -> None:
        path = ROOT / "docs" / "assets" / "tune30-r14-r15-aggregate.json"
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertNotIn("forum", lowered)
        self.assertNotIn("openreview.net", lowered)
        self.assertNotIn("title", lowered)
        self.assertNotIn("results/", lowered)
        payload = json.loads(text)
        same = payload["tune30_same_scaffold_openjudge_vs_main"]
        readme = README.read_text(encoding="utf-8")
        chinese = README_ZH.read_text(encoding="utf-8")
        quoted = [
            r3(same["opus-5.5"]["openjudge"]["spearman"]),
            r3(same["opus-5.5"]["openjudge"]["auc"]),
            r3(same["grok-4.7"]["openjudge"]["spearman"]),
            r3(same["grok-4.7"]["openjudge"]["auc"]),
            "0.18",
            "0.22",
            "0.12",
            "0.554/0.766",
            "0.493/0.775",
            "0.421/0.749",
            "0.619/0.801",
        ]
        for draw in same["opus-5.5"]["apr_main_forecast_k1_draws"]:
            quoted.append(r3(draw["spearman"]))
        for token in quoted:
            self.assertIn(token, readme)
            self.assertIn(token, chinese)


if __name__ == "__main__":
    unittest.main()
