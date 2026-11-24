"""Author-facing feedback from the optional grounded forecast review."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .anchors import normalize_forecast_seat

if TYPE_CHECKING:
    from .model_exec import ForecastJudgment


GROUNDED_WEAKNESSES = """For each reviewer's weaknesses, trace a consequential claim to the method, comparison, or result that would establish it. Check the rest of the supplied manuscript for a qualification, control, or other evidence that resolves the concern. Keep up to two distinct concerns per reviewer. If none survives that check, give an empty list.

Write each weakness as a short comment addressed to the authors. Identify the passage by its actual heading, table, or other printed label when available. State the specific gap and which inference it affects. Ask for the smallest change or check that would resolve it, or suggest narrowing the claim. If the text leaves a detail unclear, say what is unclear. End the same string with <quote>one short distinctive passage copied exactly from the manuscript</quote> so the author can find the evidence. Use a continuous excerpt, including the relevant numbers or qualifiers, with no ellipses. Each weakness is still a single JSON string."""


def locate_weakness(weakness: str, manuscript: str, shown_source: str) -> tuple[str, int, int] | None:
    """Return the comment and its unique source offsets, allowing whitespace changes."""
    comment, marker, tail = weakness.partition("<quote>")
    excerpt, closing, rest = tail.partition("</quote>")
    words = excerpt.split()
    if not (comment.strip() and marker and words and closing) or rest.strip() not in {"", "."}:
        return None
    text = r"\s+".join(re.escape(word) for word in words)
    left = r"(?<!\w)" if words[0][0].isalnum() else ""
    right = r"(?!\w)" if words[-1][-1].isalnum() else ""
    pattern = re.compile(left + text + right)
    if not pattern.search(shown_source):
        return None
    match = pattern.search(manuscript)
    if match is None or pattern.search(manuscript, match.start() + 1):
        return None
    return " ".join(comment.split()), match.start(), match.end()


def write_forecast_feedback(
    path: Path, panels: list["ForecastJudgment"], manuscript: str, shown_source: str
) -> list[str]:
    """Write located comments in panel order; return the ids of unlocated comments."""
    lines = ["Forecast review feedback", ""]
    dropped = []
    total = 0
    for panel_no, panel in enumerate(panels, 1):
        for seat_no, review in enumerate(panel.reviews, 1):
            role = normalize_forecast_seat(review.reviewer) or f"reviewer {seat_no}"
            for point_no, weakness in enumerate(review.weaknesses, 1):
                total += 1
                located = locate_weakness(weakness, manuscript, shown_source)
                if located is None:
                    dropped.append(f"P{panel_no}-R{seat_no}-W{point_no}")
                    continue
                comment, start, end = located
                first_line = manuscript.count("\n", 0, start) + 1
                last_line = first_line + manuscript.count("\n", start, end)
                location = f"line {first_line}" if first_line == last_line else f"lines {first_line}–{last_line}"
                excerpt = " ".join(manuscript[start:end].split())
                lines.extend([
                    f"Panel {panel_no}, {role} ({location}, characters {start}–{end})",
                    comment,
                    f"Manuscript: {excerpt}",
                    "",
                ])
    lines.append(f"Located {total - len(dropped)} of {total} submitted comments in the manuscript.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dropped
