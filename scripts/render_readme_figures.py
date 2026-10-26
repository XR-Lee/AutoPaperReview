#!/usr/bin/env python3
"""Render the README figures from the synthetic fixture and the holdout aggregate.

The annotated page is produced by running ``examples/synthetic`` with the
fixture adapter, then opening the annotated PDF that the report stage writes.
The chart and the forecast panel are drawn from numbers: the aggregate JSON,
and invented probabilities passed through ``anchors.expected_rating``.

Nothing here reads a real manuscript. Outputs land in ``docs/assets/``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.anchors import (  # noqa: E402
    OFFICIAL_RATINGS,
    expected_rating,
    forecast_mean,
)

ASSETS = ROOT / "docs" / "assets"
AGGREGATE = ASSETS / "holdout15-aggregate.json"
PREVIEW_ROOT = ROOT / "workdir" / "readme-preview"

INTER = "/usr/share/fonts/truetype/macos/Inter-Regular.ttf"
INTER_MED = "/usr/share/fonts/truetype/macos/Inter-Medium.ttf"
INTER_SEMI = "/usr/share/fonts/truetype/macos/Inter-SemiBold.ttf"
INTER_BOLD = "/usr/share/fonts/truetype/macos/Inter-Bold.ttf"
SERIF = "/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf"
SERIF_BOLD = "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf"
SERIF_ITALIC = "/usr/share/fonts/truetype/noto/NotoSerif-Italic.ttf"
MONO = "/usr/share/fonts/truetype/macos/JetBrainsMono-Regular.ttf"
CJK = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
CJK_FAMILY = "WenQuanYi Micro Hei, Noto Sans CJK SC, sans-serif"

INK = (28, 25, 23)
MUTED = (87, 83, 78)
FAINT = (120, 113, 108)
LINE = (221, 216, 208)
PAPER = (250, 248, 244)
WHITE = (255, 255, 255)
TEAL = (15, 110, 107)
TEAL_DARK = (10, 74, 72)
NAVY = (29, 78, 137)
AMBER = (180, 83, 9)
STONE = (87, 83, 78)
WEAK = (168, 36, 40)

# Invented weights for the forecast figure. They sum to one. They are not a paper.
FORECAST_DEMO = (
    ("Specialist", (0.04, 0.16, 0.40, 0.26, 0.10, 0.04)),
    ("Generalist", (0.02, 0.08, 0.22, 0.36, 0.24, 0.08)),
    ("Senior", (0.08, 0.22, 0.34, 0.22, 0.10, 0.04)),
)


def r3(value: float) -> str:
    """Three decimals, half away from zero, so 0.8505 renders as 0.851."""
    quantized = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return f"{quantized:.3f}"


def r2(value: float) -> str:
    quantized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{quantized:.2f}"


def money(value: float) -> str:
    quantized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${quantized:.2f}"


def load_aggregate() -> dict:
    return json.loads(AGGREGATE.read_text(encoding="utf-8"))


def chart_rows(payload: dict) -> list[dict]:
    apr = payload["APR_main_r11_forecast_k1"]
    opus = apr["holdout_opus55"]
    grok = apr["holdout_grok47"]
    specs = [
        ("APR", "Opus 5.5", TEAL, opus["n"], opus["spearman"], tuple(opus["spearman_ci"]), opus["auc"], None),
        ("APR", "Grok 4.7", TEAL, grok["n"], grok["spearman"], tuple(grok["spearman_ci"]), grok["auc"], None),
        ("OpenJudge", "Opus 5.5", NAVY, payload["openjudge_opus55"]["n"], payload["openjudge_opus55"]["point"]["spearman"], tuple(payload["openjudge_opus55"]["ci95"]["spearman"]), payload["openjudge_opus55"]["point"]["auc"], tuple(payload["openjudge_opus55"]["ci95"]["auc"])),
        ("OpenJudge", "Grok run 1", NAVY, payload["openjudge_grok_run1"]["n"], payload["openjudge_grok_run1"]["point"]["spearman"], tuple(payload["openjudge_grok_run1"]["ci95"]["spearman"]), payload["openjudge_grok_run1"]["point"]["auc"], tuple(payload["openjudge_grok_run1"]["ci95"]["auc"])),
        ("OpenJudge", "Grok run 2", NAVY, payload["openjudge_grok_run2"]["n"], payload["openjudge_grok_run2"]["point"]["spearman"], tuple(payload["openjudge_grok_run2"]["ci95"]["spearman"]), payload["openjudge_grok_run2"]["point"]["auc"], tuple(payload["openjudge_grok_run2"]["ci95"]["auc"])),
        ("ProReviewer", "model n/a", AMBER, payload["proreviewer"]["n"], payload["proreviewer"]["point"]["spearman"], tuple(payload["proreviewer"]["ci95"]["spearman"]), payload["proreviewer"]["point"]["auc"], tuple(payload["proreviewer"]["ci95"]["auc"])),
        ("Sakana", "model n/a", STONE, payload["sakana_ai_scientist_reviewer"]["n"], payload["sakana_ai_scientist_reviewer"]["point"]["spearman"], tuple(payload["sakana_ai_scientist_reviewer"]["ci95"]["spearman"]), payload["sakana_ai_scientist_reviewer"]["point"]["auc"], tuple(payload["sakana_ai_scientist_reviewer"]["ci95"]["auc"])),
    ]
    rows = []
    for group, name, color, n, spearman, spearman_ci, auc, auc_ci in specs:
        rows.append(
            {
                "group": group,
                "name": name,
                "color": color,
                "n": n,
                "spearman": spearman,
                "spearman_ci": spearman_ci,
                "auc": auc,
                "auc_ci": auc_ci,
            }
        )
    return rows


def _font(draw_module, path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size=size)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for word in paragraph.split():
            trial = word if not current else f"{current} {word}"
            if draw.textlength(trial, font=font) <= width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return lines


def _rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def render_chart_png(rows: list[dict], path: Path) -> None:
    from PIL import Image, ImageDraw

    width, height = 1480, 920
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    title = _font(ImageDraw, INTER_BOLD, 32)
    subtitle = _font(ImageDraw, INTER, 16)
    label = _font(ImageDraw, INTER_MED, 13)
    small = _font(ImageDraw, INTER, 12)
    tiny = _font(ImageDraw, INTER, 11)
    draw.text((48, 36), "Holdout-15  ·  rank agreement with human reviews", font=title, fill=INK)
    draw.text(
        (48, 80),
        "Same 15 public OpenReview papers. Whiskers are 95% bootstrap intervals (4000 resamples, seed 20261021).",
        font=subtitle,
        fill=MUTED,
    )

    panels = [
        ("Spearman vs mean human rating", "spearman", "spearman_ci", -0.25, 1.0),
        ("AUC  ·  accept vs reject", "auc", "auc_ci", 0.25, 1.0),
    ]
    top = 168
    bottom = 700
    left0 = 96
    panel_w = 620
    gap = 64
    for index, (heading, key, ci_key, y_min, y_max) in enumerate(panels):
        left = left0 + index * (panel_w + gap)
        draw.text((left, top - 36), heading, font=_font(ImageDraw, INTER_SEMI, 18), fill=INK)
        plot_top = top
        plot_bottom = bottom
        plot_left = left + 36
        plot_right = left + panel_w - 8

        def y_of(value: float) -> float:
            span = y_max - y_min
            return plot_bottom - (value - y_min) / span * (plot_bottom - plot_top)

        for tick in _ticks(y_min, y_max):
            y = y_of(tick)
            draw.line((plot_left, y, plot_right, y), fill=LINE, width=1)
            tick_label = f"{tick:.1f}"
            draw.text((left - 42, y - 7), tick_label, font=tiny, fill=FAINT)
        zero = y_of(0.0) if y_min < 0 < y_max else None
        if zero is not None:
            draw.line((plot_left, zero, plot_right, zero), fill=(180, 174, 166), width=1)
        if key == "auc":
            chance = y_of(0.5)
            draw.line((plot_left, chance, plot_right, chance), fill=(196, 164, 132), width=1)
            draw.text((plot_left + 6, chance - 16), "chance 0.50", font=tiny, fill=AMBER)

        count = len(rows)
        slot = (plot_right - plot_left) / count
        bar_w = min(46, slot * 0.62)
        for row_index, row in enumerate(rows):
            cx = plot_left + slot * (row_index + 0.5)
            value = row[key]
            ci = row[ci_key]
            y_val = y_of(value)
            y_base = y_of(0.0) if y_min < 0 else plot_bottom
            x0 = cx - bar_w / 2
            x1 = cx + bar_w / 2
            top_y = min(y_val, y_base)
            bot_y = max(y_val, y_base)
            draw.rounded_rectangle((x0, top_y, x1, bot_y), radius=5, fill=row["color"])
            if ci is not None:
                y_lo = y_of(ci[0])
                y_hi = y_of(ci[1])
                draw.line((cx, y_lo, cx, y_hi), fill=INK, width=2)
                draw.line((cx - 7, y_lo, cx + 7, y_lo), fill=INK, width=2)
                draw.line((cx - 7, y_hi, cx + 7, y_hi), fill=INK, width=2)
            else:
                draw.ellipse((cx - 4, y_val - 4, cx + 4, y_val + 4), fill=WHITE, outline=INK, width=2)
            value_text = r3(value)
            tw = draw.textlength(value_text, font=tiny)
            draw.text((cx - tw / 2, top_y - 16), value_text, font=tiny, fill=INK)
            name_y = plot_bottom + 16
            for line_index, line in enumerate((row["group"], row["name"])):
                lw = draw.textlength(line, font=label if line_index == 0 else small)
                draw.text(
                    (cx - lw / 2, name_y + line_index * 18),
                    line,
                    font=label if line_index == 0 else small,
                    fill=INK if line_index == 0 else MUTED,
                )
            n_text = f"n={row['n']}"
            nw = draw.textlength(n_text, font=tiny)
            draw.text((cx - nw / 2, name_y + 40), n_text, font=tiny, fill=FAINT)

    draw.text(
        (48, 848),
        "White dots: APR AUC has no interval in the aggregate. OpenJudge “Grok” does not name a version. ProReviewer and Sakana models are not in the file.",
        font=small,
        fill=MUTED,
    )
    draw.text(
        (48, 874),
        "Intervals overlap at n=15. OpenJudge Opus Spearman is higher than APR Opus. APR Opus AUC is 1.00 on 14 papers.",
        font=small,
        fill=MUTED,
    )
    image.save(path, "PNG", optimize=True)


def _ticks(low: float, high: float) -> list[float]:
    if low < 0:
        return [-0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    return [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def render_chart_svg(rows: list[dict], path: Path) -> None:
    """Text twin of the PNG chart so the plotted numbers stay greppable."""
    width, height = 1480, 860
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        '<text x="48" y="64" font-family="Inter, Helvetica, sans-serif" font-size="32" font-weight="700" fill="#1c1917">Holdout-15 · rank agreement with human reviews</text>',
        '<text x="48" y="96" font-family="Inter, Helvetica, sans-serif" font-size="16" fill="#57534e">Same 15 public OpenReview papers. Whiskers are 95% bootstrap intervals (4000 resamples, seed 20261021).</text>',
    ]
    panels = [
        ("Spearman vs mean human rating", "spearman", "spearman_ci", -0.25, 1.0, 78),
        ("AUC · accept vs reject", "auc", "auc_ci", 0.25, 1.0, 788),
    ]
    for heading, key, ci_key, y_min, y_max, left in panels:
        parts.append(
            f'<text x="{left}" y="140" font-family="Inter, Helvetica, sans-serif" font-size="18" font-weight="600" fill="#1c1917">{heading}</text>'
        )
        plot_top, plot_bottom = 160, 700
        plot_left, plot_right = left + 36, left + 632
        span = y_max - y_min

        def y_of(value: float) -> float:
            return plot_bottom - (value - y_min) / span * (plot_bottom - plot_top)

        for tick in _ticks(y_min, y_max):
            y = y_of(tick)
            parts.append(f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" stroke="#ddd8d0"/>')
        slot = (plot_right - plot_left) / len(rows)
        for row_index, row in enumerate(rows):
            cx = plot_left + slot * (row_index + 0.5)
            value = float(row[key])
            y_val = y_of(value)
            y_base = y_of(0.0) if y_min < 0 else plot_bottom
            color = "#{:02x}{:02x}{:02x}".format(*row["color"])
            top_y = min(y_val, y_base)
            bot_y = max(y_val, y_base)
            parts.append(
                f'<rect x="{cx - 18:.1f}" y="{top_y:.1f}" width="36" height="{max(bot_y - top_y, 1):.1f}" rx="4" fill="{color}"/>'
            )
            ci = row[ci_key]
            if ci is not None:
                parts.append(
                    f'<line x1="{cx:.1f}" y1="{y_of(ci[0]):.1f}" x2="{cx:.1f}" y2="{y_of(ci[1]):.1f}" stroke="#1c1917" stroke-width="2"/>'
                )
            parts.append(
                f'<text x="{cx:.1f}" y="{top_y - 6:.1f}" text-anchor="middle" font-family="Inter, Helvetica, sans-serif" font-size="11" fill="#1c1917">{r3(value)}</text>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{plot_bottom + 28}" text-anchor="middle" font-family="Inter, Helvetica, sans-serif" font-size="13" fill="#1c1917">{row["group"]}</text>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{plot_bottom + 46}" text-anchor="middle" font-family="Inter, Helvetica, sans-serif" font-size="12" fill="#57534e">{row["name"]}</text>'
            )
    parts.append(
        '<text x="48" y="830" font-family="Inter, Helvetica, sans-serif" font-size="13" fill="#57534e">APR AUC has no interval in the aggregate. OpenJudge Grok does not name a version. ProReviewer and Sakana models are not in the file. Intervals overlap at this n.</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def forecast_numbers() -> list[dict]:
    expectations = []
    rows = []
    for name, weights in FORECAST_DEMO:
        total = sum(weights)
        if abs(total - 1.0) > 1e-9:
            raise SystemExit(f"{name} probabilities sum to {total}")
        expected, normalized = expected_rating(list(weights))
        expectations.append(expected)
        mode = OFFICIAL_RATINGS[max(range(len(normalized)), key=lambda index: normalized[index])]
        rows.append(
            {
                "name": name,
                "weights": normalized,
                "expected": expected,
                "mode": mode,
            }
        )
    rows.append({"rank": forecast_mean(expectations)})
    return rows


def render_forecast_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    rows = forecast_numbers()
    rank = rows[-1]["rank"]
    seats = rows[:-1]
    width, height = 1280, 820
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.text((48, 40), "forecast-k1  ·  one call, three reviewers", font=_font(ImageDraw, INTER_BOLD, 32), fill=INK)
    draw.text(
        (48, 86),
        "Synthetic illustration. These probabilities are invented. They are not a holdout paper.",
        font=_font(ImageDraw, INTER, 16),
        fill=MUTED,
    )
    colors = [TEAL, NAVY, AMBER]
    for seat_index, seat in enumerate(seats):
        x = 48 + seat_index * 400
        y = 132
        draw.rounded_rectangle((x, y, x + 16, y + 16), radius=4, fill=colors[seat_index])
        draw.text((x + 26, y - 3), seat["name"], font=_font(ImageDraw, INTER_SEMI, 18), fill=INK)
        draw.text(
            (x + 26, y + 24),
            f"most likely {seat['mode']}    expected {seat['expected']:.2f}",
            font=_font(ImageDraw, INTER, 15),
            fill=MUTED,
        )
    ratings = list(OFFICIAL_RATINGS)
    origin_x = 120
    bar_w = 28
    group_w = bar_w * 3 + 10
    gap = 64
    max_h = 250
    base_y = 520
    for rating_index, rating in enumerate(ratings):
        gx = origin_x + rating_index * (group_w + gap)
        label = str(rating)
        lw = draw.textlength(label, font=_font(ImageDraw, INTER_SEMI, 18))
        draw.text((gx + group_w / 2 - lw / 2, base_y + 12), label, font=_font(ImageDraw, INTER_SEMI, 18), fill=INK)
        for seat_index, seat in enumerate(seats):
            weight = float(seat["weights"][rating_index])
            h = weight / 0.45 * max_h
            x = gx + seat_index * (bar_w + 4)
            draw.rounded_rectangle((x, base_y - h, x + bar_w, base_y), radius=4, fill=colors[seat_index])
    axis = _font(ImageDraw, INTER, 13)
    draw.text((origin_x, base_y + 42), "Official rating  ·  bar height is probability", font=axis, fill=FAINT)
    card = (48, 600, 1232, 776)
    _rounded(draw, card, 16, WHITE, LINE, 1)
    draw.text((72, 622), "Shipped rank", font=_font(ImageDraw, INTER_SEMI, 14), fill=TEAL_DARK)
    draw.text((72, 650), f"{rank:.2f}", font=_font(ImageDraw, INTER_BOLD, 52), fill=INK)
    expectation_text = "   ".join(f"{seat['expected']:.2f}" for seat in seats)
    draw.text((250, 668), "mean of the three expected ratings", font=_font(ImageDraw, INTER, 18), fill=INK)
    draw.text((250, 698), expectation_text, font=_font(ImageDraw, INTER_SEMI, 18), fill=TEAL_DARK)
    draw.text(
        (72, 736),
        "Expected rating = sum of p(r) times r, for r in 1, 3, 5, 6, 8, 10. The most likely integer is diagnostic.",
        font=_font(ImageDraw, INTER, 15),
        fill=MUTED,
    )
    image.save(path, "PNG", optimize=True)


def render_forecast_svg(path: Path) -> None:
    rows = forecast_numbers()
    rank = rows[-1]["rank"]
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="200" viewBox="0 0 1280 200">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        '<text x="32" y="48" font-family="Inter, Helvetica, sans-serif" font-size="24" font-weight="700" fill="#1c1917">forecast-k1 synthetic illustration (not a holdout paper)</text>',
        f'<text x="32" y="96" font-family="Inter, Helvetica, sans-serif" font-size="20" fill="#1c1917">rank {rank:.4f} = mean of expected ratings</text>',
    ]
    y = 140
    for seat in rows[:-1]:
        weights = " ".join(f"{rating}:{weight:.2f}" for rating, weight in zip(OFFICIAL_RATINGS, seat["weights"], strict=True))
        lines.append(
            f'<text x="32" y="{y}" font-family="Inter, Helvetica, sans-serif" font-size="16" fill="#57534e">{seat["name"]}: mode {seat["mode"]}, expected {seat["expected"]:.4f}, p [{weights}]</text>'
        )
        y += 24
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_synthetic_pdf(path: Path) -> None:
    import pymupdf

    manuscript = (ROOT / "examples" / "synthetic" / "manuscript.txt").read_text(encoding="utf-8")
    # The page must contain the fixture sentences verbatim so quote search hits.
    document = pymupdf.open()
    page = document.new_page(width=612, height=560)
    page.insert_font(fontname="serif", fontfile=SERIF)
    page.insert_font(fontname="serifb", fontfile=SERIF_BOLD)
    page.insert_font(fontname="serifi", fontfile=SERIF_ITALIC)
    page.insert_font(fontname="sans", fontfile=INTER)
    page.insert_font(fontname="sansb", fontfile=INTER_BOLD)
    page.draw_rect(pymupdf.Rect(0, 0, 612, 36), color=None, fill=(15 / 255, 110 / 255, 107 / 255))
    page.insert_text(
        (40, 24),
        "SYNTHETIC FIXTURE   ·   NOT A CONFERENCE SUBMISSION",
        fontname="sansb",
        fontsize=9,
        color=(1, 1, 1),
    )
    page.insert_text((40, 78), "A Synthetic Tracking Study", fontname="serifb", fontsize=22, color=(0.11, 0.10, 0.09))
    page.insert_text(
        (40, 102),
        "A. Example and B. Fixture    Example Institute",
        fontname="serifi",
        fontsize=11,
        color=(0.34, 0.32, 0.30),
    )
    page.draw_line(pymupdf.Point(40, 116), pymupdf.Point(540, 116), color=(0.75, 0.72, 0.68), width=0.6)
    y = 148
    blocks = [
        ("Abstract", "The proposed system achieves 95% accuracy and generalizes to unseen sequences."),
        (
            "Methods",
            "Eight temporally related images from one specimen were randomly divided at image level. "
            "Six images were used for training and two for testing.",
        ),
        (
            "Results",
            "Accuracy was reported on the two held-out images. No specimen-level split, confidence interval, "
            "repeated seed, or external test set was reported.",
        ),
        ("Conclusion", "The method is ready for deployment across unseen structures."),
    ]
    joined = "\n".join(paragraph for _, paragraph in blocks)
    if "Eight temporally related images" not in manuscript or "Eight temporally related images" not in joined:
        raise SystemExit("synthetic PDF drifted from examples/synthetic/manuscript.txt")
    for heading, paragraph in blocks:
        page.insert_text((40, y), heading, fontname="sansb", fontsize=12, color=(0.06, 0.29, 0.28))
        y += 8
        rect = pymupdf.Rect(40, y, 520, y + 120)
        spare = page.insert_textbox(rect, paragraph, fontname="serif", fontsize=12, color=(0.12, 0.11, 0.10), align=0)
        used = 120 - max(spare, 0)
        y += used + 22
    page.insert_text(
        (40, 536),
        "Written for the AutoPaperReview synthetic example. No real study is described.",
        fontname="sans",
        fontsize=8,
        color=(0.45, 0.42, 0.39),
    )
    document.save(path)
    document.close()


def run_synthetic_preview(work: Path) -> tuple[Path, Path]:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(
        ROOT / "examples" / "synthetic",
        work,
        ignore=shutil.ignore_patterns(".runs", "__pycache__", "*.pdf", "review_report.md"),
    )
    pdf_path = work / "manuscript.pdf"
    _write_synthetic_pdf(pdf_path)
    subprocess.run(
        [sys.executable, "-m", "autopaperreview", "run", str(work / "review.toml")],
        cwd=ROOT,
        check=True,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(ROOT / "src")},
    )
    annotated = work / "annotated_manuscript.pdf"
    report = work / "review_report.md"
    if not annotated.is_file() or not report.is_file():
        raise SystemExit("synthetic run did not write the annotated PDF and the report")
    return annotated, report


def _comment_text(pdf_path: Path) -> tuple[str, tuple[float, float, float, float]]:
    import pymupdf

    document = pymupdf.open(pdf_path)
    page = document[0]
    chosen = ""
    rect = (0.0, 0.0, 0.0, 0.0)
    for annot in page.annots() or []:
        info = annot.info or {}
        content = str(info.get("content") or "")
        if content.startswith("E1"):
            chosen = content
            box = annot.rect
            rect = (float(box.x0), float(box.y0), float(box.x1), float(box.y1))
            break
    document.close()
    if not chosen:
        raise SystemExit("annotated PDF has no E1 comment")
    return chosen, rect


def _split_comment(comment: str) -> tuple[str, list[str], str, str]:
    parts = comment.split("\n\n")
    head = parts[0] if parts else comment
    head_lines = head.split("\n")
    evidence_id = head_lines[0] if head_lines else "E1"
    field = head_lines[1] if len(head_lines) > 1 else ""
    labels = [item.strip() for item in field.split(";") if item.strip()]
    note = parts[1].strip() if len(parts) > 1 else ""
    status = parts[2].strip() if len(parts) > 2 else ""
    return evidence_id, labels, note, status


def render_annotated_preview(pdf_path: Path, dest: Path):
    import pymupdf
    from PIL import Image, ImageDraw

    comment, anchor = _comment_text(pdf_path)
    document = pymupdf.open(pdf_path)
    page = document[0]
    zoom = 1.7
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), annots=True, alpha=False)
    page_image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    document.close()

    evidence_id, labels, note, status = _split_comment(comment)
    primary = labels[0] if labels else "Weakness"
    also = labels[1:]

    card_w = 430
    canvas_w = page_image.width + card_w + 64
    canvas_h = page_image.height + 28
    canvas = Image.new("RGB", (canvas_w, canvas_h), PAPER)
    draw = ImageDraw.Draw(canvas)
    ox, oy = 16, 14
    draw.rectangle(
        (ox + 6, oy + 8, ox + page_image.width + 6, oy + page_image.height + 8),
        fill=(214, 210, 202),
    )
    canvas.paste(page_image, (ox, oy))

    card_x = ox + page_image.width + 32
    card_y = 36
    title_font = _font(ImageDraw, INTER_SEMI, 13)
    body_font = _font(ImageDraw, INTER, 16)
    meta_font = _font(ImageDraw, INTER, 13)
    id_font = _font(ImageDraw, INTER_BOLD, 26)
    note_lines = _wrap(draw, note, body_font, card_w - 40)
    status_lines = _wrap(draw, status, meta_font, card_w - 40)
    also_lines = _wrap(draw, "; ".join(also), meta_font, card_w - 40)
    card_h = 168 + 24 * len(note_lines) + 28 + 18 * len(also_lines) + 24 + 18 * len(status_lines)
    card_h = min(card_h, canvas_h - 56)
    _rounded(draw, (card_x, card_y, card_x + card_w, card_y + card_h), 16, WHITE, WEAK, 2)
    draw.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + 40), radius=14, fill=(253, 236, 234))
    draw.rectangle((card_x, card_y + 22, card_x + card_w, card_y + 40), fill=(253, 236, 234))
    draw.text((card_x + 18, card_y + 12), "PDF comment   ·   APR reviewer", font=title_font, fill=WEAK)
    cursor = card_y + 56
    draw.text((card_x + 18, cursor), evidence_id, font=id_font, fill=INK)
    cursor += 40
    draw.text((card_x + 18, cursor), primary, font=_font(ImageDraw, INTER_SEMI, 18), fill=WEAK)
    cursor += 32
    for line in note_lines:
        draw.text((card_x + 18, cursor), line, font=body_font, fill=INK)
        cursor += 24
    cursor += 12
    draw.text((card_x + 18, cursor), "Also cited from this quote", font=title_font, fill=MUTED)
    cursor += 22
    for line in also_lines:
        if cursor > card_y + card_h - 48:
            break
        draw.text((card_x + 18, cursor), line, font=meta_font, fill=MUTED)
        cursor += 18
    for line in status_lines:
        draw.text((card_x + 18, card_y + card_h - 28), line, font=meta_font, fill=FAINT)

    ax = ox + max(anchor[2], anchor[0]) * zoom
    ay = oy + (anchor[1] + anchor[3]) / 2 * zoom
    draw.line((min(ax, card_x - 4), ay, card_x, card_y + 70), fill=WEAK, width=2)
    print("--- E1 COMMENT ---")
    print(comment)
    print("--- END COMMENT ---")
    canvas.save(dest, "PNG", optimize=True)
    return page_image


def render_hero(page_image, payload: dict, dest: Path) -> None:
    from PIL import Image, ImageDraw

    target_h = 800
    scale = target_h / page_image.height
    crop = page_image.resize((int(page_image.width * scale), target_h), Image.Resampling.LANCZOS)
    width, height = 1680, 900
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 8), fill=TEAL)
    left = 28
    top = 50
    if crop.width > 860:
        fitted_w = 860
        fitted_h = int(crop.height * fitted_w / crop.width)
        crop = crop.resize((fitted_w, fitted_h), Image.Resampling.LANCZOS)
        top = 50 + (800 - fitted_h) // 2
    draw.rectangle((left + 8, top + 10, left + crop.width + 8, top + crop.height + 10), fill=(214, 210, 202))
    image.paste(crop, (left, top))
    right = left + crop.width + 40
    draw.text((right, 64), "AUTOPAPERREVIEW", font=_font(ImageDraw, INTER_SEMI, 14), fill=TEAL_DARK)
    draw.multiline_text(
        (right, 96),
        "A review that cites\nthe sentence it doubts.",
        font=_font(ImageDraw, INTER_BOLD, 40),
        fill=INK,
        spacing=4,
    )
    blurb = _wrap(
        draw,
        "Read a paper locally. Get an evidence-linked report, a fatal-flaw check, a three-reviewer forecast, and an annotated PDF.",
        _font(ImageDraw, INTER, 18),
        width - right - 40,
    )
    y = 230
    for line in blurb:
        draw.text((right, y), line, font=_font(ImageDraw, INTER, 18), fill=MUTED)
        y += 26
    apr = payload["APR_main_r11_forecast_k1"]
    y += 18
    for label, row, n_label in (
        ("Opus 5.5", apr["holdout_opus55"], "n=14"),
        ("Grok 4.7", apr["holdout_grok47"], "n=15"),
    ):
        _rounded(draw, (right, y, width - 36, y + 148), 14, WHITE, LINE, 1)
        draw.text((right + 20, y + 16), label, font=_font(ImageDraw, INTER_SEMI, 16), fill=MUTED)
        draw.text((right + 20, y + 44), r3(row["spearman"]), font=_font(ImageDraw, INTER_BOLD, 40), fill=INK)
        draw.text((right + 160, y + 58), "Spearman", font=_font(ImageDraw, INTER, 16), fill=MUTED)
        ci = row["spearman_ci"]
        draw.text(
            (right + 160, y + 82),
            f"95% CI {r3(ci[0])} – {r3(ci[1])}",
            font=_font(ImageDraw, INTER, 15),
            fill=FAINT,
        )
        draw.text(
            (right + 20, y + 110),
            f"AUC {r3(row['auc'])}    {n_label}    holdout-15",
            font=_font(ImageDraw, INTER, 15),
            fill=INK,
        )
        y += 164
    draw.text(
        (right, height - 48),
        "Public OpenReview holdout. Intervals are wide. Page at left is synthetic.",
        font=_font(ImageDraw, INTER, 14),
        fill=FAINT,
    )
    image.save(dest, "PNG", optimize=True)


def print_tables(payload: dict) -> None:
    apr = payload["APR_main_r11_forecast_k1"]

    def cell_ci(point, ci) -> str:
        if ci is None:
            return r3(point) if point is not None else "—"
        return f"{r3(point)} [{r3(ci[0])}, {r3(ci[1])}]"

    print("RANK", forecast_numbers()[-1]["rank"])
    for key in ("holdout_opus55", "holdout_grok47", "tune30_opus55", "tune30_grok47"):
        row = apr[key]
        print(key, "n", row["n"], "rho", r3(row["spearman"]), "auc", r3(row["auc"]))


def _cjk(size: int):
    from PIL import ImageFont

    return ImageFont.truetype(CJK, size=size)


def _wrap_chars(draw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            trial = current + char
            if draw.textlength(trial, font=font) <= width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines


def _zh_rows(rows: list[dict]) -> list[dict]:
    renamed = {
        ("OpenJudge", "Grok run 1"): "Grok 第 1 次",
        ("OpenJudge", "Grok run 2"): "Grok 第 2 次",
        ("ProReviewer", "model n/a"): "模型未记录",
        ("Sakana", "model n/a"): "模型未记录",
    }
    copied = []
    for row in rows:
        item = dict(row)
        item["name"] = renamed.get((row["group"], row["name"]), row["name"])
        copied.append(item)
    return copied


def render_chart_png_zh(rows: list[dict], path: Path) -> None:
    from PIL import Image, ImageDraw

    rows = _zh_rows(rows)
    width, height = 1480, 980
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.text((48, 32), "Holdout-15  ·  排序分和人类评分", font=_cjk(30), fill=INK)
    subtitle = _wrap_chars(
        draw,
        "同一批 15 篇公开 OpenReview 论文。误差线是 95% 自助法区间（重抽样 4000 次，种子 20261021）。",
        _cjk(16),
        width - 96,
    )
    y = 78
    for line in subtitle:
        draw.text((48, y), line, font=_cjk(16), fill=MUTED)
        y += 24

    panels = [
        ("Spearman · 和人类均分的相关性", "spearman", "spearman_ci", -0.25, 1.0),
        ("AUC · 录用对拒稿", "auc", "auc_ci", 0.25, 1.0),
    ]
    top = 188
    bottom = 720
    left0 = 96
    panel_w = 620
    gap = 64
    for index, (heading, key, ci_key, y_min, y_max) in enumerate(panels):
        left = left0 + index * (panel_w + gap)
        draw.text((left, top - 36), heading, font=_cjk(18), fill=INK)
        plot_top = top
        plot_bottom = bottom
        plot_left = left + 36
        plot_right = left + panel_w - 8

        def y_of(value: float) -> float:
            span = y_max - y_min
            return plot_bottom - (value - y_min) / span * (plot_bottom - plot_top)

        for tick in _ticks(y_min, y_max):
            tick_y = y_of(tick)
            draw.line((plot_left, tick_y, plot_right, tick_y), fill=LINE, width=1)
            draw.text((left - 42, tick_y - 7), f"{tick:.1f}", font=_cjk(11), fill=FAINT)
        if y_min < 0 < y_max:
            draw.line((plot_left, y_of(0.0), plot_right, y_of(0.0)), fill=(180, 174, 166), width=1)
        if key == "auc":
            chance = y_of(0.5)
            draw.line((plot_left, chance, plot_right, chance), fill=(196, 164, 132), width=1)
            draw.text((plot_left + 6, chance - 18), "随机线 0.50", font=_cjk(12), fill=AMBER)

        count = len(rows)
        slot = (plot_right - plot_left) / count
        bar_w = min(46, slot * 0.62)
        for row_index, row in enumerate(rows):
            cx = plot_left + slot * (row_index + 0.5)
            value = row[key]
            ci = row[ci_key]
            y_val = y_of(value)
            y_base = y_of(0.0) if y_min < 0 else plot_bottom
            x0 = cx - bar_w / 2
            x1 = cx + bar_w / 2
            top_y = min(y_val, y_base)
            bot_y = max(y_val, y_base)
            draw.rounded_rectangle((x0, top_y, x1, bot_y), radius=5, fill=row["color"])
            if ci is not None:
                y_lo = y_of(ci[0])
                y_hi = y_of(ci[1])
                draw.line((cx, y_lo, cx, y_hi), fill=INK, width=2)
                draw.line((cx - 7, y_lo, cx + 7, y_lo), fill=INK, width=2)
                draw.line((cx - 7, y_hi, cx + 7, y_hi), fill=INK, width=2)
            else:
                draw.ellipse((cx - 4, y_val - 4, cx + 4, y_val + 4), fill=WHITE, outline=INK, width=2)
            value_text = r3(value)
            tw = draw.textlength(value_text, font=_cjk(11))
            draw.text((cx - tw / 2, top_y - 16), value_text, font=_cjk(11), fill=INK)
            name_y = plot_bottom + 16
            for line_index, line in enumerate((row["group"], row["name"])):
                font = _cjk(13 if line_index == 0 else 12)
                lw = draw.textlength(line, font=font)
                draw.text(
                    (cx - lw / 2, name_y + line_index * 18),
                    line,
                    font=font,
                    fill=INK if line_index == 0 else MUTED,
                )
            n_text = f"n={row['n']}"
            nw = draw.textlength(n_text, font=_cjk(11))
            draw.text((cx - nw / 2, name_y + 40), n_text, font=_cjk(11), fill=FAINT)

    notes = [
        "白点表示汇总里没有 APR 的 AUC 区间。OpenJudge 的 Grok 没有记录版本。ProReviewer 和 Sakana 的模型也没有记录。",
        "n=15 时区间是重叠的。OpenJudge Opus 的 Spearman 高于 APR Opus。APR Opus 的 AUC 是 1.00，只有 14 篇。",
    ]
    note_y = 860
    for note in notes:
        for line in _wrap_chars(draw, note, _cjk(14), width - 96):
            draw.text((48, note_y), line, font=_cjk(14), fill=MUTED)
            note_y += 22
    image.save(path, "PNG", optimize=True)


def render_chart_svg_zh(rows: list[dict], path: Path) -> None:
    rows = _zh_rows(rows)
    width, height = 1480, 900
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="48" y="64" font-family="{CJK_FAMILY}" font-size="30" font-weight="700" fill="#1c1917">Holdout-15 · 排序分和人类评分</text>',
        f'<text x="48" y="96" font-family="{CJK_FAMILY}" font-size="16" fill="#57534e">同一批 15 篇公开 OpenReview 论文。误差线是 95% 自助法区间（重抽样 4000 次，种子 20261021）。</text>',
    ]
    panels = [
        ("Spearman · 和人类均分的相关性", "spearman", "spearman_ci", -0.25, 1.0, 78),
        ("AUC · 录用对拒稿", "auc", "auc_ci", 0.25, 1.0, 788),
    ]
    for heading, key, ci_key, y_min, y_max, left in panels:
        parts.append(
            f'<text x="{left}" y="148" font-family="{CJK_FAMILY}" font-size="18" font-weight="600" fill="#1c1917">{heading}</text>'
        )
        plot_top, plot_bottom = 172, 720
        plot_left, plot_right = left + 36, left + 632
        span = y_max - y_min

        def y_of(value: float) -> float:
            return plot_bottom - (value - y_min) / span * (plot_bottom - plot_top)

        for tick in _ticks(y_min, y_max):
            y = y_of(tick)
            parts.append(f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" stroke="#ddd8d0"/>')
        slot = (plot_right - plot_left) / len(rows)
        for row_index, row in enumerate(rows):
            cx = plot_left + slot * (row_index + 0.5)
            value = float(row[key])
            y_val = y_of(value)
            y_base = y_of(0.0) if y_min < 0 else plot_bottom
            color = "#{:02x}{:02x}{:02x}".format(*row["color"])
            top_y = min(y_val, y_base)
            bot_y = max(y_val, y_base)
            parts.append(
                f'<rect x="{cx - 18:.1f}" y="{top_y:.1f}" width="36" height="{max(bot_y - top_y, 1):.1f}" rx="4" fill="{color}"/>'
            )
            ci = row[ci_key]
            if ci is not None:
                parts.append(
                    f'<line x1="{cx:.1f}" y1="{y_of(ci[0]):.1f}" x2="{cx:.1f}" y2="{y_of(ci[1]):.1f}" stroke="#1c1917" stroke-width="2"/>'
                )
            parts.append(
                f'<text x="{cx:.1f}" y="{top_y - 6:.1f}" text-anchor="middle" font-family="{CJK_FAMILY}" font-size="11" fill="#1c1917">{r3(value)}</text>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{plot_bottom + 28}" text-anchor="middle" font-family="{CJK_FAMILY}" font-size="13" fill="#1c1917">{row["group"]}</text>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{plot_bottom + 48}" text-anchor="middle" font-family="{CJK_FAMILY}" font-size="12" fill="#57534e">{row["name"]}</text>'
            )
    parts.append(
        f'<text x="48" y="860" font-family="{CJK_FAMILY}" font-size="13" fill="#57534e">汇总里没有 APR 的 AUC 区间。OpenJudge 的 Grok 没有记录版本。ProReviewer 和 Sakana 的模型也没有记录。这个样本量下区间重叠。</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _forecast_zh_names() -> list[tuple[str, str]]:
    return (
        ("Specialist", "领域专家"),
        ("Generalist", "通才"),
        ("Senior", "资深审稿人"),
    )


def render_forecast_png_zh(path: Path) -> None:
    from PIL import Image, ImageDraw

    rows = forecast_numbers()
    rank = rows[-1]["rank"]
    seats = rows[:-1]
    names = dict(_forecast_zh_names())
    width, height = 1280, 860
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.text((48, 36), "forecast-k1  ·  一次调用，三个席位", font=_cjk(30), fill=INK)
    draw.text(
        (48, 84),
        "示意图。这些概率是编的，不是留出集里的论文。",
        font=_cjk(16),
        fill=MUTED,
    )
    colors = [TEAL, NAVY, AMBER]
    for seat_index, seat in enumerate(seats):
        x = 48 + seat_index * 400
        y = 132
        draw.rounded_rectangle((x, y, x + 16, y + 16), radius=4, fill=colors[seat_index])
        draw.text((x + 26, y - 4), names[seat["name"]], font=_cjk(18), fill=INK)
        draw.text(
            (x + 26, y + 26),
            f"最可能 {seat['mode']}    期望 {seat['expected']:.2f}",
            font=_cjk(15),
            fill=MUTED,
        )
    ratings = list(OFFICIAL_RATINGS)
    origin_x = 120
    bar_w = 28
    group_w = bar_w * 3 + 10
    gap = 64
    max_h = 250
    base_y = 540
    for rating_index, rating in enumerate(ratings):
        gx = origin_x + rating_index * (group_w + gap)
        label = str(rating)
        lw = draw.textlength(label, font=_cjk(18))
        draw.text((gx + group_w / 2 - lw / 2, base_y + 12), label, font=_cjk(18), fill=INK)
        for seat_index, seat in enumerate(seats):
            weight = float(seat["weights"][rating_index])
            h = weight / 0.45 * max_h
            x = gx + seat_index * (bar_w + 4)
            draw.rounded_rectangle((x, base_y - h, x + bar_w, base_y), radius=4, fill=colors[seat_index])
    draw.text((origin_x, base_y + 44), "官方评分  ·  柱高为概率", font=_cjk(14), fill=FAINT)
    card = (48, 630, 1232, 820)
    _rounded(draw, card, 16, WHITE, LINE, 1)
    draw.text((72, 652), "已发布的排序分", font=_cjk(15), fill=TEAL_DARK)
    draw.text((72, 682), f"{rank:.2f}", font=_cjk(48), fill=INK)
    expectation_text = "   ".join(f"{seat['expected']:.2f}" for seat in seats)
    draw.text((280, 700), "三个期望评分的平均", font=_cjk(18), fill=INK)
    draw.text((280, 732), expectation_text, font=_cjk(18), fill=TEAL_DARK)
    formula = _wrap_chars(
        draw,
        "期望评分 = p(r)×r 之和，r 取 1、3、5、6、8、10。最可能的整数只作对照，不是排序分。",
        _cjk(15),
        1100,
    )
    formula_y = 772
    for line in formula:
        draw.text((72, formula_y), line, font=_cjk(15), fill=MUTED)
        formula_y += 22
    image.save(path, "PNG", optimize=True)


def render_forecast_svg_zh(path: Path) -> None:
    rows = forecast_numbers()
    rank = rows[-1]["rank"]
    names = dict(_forecast_zh_names())
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="220" viewBox="0 0 1280 220">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="32" y="48" font-family="{CJK_FAMILY}" font-size="24" font-weight="700" fill="#1c1917">forecast-k1 示意图（不是留出集论文）</text>',
        f'<text x="32" y="96" font-family="{CJK_FAMILY}" font-size="20" fill="#1c1917">排序分 {rank:.4f} = 三个期望评分的平均</text>',
    ]
    y = 140
    for seat in rows[:-1]:
        weights = " ".join(f"{rating}:{weight:.2f}" for rating, weight in zip(OFFICIAL_RATINGS, seat["weights"], strict=True))
        lines.append(
            f'<text x="32" y="{y}" font-family="{CJK_FAMILY}" font-size="16" fill="#57534e">{names[seat["name"]]}：众数 {seat["mode"]}，期望 {seat["expected"]:.4f}，p [{weights}]</text>'
        )
        y += 26
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_hero_zh(page_image, payload: dict, dest: Path) -> None:
    from PIL import Image, ImageDraw

    target_h = 800
    scale = target_h / page_image.height
    crop = page_image.resize((int(page_image.width * scale), target_h), Image.Resampling.LANCZOS)
    width, height = 1680, 940
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 8), fill=TEAL)
    left = 28
    top = 50
    if crop.width > 820:
        fitted_w = 820
        fitted_h = int(crop.height * fitted_w / crop.width)
        crop = crop.resize((fitted_w, fitted_h), Image.Resampling.LANCZOS)
        top = 50 + (800 - fitted_h) // 2
    draw.rectangle((left + 8, top + 10, left + crop.width + 8, top + crop.height + 10), fill=(214, 210, 202))
    image.paste(crop, (left, top))
    right = left + crop.width + 36
    text_w = width - right - 40
    draw.text((right, 56), "AUTOPAPERREVIEW", font=_cjk(14), fill=TEAL_DARK)
    draw.text((right, 88), "审稿会标出", font=_cjk(40), fill=INK)
    draw.text((right, 140), "它质疑的句子。", font=_cjk(40), fill=INK)
    blurb = _wrap_chars(
        draw,
        "在本地读稿。输出带证据的审稿报告、致命问题检查、三个席位的预测，以及带批注的 PDF。",
        _cjk(18),
        text_w,
    )
    y = 210
    for line in blurb:
        draw.text((right, y), line, font=_cjk(18), fill=MUTED)
        y += 28
    apr = payload["APR_main_r11_forecast_k1"]
    y += 16
    for label, row, n_label in (
        ("Opus 5.5", apr["holdout_opus55"], "n=14"),
        ("Grok 4.7", apr["holdout_grok47"], "n=15"),
    ):
        _rounded(draw, (right, y, width - 36, y + 156), 14, WHITE, LINE, 1)
        draw.text((right + 20, y + 14), label, font=_cjk(16), fill=MUTED)
        draw.text((right + 20, y + 44), r3(row["spearman"]), font=_cjk(40), fill=INK)
        draw.text((right + 180, y + 58), "Spearman", font=_cjk(16), fill=MUTED)
        ci = row["spearman_ci"]
        draw.text(
            (right + 180, y + 86),
            f"95% 区间 {r3(ci[0])} – {r3(ci[1])}",
            font=_cjk(15),
            fill=FAINT,
        )
        draw.text(
            (right + 20, y + 116),
            f"AUC {r3(row['auc'])}    {n_label}    holdout-15",
            font=_cjk(15),
            fill=INK,
        )
        y += 172
    footer = _wrap_chars(
        draw,
        "公开 OpenReview 留出集。区间很宽。左侧是合成稿，正文是英文。",
        _cjk(14),
        text_w,
    )
    footer_y = height - 28 - 20 * len(footer)
    for line in footer:
        draw.text((right, footer_y), line, font=_cjk(14), fill=FAINT)
        footer_y += 20
    image.save(dest, "PNG", optimize=True)


def render_zh() -> None:
    """Chinese labels for figures whose English sentences are not language-neutral.

    The annotated manuscript page stays the English fixture render: the synthetic
    paper and the PDF comment are English. This does not rewrite the English assets.
    """
    payload = load_aggregate()
    ASSETS.mkdir(parents=True, exist_ok=True)
    rows = chart_rows(payload)
    render_chart_png_zh(rows, ASSETS / "holdout-ranking.zh-CN.png")
    render_chart_svg_zh(rows, ASSETS / "holdout-ranking.zh-CN.svg")
    render_forecast_png_zh(ASSETS / "forecast-panel.zh-CN.png")
    render_forecast_svg_zh(ASSETS / "forecast-panel.zh-CN.svg")
    annotated, _report = run_synthetic_preview(PREVIEW_ROOT)
    page_image = render_annotated_preview(annotated, PREVIEW_ROOT / "annotated-preview.png")
    render_hero_zh(page_image, payload, ASSETS / "hero.zh-CN.png")


def main() -> None:
    payload = load_aggregate()
    ASSETS.mkdir(parents=True, exist_ok=True)
    rows = chart_rows(payload)
    render_chart_png(rows, ASSETS / "holdout-ranking.png")
    render_chart_svg(rows, ASSETS / "holdout-ranking.svg")
    render_forecast_png(ASSETS / "forecast-panel.png")
    render_forecast_svg(ASSETS / "forecast-panel.svg")
    annotated, report = run_synthetic_preview(PREVIEW_ROOT)
    page_image = render_annotated_preview(annotated, ASSETS / "annotated-preview.png")
    render_hero(page_image, payload, ASSETS / "hero.png")
    print_tables(payload)
    text = report.read_text(encoding="utf-8")
    print("--- REPORT EXCERPT MARKER ---")
    # Print the weakness and first critical issue, which the README quotes.
    start = text.find("## Weaknesses")
    end = text.find("## Questions")
    if start < 0 or end < 0:
        raise SystemExit("report is missing Weaknesses or Questions")
    print(text[start:end])
    issue = text.find("### I001")
    issue_end = text.find("### I002")
    print(text[issue:issue_end])
    print("--- END EXCERPT ---")


if __name__ == "__main__":
    if "--zh" in sys.argv:
        render_zh()
    else:
        main()
