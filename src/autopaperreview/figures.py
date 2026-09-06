"""Caption inventory and sibling-PDF discovery for figure-grounded review."""

from __future__ import annotations

import re
from pathlib import Path

_CAPTION_RE = re.compile(r"\b(Figure|Fig\.|Table)\s+(\d+)\b", re.IGNORECASE)


def sibling_pdf(source: Path) -> Path | None:
    """Return a PDF next to a text extract, or the source itself when it is a PDF."""
    if source.suffix.lower() == ".pdf":
        return source if source.is_file() else None
    candidate = source.with_suffix(".pdf")
    return candidate if candidate.is_file() else None


def caption_inventory(text: str) -> list[str]:
    """Stable Figure/Table ids mentioned in extracted manuscript text."""
    items: list[str] = []
    seen: set[str] = set()
    for kind, number in _CAPTION_RE.findall(text):
        label = f"{'Table' if kind.lower().startswith('table') else 'Figure'} {number}"
        if label not in seen:
            seen.add(label)
            items.append(label)
    return items
