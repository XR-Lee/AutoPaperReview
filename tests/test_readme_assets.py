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
)
PNGS_ZH = (
    "docs/assets/hero.zh-CN.png",
    "docs/assets/holdout-ranking.zh-CN.png",
    "docs/assets/forecast-panel.zh-CN.png",
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

    def test_showcase_pngs_are_allowlisted_images(self) -> None:
        allow = (ROOT / "scripts" / "confidentiality_allowlist.txt").read_text(encoding="utf-8")
        for relative in PNGS:
            self.assertIn(relative, allow)
            blob = (ROOT / relative).read_bytes()
            self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"), relative)
            self.assertGreater(len(blob), 10_000, relative)
        svg = (ROOT / "docs" / "assets" / "holdout-ranking.svg").read_text(encoding="utf-8")
        self.assertIn(r3(0.8505), svg)
        self.assertNotIn("openreview.net", svg.lower())
        self.assertIsNone(HAN.search(svg))

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
        for relative in ("docs/assets/hero.zh-CN.png", "docs/assets/holdout-ranking.zh-CN.png", "docs/assets/forecast-panel.zh-CN.png", "docs/assets/annotated-preview.png"):
            self.assertIn(relative, chinese)

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


if __name__ == "__main__":
    unittest.main()
