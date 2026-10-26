"""The README quotes the holdout aggregate, and the showcase images are synthetic files."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = ROOT / "docs" / "assets" / "holdout15-aggregate.json"
README = ROOT / "README.md"
PNGS = (
    "docs/assets/hero.png",
    "docs/assets/holdout-ranking.png",
    "docs/assets/annotated-preview.png",
    "docs/assets/forecast-panel.png",
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


if __name__ == "__main__":
    unittest.main()
