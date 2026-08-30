"""Extract author-listed contributions so review and retrieval can be per-item."""

from __future__ import annotations

import re

_HEADER = re.compile(
    r"\b(?:our\s+|main\s+)?contributions?\b",
    re.IGNORECASE,
)
_PAREN = re.compile(r"\((\d{1,2})\)\s+")
_DOTTED = re.compile(r"(?m)^\s*(\d{1,2})[.)]\s+")
_STOP = re.compile(
    r"\n\s*(?:Related Work|Related-Work|Methodology|Methods|Experiments|"
    r"Preliminaries|Background|Related works)\b",
    re.IGNORECASE,
)
_TRAILING_HEADING = re.compile(
    r"\s+(?:Related Work|Methodology|Methods|Experiments)\s*$",
    re.IGNORECASE,
)


def _column_streams(text: str) -> list[str]:
    """Yield left column, right column, and raw text for two-column PDF extracts."""
    left: list[str] = []
    right: list[str] = []
    for line in text.splitlines():
        parts = [part for part in re.split(r" {2,}", line.strip()) if part]
        if not parts:
            continue
        left.append(parts[0])
        right.append(parts[-1] if len(parts) > 1 else parts[0])
    return ["\n".join(left), "\n".join(right), text]


def _dehyphenate(text: str) -> str:
    return re.sub(r"-\n\s*(?=[a-z])", "", text)


def _collapse(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def _looks_like_claim(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith(("we ", "this ", "our ", "a ", "an ", "the ", "it "))


def _extract_from_stream(text: str) -> list[dict[str, object]]:
    header = _HEADER.search(text)
    if not header:
        return []
    window = text[header.start() : header.start() + 8000]
    stop = _STOP.search(window[120:] if len(window) > 120 else window)
    if stop:
        offset = 120 if len(window) > 120 else 0
        window = window[: offset + stop.start()]
    window = _dehyphenate(window)
    scored: list[list[dict[str, object]]] = []
    for markers in (list(_PAREN.finditer(window)), list(_DOTTED.finditer(window))):
        if len(markers) < 2:
            continue
        found: dict[int, str] = {}
        for position, match in enumerate(markers):
            index = int(match.group(1))
            if index < 1 or index > 6 or index in found:
                continue
            start = match.end()
            end = markers[position + 1].start() if position + 1 < len(markers) else len(window)
            body = _TRAILING_HEADING.sub("", _collapse(window[start:end])).strip()
            if len(body) < 24 or not _looks_like_claim(body):
                continue
            found[index] = body[:600]
        items: list[dict[str, object]] = []
        expected = 1
        while expected in found:
            items.append({"index": expected, "text": found[expected]})
            expected += 1
        if len(items) >= 2:
            scored.append(items)
    if not scored:
        return []
    return max(scored, key=lambda items: (len(items), sum(str(item["text"]).lower().startswith("we ") for item in items)))


def extract_listed_contributions(text: str) -> list[dict[str, object]]:
    """Return [{index, text}, ...] for numbered contributions, else []."""
    if not text.strip():
        return []
    candidates = [_extract_from_stream(stream) for stream in _column_streams(text)]
    candidates = [items for items in candidates if items]

    def score(items: list[dict[str, object]]) -> tuple[int, int, int]:
        we = sum(str(item["text"]).lower().startswith("we ") for item in items)
        n = len(items)
        return (we, 1 if n in {3, 4} else 0, n)

    return max(candidates, key=score) if candidates else []
