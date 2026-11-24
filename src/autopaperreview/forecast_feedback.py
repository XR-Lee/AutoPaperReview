"""Author-facing feedback from the optional grounded forecast review."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .anchors import normalize_forecast_seat

if TYPE_CHECKING:
    from .model_exec import ForecastJudgment


GROUNDED_WEAKNESSES = """Keep the original diagnosis, then add one short fix. Each reviewer writes up to two weaknesses. The first sentence of every weakness is only that diagnosis: the specific flaw, with no remedy and no quotation in the sentence. Keep a second distinct concern when the reviewer has one. Do not drop it to lengthen the first, and do not drop a concern because the manuscript mentions a partial mitigation. State the gap that remains.

After both diagnoses are written, add one short fix to the last weakness only. That sentence asks for the smallest change, check, or narrowed claim that would resolve the gap, and it names the heading, table, or other printed label when one is available. End that same last string with <quote>one short distinctive passage copied exactly from the manuscript, at most 20 words, no ellipses</quote>. Earlier weaknesses are the diagnosis alone: no fix and no quote. Each weakness is still a single JSON string."""


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


def _quoted(weakness: str) -> bool:
    return "<quote>" in weakness or "</quote>" in weakness


def write_forecast_feedback(
    path: Path, panels: list["ForecastJudgment"], manuscript: str, shown_source: str
) -> list[str]:
    """Write diagnoses and located quotes; return the ids of quotes that do not resolve.

    A weakness with no quote tags is kept as a diagnosis. A quote is a fact check:
    it is kept only when it is one continuous passage visible to the reviewer and
    unique in the manuscript. Assessing whether the quote supports the criticism
    stays with the reader.
    """
    lines = ["Forecast review feedback", ""]
    dropped = []
    total = 0
    located_count = 0
    for panel_no, panel in enumerate(panels, 1):
        for seat_no, review in enumerate(panel.reviews, 1):
            role = normalize_forecast_seat(review.reviewer) or f"reviewer {seat_no}"
            for point_no, weakness in enumerate(review.weaknesses, 1):
                total += 1
                item_id = f"P{panel_no}-R{seat_no}-W{point_no}"
                if not _quoted(weakness):
                    comment = " ".join(weakness.split())
                    if not comment:
                        dropped.append(item_id)
                        continue
                    lines.extend([f"Panel {panel_no}, {role} (diagnosis)", comment, ""])
                    continue
                located = locate_weakness(weakness, manuscript, shown_source)
                if located is None:
                    dropped.append(item_id)
                    continue
                comment, start, end = located
                located_count += 1
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
    lines.append(f"Located {located_count} of {total} submitted comments in the manuscript.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dropped
