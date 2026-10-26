#!/usr/bin/env python3
"""Grouped ablation chart for the README.

Reads ``docs/assets/ablation-aggregate.json`` and draws the Grok 4.7 holdout
series in file order: three pre-fusion seven-dimension readouts, then
r2 mean-k1, r8 panel-k2, and the shipped r11 forecast-k1. Every series is a
bar with an error bar. The committed PNG and SVG are what the README shows.

Requires matplotlib.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
AGGREGATE = ASSETS / "ablation-aggregate.json"
CJK = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"

ORDER = (
    "pre-fusion 7-dim weighted",
    "pre-fusion 7-dim bin",
    "pre-fusion 7-dim capped",
    "r2 mean-k1",
    "r8 panel-k2",
    "r11 forecast-k1",
)
SHIPPED = "r11 forecast-k1"

INK = "#1c1917"
MUTED = "#57534e"
GRID = "#e7e5e4"
SHIPPED_SPEARMAN = "#0f766e"
SHIPPED_AUC = "#c2410c"
EARLIER_SPEARMAN = "#64748b"
EARLIER_AUC = "#fdba74"
BAND = "#f0fdfa"


def load_rows() -> list[dict]:
    payload = json.loads(AGGREGATE.read_text(encoding="utf-8"))
    block = payload["holdout15_grok47"]
    if list(block) != list(ORDER):
        raise SystemExit(f"holdout series order changed: {list(block)}")
    rows = []
    for key in ORDER:
        row = block[key]
        if row["n"] != 15:
            raise SystemExit(f"{key} is not n=15")
        rows.append(
            {
                "key": key,
                "highlight": key == SHIPPED,
                "spearman": float(row["spearman"]),
                "spearman_ci": (float(row["spearman_ci"][0]), float(row["spearman_ci"][1])),
                "auc": float(row["auc"]),
                "auc_ci": (float(row["auc_ci"][0]), float(row["auc_ci"][1])),
            }
        )
    return rows


def _labels(language: str) -> dict[str, object]:
    ticks = {
        "pre-fusion 7-dim weighted": "7-dim\nweighted",
        "pre-fusion 7-dim bin": "7-dim\nbin",
        "pre-fusion 7-dim capped": "7-dim\ncapped",
        "r2 mean-k1": "r2\nmean-k1",
        "r8 panel-k2": "r8\npanel-k2",
        "r11 forecast-k1": "r11\nforecast-k1",
    }
    if language == "zh":
        ticks = {
            "pre-fusion 7-dim weighted": "融合前\n七维加权",
            "pre-fusion 7-dim bin": "融合前\n七维分档",
            "pre-fusion 7-dim capped": "融合前\n七维封顶",
            "r2 mean-k1": "r2\nmean-k1",
            "r8 panel-k2": "r8\npanel-k2",
            "r11 forecast-k1": "r11\nforecast-k1",
        }
        return {
            "title": "Grok 4.7 留出集  ·  消融",
            "note": "n=15。第 2、8、11 轮打开过，所以不是同一份未动过的留出集。",
            "shipped_spearman": "已发布的 Spearman",
            "shipped_auc": "已发布的 AUC",
            "earlier_spearman": "更早的 Spearman",
            "earlier_auc": "更早的 AUC",
            "ticks": ticks,
        }
    if language == "en":
        return {
            "title": "Grok 4.7 holdout  ·  ablation",
            "note": "n=15. Opened in rounds 2, 8, and 11, so not one untouched holdout.",
            "shipped_spearman": "shipped Spearman",
            "shipped_auc": "shipped AUC",
            "earlier_spearman": "earlier Spearman",
            "earlier_auc": "earlier AUC",
            "ticks": ticks,
        }
    raise SystemExit(f"unknown language {language}")


def _degenerate(value: float, ci: tuple[float, float]) -> bool:
    low, high = ci
    return low == high == value


def _bar(axis, x: float, value: float, ci: tuple[float, float], color: str, legend: dict, label: str) -> None:
    low, high = ci
    bar = axis.bar(
        [x],
        [value],
        width=0.32,
        color=color,
        zorder=3,
        label=label if label not in legend else None,
    )
    axis.errorbar(
        [x],
        [value],
        yerr=[[value - low], [high - value]],
        fmt="none",
        ecolor=INK,
        elinewidth=0.85,
        capsize=3.5,
        capthick=0.9,
        zorder=4,
    )
    if _degenerate(value, ci):
        half = 0.07
        axis.plot(
            [x - half, x + half],
            [value, value],
            color=INK,
            linewidth=0.9,
            solid_capstyle="butt",
            zorder=5,
        )
    if label not in legend:
        legend[label] = bar


def render(language: str, png: Path, svg: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    text = _labels(language)
    if language == "zh":
        font_manager.fontManager.addfont(CJK)
        family = font_manager.FontProperties(fname=CJK).get_name()
    else:
        family = "DejaVu Sans"
    plt.rcParams.update(
        {
            "font.family": family,
            "axes.linewidth": 0.6,
            "axes.edgecolor": "#44403c",
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )
    rows = load_rows()
    figure, axis = plt.subplots(figsize=(11.6, 5.8), dpi=200)
    axis.axvspan(4.45, 5.55, color=BAND, zorder=0)
    axis.axhline(0.0, color="#d6d3d1", linewidth=0.8, zorder=1)
    axis.yaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    axis.set_axisbelow(True)

    width = 0.34
    legend_handles: dict = {}
    ticks = text["ticks"]
    assert isinstance(ticks, dict)
    for index, row in enumerate(rows):
        spearman_color = SHIPPED_SPEARMAN if row["highlight"] else EARLIER_SPEARMAN
        auc_color = SHIPPED_AUC if row["highlight"] else EARLIER_AUC
        spearman_label = text["shipped_spearman"] if row["highlight"] else text["earlier_spearman"]
        auc_label = text["shipped_auc"] if row["highlight"] else text["earlier_auc"]
        _bar(
            axis,
            index - width / 2,
            row["spearman"],
            row["spearman_ci"],
            spearman_color,
            legend_handles,
            str(spearman_label),
        )
        _bar(
            axis,
            index + width / 2,
            row["auc"],
            row["auc_ci"],
            auc_color,
            legend_handles,
            str(auc_label),
        )

    axis.set_xticks(range(len(rows)))
    axis.set_xticklabels([str(ticks[row["key"]]) for row in rows], fontsize=8.5, color=INK)
    axis.set_xlim(-0.6, len(rows) - 0.4)
    axis.set_ylim(-0.62, 1.18)
    axis.set_yticks([-0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    axis.tick_params(axis="y", labelsize=9, colors=INK)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.set_title(str(text["title"]), loc="left", fontsize=15, color=INK, pad=26)
    order = (
        str(text["shipped_spearman"]),
        str(text["shipped_auc"]),
        str(text["earlier_spearman"]),
        str(text["earlier_auc"]),
    )
    handles = [legend_handles[key] for key in order if key in legend_handles]
    axis.legend(
        handles,
        [handle.get_label() for handle in handles],
        frameon=False,
        fontsize=8.5,
        loc="lower right",
        bbox_to_anchor=(1.0, 1.02),
        ncol=2,
        borderaxespad=0.15,
    )
    figure.text(0.012, 0.012, str(text["note"]), fontsize=8.5, color=MUTED)
    figure.tight_layout(rect=(0, 0.055, 1, 0.98))
    png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(png, dpi=200)
    figure.savefig(svg)
    plt.close(figure)


def main() -> None:
    render("en", ASSETS / "ablation-holdout.png", ASSETS / "ablation-holdout.svg")
    render("zh", ASSETS / "ablation-holdout.zh-CN.png", ASSETS / "ablation-holdout.zh-CN.svg")


if __name__ == "__main__":
    main()
