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
# These zero-width fillers are outside Unicode's Cf (format) category.
_ZERO_WIDTH = re.compile(r"[\u034f\u115f\u1160\u17b4\u17b5]")
_BIDI_OVERRIDE = re.compile(r"[\u202d\u202e]")
_CONFUSABLES = {
    char: target for target, characters in ASCII_LOOKALIKES.items() for char in characters
}
_CASE_LOOKALIKES = {
    char.lower(): target.lower() for char, target in _CONFUSABLES.items()
    if char.lower() != char and len(char.lower()) == 1
}
_CONFUSABLE_TRANSLATION = str.maketrans(_CASE_LOOKALIKES | _CONFUSABLES)
_KEY_SEPARATOR = re.compile(r"[\W_]+")
_COMPACT_SCORE_FIELDS = frozenset(_KEY_SEPARATOR.sub("", key) for key in SCORE_FIELDS)
_KEY_OUTCOME = re.compile(
    r"(?:(?:aggregated?|actual|author|confidence|final|first|human|initial|meta|official|original|overall|paper|peer|"
    r"published|prospective|review(?:er)?|submission|target|venue)){0,4}"
    r"(?:ratings?|scores?|grades?|decisions?|recommendations?|awards?|acceptance|rejection)"
    r"(?:(?:average|distribution|label|mean|median|text|value)s?)?\Z"
)
_LATEX_COMMAND = re.compile(r"\\[a-z]+\*?\s*", re.IGNORECASE)
_LATEX_FORMAT_ARGUMENT = re.compile(
    r"\\(?:textcolor|colorbox|href|foreignlanguage)\s*\{[^{}]*\}\s*(?=\{)"
    r"|\\color\s*\{[^{}]*\}", re.IGNORECASE
)
_LATEX_SPACE = re.compile(
    r"\\(?:[,!:;> ]|(?:hspace\*?|kern|mkern)\s*\{[^{}]*\}|"
    r"(?:thinspace|negthinspace|medspace|thickspace|enspace|enskip|qquad|quad)\b\s*)",
    re.IGNORECASE,
)
_HTML_TAG = re.compile(r"</?[a-z][a-z0-9:-]*(?:\s+(?:[^<>\"']|\"[^\"]*\"|'[^']*')*)?\s*/?>", re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MARKDOWN_LINK = re.compile(r"!?\[([^\[\]\n]{1,160})\]\([^\s()]{1,2048}\)")
_MARKUP_DELIMITER = re.compile(r"\\(?=[*_\x60{}$])|[*_\x60{}$]|~~")
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
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
    r"(?:forum|pdf|notes?|attachment|revisions|group|profiles?)(?![\w-])"
    r"|\bforum\s*\?\s*id\s*=\s*[a-z0-9]{10}"
    r"|\battachment\s*\?\s*id\s*="
    r"|(?<!\w)~[a-z][a-z_-]+\d+(?!\w)"
)
_ARXIV = re.compile(
    r"\barxiv\s*\.\s*org\.?\s*/\s*(?:abs|pdf|html|ftp)\s*/"
    r"|\b10\.48550\s*/\s*arxiv\."
    r"|\barxiv\s*:\s*\d{4}\s*\.\s*\d{4,5}\b"
    r"|\barxiv\s*:\s*[a-z]+(?:-[a-z]+)*(?:\.[a-z]{2})?/\d{7}(?:v\d+)?\b"
    r"|\b(?:astro-ph|cond-mat|cs|eess|gr-qc|hep-(?:ex|lat|ph|th)|math(?:-ph)?|"
    r"nlin|nucl-(?:ex|th)|physics|q-bio|q-fin|quant-ph|stat)(?:\.[a-z]{2})?"
    r"/\d{7}(?:v\d+)?\b"
)
_ARXIV_OR_MEASUREMENT = re.compile(r"(?<![\w.])\d{4}\s*\.\s*\d{4,5}(?:v\d+)?(?!\d)")
_MEASUREMENT_UNIT = re.compile(
    r"\s*(?:±\s*\d+(?:\.\d+)?\s*)?(?:nm|[uµμ]m|mm|cm|km|ns|ms|mg|kg|g|hz|khz|mhz|ghz|kpa|mpa|"
    r"(?:nano|micro|milli)?met(?:er|re)s?|(?:milli|micro|nano)?seconds?|"
    r"(?:milli|kilo)?grams?|kelvin|angstroms?|degrees|°[cfk]?|%)(?![\w])"
)
_DECIMAL_SCORE = r"[+-]?\d+(?:\.\d+)?"
_SCORE_VALUE = rf"(?:{_DECIMAL_SCORE}|(?=[ivx])x{{0,3}}(?:ix|iv|v?i{{0,3}}))"
_FACT_SEPARATOR = r"[:=\-\u2010-\u2015\u2212]"
_LABELED_REVIEW = re.compile(r"\b(?:meta|reviewer|my)\s+(?:ratings?|scores?|recommendations?)\b")
# A review number may be followed by an explanation. A unit or a planned-test
# word keeps the phrase in ordinary scientific prose.
_SCORE_TAIL = (
    r"(?=\s*(?:$|[.!?;,\]\)\(]|because\b|since\b|which\b|where\b|"
    r"for\s+the\b|out\s+of\b|and\b|or\b|as\s+(?:an?\s+)?(?:example|review)\b))"
)
_SCORE = re.compile(
    rf"\b(?P<label>ratings?|scores?|grades?|recommendations?)\s*"
    rf"(?:{_FACT_SEPARATOR}\s*{_SCORE_VALUE}|(?:is|of)\s+{_DECIMAL_SCORE})"
    + _SCORE_TAIL
)
_SCIENTIFIC_SCORE = re.compile(
    r"\b(?:cosine|dot product|similarity|distance|overlap|f1|bleu|rouge|brier|"
    r"z|loss|metric|accuracy|objective|function|silhouette|dice|jaccard)(?:\s+\w+)?\s*$"
    r"|\bfor this (?:scale|metric|formula)\s+(?:the\s+)?reference\s*$"
)
_SCIENCE_FRACTION = re.compile(r"\b(?:ratio|fraction|rate|probability|factor|proportion)\s*(?:of|is|=|:)?\s*$")
_SCIENTIFIC_NUMBER_REFERENCE = re.compile(
    r"\b(?:eq(?:uation)?s?|formula|lemma|theorem|definition|fig(?:ure)?|case|step|section|table)\.?\s*$"
)
_SCORE_FRACTION = rf"{_SCORE_VALUE}\s*(?:/|out\s+of)\s*{_SCORE_VALUE}"
_SCORE_ATOM = rf"(?:{_SCORE_FRACTION}|{_SCORE_VALUE})"
_BRACKETED_SCORE = rf"(?:\(\s*{_SCORE_ATOM}\s*\)|\[\s*{_SCORE_ATOM}\s*\])"
_BARE_SCORE = re.compile(
    rf"(?:^|(?<=\n)|(?<=[.!?;]) +) *(?:(?:[-+•]|\d+[.)]) +)?"
    rf"(?:{_BRACKETED_SCORE}|{_SCORE_ATOM}) *(?=[.!?;\n]|$)"
    rf"|(?<=[,:]) *{_SCORE_FRACTION}(?= *(?:[,!.?;)]|$))"
    rf"|\( *{_SCORE_FRACTION} *\)|\[ *{_SCORE_FRACTION} *\]"
)
_PARENTHETICAL_SCORE = re.compile(r"\( *[+-]?(?:\d|10)(?:\.\d+)? *\)(?= *[.!?;\n]| *$)")
_HEADED_SCORE = re.compile(
    rf"(?:^|\n) *{_SCORE_ATOM} *{_FACT_SEPARATOR} *"
    r"(?:borderline|strong|weak|excellent|good|poor|clear|accept|reject)\b"
)
_WRAP = r"[\"'`“”‘’«»()（）\[\]【】]*"
_DECISION = re.compile(
    rf"\b(?:decision|recommendation|verdict|outcome)\s*{_FACT_SEPARATOR}\s*{_WRAP}\s*"
    rf"(?:\w+\s+){{0,3}}{_WRAP}\s*(?:accept|reject)\w*"
    r"|\b(?:accept|reject)(?:ed|ance)?\s+as\s+(?:an?\s+)?(?:oral|spotlight|poster)\b"
    r"|\b(?:strong|weak|clear|marginal)\s+(?:accept|reject)(?:ance)?\b"
    r"|\bdesk[\s-]*reject(?:s|ed|ion)?\b"
    r"|\bcamera[\s\-]*ready\b"
    r"|\brecommend(?:ation)?\s+(?:to\s+)?(?:accept|reject)\w*"
    r"|\bvote\s+to\s+(?:accept|reject)\b"
    rf"|(?:决定|建议|推荐|结论)\s*[:=：]\s*{_WRAP}\s*(?:录用|拒稿|接受|拒绝|接收)"
    r"|(?:评分|分数|打分|评级)\s*(?:[:=：]\s*)?\d"
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
_NAME_PART = r"[^\W\d_]+(?:[-'’][^\W\d_]+)*"
_NAME = rf"{_NAME_PART} +{_NAME_PART}"
_INITIAL_NAME = r"(?:[A-Z]\.\s+){1,3}[A-Z][a-z]+(?:[-'’][A-Z][a-z]+)*"
_SIGNATURE = rf"(?:{_INITIAL_NAME}|{_NAME}(?: *(?:&|and) *{_NAME})*|{_NAME_PART} *& *{_NAME_PART})"
_BYLINE = re.compile(rf"(?:^|\n) *(?:(?i:by) +|{_FACT_SEPARATOR} +)(?P<names>{_SIGNATURE})[.!]? *(?=\n|$)")
_BYLINE_PREFIX = re.compile(rf"(?:^|\n) *(?i:by) +(?P<names>{_INITIAL_NAME}|{_NAME})\b")
_INITIAL_BYLINE = re.compile(rf"(?:^|\n) *(?P<names>{_INITIAL_NAME})[.!]? *(?=\n|$)")
_ATTRIBUTION = re.compile(
    rf"\b(?P<verb>written|authored|submitted|signed|prepared|created) +by +(?P<names>{_SIGNATURE})\b",
    re.IGNORECASE,
)
_SCIENTIFIC_ATTRIBUTION = re.compile(
    r"(?:monte carlo|least squares|direct inspection|manual inspection|random sampling|"
    r"iterative simulation|numerical integration|principal components)\Z"
)
_ACCESSION = re.compile(
    r"(?i:\b(?:accession|gene|protein|catalog)\s+(?:id\s+)?)[A-Z]{1,5}_?\d{4,10}\b"
)
_SOURCE_UNIT = re.compile(r"[su]\d+\Z")
_AUDIT_ITEM = re.compile(r"r\d+\.(importance|novelty|potential_value|community_impact)\Z")
_CONTRAST_ALIAS = re.compile(r"pair[1-3][ab]\Z")
_EXAMPLE_ALIAS = re.compile(r"ex[1-4]\Z")
LEAK_MESSAGE = "contains a rating, decision, award, acceptance, or identity leak"

_FACT_WORDS = (
    "attachment", "author", "authors", "award", "byline", "decision", "decisions", "desk",
    "email", "forum", "grade", "grades", "openreview", "profile", "rating", "ratings",
    "recommendation", "recommendations", "reject", "rejected", "rejection", "score",
    "scores", "signature", "submission", "accept", "accepted", "acceptance",
)
_FACT_WORD_SEPARATOR = r"[\s_.|·•‐‑–—-]*"
_SPLIT_FACT_WORD = re.compile(r"\b(?:" + "|".join(
    _FACT_WORD_SEPARATOR.join(word) for word in _FACT_WORDS
) + r")\b")


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
    return _without_invisibles(unicodedata.normalize("NFKC", text)).casefold()


def _without_invisibles(text: str) -> str:
    return _ZERO_WIDTH.sub("", "".join(c for c in text if unicodedata.category(c) != "Cf"))


def _decode(text: str) -> tuple[str, bool]:
    has_override = False
    while True:
        normalized = unicodedata.normalize("NFKC", text)
        has_override = has_override or bool(_BIDI_OVERRIDE.search(normalized))
        normalized = _without_invisibles(normalized)
        decoded = unescape(unquote(normalized))
        if decoded == text:
            return decoded, has_override
        text = decoded


def _plain_text(text: str) -> str:
    text = _HORIZONTAL_SPACE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.translate(_CONFUSABLE_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.category(c).startswith("M"))


def _without_markup(text: str) -> str:
    text = _HTML_TAG.sub("", _HTML_COMMENT.sub("", text))
    text = _LATEX_SPACE.sub("", text)
    text = _LATEX_COMMAND.sub("", _LATEX_FORMAT_ARGUMENT.sub("", text))
    text = _MARKDOWN_LINK.sub(r"\1", text)
    return _MARKUP_DELIMITER.sub("", text)


def _compact(text: str) -> str:
    return _KEY_SEPARATOR.sub("", text)


def _fact_texts(text: str):
    for form in dict.fromkeys((text, _without_markup(text))):
        yield _plain_text(form)


def normalized_fact_text(text: str) -> str:
    """Normalize obscured text for semantic indicators without changing the input."""
    decoded, _ = _decode(text)
    return _plain_text(_without_markup(decoded)).casefold()


def fold_facts(folded: str) -> str:
    """Assemble obfuscated emails and spelled-out score words."""
    folded = _OBFUSCATED_EMAIL.sub(r"\1@\2.\3", folded)
    folded = _SPELLED_EMAIL.sub(r"\1@\2.\3", folded)
    return _SPELLED_NUMBER.sub(lambda match: _NUMBER_WORDS[match.group(0)], folded)


_KEY_LEMMAS = frozenset({
    "accept", "accepted", "acceptance", "acceptances", "arxiv", "author", "authors",
    "award", "awards", "byline", "bylines", "decision", "decisions", "email", "emails",
    "forum", "forums", "grade", "grades", "outcome", "outcomes", "rating", "ratings",
    "recommendation", "recommendations", "reject", "rejected", "rejection", "rejections",
    "score", "scores", "verdict", "verdicts",
})
_KEY_PREFIXES = frozenset({
    "aggregated", "aggregate", "actual", "author", "confidence", "final", "first", "human",
    "initial", "meta", "official", "original", "overall", "paper", "peer", "published",
    "prospective", "review", "reviewer", "submission", "target", "venue",
})
_KEY_SUFFIXES = frozenset({
    "address", "addresses", "comment", "comments", "field", "fields", "id", "identifier",
    "identifiers", "ids", "info", "label", "labels", "list", "name", "names", "status",
    "text", "value", "values",
})
_STATUS_OWNERS = _KEY_PREFIXES | {"decision", "outcome", "paper", "review", "reviewer", "submission", "venue"}


def _key_word_blocked(word: str) -> bool:
    if word in _KEY_LEMMAS:
        return True
    if word.endswith("status") and word[:-len("status")] in _STATUS_OWNERS:
        return True
    for lemma in _KEY_LEMMAS:
        if word.startswith(lemma) and word[len(lemma):] in _KEY_SUFFIXES:
            return True
        if word.endswith(lemma) and word[:-len(lemma)] in _KEY_PREFIXES:
            return True
    return False


def score_field(key: object) -> bool:
    """Reject a key that names a decision, score, or identity, in any nesting."""
    text = normalized_fact_text(str(key))
    compact = _compact(text)
    if compact in _COMPACT_SCORE_FIELDS or _KEY_OUTCOME.fullmatch(compact) is not None:
        return True
    words = [word for word in re.split(r"[^0-9a-z]+", text) if word]
    if "status" in words and set(words) & _STATUS_OWNERS:
        return True
    return any(_key_word_blocked(word) for word in words)


def _has_signature(text: str) -> bool:
    for pattern in (_BYLINE, _BYLINE_PREFIX, _INITIAL_BYLINE, _ATTRIBUTION):
        for match in pattern.finditer(text):
            names = match["names"]
            if _SCIENTIFIC_ATTRIBUTION.fullmatch(names.casefold()):
                continue
            if pattern is _BYLINE or (
                pattern is _ATTRIBUTION
                and match["verb"].casefold() in {"written", "authored", "submitted", "signed"}
            ):
                return True
            if all(part[0].isupper() for part in names.split()[:2]):
                return True
    return False


def factual_leak(text: str, *, key: bool = False) -> bool:
    """Recognize explicit outcome and identity facts through common text encodings."""
    if not isinstance(text, str) or not text:
        return False
    decoded, has_override = _decode(text)
    if has_override:
        return True
    for plain in _fact_texts(decoded):
        if _has_signature(plain):
            return True
        for match in _BARE_ALPHA_ID.finditer(plain):
            word = match[0]
            if sum(a.isupper() != b.isupper() for a, b in zip(word, word[1:])) >= _BARE_ALPHA_CASE_CHANGES:
                return True
        if _has_facts(plain.casefold()):
            return True
        # Scientific accessions can have the shape of a submission identifier.
        identifiers = plain if key else _ACCESSION.sub("", plain)
        if any(
            any(c.isdigit() for c in match[0]) and (key or any(c.isalpha() for c in match[0]))
            for match in _BARE_ID.finditer(identifiers.casefold())
        ):
            return True
    return False


def _has_scores(text: str) -> bool:
    if _LABELED_REVIEW.search(text) or _HEADED_SCORE.search(text):
        return True
    for match in _SCORE.finditer(text):
        if match["label"] not in {"score", "scores"} or not _SCIENTIFIC_SCORE.search(text[:match.start()]):
            return True
    if any(not _SCIENCE_FRACTION.search(text[:match.start()]) for match in _BARE_SCORE.finditer(text)):
        return True
    return any(
        not _SCIENTIFIC_NUMBER_REFERENCE.search(text[:match.start()])
        for match in _PARENTHETICAL_SCORE.finditer(text)
    )


def _has_facts(plain: str) -> bool:
    compact = _SPLIT_FACT_WORD.sub(lambda match: _compact(match[0]), plain)
    for variant in dict.fromkeys((plain, compact)):
        if _IDENTITY.search(variant) or _ID_LABEL.search(variant):
            return True
        if _ARXIV.search(variant) or any(
            "v" in match[0] or not _MEASUREMENT_UNIT.match(variant, match.end())
            for match in _ARXIV_OR_MEASUREMENT.finditer(variant)
        ):
            return True
        if any(rule.search(variant) for rule in (_EMAIL, _OPENREVIEW, _DECISION, _AWARD)):
            return True
        folded = fold_facts(variant)
        if _EMAIL.search(folded) or _has_scores(folded):
            return True
    return False


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
            "nfkc-strip-cf-zero-width-unquote-unescape-to-stability,"
            "strip-markup,collapse-horizontal-space,confusables,nfkd-strip-marks,casefold,compact-fact-words"
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
                "bidi_override": _BIDI_OVERRIDE,
                "key_separator": _KEY_SEPARATOR,
                "compound_outcome_key": _KEY_OUTCOME,
                "latex": _LATEX_COMMAND,
                "latex_format_argument": _LATEX_FORMAT_ARGUMENT,
                "latex_space": _LATEX_SPACE,
                "html_tags": _HTML_TAG,
                "html_comments": _HTML_COMMENT,
                "markdown_links": _MARKDOWN_LINK,
                "markup": _MARKUP_DELIMITER,
                "horizontal_space": _HORIZONTAL_SPACE,
                "split_fact_word": _SPLIT_FACT_WORD,
                "spelled_number": _SPELLED_NUMBER,
                "obfuscated_email": _OBFUSCATED_EMAIL,
                "spelled_email": _SPELLED_EMAIL,
                "email": _EMAIL,
                "openreview": _OPENREVIEW,
                "arxiv": _ARXIV,
                "arxiv_or_measurement": _ARXIV_OR_MEASUREMENT,
                "measurement_unit": _MEASUREMENT_UNIT,
                "score": _SCORE,
                "labeled_review": _LABELED_REVIEW,
                "bare_score": _BARE_SCORE,
                "parenthetical_score": _PARENTHETICAL_SCORE,
                "headed_score": _HEADED_SCORE,
                "scientific_score": _SCIENTIFIC_SCORE,
                "scientific_fraction": _SCIENCE_FRACTION,
                "scientific_number_reference": _SCIENTIFIC_NUMBER_REFERENCE,
                "decision": _DECISION,
                "award": _AWARD,
                "bare_id": _BARE_ID,
                "bare_alpha_id": _BARE_ALPHA_ID,
                "id_label": _ID_LABEL,
                "identity": _IDENTITY,
                "byline": _BYLINE,
                "byline_prefix": _BYLINE_PREFIX,
                "attribution": _ATTRIBUTION,
                "scientific_attribution": _SCIENTIFIC_ATTRIBUTION,
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
        text = source["proposal"].strip()
        # An otherwise empty free-text proposal can be a bare OpenReview ID.
        if _BARE_ALPHA_ID.fullmatch(normalized_fact_text(text)):
            raise ValueError(f"proposal {LEAK_MESSAGE}")
        fields = {"proposal": text}
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
            canonical = normalized_fact_text(key)
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
