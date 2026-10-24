"""Annotate a manuscript PDF with cited evidence.

Each cited evidence id keeps its highlight and gains a visible margin tag,
a real PDF comment, and a row on an appended evidence-index page. A quote
that cannot be found becomes a page-level note instead of being dropped.

This does not change review text, scores, or the Markdown/PDF report.
Re-running it only needs the finished package and the source PDF.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .figures import sibling_pdf
from .models import (
    AnchorKind,
    CommentIntent,
    EvidenceRecord,
    ReviewClaimKind,
    ReviewIssue,
    ReviewPackage,
    Severity,
)
from .readability import dimension_label

AUTHOR = "APR reviewer"

# Highlight strokes stay light enough to read the manuscript through them.
HIGHLIGHT_COLORS = {
    "strength": (0.45, 0.82, 0.52),
    "weakness": (0.98, 0.45, 0.42),
    "question": (0.45, 0.68, 0.98),
    "neutral": (0.98, 0.78, 0.28),
}
# Margin tags and note icons use the darker role color.
MARKER_COLORS = {
    "strength": (0.10, 0.45, 0.26),
    "weakness": (0.75, 0.14, 0.16),
    "question": (0.12, 0.32, 0.70),
    "neutral": (0.55, 0.38, 0.05),
}
ROLE_RANK = {"weakness": 0, "question": 1, "strength": 2, "neutral": 3}
SEVERITY_RANK = {
    Severity.critical: 0,
    Severity.major: 1,
    Severity.moderate: 2,
    Severity.minor: 3,
    Severity.editorial: 4,
}
_LEGEND = (
    ("strength", "Strength"),
    ("weakness", "Weakness"),
    ("question", "Question"),
    ("neutral", "Neutral"),
)
_KIND_ROLE = {
    ReviewClaimKind.summary: "neutral",
    ReviewClaimKind.strength: "strength",
    ReviewClaimKind.weakness: "weakness",
    ReviewClaimKind.question: "question",
    ReviewClaimKind.comment: "neutral",
}
_KIND_LABEL = {
    ReviewClaimKind.summary: "Summary",
    ReviewClaimKind.strength: "Strength",
    ReviewClaimKind.weakness: "Weakness",
    ReviewClaimKind.question: "Question",
    ReviewClaimKind.comment: "Comment",
}
_PAGE_HINT = re.compile(r"\b(?:pages?|pp?)\.?\s*(\d{1,4})\b", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_CJK_CHAR = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# "Evidence" does not match: the character after E must not be a letter.
_EVIDENCE_ID = re.compile(
    r"(?<![A-Za-z0-9])(E(?![A-Za-z])[A-Za-z0-9._-]*[A-Za-z0-9])",
    re.IGNORECASE,
)
_EVIDENCE_LINK = re.compile(
    r"\[\[?\s*E(?![A-Za-z])[A-Za-z0-9._-]*[A-Za-z0-9]\s*\]\([^)]*\)\]?",
    re.IGNORECASE,
)
_SEVERITY_WORD = re.compile(r"\b(critical|major|moderate|minor)\b", re.IGNORECASE)
_ARROW = re.compile(r"(?:-->|=>|->|←|→|⇒|⇐|⟶|⟵|➔|➜|➡|↔|⇔|⟹|⟸)")
_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)
_ROLE_WORD = {
    "strength": "strength",
    "strengths": "strength",
    "优点": "strength",
    "weakness": "weakness",
    "weaknesses": "weakness",
    "不足": "weakness",
    "question": "question",
    "questions": "question",
    "问题": "question",
}
_REVIEW_LABEL = {"strength": "Strength", "weakness": "Weakness", "question": "Question"}
_CJK_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
)
_SKIP_PDF_NAMES = {"review_report.pdf", "annotated_manuscript.pdf"}


def pymupdf_available() -> bool:
    return importlib.util.find_spec("pymupdf") is not None


@dataclass(frozen=True)
class EvidenceAnnotation:
    """One cited evidence item after it has been placed on the PDF."""

    evidence_id: str
    role: str
    labels: tuple[str, ...]
    note: str
    page: int
    located: bool
    placement: str
    quote: str | None = None
    dest: tuple[float, float] | None = None

    @property
    def field(self) -> str:
        return "; ".join(self.labels) or "Evidence"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "role": self.role,
            "field": self.field,
            "page": self.page,
            "located": self.located,
            "placement": self.placement,
            "note": self.note,
        }


@dataclass(frozen=True)
class AnnotationRequest:
    package_path: Path
    pdf_path: Path
    output_path: Path
    project_dir: Path | None = None
    run_dir: Path | None = None


@dataclass(frozen=True)
class _Citation:
    role: str
    label: str
    note: str


def _clip(text: str, limit: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _two_sentences(text: str, limit: int = 420) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return ""
    parts = [part for part in _SENTENCE_BREAK.split(collapsed) if part]
    chosen = " ".join(parts[:2]) if parts else collapsed
    return _clip(chosen, limit)


def _norm_pieces(raw: str) -> list[str]:
    """Tokens for one already-joined word, with line numbers removed.

    Ligatures, arrows, and brackets fold onto the same token stream on both
    the quote and the page. Digit-only tokens are margin line numbers or
    numerals that the quote may have absorbed from those margins.
    """
    text = unicodedata.normalize("NFKC", raw).casefold().replace("\u00ad", "")
    text = text.translate(_LIGATURES)
    text = _ARROW.sub(" arrow ", text)
    text = text.replace("-", "")
    pieces = []
    for part in text.split():
        token = "".join(char for char in part if char.isalnum())
        if token and not token.isdigit():
            pieces.append(token)
    return pieces


def _natural_id(evidence_id: str) -> list[Any]:
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", evidence_id)
    ]


def _page_hint(evidence: EvidenceRecord) -> int | None:
    if evidence.anchor is not None and evidence.anchor.page is not None:
        return evidence.anchor.page
    match = _PAGE_HINT.search(evidence.locator or "")
    if match:
        return int(match.group(1))
    return None


def _quote_text(evidence: EvidenceRecord) -> str | None:
    if evidence.excerpt and evidence.excerpt.strip():
        return evidence.excerpt.strip()
    if evidence.anchor is not None and evidence.anchor.excerpt and evidence.anchor.excerpt.strip():
        return evidence.anchor.excerpt.strip()
    if evidence.claim and evidence.claim.strip():
        return evidence.claim.strip()
    return None


def _quote_tokens(quote: str) -> list[str]:
    text = unicodedata.normalize("NFKC", quote).replace("\u00ad", "")
    text = text.translate(_LIGATURES)
    # A line-break hyphen in the stored quote is the same word as the page.
    text = re.sub(r"-\s+", "", text)
    text = text.casefold()
    text = _ARROW.sub(" arrow ", text)
    text = text.replace("-", "")
    tokens = []
    for part in text.split():
        token = "".join(char for char in part if char.isalnum())
        if token and not token.isdigit():
            tokens.append(token)
    return tokens


def _heading_role(title: str) -> str | None:
    cleaned = re.sub(r"[*_`]", "", title).strip()
    for segment in re.split(r"\s*/\s*|\s+\|\s+", cleaned):
        match = re.match(r"([A-Za-z]+|[\u3400-\u9fff]+)", segment.strip())
        if match is None:
            continue
        role = _ROLE_WORD.get(match.group(1).casefold())
        if role is not None:
            return role
    return None


def _ids_in(text: str, known: set[str]) -> list[str]:
    by_fold = {item.casefold(): item for item in known}
    found: list[str] = []
    for match in _EVIDENCE_ID.finditer(text):
        canonical = by_fold.get(match.group(1).casefold())
        if canonical and canonical not in found:
            found.append(canonical)
    return found


def _section_blocks(lines: list[str]) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        if current and _BULLET.match(line.strip()) and not line[:1].isspace():
            blocks.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block) for block in blocks]


def _note_from_block(text: str) -> str:
    stripped = _EVIDENCE_LINK.sub(" ", text)
    stripped = _EVIDENCE_ID.sub(" ", stripped)
    stripped = re.sub(r"\[(?:\s|,)*\]", " ", stripped)
    return _two_sentences(stripped)


def citations_from_review(markdown: str, known: set[str]) -> dict[str, list[_Citation]]:
    """Role citations from Strengths, Weaknesses, and Questions sections."""
    citations: dict[str, list[_Citation]] = {}
    if not markdown.strip() or not known:
        return citations
    role: str | None = None
    section: list[str] = []
    sections: list[tuple[str | None, list[str]]] = []
    for line in markdown.splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            sections.append((role, section))
            role = _heading_role(heading.group(2))
            section = []
            continue
        section.append(line)
    sections.append((role, section))

    seen: set[tuple[str, str]] = set()
    for section_role, lines in sections:
        if section_role is None:
            continue
        number = 0
        for block in _section_blocks(lines):
            evidence_ids = _ids_in(block, known)
            if not evidence_ids:
                continue
            number += 1
            label = f"{_REVIEW_LABEL[section_role]} {number}"
            if section_role == "weakness":
                severity = _SEVERITY_WORD.search(block)
                if severity:
                    label = f"{label} ({severity.group(1).casefold()})"
            note = _note_from_block(block)
            for evidence_id in evidence_ids:
                key = (evidence_id, section_role)
                if key in seen:
                    continue
                seen.add(key)
                _add(citations, evidence_id, _Citation(section_role, label, note))
    return citations


def apply_review_roles(
    citations: dict[str, list[_Citation]],
    markdown: str | None,
    known: set[str],
) -> None:
    """Add a section role when the package citation does not already have it."""
    if not markdown:
        return
    for evidence_id, items in citations_from_review(markdown, known).items():
        existing = {item.role for item in citations.get(evidence_id, [])}
        for citation in items:
            if citation.role in existing:
                continue
            _add(citations, evidence_id, citation)
            existing.add(citation.role)


def _issue_role(issue: ReviewIssue) -> str:
    if issue.first_read is not None:
        if issue.first_read.intent == CommentIntent.praise:
            return "strength"
        if issue.first_read.intent == CommentIntent.question:
            return "question"
    if issue.severity in {Severity.critical, Severity.major, Severity.moderate}:
        return "weakness"
    return "neutral"


def _issue_note(issue: ReviewIssue) -> str:
    if issue.first_read is not None and issue.first_read.explanation.primary.strip():
        return _two_sentences(issue.first_read.explanation.primary)
    if issue.impact.primary.strip():
        return _two_sentences(issue.impact.primary)
    return _two_sentences(issue.evidence.primary)


def _worst_severity(issues: list[ReviewIssue]) -> str | None:
    if not issues:
        return None
    chosen = min(issues, key=lambda issue: SEVERITY_RANK[issue.severity])
    return chosen.severity.value


def _add(bucket: dict[str, list[_Citation]], evidence_id: str, citation: _Citation) -> None:
    bucket.setdefault(evidence_id, []).append(citation)


def collect_citations(package: ReviewPackage) -> dict[str, list[_Citation]]:
    """Map each cited evidence id to the review fields that use it."""
    known = {item.id for item in package.evidence}
    issues_for: dict[str, list[ReviewIssue]] = {}
    for issue in package.issues:
        for evidence_id in issue.evidence_ids:
            issues_for.setdefault(evidence_id, []).append(issue)

    citations: dict[str, list[_Citation]] = {}
    kind_counts = {kind: 0 for kind in ReviewClaimKind}
    for claim in package.claims:
        kind_counts[claim.kind] += 1
        number = kind_counts[claim.kind]
        role = _KIND_ROLE[claim.kind]
        base = _KIND_LABEL[claim.kind]
        label = base if claim.kind == ReviewClaimKind.summary else f"{base} {number}"
        note = _two_sentences(claim.text.primary)
        for evidence_id in claim.evidence_ids:
            if evidence_id not in known:
                continue
            specific = label
            if claim.kind == ReviewClaimKind.weakness:
                severity = _worst_severity(issues_for.get(evidence_id, []))
                if severity:
                    specific = f"{label} ({severity})"
            _add(citations, evidence_id, _Citation(role, specific, note))

    for issue in package.issues:
        note = _issue_note(issue)
        label = f"Issue {issue.id} ({issue.severity.value})"
        role = _issue_role(issue)
        for evidence_id in issue.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation(role, label, note))

    for score in package.dimension_scores:
        label = f"Dimension: {dimension_label(score.dimension, 'en')}"
        note = _two_sentences(score.rationale.primary) if score.rationale else label
        for evidence_id in score.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", label, note))

    for item in package.venue_conclusions:
        label = f"Venue {item.id} ({item.outcome.value})"
        note = _two_sentences(item.rationale.primary)
        for evidence_id in item.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", label, note))

    for item in package.ledger_claims:
        note = _two_sentences(item.claim.primary)
        for evidence_id in item.in_paper_evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", f"Ledger {item.id}", note))

    for item in package.integrity_records:
        note = _two_sentences(item.notes.primary) if item.notes else item.subject
        for evidence_id in item.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", f"Integrity {item.id}", note))

    for item in package.novelty_assessments:
        note = _two_sentences(item.notes.primary) if item.notes else item.tag.value
        for evidence_id in item.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", f"Novelty {item.id}", note))

    for item in package.visual_findings:
        note = _two_sentences(item.visible_mark)
        for evidence_id in item.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", f"Visual {item.id}", note))

    if package.overall_score is not None:
        note = _two_sentences(package.overall_score.notes or "Overall score")
        for evidence_id in package.overall_score.evidence_ids:
            if evidence_id not in known:
                continue
            _add(citations, evidence_id, _Citation("neutral", "Overall score", note))

    for item in package.cap_exemptions:
        for evidence_id in item.decision_basis_evidence_ids:
            if evidence_id not in known:
                continue
            _add(
                citations,
                evidence_id,
                _Citation("neutral", f"Cap exemption {item.id}", "Decision-basis evidence for a cap exemption."),
            )
    return citations


def _ordered_labels(citations: list[_Citation]) -> tuple[str, ...]:
    ranked = sorted(enumerate(citations), key=lambda pair: (ROLE_RANK[pair[1].role], pair[0]))
    labels: list[str] = []
    for _, citation in ranked:
        if citation.label not in labels:
            labels.append(citation.label)
    return tuple(labels)


def _primary_role(citations: list[_Citation]) -> str:
    return min(citations, key=lambda citation: ROLE_RANK[citation.role]).role


def _reviewer_note(evidence: EvidenceRecord, issues: list[ReviewIssue], citations: list[_Citation]) -> str:
    ranked_issues = sorted(issues, key=lambda issue: SEVERITY_RANK[issue.severity])
    for issue in ranked_issues:
        if issue.first_read is not None and issue.first_read.explanation.primary.strip():
            return _two_sentences(issue.first_read.explanation.primary)
    for citation in sorted(citations, key=lambda item: ROLE_RANK[item.role]):
        if citation.note.strip():
            return citation.note
    return _two_sentences(evidence.claim)


def _comment(evidence_id: str, field: str, note: str, status: str) -> str:
    return f"{evidence_id}\n{field}\n\n{note}\n\n{status}"


def _line_rects(spans: list[Any]) -> list[Any]:
    import pymupdf

    lines: list[pymupdf.Rect] = []
    for rect in spans:
        if lines and abs(lines[-1].y0 - rect.y0) <= 3:
            lines[-1].include_rect(rect)
        else:
            lines.append(pymupdf.Rect(rect))
    return lines


def _digit_word(text: str) -> bool:
    pieces = _norm_pieces(text)
    return not pieces


def _tokens_on_page(page: Any) -> list[tuple[str, Any]]:
    import pymupdf

    raw_words: list[tuple[str, pymupdf.Rect]] = []
    for word in page.get_text("words") or []:
        raw_words.append((str(word[4]), pymupdf.Rect(word[0], word[1], word[2], word[3])))
    # A line-end hyphen is one token, but each physical line keeps its own rect
    # so the highlight does not paint the gap between the lines. Margin line
    # numbers sit between those words and must not become part of the join.
    merged: list[tuple[str, list[pymupdf.Rect]]] = []
    pending: tuple[str, list[pymupdf.Rect]] | None = None
    for text, rect in raw_words:
        if _digit_word(text):
            merged.append((text, [rect]))
            continue
        if pending is not None:
            stem, rects = pending
            pending = None
            if abs(rects[-1].y0 - rect.y0) > 2:
                merged.append((stem + text, [*rects, rect]))
                continue
            merged.append((stem + "-", rects))
        if text.endswith("-") and not text.endswith("--"):
            pending = (text[:-1], [rect])
            continue
        merged.append((text, [rect]))
    if pending is not None:
        stem, rects = pending
        merged.append((stem + "-", rects))
    tokens: list[tuple[str, list[pymupdf.Rect]]] = []
    for text, rects in merged:
        for token in _norm_pieces(text):
            tokens.append((token, rects))
    return tokens


def _word_rects(page: Any) -> list[Any]:
    import pymupdf

    rects = []
    for word in page.get_text("words") or []:
        rects.append(pymupdf.Rect(word[0], word[1], word[2], word[3]))
    return rects


def _find_tokens(tokens: list[tuple[str, Any]], quote_tokens: list[str]) -> list[Any] | None:
    count = len(quote_tokens)
    if count == 0 or count > len(tokens):
        return None
    for start in range(len(tokens) - count + 1):
        if all(tokens[start + offset][0] == quote_tokens[offset] for offset in range(count)):
            spans: list[Any] = []
            for offset in range(count):
                spans.extend(tokens[start + offset][1])
            return _line_rects(spans)
    return None


def _fuzzy_find(tokens: list[tuple[str, Any]], quote_tokens: list[str]) -> tuple[list[Any], float] | None:
    """Recover a span when a few tokens differ or a line number was stored in the quote."""
    count = len(quote_tokens)
    page_count = len(tokens)
    if count < 4 or page_count < 4:
        return None
    budget = max(1, count // 5)
    if page_count < count - budget:
        return None
    page_words = [item[0] for item in tokens]
    anchors = set(quote_tokens[: budget + 1])
    window = count + budget
    best: tuple[tuple[float, int, int], int, int] | None = None
    for start, word in enumerate(page_words):
        if word not in anchors:
            continue
        segment = page_words[start : start + window]
        matcher = difflib.SequenceMatcher(a=quote_tokens, b=segment, autojunk=False)
        blocks = [block for block in matcher.get_matching_blocks() if block.size]
        if not blocks:
            continue
        matched = sum(block.size for block in blocks)
        if matched < 4 or matched / count < 0.75:
            continue
        first = blocks[0]
        last = blocks[-1]
        local_end = last.b + last.size
        span = local_end - first.b
        extra = span - matched
        missing = count - matched
        if extra > budget or missing > budget:
            continue
        abs_start = start + first.b
        abs_end = start + local_end
        score = matched / count
        key = (score, -(extra + missing), -abs_start)
        if best is None or key > best[0]:
            best = (key, abs_start, abs_end)
    if best is None:
        return None
    _, abs_start, abs_end = best
    spans: list[Any] = []
    for index in range(abs_start, abs_end):
        spans.extend(tokens[index][1])
    return _line_rects(spans), best[0][0]


def _pick_hit(
    hits: list[tuple[int, list[Any], float]],
    hint: int | None,
) -> tuple[int, list[Any]]:
    pool = hits
    if hint is not None:
        hinted = [hit for hit in hits if hit[0] == hint - 1]
        if hinted:
            pool = hinted
    pool.sort(key=lambda hit: (-hit[2], hit[0]))
    return pool[0][0], pool[0][1]


def _region_rect(evidence: EvidenceRecord, page: Any) -> Any | None:
    """Return a page rect for a stored region.

    Coordinates are points with the origin at the top-left, matching PyMuPDF.
    A box that misses the page is flipped once from PDF bottom-left space.
    """
    import pymupdf

    anchor = evidence.anchor
    if anchor is None or anchor.page is None or anchor.kind != AnchorKind.pdf_region:
        return None
    if not anchor.bbox or len(anchor.bbox) != 4:
        return None
    x0, y0, x1, y1 = (float(value) for value in anchor.bbox)
    rect = pymupdf.Rect(x0, y0, x1, y1)
    if rect.is_empty or rect.is_infinite:
        return None
    if rect.intersects(page.rect):
        return rect
    flipped = pymupdf.Rect(x0, page.rect.height - y1, x1, page.rect.height - y0)
    if flipped.intersects(page.rect):
        return flipped
    return None


def _clamp_page(number: int | None, page_count: int) -> int:
    if page_count < 1:
        return 1
    if number is None or number < 1:
        return 1
    return min(number, page_count)


def _cjk_file() -> str | None:
    for raw in _CJK_FONTS:
        if Path(raw).is_file():
            return raw
    return None


def _font_for(page: Any, text: str, ready: set[int]) -> str:
    """Latin text stays on the built-in Helvetica face.

    A CJK face substitutes a non-breaking space for the ASCII space, so an
    index title drawn with that face no longer extracts as ``Evidence index``.
    """
    if not text or _CJK_CHAR.search(text) is None:
        return "helv"
    if page.number in ready:
        return "apr-cjk"
    path = _cjk_file()
    if path is None:
        return "helv"
    try:
        page.insert_font(fontname="apr-cjk", fontfile=path)
    except (RuntimeError, ValueError):
        return "helv"
    ready.add(page.number)
    return "apr-cjk"


def _top_is_clear(page: Any, band: float = 40) -> bool:
    for block in page.get_text("blocks") or []:
        if len(block) < 5:
            continue
        if block[1] < band and str(block[4]).strip():
            return False
    return True


def _draw_legend(page: Any, x: float, y: float, ready: set[int] | None = None) -> float:
    """Draw the role legend. `y` is the top of the swatches. Returns the next y."""
    import pymupdf

    fonts = ready if ready is not None else set()
    page.insert_text(
        (x, y + 8),
        "Legend",
        fontsize=8,
        fontname=_font_for(page, "Legend", fonts),
        color=(0.25, 0.25, 0.25),
    )
    cursor = x + 40
    for role, label in _LEGEND:
        swatch = pymupdf.Rect(cursor, y, cursor + 9, y + 9)
        page.draw_rect(swatch, color=MARKER_COLORS[role], fill=HIGHLIGHT_COLORS[role], width=0.6)
        page.insert_text(
            (cursor + 12, y + 8),
            label,
            fontsize=8,
            fontname=_font_for(page, label, fonts),
            color=(0.15, 0.15, 0.15),
        )
        cursor += 12 + 4.5 * len(label) + 14
    return y + 18


def _finish(
    annot: Any,
    *,
    title: str,
    content: str,
    subject: str,
    stroke: tuple[float, float, float] | None = None,
    opacity: float | None = None,
) -> None:
    import pymupdf

    annot.set_info(title=title, content=content, subject=subject)
    if stroke is not None:
        annot.set_colors(stroke=stroke)
    if opacity is not None:
        annot.set_opacity(opacity)
    annot.set_flags(annot.flags | pymupdf.PDF_ANNOT_IS_PRINT)
    annot.update()


def _tag_label(evidence_id: str) -> str:
    if len(evidence_id) <= 18:
        return evidence_id
    return evidence_id[:15] + "..."


def _allocate_margin(
    page: Any,
    anchor: Any,
    label: str,
    occupied: list[Any],
    words: list[Any],
) -> tuple[Any, Any]:
    """Put the icon and E# tag in the free margin that has more room.

    The slot has to miss the anchor, already placed tags, and every text
    glyph, including template line numbers in the left margin.
    """
    import pymupdf

    tag_width = min(130.0, max(26.0, 4.3 * len(label) + 8))
    icon_width = 16.0
    gap = 2.0
    height = 14.0
    total = icon_width + gap + tag_width
    pad = 1.5
    line_words = [
        rect
        for rect in words
        if rect.y0 < anchor.y1 + 2 and rect.y1 > anchor.y0 - 2
    ]
    if line_words:
        content_left = min(rect.x0 for rect in line_words)
        content_right = max(rect.x1 for rect in line_words)
    else:
        content_left = anchor.x0
        content_right = anchor.x1
    left_room = content_left - page.rect.x0
    right_room = page.rect.x1 - content_right
    sides = ("right", "left") if right_room >= left_room else ("left", "right")

    def blocked(rect: Any) -> bool:
        padded = pymupdf.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
        if padded.intersects(anchor):
            return True
        if any(padded.intersects(other) for other in occupied):
            return True
        return any(padded.intersects(word) for word in words)

    def x_positions(side: str) -> list[float]:
        if side == "right":
            outer = page.rect.x1 - 4 - total
            beside = content_right + 6
            ordered = [outer, beside] if right_room >= left_room else [beside, outer]
        else:
            outer = page.rect.x0 + 4
            beside = content_left - 6 - total
            ordered = [beside, outer] if left_room >= right_room else [outer, beside]
        chosen: list[float] = []
        for value in ordered:
            if value < page.rect.x0 or value + total > page.rect.x1:
                continue
            if value not in chosen:
                chosen.append(value)
        return chosen

    chosen_rect = None
    for side in sides:
        for x0 in x_positions(side):
            for nudge in range(48):
                y0 = anchor.y0 + nudge * (height + 2)
                rect = pymupdf.Rect(x0, y0, x0 + total, y0 + height)
                if rect.y1 > page.rect.y1 - 4:
                    break
                if blocked(rect):
                    continue
                chosen_rect = rect
                break
            if chosen_rect is not None:
                break
        if chosen_rect is not None:
            break
    if chosen_rect is None:
        x0 = page.rect.x1 - 4 - total if right_room >= left_room else page.rect.x0 + 4
        x0 = min(max(x0, page.rect.x0), page.rect.x1 - total)
        chosen_rect = pymupdf.Rect(x0, anchor.y0, x0 + total, anchor.y0 + height)
        guard = 0
        while any(chosen_rect.intersects(other) for other in occupied) and guard < 40:
            chosen_rect.y0 += height + 2
            chosen_rect.y1 += height + 2
            guard += 1
        if chosen_rect.y1 > page.rect.y1 - 4:
            chosen_rect.y1 = page.rect.y1 - 4
            chosen_rect.y0 = chosen_rect.y1 - height
    occupied.append(pymupdf.Rect(chosen_rect))
    icon = pymupdf.Rect(chosen_rect.x0, chosen_rect.y0, chosen_rect.x0 + icon_width, chosen_rect.y1)
    tag = pymupdf.Rect(chosen_rect.x0 + icon_width + gap, chosen_rect.y0, chosen_rect.x1, chosen_rect.y1)
    return icon, tag


def _add_marker(
    page: Any,
    anchor: Any,
    *,
    evidence_id: str,
    role: str,
    comment: str,
    occupied: list[Any],
    words: list[Any],
) -> tuple[float, float]:
    import pymupdf

    label = _tag_label(evidence_id)
    icon_rect, tag_rect = _allocate_margin(page, anchor, label, occupied, words)
    marker = MARKER_COLORS[role]
    note = page.add_text_annot(pymupdf.Point(icon_rect.x0, icon_rect.y0), comment, icon="Note")
    _finish(note, title=AUTHOR, content=comment, subject=role, stroke=marker)
    tag = page.add_freetext_annot(
        tag_rect,
        label,
        fontsize=7,
        fontname="helv",
        text_color=(1, 1, 1),
        fill_color=marker,
        align=pymupdf.TEXT_ALIGN_CENTER,
    )
    # Leave the FreeText fill set by add_freetext_annot. A later stroke color
    # replaces that fill in some PyMuPDF versions.
    _finish(tag, title=AUTHOR, content=label, subject=role)
    return float(icon_rect.x0), float(icon_rect.y0)


def _place_highlight(page: Any, rects: list[Any], *, role: str, comment: str) -> None:
    highlight = page.add_highlight_annot(rects)
    _finish(
        highlight,
        title=AUTHOR,
        content=comment,
        subject=role,
        stroke=HIGHLIGHT_COLORS[role],
        opacity=0.45,
    )


def _append_index(doc: Any, placements: list[EvidenceAnnotation], manuscript_pages: int) -> None:
    import pymupdf

    if manuscript_pages < 1:
        return
    base = doc[0].rect
    rows_per_page = max(8, int((base.height - 150) // 36))
    chunks: list[list[EvidenceAnnotation]] = []
    if placements:
        for start in range(0, len(placements), rows_per_page):
            chunks.append(placements[start : start + rows_per_page])
    else:
        chunks.append([])

    # new_page hands back a page that later new_page calls unbind. Fetch
    # each index page by number after every page exists.
    first_index_page = doc.page_count
    for _chunk in chunks:
        doc.new_page(width=base.width, height=base.height)

    for index, chunk in enumerate(chunks):
        page = doc[first_index_page + index]
        ready: set[int] = set()

        def paint(origin: Any, text: str, size: float, color: tuple[float, float, float] | None = None, *, box: bool = False) -> None:
            font = _font_for(page, text, ready)
            options: dict[str, Any] = {"fontsize": size, "fontname": font}
            if color is not None:
                options["color"] = color
            if box:
                page.insert_textbox(origin, text, **options)
            else:
                page.insert_text(origin, text, **options)

        title = "Evidence index" if index == 0 else "Evidence index (continued)"
        paint((48, 40), title, 16, (0.12, 0.12, 0.12))
        paint(
            (48, 58),
            f"{AUTHOR}. Margin tags match the comment popups.",
            9,
            (0.30, 0.30, 0.30),
        )
        next_y = _draw_legend(page, 48, 70, ready)
        if index == 0:
            paint(
                (48, next_y + 12),
                "A page note means the quote was not found. The link still opens that page.",
                8,
                (0.35, 0.35, 0.35),
            )
            next_y += 18
        header_y = next_y + 16
        paint((48, header_y), "Id", 8, (0.25, 0.25, 0.25))
        paint((130, header_y), "Page", 8, (0.25, 0.25, 0.25))
        paint((175, header_y), "Field", 8, (0.25, 0.25, 0.25))
        paint((330, header_y), "Note", 8, (0.25, 0.25, 0.25))
        y = header_y + 8
        if not chunk and index == 0:
            paint((48, y + 16), "No cited evidence items.", 10, (0.20, 0.20, 0.20))
            continue
        for item in chunk:
            row = pymupdf.Rect(44, y, base.width - 40, y + 34)
            swatch = pymupdf.Rect(48, y + 8, 57, y + 17)
            page.draw_rect(
                swatch,
                color=MARKER_COLORS[item.role],
                fill=HIGHLIGHT_COLORS[item.role],
                width=0.4,
            )
            paint(pymupdf.Rect(62, y + 4, 128, y + 32), item.evidence_id, 8, (0.10, 0.10, 0.10), box=True)
            paint(pymupdf.Rect(130, y + 4, 170, y + 32), str(item.page), 8, box=True)
            paint(pymupdf.Rect(175, y + 2, 325, y + 32), _clip(item.field, 80), 7.5, box=True)
            status = "highlighted" if item.placement == "text" else (
                "region highlight" if item.placement == "region" else "page note"
            )
            paint(
                pymupdf.Rect(330, y + 2, base.width - 48, y + 32),
                _clip(f"{status}. {item.note}", 180),
                7.5,
                box=True,
            )
            if item.dest is not None and 1 <= item.page <= manuscript_pages:
                page.insert_link(
                    {
                        "kind": pymupdf.LINK_GOTO,
                        "from": row,
                        "page": item.page - 1,
                        "to": pymupdf.Point(item.dest[0], max(item.dest[1] - 12, 0)),
                    }
                )
            page.draw_line(
                pymupdf.Point(48, y + 34),
                pymupdf.Point(base.width - 48, y + 34),
                color=(0.85, 0.85, 0.85),
                width=0.4,
            )
            y += 36

    try:
        toc = list(doc.get_toc(simple=True) or [])
        first_index = manuscript_pages + 1
        toc.append([1, "Evidence index", first_index])
        for item in placements:
            title = _clip(f"{item.evidence_id} {item.field}", 80)
            toc.append([2, title, item.page])
        doc.set_toc(toc)
    except (RuntimeError, ValueError):
        return


def annotate_pdf(
    pdf_path: Path,
    package: ReviewPackage,
    output_path: Path,
    *,
    review_markdown: str | None = None,
) -> list[EvidenceAnnotation]:
    """Write an annotated copy of `pdf_path`. The source file is left unchanged.

    `review_markdown`, when given, colors each evidence id from the Strengths,
    Weaknesses, or Questions section that cites it.
    """
    import pymupdf

    source = Path(pdf_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"manuscript PDF does not exist: {source}")
    if source == output:
        raise ValueError("annotated PDF output must be a different file from the source PDF")

    by_id = {item.id: item for item in package.evidence}
    citations = collect_citations(package)
    apply_review_roles(citations, review_markdown, set(by_id))
    issues_for: dict[str, list[ReviewIssue]] = {}
    for issue in package.issues:
        for evidence_id in issue.evidence_ids:
            issues_for.setdefault(evidence_id, []).append(issue)
    ordered_ids = sorted(citations, key=_natural_id)

    document = pymupdf.open(source)
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise ValueError("manuscript PDF is encrypted; annotations were not written")
        if document.page_count < 1:
            raise ValueError("manuscript PDF has no pages")

        token_cache: dict[int, list[tuple[str, Any]]] = {}
        word_cache = {index: _word_rects(document[index]) for index in range(document.page_count)}
        occupied: dict[int, list[Any]] = {index: [] for index in range(document.page_count)}
        note_slots: dict[int, int] = {}
        legend_on_first = _top_is_clear(document[0])
        placements: list[EvidenceAnnotation] = []

        prepared: list[tuple[Any, ...]] = []
        for evidence_id in ordered_ids:
            evidence = by_id[evidence_id]
            group = citations[evidence_id]
            role = _primary_role(group)
            labels = _ordered_labels(group)
            note = _reviewer_note(evidence, issues_for.get(evidence_id, []), group)
            quote = _quote_text(evidence)
            quote_tokens = _quote_tokens(quote) if quote else []
            exact_hits: list[tuple[int, list[Any], float]] = []
            fuzzy_hits: list[tuple[int, list[Any], float]] = []
            if quote_tokens:
                for page_index in range(document.page_count):
                    tokens = token_cache.get(page_index)
                    if tokens is None:
                        tokens = _tokens_on_page(document[page_index])
                        token_cache[page_index] = tokens
                    found = _find_tokens(tokens, quote_tokens)
                    if found:
                        exact_hits.append((page_index, found, 1.0))
                if not exact_hits and len(quote_tokens) >= 4:
                    for page_index in range(document.page_count):
                        fuzzy = _fuzzy_find(token_cache[page_index], quote_tokens)
                        if fuzzy is not None:
                            fuzzy_hits.append((page_index, fuzzy[0], fuzzy[1]))
            hint = _page_hint(evidence)
            hits = exact_hits or fuzzy_hits
            chosen = None
            placement = "page-note"
            if hits:
                chosen = _pick_hit(hits, hint)
                placement = "text"
            elif evidence.anchor is not None and evidence.anchor.page is not None:
                region_page = evidence.anchor.page - 1
                if 0 <= region_page < document.page_count:
                    region = _region_rect(evidence, document[region_page])
                    if region is not None:
                        chosen = (region_page, [region])
                        placement = "region"
            if chosen is None:
                page_number = _clamp_page(hint, document.page_count)
                page_index = page_number - 1
                slot = note_slots.get(page_index, 0)
                note_slots[page_index] = slot + 1
                start_y = 48.0 if page_index == 0 and legend_on_first else 36.0
                y0 = start_y + slot * 18.0
                page_rect = document[page_index].rect
                anchor = pymupdf.Rect(page_rect.x1 - 90, y0, page_rect.x1 - 78, y0 + 14)
                status = (
                    f"Quoted span was not found in the PDF. Page-level note on page {page_number}."
                )
                if quote:
                    status = f"{status}\nQuote: {_clip(quote, 180)}"
                prepared.append(
                    (evidence_id, role, labels, note, page_index, placement, anchor, status, False)
                )
            else:
                page_index, rects = chosen
                anchor = rects[0]
                page_number = page_index + 1
                if placement == "text":
                    status = f"Highlighted on page {page_number}."
                else:
                    status = (
                        f"Highlighted on page {page_number} from the stored region. "
                        "The quote text was not found."
                    )
                prepared.append(
                    (evidence_id, role, labels, note, page_index, placement, rects, status, True)
                )

        for item in prepared:
            evidence_id, role, labels, note, page_index, placement, geometry, status, located = item
            page = document[page_index]
            comment = _comment(evidence_id, "; ".join(labels), note, status)
            if located:
                _place_highlight(page, geometry, role=role, comment=comment)
                anchor = geometry[0]
            else:
                anchor = geometry
            dest_x, dest_y = _add_marker(
                page,
                anchor,
                evidence_id=evidence_id,
                role=role,
                comment=comment,
                occupied=occupied[page_index],
                words=word_cache[page_index],
            )
            placements.append(
                EvidenceAnnotation(
                    evidence_id=evidence_id,
                    role=role,
                    labels=labels,
                    note=note,
                    page=page_index + 1,
                    located=located,
                    placement=placement,
                    quote=None,
                    dest=(dest_x, dest_y),
                )
            )

        if legend_on_first:
            _draw_legend(document[0], 36, 10)

        manuscript_pages = document.page_count
        _append_index(document, placements, manuscript_pages)
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(output.name + ".partial")
        try:
            document.save(str(partial))
            partial.replace(output)
        finally:
            if partial.exists():
                partial.unlink()
        return placements
    finally:
        document.close()


def _find_package(run_dir: Path) -> Path | None:
    preferred = [
        run_dir / "stages" / "report" / "review_package.json",
        run_dir / "stages" / "report" / "package.json",
        run_dir / "stages" / "consensus" / "consensus.json",
        run_dir / "stages" / "consensus" / "package.json",
    ]
    for path in preferred:
        if path.is_file():
            return path
    for pattern in ("stages/*/review_package.json", "stages/*/consensus.json", "stages/*/package.json"):
        found = sorted(path for path in run_dir.glob(pattern) if path.is_file())
        if found:
            return found[-1]
    return None


def _pdf_from_source(source: str, workspaces: list[Path]) -> Path | None:
    for workspace in workspaces:
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = workspace / source
        if candidate.suffix.lower() == ".pdf" and candidate.is_file():
            return candidate.resolve()
        sibling = sibling_pdf(candidate)
        if sibling is not None:
            return sibling.resolve()
    return None


def _find_pdf(run_dir: Path, project_dir: Path | None) -> Path | None:
    ingest = run_dir / "stages" / "ingest"
    if ingest.is_dir():
        pdfs = sorted(path for path in ingest.glob("*.pdf") if path.is_file())
        if pdfs:
            return pdfs[0].resolve()
    if project_dir is not None:
        sibling = sibling_pdf(project_dir / "manuscript.txt")
        if sibling is not None:
            return sibling.resolve()
        direct = project_dir / "manuscript.pdf"
        if direct.is_file():
            return direct.resolve()
    snapshot = run_dir / "config.snapshot.json"
    if snapshot.is_file():
        try:
            raw = json.loads(snapshot.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
        source = raw.get("project", {}).get("source") if isinstance(raw, dict) else None
        if isinstance(source, str) and source.strip():
            workspaces = []
            if project_dir is not None:
                workspaces.append(project_dir)
            workspaces.extend([run_dir.parent.parent, run_dir.parent, run_dir])
            found = _pdf_from_source(source, workspaces)
            if found is not None:
                return found
    for path in sorted(run_dir.rglob("*.pdf")):
        if path.name.lower() in _SKIP_PDF_NAMES or ".attempts" in path.parts:
            continue
        return path.resolve()
    return None


def discover_review_markdown(run_dir: Path | None, project_dir: Path | None) -> str | None:
    """Return the finished review markdown for a run, when one was written."""
    candidates: list[Path] = []
    if run_dir is not None:
        candidates.append(Path(run_dir) / "stages" / "report" / "review_report.md")
    if project_dir is not None:
        candidates.append(Path(project_dir) / "review_report.md")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def resolve_annotation_request(
    target: Path,
    *,
    pdf: Path | None = None,
    package: Path | None = None,
    output: Path | None = None,
) -> AnnotationRequest:
    """Locate the package, manuscript PDF, and output for an existing run."""
    from .config import load_config

    target = Path(target).expanduser().resolve()
    pdf_path = Path(pdf).expanduser().resolve() if pdf else None
    package_path = Path(package).expanduser().resolve() if package else None
    output_path = Path(output).expanduser().resolve() if output else None
    run_dir: Path | None = None
    project_dir: Path | None = None

    if target.is_file() and target.name == "review.toml":
        loaded = load_config(target)
        project_dir = loaded.workspace
        run_dir = (loaded.resolve(loaded.config.project.run_root) / loaded.config.project.id).resolve()
        if pdf_path is None:
            pdf_path = sibling_pdf(loaded.resolve(loaded.config.project.source))
    elif target.is_file() and target.name == "run_manifest.json":
        run_dir = target.parent
    elif target.is_dir():
        run_dir = target
    elif target.is_file() and package_path is None:
        package_path = target
    else:
        raise FileNotFoundError(f"annotation target does not exist: {target}")

    if package_path is None and run_dir is not None:
        package_path = _find_package(run_dir)
    if package_path is None or not package_path.is_file():
        where = run_dir or target
        raise FileNotFoundError(
            f"review package not found under {where}. Pass --package, or export the report first."
        )
    if pdf_path is None and run_dir is not None:
        pdf_path = _find_pdf(run_dir, project_dir)
    if pdf_path is None or not pdf_path.is_file():
        raise FileNotFoundError(
            "manuscript PDF not found. Pass --pdf with the source PDF from the finished run."
        )
    if output_path is None:
        if run_dir is not None:
            output_path = run_dir / "annotated_manuscript.pdf"
        else:
            output_path = package_path.parent / "annotated_manuscript.pdf"
    return AnnotationRequest(
        package_path=package_path,
        pdf_path=pdf_path,
        output_path=output_path,
        project_dir=project_dir,
        run_dir=run_dir,
    )
