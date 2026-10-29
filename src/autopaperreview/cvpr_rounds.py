"""Append-only registry of CVPR tier candidates.

``rounds.jsonl`` records every round the operator runs. The report prints
the file. It does not rewrite it, and it does not put the rows into a score.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PROTOCOL = "cvpr-tier-rounds-v1"
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RoundError(ValueError):
    """A rounds.jsonl row is missing a model or a round id."""


def rounds_path(cache: Path) -> Path:
    return cache / "rounds.jsonl"


def append_round(cache: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one candidate. Earlier lines stay where they are."""
    row = {
        "backend": record.get("backend"),
        "calls": record.get("calls"),
        "macro_auc": record.get("macro_auc"),
        "model_id": _token(record.get("model_id"), what="model_id"),
        "notes": str(record.get("notes") or ""),
        "parsed": record.get("parsed"),
        "protocol": PROTOCOL,
        "repeats": record.get("repeats"),
        "round": _token(record.get("round"), what="round"),
        "set_id": _optional_token(record.get("set_id"), what="set_id"),
        "tokens": record.get("tokens"),
        "variant": _optional_token(record.get("variant"), what="variant"),
    }
    path = rounds_path(cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)
    return row


def load_rounds(cache: Path) -> list[dict[str, Any]]:
    path = rounds_path(cache)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RoundError(f"rounds.jsonl line {line_number} is not JSON") from exc
        if not isinstance(payload, dict):
            raise RoundError(f"rounds.jsonl line {line_number} is not an object")
        rows.append(payload)
    return rows


def format_rounds_table(rows: list[dict[str, Any]]) -> list[str]:
    """Markdown table of every recorded candidate."""
    lines = [
        "## Rounds",
        "",
        "Every line of rounds.jsonl, in append order. A later report does not delete a candidate.",
        "",
        "| round | model | variant | set | repeats | calls | tokens | parsed | macro-AUC | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | none |")
        return lines
    for row in rows:
        macro = row.get("macro_auc")
        macro_text = "n/a" if not isinstance(macro, (int, float)) else f"{float(macro):.3f}"
        lines.append(
            "| {round} | {model} | {variant} | {set_id} | {repeats} | {calls} | {tokens} | {parsed} | {macro} | {notes} |".format(
                round=_cell(row.get("round")),
                model=_cell(row.get("model_id")),
                variant=_cell(row.get("variant")),
                set_id=_cell(row.get("set_id")),
                repeats=_cell(row.get("repeats")),
                calls=_cell(row.get("calls")),
                tokens=_cell(row.get("tokens")),
                parsed=_cell(row.get("parsed")),
                macro=macro_text,
                notes=_cell(row.get("notes")) or "",
            )
        )
    return lines


def _cell(value: object) -> str:
    if value is None or value == "":
        return ""
    return str(value).replace("|", "/")


def _token(value: object, *, what: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN.fullmatch(text):
        raise RoundError(f"{what} must be letters, digits, dot, underscore, or hyphen")
    return text


def _optional_token(value: object, *, what: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _token(value, what=what)
