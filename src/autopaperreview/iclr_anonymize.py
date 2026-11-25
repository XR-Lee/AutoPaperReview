"""Deterministic ICLR submission anonymizer. No model calls.

Camera-ready PDFs name authors, affiliations, emails, acknowledgements, and
a "Published as a conference paper" banner. Rejected PDFs stay anonymous, so
those strings leak the decision. This module strips the factual markers.
It does not judge the science and it does not mask the title.
"""

from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_GITHUB = re.compile(
    r"(?i)\b(?:https?://)?(?:www\.)?github\.com/(?!anonymous(?:/|\b))[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?"
)
_STATUS_LINE = re.compile(
    r"(?i)^\s*(?:published as a conference paper|under review as a conference paper|"
    r"accepted (?:to|at) (?:iclr|neurips|icml|cvpr|acl)|"
    r"camera[\s-]*ready(?:\s+(?:version|paper|copy))?|"
    r"final published version)\b"
)
_STATUS_INLINE = re.compile(
    r"(?i)\b(?:published as a conference paper|under review as a conference paper|"
    r"accepted (?:to|at) (?:iclr|neurips|icml|cvpr|acl)\b[^\n.]{0,80}|"
    r"\bcamera[\s-]*ready(?:\s+(?:version|paper|copy))?|"
    r"\bfinal published version of (?:this|the) (?:paper|proceedings)\b)"
)
_ABSTRACT = re.compile(
    r"(?i)^[ \t]*(?:\d+(?:\.\d+)*\.?[ \t]+)?abstract\b[ \t]*[:.]?[ \t]*(.*)$"
)
_ACK_HEAD = re.compile(
    r"(?i)^[ \t]*(?:\d+(?:\.\d+)*\.?[ \t]+)?"
    r"(?:acknowledgements?|acknowledgments?|funding(?:[ \t]+information)?|"
    r"acknowledgements?[ \t]+and[ \t]+funding|acknowledgments?[ \t]+and[ \t]+funding|"
    r"funding[ \t]+and[ \t]+acknowledgements?|funding[ \t]+and[ \t]+acknowledgments?)"
    r"(?:[ \t]*[:.][ \t]*|[ \t]*$)"
)
_SECTION_END = re.compile(
    r"(?i)^[ \t]*(?:\d+(?:\.\d+)*\.?[ \t]+|[A-Z]\.[ \t]+)?"
    r"(?:references|bibliography|appendix|supplementary(?:[ \t]+material)?|"
    r"\d+[ \t]+[A-Za-z])\b"
)
_AFFILIATION = re.compile(
    r"(?i)\b(?:university|universit[aä]t|institute|institutes|laboratory|laboratories|"
    r"department|school of|college of|faculty|google|microsoft|nvidia|meta ai|"
    r"deepmind|openai|fair|research\b.*\buniversity)\b"
)
_AUTHOR_META = re.compile(
    r"(?i)\b(?:corresponding author|equal contribution|work done (?:at|while)|"
    r"interned at|currently at|affiliation\s*\d)\b"
)
_ARXIV_STAMP = re.compile(
    r"(?i)^\s*arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?(?:\s*\[[^\]]+\])?(?:\s+\d{1,2}\s+\w+\s+\d{4})?\s*$"
)
_ARXIV_ID = re.compile(r"(?i)\barxiv:\s*(\d{4}\.\d{4,5})(v\d+)?")


def own_arxiv_ids(text: str, *, head_lines: int = 30) -> list[str]:
    """arXiv ids printed as a stamp on the first page, not ids inside the bibliography."""
    found: list[str] = []
    for line in text.splitlines()[:head_lines]:
        if _ABSTRACT.match(line.strip()):
            break
        for match in _ARXIV_ID.finditer(line):
            token = match.group(1) + (match.group(2) or "")
            if token not in found:
                found.append(token)
    return found


