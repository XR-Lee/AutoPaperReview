"""Deterministic CVPR manuscript anonymization. No model calls.

A0 keeps the historical banner strip. A1 is the submission-shaped input:
title, authors, affiliations, emails, footnotes, acknowledgements, URLs, and
the full CVF watermark come out. A2 also masks method and dataset names.
``meta`` keeps the title and authors for anonymization and later probe
scoring. That file is not a prompt.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .hashing import sha256_bytes

ANONYMIZER_VERSION = "a1-a2-v2"
ANONYMIZER_RULES = """
a1-a2-v2
A0: strip_cvpr_banner only (single-line open-access stamp).
A1: A0, then the whole CVF watermark cluster (open access version,
    accepted version, proceedings, Computer Vision Foundation, IEEE Xplore
    when it sits with those lines); replace the title with [TITLE]; delete the
    block before Abstract (authors, affiliations, emails, footnotes); delete
    acknowledgement and funding sections (acknowledgement/acknowledgment,
    singular or plural, numbered or inline, through the references heading);
    replace URLs and project links with [URL]; delete short conference header
    and footer lines; replace title 4-grams in the first 3000 characters after
    normalizing line breaks, hyphenation, ligatures, and case; mask author
    surnames outside References.
A2: A1, then mask a title NAME: phrase and invented Capitalized tokens that
    occur at least 5 times. Dataset context becomes [DATASET], otherwise
    [METHOD].
