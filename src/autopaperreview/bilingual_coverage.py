"""Refuse bilingual reports that still contain unpaired English or Chinese prose."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .i18n import MissingTranslationError, contains_cjk, script_matches
from .models import LocalizedText, ReviewPackage

_LANG_HEADER = re.compile(r"(?m)^### (en|zh-Hans)\s*$")
_LANG_PREFIX = re.compile(r"(?m)^(?:[ \t]*- )?\*\*(en|zh-Hans):\*\*")
_H2 = re.compile(r"(?m)^## (.+)$")
_LANGUAGES_LINE = re.compile(r"(?m)^- Languages:\s*(.+)$")
_SKIP_SECTIONS = (
    "evidence index",
    "evidence catalog",
    "appendix",
    "related-work queries",
    "retrieved sources",
    "integrity",
    "novelty",
    "investigation agenda",
    "claim ledger",
)
_META_PREFIX = re.compile(
    r"^(?:[ \t]*- )?\*\*(?:Location|Confidence|Routes|Sources|Evidence IDs|Outcome|"
    r"Old ID|Status):\*\*"
)
_ID_QUOTE = re.compile(r"^- `[A-Za-z0-9._-]+`\b")
_HEADING = re.compile(r"^#{1,6} ")
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


def claims_bilingual(markdown: str) -> bool:
    match = _LANGUAGES_LINE.search(markdown)
    if not match:
        return "### zh-Hans" in markdown and "### en" in markdown
    languages = {item.strip() for item in match.group(1).split(",") if item.strip()}
    return "en" in languages and "zh-Hans" in languages


def _skip_section(title: str) -> bool:
    lowered = title.split("/", 1)[0].strip().lower()
    return any(lowered.startswith(prefix) for prefix in _SKIP_SECTIONS)


def _paragraphs(body: str) -> list[str]:
    blocks: list[str] = []
    buf: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line.rstrip())
    if buf:
        blocks.append("\n".join(buf).strip())
    return blocks


def _is_tagged(text: str) -> bool:
    first = text.splitlines()[0].strip()
    return bool(_LANG_HEADER.match(first) or _LANG_PREFIX.match(first))


def _is_structural(text: str) -> bool:
    stripped = text.strip()
    first = stripped.splitlines()[0].strip() if stripped else ""
    if not stripped:
        return True
    if _HEADING.match(first) or _META_PREFIX.match(first) or _ID_QUOTE.match(first):
        return True
    if first.startswith("|") or first.startswith("<a id="):
        return True
    return False


def _is_english_narrative(text: str) -> bool:
    stripped = text.strip()
    if not stripped or contains_cjk(stripped) or _is_structural(stripped):
        return False
    letters = [ch for ch in stripped if ch.isalpha()]
    return len(letters) >= 40 and bool(_WORD.search(stripped))


def _is_chinese_narrative(text: str) -> bool:
    stripped = text.strip()
    if not stripped or _is_structural(stripped):
        return False
    cjk = sum(1 for ch in stripped if "\u3400" <= ch <= "\u9fff")
    if cjk < 18:
        return False
    return len(_WORD.findall(stripped)) < 8


def markdown_bilingual_gaps(markdown: str) -> list[str]:
    """Return human-readable gaps. Empty when the file is monolingual or fully paired."""
    if not claims_bilingual(markdown):
        return []
    gaps: list[str] = []
    parts = _H2.split(markdown)
    # split keeps headings in odd slots after the preamble
    preamble = parts[0] if parts else ""
    if _is_english_narrative(preamble) and "Languages:" not in preamble:
        gaps.append(f"preamble: English-only prose: {preamble[:80]}")
    for index in range(1, len(parts), 2):
        title = parts[index].strip()
        body = parts[index + 1] if index + 1 < len(parts) else ""
        if _skip_section(title):
            continue
        en_heads = len(re.findall(r"(?m)^### en\s*$", body))
        zh_heads = len(re.findall(r"(?m)^### zh-Hans\s*$", body))
        if en_heads != zh_heads:
            gaps.append(f"{title}: ### en ({en_heads}) != ### zh-Hans ({zh_heads})")
        en_marked = len(re.findall(r"(?m)^(?:[ \t]*- )?\*\*en:\*\*", body))
        zh_marked = len(re.findall(r"(?m)^(?:[ \t]*- )?\*\*zh-Hans:\*\*", body))
        if en_marked != zh_marked:
            gaps.append(f"{title}: **en:** ({en_marked}) != **zh-Hans:** ({zh_marked})")
        lang_block: str | None = None
        for para in _paragraphs(body):
            first = para.splitlines()[0].strip()
            header = _LANG_HEADER.match(first)
            if header:
                lang_block = header.group(1)
                continue
            if _HEADING.match(first):
                lang_block = None
            if lang_block or _is_tagged(para):
                continue
            preview = re.sub(r"\s+", " ", para)[:88]
            if _is_english_narrative(para):
                gaps.append(f"{title}: English-only prose: {preview}")
            elif _is_chinese_narrative(para):
                gaps.append(f"{title}: Chinese-only prose: {preview}")
    return gaps


def require_bilingual_markdown(markdown: str) -> None:
    gaps = markdown_bilingual_gaps(markdown)
    if gaps:
        raise MissingTranslationError(
            "bilingual report is incomplete:\n- " + "\n- ".join(gaps[:20])
        )


def _check_localized(text: LocalizedText | None, field: str, languages: Sequence[str], errors: list[str]) -> None:
    if text is None:
        return
    for language in languages:
        value = text.get(language)
        if not value or not script_matches(value, language):
            errors.append(f"{field} missing usable {language} text")


def package_bilingual_gaps(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    if len(languages) < 2:
        return []
    errors: list[str] = []
    _check_localized(package.summary, "summary", languages, errors)
    _check_localized(package.recommendation, "recommendation", languages, errors)
    for issue in package.issues:
        for name in ("title", "evidence", "impact", "required_action"):
            _check_localized(getattr(issue, name), f"{issue.id}.{name}", languages, errors)
    for claim in package.claims:
        _check_localized(claim.text, f"{claim.id}.text", languages, errors)
    for score in package.dimension_scores:
        _check_localized(score.rationale, f"{score.dimension.value}.rationale", languages, errors)
    for item in package.venue_conclusions:
        _check_localized(item.label, f"{item.id}.label", languages, errors)
        _check_localized(item.rationale, f"{item.id}.rationale", languages, errors)
    for item in package.ledger_claims:
        if item.metadata.get("kind") == "listed_contribution":
            _check_localized(item.risk, f"{item.id}.risk", languages, errors)
        else:
            _check_localized(item.claim, f"{item.id}.claim", languages, errors)
            _check_localized(item.risk, f"{item.id}.risk", languages, errors)
    if package.strengths:
        strength_counts = [len(package.strengths.get(language) or []) for language in languages]
        if len(set(strength_counts)) > 1:
            errors.append("strengths list lengths differ across languages")
    if package.acceptance_gate:
        gate_counts = [len(package.acceptance_gate.get(language) or []) for language in languages]
        if len(set(gate_counts)) > 1:
            errors.append("acceptance_gate list lengths differ across languages")
    for language in languages:
        strengths = package.strengths.get(language) or []
        if package.strengths and (not strengths or not all(script_matches(item, language) for item in strengths)):
            errors.append(f"strengths missing usable {language} text")
        gate = package.acceptance_gate.get(language) or []
        if package.acceptance_gate and (not gate or not all(script_matches(item, language) for item in gate)):
            errors.append(f"acceptance_gate missing usable {language} text")
    return errors