def _strip_status(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if _STATUS_LINE.match(line):
            continue
        cleaned = _STATUS_INLINE.sub("", line)
        if cleaned.strip():
            kept.append(cleaned.rstrip())
        elif line.strip() == "":
            kept.append("")
    return "\n".join(kept)


def _split_abstract(lines: list[str]) -> tuple[list[str], list[str]] | None:
    for index, line in enumerate(lines):
        match = _ABSTRACT.match(line.strip())
        if not match:
            continue
        rest = match.group(1).strip()
        head = "Abstract" if not rest else "Abstract\n" + rest
        return lines[:index], [head, *lines[index + 1 :]]
    return None


def _authorish(line: str) -> bool:
    if _EMAIL.search(line) or _AUTHOR_META.search(line) or _AFFILIATION.search(line):
        return True
    if _GITHUB.search(line):
        return True
    return False


def _drop_preamble_authors(text: str) -> str:
    lines = text.splitlines()
    split = _split_abstract(lines)
    if split is None:
        kept: list[str] = []
        for index, line in enumerate(lines):
            if index < 50 and _authorish(line):
                continue
            if index < 25 and _ARXIV_STAMP.match(line):
                continue
            kept.append(line)
        return "\n".join(kept)
    preamble, body = split
    title: list[str] = []
    for line in preamble:
        if _ARXIV_STAMP.match(line) or not line.strip():
            continue
        title.append(line.strip())
        break
    return "\n".join([*title, "", *body])


def _drop_acknowledgements(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if not skipping and _ACK_HEAD.match(stripped):
            skipping = True
            continue
        if skipping and _SECTION_END.match(stripped) and not _ACK_HEAD.match(stripped):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _collapse(text: str) -> str:
    collapsed = re.sub(r"\n{3,}", "\n\n", text).strip()
    return collapsed + ("\n" if collapsed else "")


def anonymize_iclr_submission(text: str) -> tuple[str, dict[str, int]]:
    """Return anonymized manuscript text and counts of removed marker hits.

    The title is kept. Author blocks before the abstract, acknowledgement and
    funding sections, emails, non-anonymous GitHub links, camera-ready banners,
    and a leading arXiv stamp are removed.
    """
    before = residual_markers(text)
    cleaned = _strip_status(text)
    cleaned = _drop_preamble_authors(cleaned)
    cleaned = _drop_acknowledgements(cleaned)
    cleaned = _GITHUB.sub("[URL]", cleaned)
    cleaned = _EMAIL.sub("[EMAIL]", cleaned)
    cleaned = _collapse(cleaned)
    after = residual_markers(cleaned)
    removed = {key: max(0, before.get(key, 0) - after.get(key, 0)) for key in before}
    return cleaned, removed


def residual_markers(text: str) -> dict[str, int]:
    """Counts of decision-leaking markers. No matched text is returned."""
    lines = text.splitlines()
    preamble: list[str] = []
    for line in lines:
        if _ABSTRACT.match(line.strip()):
            break
        preamble.append(line)
    preamble_text = "\n".join(preamble)
    ack_headings = sum(1 for line in lines if _ACK_HEAD.match(line.strip()))
    status_hits = len(_STATUS_LINE.findall(text)) + len(_STATUS_INLINE.findall(text))
    return {
        "email": len(_EMAIL.findall(text)),
        "github_named": len(_GITHUB.findall(text)),
        "acknowledgement_heading": ack_headings,
        "camera_ready_or_status": status_hits,
        "affiliation_before_abstract": len(_AFFILIATION.findall(preamble_text)),
        "arxiv_stamp_before_abstract": len(_ARXIV_STAMP.findall(preamble_text)),
    }


def residual_total(counts: dict[str, int]) -> int:
    return sum(int(value) for value in counts.values())


def audit_by_tier(
    rows: list[dict[str, Any]],
    *,
    apply_anonymizer: bool = True,
) -> dict[str, Any]:
    """Per-tier residual counts.

    Each row needs ``tier`` and ``text``. Identifiers on the row are ignored
    and are not copied into the result.
    """
    tiers: dict[str, dict[str, int]] = {}
    files = {"rows": 0, "empty": 0}
    for row in rows:
        tier = str(row.get("tier") or "unknown")
        text = str(row.get("text") or "")
        bucket = tiers.setdefault(
            tier,
            {
                "papers": 0,
                "email": 0,
                "github_named": 0,
                "acknowledgement_heading": 0,
                "camera_ready_or_status": 0,
                "affiliation_before_abstract": 0,
                "arxiv_stamp_before_abstract": 0,
                "papers_with_any_marker": 0,
            },
        )
        files["rows"] += 1
        if not text.strip():
            files["empty"] += 1
            continue
        scanned = anonymize_iclr_submission(text)[0] if apply_anonymizer else text
        counts = residual_markers(scanned)
        bucket["papers"] += 1
        for key, value in counts.items():
            bucket[key] += int(value)
        if residual_total(counts) > 0:
            bucket["papers_with_any_marker"] += 1
    return {
        "anonymizer_applied": apply_anonymizer,
        "rows": files["rows"],
        "empty": files["empty"],
        "tiers": tiers,
    }
