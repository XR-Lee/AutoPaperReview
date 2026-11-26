"""Validate and freeze the proposal payloads sent to models."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from html import unescape
from types import MappingProxyType
from typing import Any
from urllib.parse import unquote

from .confusables import ASCII_LOOKALIKES

# Source fields a proposal may carry. Score, decision, and identity keys are absent.
PROPOSAL_FIELDS = frozenset(
    {
        "proposal",
        "problem",
        "approach",
        "difference",
        "intended_value",
        "validation_plan",
        "prior_alternatives",
        "mechanism",
        "assumptions",
        "discriminating_test",
        "contribution",
        "novelty",
        "serves_motivation",
        "statement",
        "intended_contribution",
        "standpoint",
        "strengths",
        "weaknesses",
        "strengths_and_weaknesses",
        "questions",
        "title",
        "abstract",
    }
)
_DYNAMIC_FIELD = re.compile(r"(?:plan|s|u)\d+\Z")
SCORE_FIELDS = frozenset(
    {
        "rating",
        "ratings",
        "score",
        "scores",
        "grade",
        "recommendation",
        "recommendations",
        "decision",
        "decisions",
        "confidence",
        "soundness",
        "presentation",
        "award",
        "awards",
        "author",
        "authors",
        "author(s)",
        "author_name",
        "author_list",
        "corresponding_author",
        "authorids",
        "author_ids",
        "byline",
        "signature",
        "reviewer_name",
        "email",
        "emails",
        "venue",
        "venueid",
        "final_rating",
        "final_score",
        "overall_rating",
        "overall_score",
        "reviewer_mean",
        "prospective_grade",
        "forum",
        "forum_id",
        "note_id",
        "openreview_id",
        "arxiv",
        "arxiv_id",
        "accept",
        "meta_review",
    }
)
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e]")
_BIDI = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_BIDI_OVERRIDE = re.compile(r"[\u202d\u202e]")
_CONFUSABLES = {
    char: target for target, characters in ASCII_LOOKALIKES.items() for char in characters
}
_CASE_LOOKALIKES = {
    char.lower(): target.lower() for char, target in _CONFUSABLES.items()
    if char.lower() != char and len(char.lower()) == 1
}
_CONFUSABLE_TRANSLATION = str.maketrans(_CASE_LOOKALIKES | _CONFUSABLES)
_KEY_SEPARATOR = re.compile(r"[\s_.-]+")
_COMPACT_SCORE_FIELDS = frozenset(_KEY_SEPARATOR.sub("", key) for key in SCORE_FIELDS)
_LATEX_COMMAND = re.compile(
    r"\\(?:[a-z]+(?=\s*\{)|(?:protect|bfseries|itshape|rmfamily|sffamily|bf|it|rm)"
    r"(?=[\s\\]|$))\s*"
)
_LATEX_FORMAT_ARGUMENT = re.compile(
    r"\\(?:textcolor|colorbox|href|foreignlanguage)\s*\{[^{}]*\}\s*(?=\{)"
    r"|\\color\s*\{[^{}]*\}"
)
_HTML_TAG = re.compile(r"</?(?:b|strong|i|em|s|span|code|u)(?:\s[^<>]*?)?\s*>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MARKDOWN_LINK = re.compile(r"!?\[([^\[\]\n]{1,160})\]\([^\s()]{1,2048}\)")
_MARKUP_DELIMITER = re.compile(r"\\(?=[*_\x60~{}$])|[*_\x60~{}$]")
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_SPELLED_NUMBER = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten)\b"
)
_OBFUSCATED_EMAIL = re.compile(
    r"([a-z0-9._%+\-]+)\s*(?:@|\[at\]|\(at\))\s*([a-z0-9\-]+)\s*(?:\.|\[dot\]|\(dot\))\s*([a-z]{2,})"
)
_SPELLED_EMAIL = re.compile(
    r"\b([a-z0-9._%+\-]+)\s+at\s+([a-z0-9\-]+)\s+dot\s+([a-z]{2,})\b"
)
_EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")
_OPENREVIEW = re.compile(
    r"(?<![\w-])openreview\s*\.\s*net\.?\s*/\s*"
    r"(?:forum|pdf|notes?|attachment|revisions|group)(?![\w-])"
    r"|\bforum\s*\?\s*id\s*=\s*[a-z0-9]{10}"
)
_ARXIV = re.compile(
    r"(?<![\w.])\d{4}\s*\.\s*\d{4,5}(?:v\d+)?(?!\w)"
    r"|\barxiv\s*\.\s*org\.?\s*/\s*(?:abs|pdf|html|ftp)\s*/"
    r"|\barxiv\s*:\s*\d{4}\s*\.\s*\d{4,5}\b"
    r"|\barxiv\s*:\s*[a-z]+(?:-[a-z]+)*(?:\.[a-z]{2})?/\d{7}(?:v\d+)?\b"
    r"|\b(?:astro-ph|cond-mat|cs|eess|gr-qc|hep-(?:ex|lat|ph|th)|math(?:-ph)?|"
    r"nlin|nucl-(?:ex|th)|physics|q-bio|q-fin|quant-ph|stat)(?:\.[a-z]{2})?"
    r"/\d{7}(?:v\d+)?\b"
)
_MEASUREMENT_VALUE = re.compile(
    r"(?<![\w.])\d{4}\.\d{4,5}(?=\s+(?:nm|mm|cm|km|ms|kg|hz|khz|mhz|ghz|"
    r"nanomet(?:er|re)s?|micromet(?:er|re)s?|millimet(?:er|re)s?|millisecond[s]?|"
    r"second[s]?|kelvin|angstrom[s]?)\b)"
)
_DECIMAL_SCORE = r"[+-]?\d+(?:\.\d+)?"
_SCORE_VALUE = rf"(?:{_DECIMAL_SCORE}|(?=[ivx])x{{0,3}}(?:ix|iv|v?i{{0,3}}))"
_FACT_SEPARATOR = r"[:=\-\u2010-\u2015\u2212]"
_SCORE = re.compile(
    r"\b(?:meta|reviewer|my)\s+(?:ratings?|scores?|recommendations?)\b"
    rf"|\b(?:ratings?|scores?|grades?|recommendations?)\s*"
    rf"(?:{_FACT_SEPARATOR}\s*{_SCORE_VALUE}|(?:is|of)\s+{_DECIMAL_SCORE})"
    r"(?=$|\s*[.!?,;()/\n\u2013\u2014-]|\s+(?:out\s+of|(?:for|to)\s+(?:the\s+)?(?:submission|paper|proposal))\b)"
)
_BARE_SCORE = re.compile(
    rf"(?:^|(?<=\n)|(?<=[.!?;])\s+)\s*{_SCORE_VALUE}"
    rf"(?:\s*(?:/|out\s+of)\s*{_SCORE_VALUE})?\s*(?:[.!?;\n]|$)"
    rf"|(?<=[,:])\s*{_SCORE_VALUE}\s*(?:/|out\s+of)\s*(?:10|x)(?=\s*(?:[,!.?;)]|$))"
)
_DECISION = re.compile(
    rf"\b(?:decision|recommendation)\s*{_FACT_SEPARATOR}\s*(?:\w+\s+){{0,2}}(?:accept|reject)\w*"
    r"|\b(?:accept|reject)(?:ed|ance)?\s+as\s+(?:an?\s+)?(?:oral|spotlight|poster)\b"
    r"|\b(?:strong|weak|clear|marginal)\s+(?:accept|reject)(?:ance)?\b"
    r"|\bcamera[\s\-]*ready\b"
    r"|\brecommend(?:ation)?\s+(?:to\s+)?(?:accept|reject)\w*"
    r"|\bvote\s+to\s+(?:accept|reject)\b"
    r"|(?:决定|建议)\s*[:=]\s*(?:录用|拒稿)"
)
_AWARD = re.compile(r"\b(?:best|outstanding)\s+paper\b|\bpaper\s+award\b")
_BARE_ID = re.compile(r"(?<![\w-])[a-z0-9]{10}(?![\w-])")
_BARE_ALPHA_ID = re.compile(r"(?<![\w-])[A-Za-z]{10}(?![\w-])")
_BARE_ALPHA_CASE_CHANGES = 5
_ID_LABEL = re.compile(r"\b(?:forum|note|submission|openreview)\s+(?:identifier|id)\s*[:=]\s*[a-z0-9]{10}\b")
_IDENTITY = re.compile(
    r"\b(?:(?:corresponding|lead|first|senior)\s+)?(?:author(?:s|\(s\))?|author\s+(?:list|names?)|byline|"
    r"(?:written|authored|submitted|signed|prepared|created)\s+by|signature)"
    rf"\s*(?:{_FACT_SEPARATOR}|(?<=\w)\s+(?:is|are|include)\b)\s*[^\W\d_]"
    r"|\\author\s*\{\s*[^\W\d_]"
    r"|(?:作者|通讯作者|投稿人|署名)\s*[:=]\s*[^\W\d_]"
)
_BYLINE = re.compile(r"(?:^|\n)[ \t]*(?i:by)[ \t]+([^\W\d_]+)[ \t]+([^\W\d_]+)\b")
_ATTRIBUTION = re.compile(
    r"\b(?i:(?:written|authored|submitted|signed|prepared|created)\s+by)\s+"
    r"([^\W\d_]+)[ \t]+([^\W\d_]+)\b"
)
_SIGNED_LINE = re.compile(
    rf"(?:^|\n)[ \t]*{_FACT_SEPARATOR}[ \t]+([^\W\d_]+)[ \t]+([^\W\d_]+)[ \t]*[.!]?(?=\n|$)"
)
_ACCESSION = re.compile(
    r"(?i:\b(?:accession|gene|protein|catalog)\s+(?:id\s+)?)[A-Z]{1,5}_?\d{4,10}\b"
)
_SOURCE_UNIT = re.compile(r"[su]\d+\Z")
_AUDIT_ITEM = re.compile(r"r\d+\.(importance|novelty|potential_value|community_impact)\Z")
_CONTRAST_ALIAS = re.compile(r"pair[1-3][ab]\Z")
_EXAMPLE_ALIAS = re.compile(r"ex[1-4]\Z")
LEAK_MESSAGE = "contains a rating, decision, award, acceptance, or identity leak"


class SanitizedProposal(Mapping[str, Any]):
    """Read-only model data created by the sanitizing factories in this module."""

    __slots__ = ("_items",)

    def __new__(cls, *args, **kwargs):
        raise TypeError("use sanitize_proposal or seal to build a SanitizedProposal")

    def __setattr__(self, key, value):
        raise TypeError("sanitized proposal cannot be modified")

    def __delattr__(self, key):
        raise TypeError("sanitized proposal cannot be modified")

    def __getitem__(self, key):
        return self._items[key]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


def to_json_data(value: Any) -> dict:
    """Encode an immutable proposal mapping in ordinary JSON serialization."""
    if isinstance(value, (SanitizedProposal, MappingProxyType)):
        return dict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def normalize_text(text: str) -> str:
    """NFKC, strip invisible formatting characters, then casefold."""
    folded = unicodedata.normalize("NFKC", text)
    folded = _BIDI.sub("", _ZERO_WIDTH.sub("", folded))
    return folded.casefold()


def _decode(text: str) -> str:
    while True:
        normalized = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))
        decoded = unescape(unquote(normalized))
        if decoded == text:
            return decoded
        text = decoded


def _plain_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", normalize_text(text))
    folded = "".join(c for c in folded if not unicodedata.category(c).startswith("M"))
    return folded


def _without_markup(text: str) -> str:
    text = _HTML_TAG.sub("", _HTML_COMMENT.sub("", text))
    text = _LATEX_COMMAND.sub("", _LATEX_FORMAT_ARGUMENT.sub("", text))
    text = _MARKDOWN_LINK.sub(r"\1", text)
    return _MARKUP_DELIMITER.sub("", text)


def fold_facts(folded: str) -> str:
    """Assemble obfuscated emails and spelled-out score words."""
    folded = _OBFUSCATED_EMAIL.sub(r"\1@\2.\3", folded)
    folded = _SPELLED_EMAIL.sub(r"\1@\2.\3", folded)
    return _SPELLED_NUMBER.sub(lambda match: _NUMBER_WORDS[match.group(0)], folded)


def score_field(key: object) -> bool:
    name = _plain_text(_decode(str(key)).translate(_CONFUSABLE_TRANSLATION))
    return any(
        _KEY_SEPARATOR.sub("", variant.strip()) in _COMPACT_SCORE_FIELDS
        for variant in (name, _without_markup(name))
    )


def factual_leak(text: str, *, key: bool = False) -> bool:
    """Recognize explicit outcome and identity facts through common text encodings."""
    if not isinstance(text, str) or not text:
        return False
    decoded = _decode(text)
    without_comments = _HTML_COMMENT.sub("", decoded)
    if _BIDI_OVERRIDE.search(decoded) or any(
        match[1][0].isupper() and match[2][0].isupper()
        for pattern in (_BYLINE, _SIGNED_LINE, _ATTRIBUTION)
        for match in pattern.finditer(without_comments)
    ):
        return True
    for match in _BARE_ALPHA_ID.finditer(_without_markup(unicodedata.normalize("NFKC", decoded))):
        word = match[0]
        if sum(a.isupper() != b.isupper() for a, b in zip(word, word[1:])) >= _BARE_ALPHA_CASE_CHANGES:
            return True
    # An explicitly labeled scientific accession can have the shape of a
    # submission identifier. Remove it only from the identifier check.
    for text_variant in dict.fromkeys((decoded, decoded.translate(_CONFUSABLE_TRANSLATION))):
        plain = _plain_text(text_variant)
        if _has_facts(plain, _plain_text(_ACCESSION.sub("", text_variant)), key=key):
            return True
    return False


def _has_facts(plain: str, without_accessions: str, *, key: bool) -> bool:
    for variant in dict.fromkeys((plain, _without_markup(plain))):
        if _IDENTITY.search(variant) or _ID_LABEL.search(variant):
            return True
        folded = fold_facts(variant)
        if _ARXIV.search(_MEASUREMENT_VALUE.sub("", folded)):
            return True
        if any(rule.search(folded) for rule in (
            _EMAIL, _OPENREVIEW, _SCORE, _BARE_SCORE, _DECISION, _AWARD
        )):
            return True
    identifiers = plain if key else without_accessions
    return any(
        any(c.isdigit() for c in match[0]) and (key or any(c.isalpha() for c in match[0]))
        for variant in dict.fromkeys((identifiers, _without_markup(identifiers)))
        for match in _BARE_ID.finditer(variant)
    )


def contains_factual_leak(value: Any) -> bool:
    """Walk keys and values. A leak in a key is the same failure as a leak in text."""
    if isinstance(value, str):
        return factual_leak(value)
    if isinstance(value, Mapping):
        return any(
            score_field(key) or factual_leak(str(key), key=True) or contains_factual_leak(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_factual_leak(item) for item in value)
    return False


def factual_patterns() -> dict[str, Any]:
    return {
        "normalization": (
            "nfkc-strip-zero-width-percent-html-to-stability,"
            "strip-bidi,casefold,nfkd,strip-marks,confusables"
        ),
        "ascii_lookalikes_unicode_version": "16.0",
        "confusables": _CASE_LOOKALIKES | _CONFUSABLES,
        "number_words": dict(_NUMBER_WORDS),
        "blocked_fields": sorted(SCORE_FIELDS),
        "bare_alpha_id_case_changes": _BARE_ALPHA_CASE_CHANGES,
        "patterns": {
            key: {"pattern": rule.pattern, "flags": rule.flags}
            for key, rule in {
                "zero_width": _ZERO_WIDTH,
                "bidi": _BIDI,
                "bidi_override": _BIDI_OVERRIDE,
                "key_separator": _KEY_SEPARATOR,
                "latex": _LATEX_COMMAND,
                "latex_format_argument": _LATEX_FORMAT_ARGUMENT,
                "html_tags": _HTML_TAG,
                "html_comments": _HTML_COMMENT,
                "markdown_links": _MARKDOWN_LINK,
                "markup": _MARKUP_DELIMITER,
                "spelled_number": _SPELLED_NUMBER,
                "obfuscated_email": _OBFUSCATED_EMAIL,
                "spelled_email": _SPELLED_EMAIL,
                "email": _EMAIL,
                "openreview": _OPENREVIEW,
                "arxiv": _ARXIV,
                "measurement_value": _MEASUREMENT_VALUE,
                "score": _SCORE,
                "bare_score": _BARE_SCORE,
                "decision": _DECISION,
                "award": _AWARD,
                "bare_id": _BARE_ID,
                "bare_alpha_id": _BARE_ALPHA_ID,
                "id_label": _ID_LABEL,
                "identity": _IDENTITY,
                "byline": _BYLINE,
                "attribution": _ATTRIBUTION,
                "signed_line": _SIGNED_LINE,
                "scientific_accession": _ACCESSION,
            }.items()
        },
    }


def _allowed_key(key: str) -> bool:
    return key in PROPOSAL_FIELDS or _DYNAMIC_FIELD.fullmatch(key) is not None


def _flat_proposal(value: SanitizedProposal) -> bool:
    return all(
        isinstance(key, str) and _allowed_key(key) and (item is None or isinstance(item, str))
        for key, item in value.items()
    )


def _proposal_fields(value: Mapping) -> dict:
    fields = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("provide proposal fields only")
        canonical = normalize_text(str.__str__(key))
        if canonical in fields:
            raise ValueError("duplicate normalized proposal field")
        fields[canonical] = item
    return fields


def sanitize_proposal(source: Any) -> SanitizedProposal:
    """Build the flat proposal object from an allowlist.

    A string becomes the ``proposal`` field. Compressor section wrappers
    contribute their text only. Other nesting, unknown keys and facts raise.
    """
    if isinstance(source, SanitizedProposal):
        if _flat_proposal(source):
            return source
        raise TypeError("model input must be a sanitized proposal")
    if isinstance(source, str):
        source = {"proposal": source.strip()}
    if not isinstance(source, Mapping):
        raise TypeError("expected proposal text or JSON object")
    source = _proposal_fields(source)
    # A proposal key holds the document: a string, a field map, or None after
    # a screen removed the only sentence. Sibling record keys such as id are
    # not proposal fields and are not copied.
    if isinstance(source.get("proposal"), Mapping):
        fields = _proposal_fields(source["proposal"])
    elif isinstance(source.get("proposal"), str):
        fields = {"proposal": source["proposal"].strip()}
    elif set(source) == {"proposal"} and source.get("proposal") is None:
        fields = {"proposal": None}
    else:
        fields = source
    flat: dict[str, str | None] = {}
    for key, value in fields.items():
        if not _allowed_key(key):
            raise ValueError("provide proposal fields only")
        if isinstance(value, Mapping):
            value = _proposal_fields(value)
            if set(value) - {"text", "evidence"}:
                raise ValueError("nested proposal field")
            if "evidence" in value:
                refs = value["evidence"]
                if (
                    "text" not in value
                    or not isinstance(refs, (list, tuple))
                    or any(not isinstance(ref, str) or not _SOURCE_UNIT.fullmatch(ref) for ref in refs)
                ):
                    raise ValueError("invalid compressed evidence references")
            value = value.get("text")
        if value is None:
            flat[key] = None
            continue
        if not isinstance(value, str):
            raise ValueError("invalid proposal section")
        if factual_leak(key, key=True) or factual_leak(value):
            raise ValueError(f"proposal {LEAK_MESSAGE}")
        flat[key] = value
    return seal(flat)


def _numeric_field(stage: str | None, path: tuple, row: Mapping, key: str, value: Any) -> bool:
    """Permit only protocol metadata, with the shape and bounds of that stage."""
    if path and path[0] == "payload":
        path = path[1:]
    if stage == "audit" and key == "score" and len(path) == 1:
        match = _AUDIT_ITEM.fullmatch(path[0]) if isinstance(path[0], str) else None
        return (
            match is not None
            and row.keys() == {"dimension", "score", "units"}
            and row["dimension"] == match[1]
            and isinstance(row["units"], (list, tuple))
            and bool(row["units"])
            and all(isinstance(unit, str) for unit in row["units"])
            and type(value) is int
            and 1 <= value <= 5
        )
    if len(path) != 2:
        return False
    kind, member = path
    if key == "year" and (
        (kind == "prior_work" and stage in {"challenge", "delta", "peer"})
        or (kind == "earlier_research" and stage == "field")
    ):
        return (
            type(member) is int
            and row.keys() in ({"title", "abstract", "year"}, {"title", "abstract", "year", "earlier_peer_opinions"})
            and type(value) is int
            and 1 <= value <= 9999
        )
    if kind == "examples" and isinstance(member, str) and type(value) in (int, float):
        if stage == "contrast" and key == "prospective_grade":
            valid_row = (
                _CONTRAST_ALIAS.fullmatch(member)
                and row.keys() == {"proposal", "prospective_grade", "reviewer_excerpts"}
            )
        elif stage == "match" and key == "reviewer_mean":
            valid_row = (
                _EXAMPLE_ALIAS.fullmatch(member)
                and row.keys() == {"idea", "reviewer_mean", "example_opinions"}
            )
        else:
            return False
        return bool(valid_row) and 1 <= value <= 5
    return False


def _copy_checked(value: Any, *, frozen: bool, stage: str | None, path: tuple = ()) -> Any:
    if isinstance(value, str):
        text = str.__str__(value)
        if factual_leak(text):
            raise ValueError(f"model input {LEAK_MESSAGE}")
        return text
    if isinstance(value, Mapping):
        copied = {}
        normalized = set()
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("model input keys must be strings")
            key = str.__str__(key)
            canonical = normalize_text(key)
            if canonical in normalized:
                raise ValueError("duplicate normalized model input key")
            normalized.add(canonical)
            if _numeric_field(stage, path, value, key, item):
                copied[key] = item
                continue
            if score_field(key) or factual_leak(key, key=True):
                raise ValueError(f"model input {LEAK_MESSAGE}")
            copied[key] = _copy_checked(item, frozen=frozen, stage=stage, path=(*path, key))
        return MappingProxyType(copied) if frozen else copied
    if isinstance(value, (list, tuple)):
        items = (
            _copy_checked(item, frozen=frozen, stage=stage, path=(*path, index))
            for index, item in enumerate(value)
        )
        return tuple(items) if frozen else list(items)
    if value is None:
        return value
    if type(value) is float and not math.isfinite(value):
        raise ValueError("model input has a non-finite number")
    if type(value) in (bool, int, float):
        raise ValueError("model input contains unexpected numeric or Boolean data")
    raise TypeError("model input must be JSON data")


def seal(document: Any, *, stage: str | None = None) -> SanitizedProposal:
    """Validate a composed JSON object and freeze every child."""
    if not isinstance(document, Mapping):
        raise TypeError("model input must be a JSON object")
    copied = _copy_checked(document, frozen=True, stage=stage)
    value = object.__new__(SanitizedProposal)
    object.__setattr__(value, "_items", copied)
    return value


def require_model_input(value: Any, *, stage: str | None = None) -> dict:
    """Check the final payload again and return the JSON snapshot to send."""
    if type(value) is not SanitizedProposal:
        raise TypeError("model input must be a SanitizedProposal")
    return _copy_checked(value, frozen=False, stage=stage)