Leak scan reports counts only, including residual acknowledgement headings
and residual normalized title 4-grams. A1 and A2 fail closed when any count
is positive.
Thresholds: preamble 3000 characters, title 4-gram, residual URLs, tier phrase
(oral|highlight|spotlight|award plus presentation|paper|session), venue markers
outside References (CVPR 20xx, IEEE Xplore, Computer Vision Foundation, Open Access),
acknowledgement heading.
"""

PREAMBLE_CHARS = 3000
TITLE_NGRAM = 4
METHOD_MIN_COUNT = 5

_HEADING_PUNCT = r"[:.\u2010\u2011\u2012\u2013\u2014\u2015\u2212\-]"
_ABSTRACT_HEAD = re.compile(rf"(?i)^abstract(?!\w)\s*(?:{_HEADING_PUNCT}\s*)?(.*)$")
_ABSTRACT_STOP = re.compile(
    r"(?i)^(?:\d+(?:\.\d+)*\.?\s*)?(?:introduction|references|bibliography|related\s+work|"
    r"acknowledgements|acknowledgments|acknowledgement|acknowledgment)\b"
)
_REFERENCES = re.compile(r"(?i)^(?:\d+(?:\.\d+)*\.?\s*)?(?:references|bibliography)\s*$")
_ACK_LABEL = (
    r"(?:acknowledgements|acknowledgments|acknowledgement|acknowledgment|"
    r"funding(?:\s+information)?|"
    r"acknowledgements\s+and\s+funding|acknowledgments\s+and\s+funding|"
    r"acknowledgement\s+and\s+funding|acknowledgment\s+and\s+funding|"
    r"funding\s+and\s+acknowledgements|funding\s+and\s+acknowledgments|"
    r"funding\s+and\s+acknowledgement|funding\s+and\s+acknowledgment)"
)
_ACK_HEAD = re.compile(
    rf"(?i)^(?:\d+(?:\.\d+)*\.?\s*)?{_ACK_LABEL}(?:\s*{_HEADING_PUNCT}\s*|\s*$)(.*)$"
)
_ACK_INLINE = re.compile(
    rf"(?i)(?:^|(?<=[.!?])\s*)(?:\d+(?:\.\d+)*\.?\s*)?{_ACK_LABEL}\s*{_HEADING_PUNCT}"
)
_SECTION = re.compile(
    r"(?i)^(?:\d+(?:\.\d+)*\.?\s*)?(?:references|bibliography|appendix|supplementary(?:\s+material)?)\b"
)
_PROSE_START = re.compile(r"(?i)^(this|we|in|our|the|a|an|it|these|those)\b")
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}
_DASHES = frozenset("-\u2010\u2011\u2012\u2013\u2014\u2015\u2212")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL = re.compile(
    r"(?i)(?:https?://|www\.)\S+|\bgithub\.com/\S+|\b(?:code|project)(?:\s+\w+){0,4}\s+is\s+available\s+at\s+\S+"
)
_FOOTNOTE = re.compile(
    r"(?i)(equal contribution|corresponding author|work done while|interned at|"
    r"this work was done|currently at|affiliation\s*\d)"
)
_AFFILIATION = re.compile(
    r"(?i)\b(university|institute|laboratory|laboratories|\blabs?\b|department|school|"
    r"college|academy|faculty|centre|center|google|microsoft|nvidia|meta|research)\b"
)
_HEADER = re.compile(
    r"(?i)^.{0,160}(?:cvpr\s*20\d{2}|conference on computer vision and pattern recognition)\b.{0,80}$"
)
_TIER_PHRASE = re.compile(
    r"(?i)\b(?:oral|highlight|spotlight|award)\s+(?:presentation|paper|session)\b"
)
_VENUE = re.compile(
    r"(?i)\bCVPR\s*20\d{2}\b|\bIEEE Xplore\b|\bComputer Vision Foundation\b|\bOpen Access\b"
)
_CAMEL = re.compile(r"\b(?:[A-Z][A-Za-z0-9]+){2,}\b")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{2,}\b")
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_STRONG_WATERMARK = (
    "open access version",
    "identical to the accepted version",
    "identical to the version",
    "except for this watermark",
    "computer vision foundation",
    "final published version of the proceedings",
)
_WATERMARK_HINT = _STRONG_WATERMARK + ("ieee xplore", "open access")
_ACRONYM_STOP = frozenset(
    {
        "IEEE",
        "CVPR",
        "ICCV",
        "ECCV",
        "ICLR",
        "ICML",
        "ACL",
        "NIPS",
        "GPU",
        "CPU",
        "API",
        "PDF",
        "URL",
        "RGB",
        "CNN",
        "RNN",
        "LSTM",
        "GAN",
        "NLP",
        "AUC",
        "ROC",
        "MSE",
        "PSNR",
        "SSIM",
        "FID",
        "SGD",
        "ADAM",
        "HTTP",
        "HTTPS",
        "JSON",
        "YAML",
        "HTML",
        "CUDA",
        "RAM",
        "USB",
    }
)
_SURNAME_STOP = frozenset({"jr", "sr", "ii", "iii", "iv"})


def anonymizer_sha256() -> str:
    return sha256_bytes(ANONYMIZER_RULES.encode("utf-8"))


def _banner(text: str) -> str:
    from .cvpr_tier import strip_cvpr_banner

    return strip_cvpr_banner(text)


def strip_cvf_watermark(text: str) -> str:
    """Drop the multi-line CVF watermark, including lines pdftotext splits apart."""
    lines = text.splitlines()
    lowered = [line.lower() for line in lines]
    drop: set[int] = set()
    for index, low in enumerate(lowered):
        strong = any(fragment in low for fragment in _STRONG_WATERMARK) and len(lines[index]) < 500
        if not strong:
            continue
        drop.add(index)
        start = max(0, index - 3)
        end = min(len(lines), index + 4)
        for neighbor in range(start, end):
            hint = any(fragment in lowered[neighbor] for fragment in _WATERMARK_HINT)
            if hint and len(lines[neighbor]) < 500:
                drop.add(neighbor)
    kept = [line for index, line in enumerate(lines) if index not in drop]
    return "\n".join(kept).strip() + ("\n" if kept else "")


def _left_column(line: str) -> str:
    parts = re.split(r"\s{3,}", line.strip(), maxsplit=1)
    return parts[0].strip() if parts else ""


def _other_column_heading(rest: str) -> bool:
    """True when text after the heading is the other column's section heading.

    A short title such as ``Abstract Models for Vision`` is not a heading.
    """
    if not rest or len(rest) >= 48:
        return False
    return bool(_ABSTRACT_STOP.match(rest))


def abstract_heading_rest(line: str) -> str | None:
    """Text after an abstract heading, or None when the line is not one.

    An empty string means the heading stands alone or the rest of the line is
    the other column. Prose after the heading is returned so a shared line
    still yields an abstract.
    """
    left = _left_column(line)
    match = _ABSTRACT_HEAD.match(left)
    if not match:
        return None
    rest = match.group(1).strip()
    if not rest or _other_column_heading(rest):
        return ""
    if rest[:1].islower() or _PROSE_START.match(rest):
        return rest
    if re.match(r"^[A-Z]", rest) and len(rest.split()) <= 12:
        return None
    return rest


def stops_abstract(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    left = _left_column(line)
    return bool(_ABSTRACT_STOP.match(left) or _ABSTRACT_STOP.match(stripped))


def _split_references(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _REFERENCES.fullmatch(line.strip()):
            outside = "\n".join(lines[:index])
            refs = "\n".join(lines[index:])
            if outside and not outside.endswith("\n"):
                outside += "\n"
            return outside, refs if refs.endswith("\n") or not refs else refs + "\n"
    return text, ""


def _split_abstract(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    inline_heading = re.compile(rf"(?:Abstract|ABSTRACT)\s*{_HEADING_PUNCT}\s+\S")
    for index, line in enumerate(lines):
        rest = abstract_heading_rest(line)
        if rest is None:
            inline = inline_heading.search(line)
            if inline is None:
                continue
            before = line[: inline.start()]
            preamble_lines = lines[:index]
            if before.strip():
                preamble_lines = [*preamble_lines, before.rstrip()]
            preamble = "\n".join(preamble_lines).strip()
            body = "\n".join([line[inline.start() :], *lines[index + 1 :]]).strip()
            return preamble, body
        preamble = "\n".join(lines[:index]).strip()
        if rest:
            body = "\n".join([rest, *lines[index + 1 :]]).strip()
        else:
            body = "\n".join(lines[index:]).strip()
        return preamble, body
    return text.strip(), ""


def _paragraphs(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n", text.strip())
    return [block.strip() for block in blocks if block.strip()]


def _surnames(authors: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for author in authors:
        parts = re.findall(r"[A-Za-z][A-Za-z'’.-]*", author)
        if not parts:
            continue
        last = parts[-1].strip(".-'’")
        key = last.lower()
        if len(last) < 2 or key in _SURNAME_STOP or key in seen:
            continue
        seen.add(key)
        found.append(last)
    return found


def extract_meta(raw: str) -> dict[str, Any]:
    """Title and authors from the camera-ready preamble. Tiers are not stored."""
    cleaned = strip_cvf_watermark(_banner(raw))
    preamble, _body = _split_abstract(cleaned)
    paragraphs = _paragraphs(preamble)
    title = " ".join(paragraphs[0].split()) if paragraphs else ""
    authors: list[str] = []
    affiliations: list[str] = []
    emails = sorted(set(_EMAIL.findall(preamble)))
    for block in paragraphs[1:]:
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or _EMAIL.search(stripped):
                continue
            if _FOOTNOTE.search(stripped):
                continue
            if _AFFILIATION.search(stripped):
                affiliations.append(stripped)
                continue
            authors.append(stripped)
    return {
        "title": title,
        "authors": authors,
        "surnames": _surnames(authors),
        "affiliations": affiliations,
        "emails": emails,
    }


def _replace_urls(text: str) -> str:
    def _swap(match: re.Match[str]) -> str:
        token = match.group(0)
        trail = ""
        while token and token[-1] in ".,);]:>\"'":
            trail = token[-1] + trail
            token = token[:-1]
        return "[URL]" + trail

    return _URL.sub(_swap, text)


def _starts_ack(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    left = _left_column(line)
    return bool(_ACK_HEAD.match(left) or _ACK_HEAD.match(stripped))


def _ends_ack(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    parts = [part for part in re.split(r"\s{3,}", stripped) if part.strip()] or [stripped]
    return any(_SECTION.match(part.strip()) for part in parts)


def _drop_acknowledgements(text: str) -> str:
    """Drop acknowledgement and funding sections through the next references heading."""
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if not skipping and _starts_ack(line):
            skipping = True
            continue
        if not skipping:
            inline = _ACK_INLINE.search(line)
            if inline:
                prefix = line[: inline.start()].rstrip()
                if prefix:
                    kept.append(prefix)
                skipping = True
                continue
            kept.append(line)
            continue
        if _ends_ack(line):
            skipping = False
            kept.append(line)
    return "\n".join(kept)


def _ack_leak_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if _starts_ack(line):
            count += 1
            continue
        if _ACK_INLINE.search(line):
            count += 1
    return count


def _drop_short_venue_lines(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        compact = line.strip()
        if compact and len(compact) < 180 and (_HEADER.match(compact) or _TIER_PHRASE.fullmatch(compact)):
            continue
        if compact and len(compact) < 180 and _FOOTNOTE.search(compact):
            continue
        kept.append(line)
    return "\n".join(kept)


def _replace_phrase(text: str, words: list[str], replacement: str) -> str:
    if not words:
        return text
    pattern = r"\b" + r"\s+".join(re.escape(word) for word in words) + r"\b"
    return re.sub(pattern, replacement, text, flags=re.IGNORECASE)


def _normalize_with_origins(text: str) -> tuple[str, list[int]]:
    """Casefold, expand ligatures, join line-break hyphens, and collapse space.

    Each output character keeps the source index it came from. A soft hyphen
    or a hyphen at a line break is dropped. Any other dash becomes a space so
    ``state-of-the-art`` matches ``state of the art``.
    """
    expanded: list[str] = []
    origins: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\u00ad":
            index += 1
            continue
        if char in _DASHES:
            cursor = index + 1
            saw_break = False
            while cursor < len(text) and text[cursor].isspace():
                if text[cursor] in "\n\r":
                    saw_break = True
                cursor += 1
            if saw_break and cursor < len(text) and text[cursor].isalpha():
                index = cursor
                continue
            expanded.append(" ")
            origins.append(index)
            index += 1
            continue
        piece = _LIGATURES.get(char) or unicodedata.normalize("NFKC", char)
        for out_char in piece.casefold():
            expanded.append(" " if out_char.isspace() else out_char)
            origins.append(index)
        index += 1
    chars: list[str] = []
    kept_origins: list[int] = []
    for char, origin in zip(expanded, origins):
        if char == " ":
            if not chars or chars[-1] == " ":
                continue
        chars.append(char)
        kept_origins.append(origin)
    if chars and chars[0] == " ":
        chars.pop(0)
        kept_origins.pop(0)
    if chars and chars[-1] == " ":
        chars.pop()
        kept_origins.pop()
    return "".join(chars), kept_origins


def _normalized_tokens(text: str) -> list[str]:
    normalized, _origins = _normalize_with_origins(text)
    return re.findall(r"[a-z0-9]+", normalized)


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    normalized, origins = _normalize_with_origins(text)
    spans: list[tuple[str, int, int]] = []
    if not origins:
        return spans
    for match in re.finditer(r"[a-z0-9]+", normalized):
        start = origins[match.start()]
        end = origins[match.end() - 1] + 1
        spans.append((match.group(0), start, end))
    return spans


def _title_grams(title: str) -> list[tuple[str, ...]]:
    words = _normalized_tokens(title)
    if not words:
        return []
    if len(words) < TITLE_NGRAM:
        return [tuple(words)]
    return [tuple(words[start : start + TITLE_NGRAM]) for start in range(len(words) - TITLE_NGRAM + 1)]


def _replace_spans(text: str, spans: list[tuple[int, int]], replacement: str) -> str:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        text = text[:start] + replacement + text[end:]
    return text


def _mask_title_ngrams(text: str, title: str) -> str:
    grams = _title_grams(title)
    if not grams:
        return text
    head, tail = text[:PREAMBLE_CHARS], text[PREAMBLE_CHARS:]
    tokens = _token_spans(head)
    spans: list[tuple[int, int]] = []
    for gram in grams:
        width = len(gram)
        for index in range(len(tokens) - width + 1):
            if tuple(token for token, _start, _end in tokens[index : index + width]) == gram:
                spans.append((tokens[index][1], tokens[index + width - 1][2]))
    return _replace_spans(head, spans, "[TITLE]") + tail


def _title_ngram_hits(text: str, title: str) -> int:
    grams = _title_grams(title)
    if not grams:
        return 0
    tokens = [token for token, _start, _end in _token_spans(text[:PREAMBLE_CHARS])]
    hits = 0
    for gram in grams:
        width = len(gram)
        if any(tuple(tokens[index : index + width]) == gram for index in range(len(tokens) - width + 1)):
            hits += 1
    return hits


def _mask_surnames(text: str, surnames: list[str]) -> str:
    outside, refs = _split_references(text)
    for surname in sorted(surnames, key=len, reverse=True):
        outside = re.sub(rf"\b{re.escape(surname)}\b", "[AUTHOR]", outside)
    return outside + refs


def _collapse(text: str) -> str:
    body = re.sub(r"\n{3,}", "\n\n", text).strip()
    return body + ("\n" if body else "")


def leak_counts(text: str, meta: dict[str, Any]) -> dict[str, int]:
    """Counts only. The matched strings are not returned."""
    outside, _refs = _split_references(text)
    surname_hits = 0
    for surname in meta.get("surnames") or []:
        if re.search(rf"\b{re.escape(str(surname))}\b", outside):
            surname_hits += 1
    return {
        "acknowledgement": _ack_leak_count(outside),
        "author_surname": surname_hits,
        "residual_url": len(_URL.findall(text)),
        "tier_phrase": len(_TIER_PHRASE.findall(outside)),
        "title_4gram": _title_ngram_hits(text, str(meta.get("title") or "")),
        "venue_marker": len(_VENUE.findall(outside)),
    }


def leak_failed(counts: dict[str, int]) -> bool:
    return any(int(value) > 0 for value in counts.values())


def _title_names(title: str) -> list[tuple[str, str]]:
    """Phrases from a ``NAME:`` title. The label is METHOD or DATASET."""
    match = re.match(r"\s*(.{1,80}?)\s*:\s+\S", title)
    if not match:
        return []
    phrase = " ".join(match.group(1).split())
    if not phrase:
        return []
    kind = "DATASET" if re.search(r"(?i)\b(dataset|corpus|benchmark)\b", title) else "METHOD"
    names = [phrase]
    for token in _CAMEL.findall(phrase) + _ACRONYM.findall(phrase):
        if token not in names and token.upper() not in _ACRONYM_STOP:
            names.append(token)
    return [(name, kind) for name in names]


def _body_names(text: str) -> list[tuple[str, str]]:
    outside, _refs = _split_references(text)
    counts: dict[str, int] = {}
    datasetish: dict[str, int] = {}
    windows = re.findall(r"\b(?:[A-Z][A-Za-z0-9]+){2,}\b|\b[A-Z][A-Z0-9]{2,}\b", outside)
    for token in windows:
        if token.upper() in _ACRONYM_STOP:
            continue
        if token in {"TITLE", "URL", "AUTHOR", "METHOD", "DATASET"}:
            continue
        counts[token] = counts.get(token, 0) + 1
    names: list[tuple[str, str]] = []
    for token, count in counts.items():
        if count < METHOD_MIN_COUNT:
            continue
        for match in re.finditer(rf"\b{re.escape(token)}\b", outside):
            window = outside[max(0, match.start() - 40) : match.end() + 40]
            if re.search(r"(?i)\b(dataset|corpus|benchmark)\b", window):
                datasetish[token] = datasetish.get(token, 0) + 1
        kind = "DATASET" if datasetish.get(token, 0) * 2 >= count else "METHOD"
        names.append((token, kind))
    names.sort(key=lambda item: len(item[0]), reverse=True)
    return names


def _mask_methods(text: str, title: str) -> str:
    outside, refs = _split_references(text)
    seen: set[str] = set()
    names = _title_names(title) + _body_names(outside)
    names.sort(key=lambda item: len(item[0]), reverse=True)
    for name, kind in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        replacement = f"[{kind}]"
        outside = _replace_phrase(outside, name.split(), replacement)
    return outside + refs


def anonymize(raw: str, variant: str) -> tuple[str, dict[str, Any], dict[str, int]]:
    """Return manuscript text, meta, and leak counts for ``A0``, ``A1``, or ``A2``."""
    if variant not in {"A0", "A1", "A2"}:
        raise ValueError("variant must be A0, A1, or A2")
    baseline = _banner(raw)
    meta = extract_meta(raw)
    if variant == "A0":
        return baseline, meta, leak_counts(baseline, meta)
    text = strip_cvf_watermark(baseline)
    _preamble, body = _split_abstract(text)
    if body:
        text = "[TITLE]\n\n" + body
    else:
        paragraphs = _paragraphs(text)
        rest = "\n\n".join(paragraphs[1:]) if paragraphs else text
        text = "[TITLE]\n\n" + rest
    text = _drop_acknowledgements(text)
    text = _replace_urls(text)
    text = _EMAIL.sub("", text)
    text = _drop_short_venue_lines(text)
    text = _mask_title_ngrams(text, str(meta.get("title") or ""))
    text = _mask_surnames(text, list(meta.get("surnames") or []))
    if variant == "A2":
        text = _mask_methods(text, str(meta.get("title") or ""))
    text = _collapse(text)
    return text, meta, leak_counts(text, meta)
