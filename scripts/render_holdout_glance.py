#!/usr/bin/env python3
"""Grouped holdout chart for the README.

Reads the published aggregate and the anonymized APR score file. APR AUC
intervals are recomputed with ``rank_metrics.bootstrap_interval`` and checked
against the aggregate. Every series is a bar with an error bar. A degenerate
interval stays a bar, with a short cap at the value.

Requires matplotlib. The committed PNG and SVG are what the README shows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.public_calibration import roc_auc
from autopaperreview.rank_metrics import bootstrap_interval

ASSETS = ROOT / "docs" / "assets"
AGGREGATE = ASSETS / "holdout15-aggregate.json"
SCORES = ASSETS / "holdout15-apr-scores.json"
CJK = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"

INK = "#1c1917"
MUTED = "#57534e"
GRID = "#e7e5e4"
APR_SPEARMAN = "#0f766e"
APR_AUC = "#c2410c"
OTHER_SPEARMAN = "#64748b"
OTHER_AUC = "#fdba74"
BAND = "#f0fdfa"


def apr_auc_interval(rows: list[dict]) -> tuple[float, float, float]:
    scores = [float(row["rank_score"]) for row in rows]
    labels = [int(row["accept"]) for row in rows]
    point = roc_auc(scores, labels)
    if point is None:
        raise SystemExit("AUC is undefined")
    interval = bootstrap_interval(scores, [0.0] * len(scores), labels, "auc")
    return float(point), float(interval["low"]), float(interval["high"])


def load_rows() -> list[dict]:
    aggregate = json.loads(AGGREGATE.read_text(encoding="utf-8"))
    scores = json.loads(SCORES.read_text(encoding="utf-8"))
    opus_rows = scores["apr_opus55"]
    grok_rows = scores["apr_grok47"]
    if len(opus_rows) != 14 or len(grok_rows) != 15:
        raise SystemExit("APR score file does not have 14 and 15 rows")
    opus_point, opus_low, opus_high = apr_auc_interval(opus_rows)
    grok_point, grok_low, grok_high = apr_auc_interval(grok_rows)
    if opus_point != 1.0 or grok_point != 0.8:
        raise SystemExit(f"AUC sanity check failed: opus {opus_point}, grok {grok_point}")
    apr = aggregate["APR_main_r11_forecast_k1"]
    stored_opus = apr["holdout_opus55"]["auc_ci"]
    stored_grok = apr["holdout_grok47"]["auc_ci"]
    if stored_opus != [opus_low, opus_high] or stored_grok != [grok_low, grok_high]:
        raise SystemExit(
            "aggregate AUC intervals do not match a fresh bootstrap: "
            f"opus {stored_opus} vs {[opus_low, opus_high]}, "
            f"grok {stored_grok} vs {[grok_low, grok_high]}"
        )

    def other(key: str, framework: str, model: str) -> dict:
        block = aggregate[key]
        point = block["point"]
        ci = block["ci95"]
        return {
            "framework": framework,
            "model": model,
            "n": block["n"],
            "highlight": False,
            "spearman": point["spearman"],
            "spearman_ci": tuple(ci["spearman"]),
            "auc": point["auc"],
            "auc_ci": tuple(ci["auc"]),
        }

    opus = apr["holdout_opus55"]
    grok = apr["holdout_grok47"]
    return [
        {
            "framework": "APR",
            "model": "Opus 5.5",
            "n": opus["n"],
            "highlight": True,
            "spearman": opus["spearman"],
            "spearman_ci": tuple(opus["spearman_ci"]),
            "auc": opus["auc"],
            "auc_ci": tuple(opus["auc_ci"]),
        },
        {
            "framework": "APR",
            "model": "Grok 4.7",
            "n": grok["n"],
            "highlight": True,
            "spearman": grok["spearman"],
            "spearman_ci": tuple(grok["spearman_ci"]),
            "auc": grok["auc"],
            "auc_ci": tuple(grok["auc_ci"]),
        },
        other("openjudge_opus55", "OpenJudge", "Opus 5.5"),
        other("openjudge_grok_run1", "OpenJudge", "Grok run 1"),
        other("openjudge_grok_run2", "OpenJudge", "Grok run 2"),
        other("proreviewer", "ProReviewer", "not recorded"),
        other("sakana_ai_scientist_reviewer", "Sakana", "not recorded"),
    ]


def _labels(language: str) -> dict[str, str]:
    if language == "en":
        return {
            "title": "Holdout  ·  Spearman and AUC",
            "note": "APR Opus AUC is a bar. The interval is degenerate: 14 finished scores separate accept from reject.",
            "spearman": "Spearman",
            "auc": "AUC",
            "apr_spearman": "APR Spearman",
            "apr_auc": "APR AUC",
            "other_spearman": "Other Spearman",
            "other_auc": "Other AUC",
            "not_recorded": "not recorded",
            "run1": "Grok run 1",
            "run2": "Grok run 2",
        }
    if language == "zh":
        return {
            "title": "留出集  ·  Spearman 和 AUC",
            "note": "APR Opus 的 AUC 仍是柱。区间退化：14 篇已完成的分数把录用和拒稿完全分开。",
            "spearman": "Spearman",
            "auc": "AUC",
            "apr_spearman": "APR 的 Spearman",
            "apr_auc": "APR 的 AUC",
            "other_spearman": "其他系统的 Spearman",
            "other_auc": "其他系统的 AUC",
            "not_recorded": "模型未记录",
            "run1": "Grok 第1次",
            "run2": "Grok 第2次",
        }
    raise SystemExit(f"unknown language {language}")


def _display_model(model: str, text: dict[str, str]) -> str:
    if model == "not recorded":
        return text["not_recorded"]
    if model == "Grok run 1":
        return text["run1"]
    if model == "Grok run 2":
        return text["run2"]
    return model


def _degenerate(value: float, ci: tuple[float, float]) -> bool:
    low, high = ci
    return low == high == value


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
    figure, axis = plt.subplots(figsize=(12.4, 5.7), dpi=200)
    axis.axvspan(-0.55, 1.55, color=BAND, zorder=0)
    axis.axhline(0.0, color="#d6d3d1", linewidth=0.8, zorder=1)
    axis.yaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    axis.set_axisbelow(True)

    width = 0.34
    legend_handles = {}
    for index, row in enumerate(rows):
        spearman_color = APR_SPEARMAN if row["highlight"] else OTHER_SPEARMAN
        auc_color = APR_AUC if row["highlight"] else OTHER_AUC
        x_spearman = index - width / 2
        x_auc = index + width / 2
        _bar(
            axis,
            x_spearman,
            row["spearman"],
            row["spearman_ci"],
            spearman_color,
            legend_handles,
            text["apr_spearman"] if row["highlight"] else text["other_spearman"],
        )
        _bar(
            axis,
            x_auc,
            row["auc"],
            row["auc_ci"],
            auc_color,
            legend_handles,
            text["apr_auc"] if row["highlight"] else text["other_auc"],
        )

    ticks = [
        f"{row['framework']}\n{_display_model(row['model'], text)}\nn={row['n']}"
        for row in rows
    ]
    axis.set_xticks(range(len(rows)))
    axis.set_xticklabels(ticks, fontsize=8.5, color=INK)
    axis.set_xlim(-0.6, len(rows) - 0.4)
    axis.set_ylim(-0.34, 1.14)
    axis.set_yticks([-0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    axis.tick_params(axis="y", labelsize=9, colors=INK)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.set_title(text["title"], loc="left", fontsize=15, color=INK, pad=12)
    handles = [
        legend_handles[key]
        for key in (text["apr_spearman"], text["apr_auc"], text["other_spearman"], text["other_auc"])
        if key in legend_handles
    ]
    labels = [handle.get_label() for handle in handles]
    axis.legend(
        handles,
        labels,
        frameon=False,
        fontsize=8.5,
        loc="upper right",
        ncol=2,
    )
    figure.text(0.012, 0.012, text["note"], fontsize=8.5, color=MUTED)
    figure.tight_layout(rect=(0, 0.045, 1, 1))
    png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(png, dpi=200)
    figure.savefig(svg)
    plt.close(figure)


def _bar(axis, x: float, value: float, ci: tuple[float, float], color: str, legend: dict, label: str) -> None:
    low, high = ci
    bar = axis.bar([x], [value], width=0.32, color=color, zorder=3, label=label if label not in legend else None)
    y_low = value - low
    y_high = high - value
    axis.errorbar(
        [x],
        [value],
        yerr=[[y_low], [y_high]],
        fmt="none",
        ecolor=INK,
        elinewidth=0.85,
        capsize=3.5,
        capthick=0.9,
        zorder=4,
    )
    # A zero-length interval has no stem. Draw the cap in data coordinates so
    # it stays visible at the top of the bar.
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


def main() -> None:
    render("en", ASSETS / "holdout-glance.png", ASSETS / "holdout-glance.svg")
    render("zh", ASSETS / "holdout-glance.zh-CN.png", ASSETS / "holdout-glance.zh-CN.svg")


if __name__ == "__main__":
    main()
