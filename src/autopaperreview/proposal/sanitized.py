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
_LATEX_IDENTITY = re.compile(
    r"\\(author|authors|affiliation|institute|email|address)\*?\s*\{([^{}]*)\}",
    re.IGNORECASE,
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
# A review number may end the text, sit before punctuation or a dash, or be
# followed by an explanation. A unit or a planned-test word stays scientific prose.
_SCORE_TAIL = (
    r"(?=$|\s*[.!?,;()/\n\u2013\u2014-]|\s+(?:"
    r"because|since|which|where|out\s+of|or|versus|vs\.?|"
    r"(?:for|to)\s+(?:the\s+)?(?:submission|paper|proposal)"
    r")\b)"
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
    r"|\b(?:the\s+)?(?:submission|manuscript|paper)\b(?:\s+\w+){0,6}\s+"
    r"(?:was|were|is|has been|have been)\s+(?:accepted|rejected)\b"
    r"(?!\s+(?:practice|assumption|hypothesis|definition|model|method)\b)"
    rf"|(?:决定|建议|推荐|结论)\s*[:=：]\s*{_WRAP}\s*(?:录用|拒稿|接受|拒绝|接收)"
    r"|(?:投稿|稿件|论文)(?:(?!。).){0,8}(?:已|被)(?:(?!。).){0,4}(?:录取|录用|接收|拒稿)"
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
    # A space, not a deletion: adjacent table cells must not glue into one token.
    # An identity command becomes a label before generic command stripping.
    text = _HTML_TAG.sub(" ", _HTML_COMMENT.sub(" ", text))
    text = _LATEX_SPACE.sub(" ", text)
    text = _LATEX_IDENTITY.sub(lambda match: f"{match.group(1)}: {match.group(2)}", text)
    text = _LATEX_COMMAND.sub(" ", _LATEX_FORMAT_ARGUMENT.sub(" ", text))
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
    "verdict", "verdicts",
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
_REVIEW_STATE_WORDS = frozenset({"status", "statuses", "state", "states", "result", "results"})
_REVIEW_STATE_OWNERS = _STATUS_OWNERS | {
    "manuscript", "editorial", "chair", "programme", "program", "acceptance", "rejection",
}
_IDENTITY_MODIFIERS = frozenset({
    "author", "authors", "email", "emails", "contact", "contacts", "reviewer", "reviewers",
    "postal", "mailing", "phone", "telephone", "affiliation", "institution", "orcid",
    "home", "office", "corresponding",
})
_SCIENTIFIC_KEY_HEADS = frozenset({
    "angle", "angles", "space", "spaces", "metric", "metrics", "value", "values",
    "map", "maps", "index", "measure", "measurement", "measurements",
})


_IDENTITY_LEMMAS = frozenset({
    "email", "emails", "phone", "telephone", "mobile", "orcid", "affiliation",
    "institution", "university", "department", "byline", "bylines",
})
_SCIENTIFIC_MEASURES = frozenset({
    "angle", "angles", "space", "spaces", "metric", "metrics", "value", "values",
    "map", "maps", "index", "measure", "measurement", "measurements", "bleu", "f1",
    "loss", "similarity", "distance", "jaccard", "cosine", "accuracy", "precision",
    "recall", "auc", "latency", "perplexity", "threshold", "surface", "surfaces",
    "optical", "water", "memory", "virtual", "reconstruction", "calibration",
    "calibrated", "indicator", "residual", "estimator", "baseline",
    "gradient", "gradients", "slope", "slopes", "derivative", "derivatives",
    "curvature", "hessian", "eigenvalue", "eigenvalues", "norm", "norms",
    "entropy", "divergence",
})
# A score key is a review key unless a scientific measure is present and no
# hard review word is. ``overall_score`` stays blocked; ``similarity_score`` does not.
_HARD_REVIEW_SCORE = frozenset({"overall", "reviewer", "reviewers", "meta", "official"})
_FORMAT_COMMANDS = frozenset({
    "mathrm", "text", "textbf", "textit", "emph", "operatorname", "mathbf", "textrm",
})
_KEY_BLOCK_TOKENS = frozenset({
    "venue", "venues", "disposition", "dispositions", "desk",
    "autor", "autores", "afiliacion", "institucion", "universidad",
    "correo", "telefono", "dictamen", "puntuacion", "calificacion", "puntaje",
    "rechazo", "aceptacion",
})
_KEY_CJK = re.compile("作者|署名|单位|机构|邮箱|电话|大学|决定|录用|拒稿|总分|总评|审稿|评审")


def _key_tokens(key: object) -> list[str]:
    """Split a key after invisible characters and markup are removed, not used as separators."""
    raw = unicodedata.normalize("NFKC", str(key))
    raw = _ZERO_WIDTH.sub("", "".join(char for char in raw if unicodedata.category(char) != "Cf"))
    raw = raw.replace("\\_", " ").replace("\\", " ")
    raw = re.sub(r"[{}$]", " ", raw)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    tokens = [token for token in re.split(r"[^0-9A-Za-z]+", spaced.casefold()) if token]
    tokens = [token for token in tokens if token not in _FORMAT_COMMANDS]
    if len(tokens) > 1 and all(len(token) == 1 for token in tokens):
        tokens = ["".join(tokens)]
    return tokens


def _key_word_blocked(word: str) -> bool:
    if word in _KEY_LEMMAS:
        return True
    for stem in ("status", "state", "result"):
        if len(word) > len(stem) and word.endswith(stem) and word[: -len(stem)] in _REVIEW_STATE_OWNERS:
            return True
    for lemma in _KEY_LEMMAS:
        if word.startswith(lemma) and word[len(lemma):] in _KEY_SUFFIXES:
            return True
        if word.endswith(lemma) and word[:-len(lemma)] in _KEY_PREFIXES:
            return True
    return False


def _structured_key(tokens: list[str]) -> bool:
    """Review and identity keys by head and modifier, not by a bare token hit.

    ``contact_angle``, ``address_space`` and ``measurement_score`` stay scientific.
    ``contact_address``, ``overall_score`` and ``previous_reviewer_average_score`` do not.
    """
    if not tokens or tokens == ["reviewer", "excerpts"]:
        return False
    head = tokens[-1]
    modifiers = set(tokens[:-1])
    if head in {"angle", "angles", "surface", "surfaces"} and "contact" in modifiers and not (modifiers & (_IDENTITY_MODIFIERS - {"contact"})):
        return False
    if head in {"space", "spaces"} and "address" in modifiers and not (modifiers & _IDENTITY_MODIFIERS):
        return False
    if set(tokens) & _IDENTITY_LEMMAS:
        return True
    if ("reviewer" in tokens or "reviewers" in tokens) and not (set(tokens) & _SCIENTIFIC_MEASURES):
        return True
    if "score" in tokens or "scores" in tokens:
        # Unknown score keys fail closed. A scientific measure keeps the key,
        # unless a hard review word (overall, reviewer, meta, official) is also present.
        scientific = set(tokens) & (_SCIENTIFIC_MEASURES | _SCIENTIFIC_KEY_HEADS)
        if scientific and not (set(tokens) & _HARD_REVIEW_SCORE):
            return False
        return True
    if head in {"contact", "contacts"}:
        return True
    if "contact" in tokens and head not in {"angle", "angles", "map", "maps"}:
        return True
    if head in {"address", "addresses"}:
        if not modifiers or modifiers & _IDENTITY_MODIFIERS:
            return True
        return False
    if "address" in tokens and head not in {"space", "spaces"} and modifiers & _IDENTITY_MODIFIERS:
        return True
    return False


def score_field(key: object) -> bool:
    """Reject a key that names a decision, score, or identity, in any nesting."""
    raw = unicodedata.normalize("NFKC", str(key))
    if _KEY_CJK.search(raw):
        return True
    tokens = _key_tokens(key)
    if set(tokens) & _KEY_BLOCK_TOKENS:
        return True
    # Protocol context, not an identity field. A reviewer token beside a score
    # or contact token is still rejected below.
    if tokens == ["reviewer", "excerpts"]:
        return False
    if _structured_key(tokens):
        return True
    text = normalized_fact_text(str(key))
    compact = _compact(text)
    if compact in _COMPACT_SCORE_FIELDS or _KEY_OUTCOME.fullmatch(compact) is not None:
        return True
    words = [word for word in re.split(r"[^0-9a-z]+", text) if word]
    if set(tokens) & _REVIEW_STATE_WORDS and set(tokens) & _REVIEW_STATE_OWNERS:
        return True
    if tokens in (["status"], ["state"], ["statuses"], ["states"]):
        return True
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
        if _explicit_construct(plain) or _has_signature(plain):
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


_CONSTRUCT_WORD = re.compile(r"[0-9]+(?:\.[0-9]+)?|[^\W\d_]+|[^\w\s]", re.UNICODE)
_STATUS_SUBJECTS = frozenset({
    "submission", "manuscript", "paper", "article", "manuscrito", "articulo", "envio",
    "propuesta", "trabajo", "投稿", "稿件", "论文", "本稿", "本文",
})
_DECISION_WORDS = frozenset({
    "accept", "accepted", "acceptance", "reject", "rejected", "rejection",
    "aceptado", "aceptada", "aceptacion", "rechazo", "rechazado", "rechazada",
    "aceptar", "acepta", "rechazar",
    "录用", "录取", "接收", "拒稿", "拒绝", "接受",
})
_STATUS_HEADS = frozenset({
    "decision", "verdict", "outcome", "status", "recommendation", "recommendations",
    "estado", "dictamen", "puntuacion", "calificacion", "puntaje", "resultado", "resolucion",
    "决定", "结论", "建议", "推荐", "状态", "总分", "总评", "评分", "审稿", "评审",
})
_STATUS_BINDERS = frozenset({
    "is", "was", "were", "been", "be", "are", "received", "gets", "got", "given",
    "reads", "marked", "es", "fue", "son", "esta", "fueron",
    ":", "=", "|", "-", "—", "–", "－", "为", "是",
})
_STATUS_ABBREV = frozenset({"acc", "rej", "rec"})
_SCORE_LABELS = frozenset({
    "rating", "ratings", "grade", "grades", "recommendation", "recommendations",
    "puntuacion", "calificacion", "puntaje", "nota",
})
_PLAN_CUES = frozenset({
    "plan", "planned", "propose", "proposed", "will", "intend", "intended",
    "previsto", "propuesto", "planeamos", "planteamos", "pretendemos", "计划", "拟",
})
_STATUS_MODALS = frozenset({
    "should", "could", "would", "may", "might", "must", "can", "shall", "ought",
})
_STATUS_DEGREES = frozenset({"strong", "weak", "clear", "marginal", "oral", "spotlight", "poster", "desk"})
_CONTENT_SKIP = frozenset({
    "", ".", "!", "?", ",", ";", ":", ")", "]", "(", "[", "—", "–", "-", "－",
    "because", "since", "as", "given", "due", "which", "where", "after", "when",
    "while", "and", "or", "for", "to", "at", "by", "from", "in", "on", "into",
    "with", "of", "the", "a", "an", "this", "that", "our", "its",
})
_SCORE_QUALIFIERS = frozenset({"overall", "total", "final", "reviewer", "meta", "official"})
_RATING_TAILS = frozenset({
    "", ".", "!", "?", ",", ";", ":", ")", "]", "because", "since", "which", "where",
    "for", "as", "given", "out", "/", "—", "–", "-", "－", "strong", "weak", "clear",
    "borderline", "accept", "reject", "on",
})
_RANGE_MARKS = frozenset({"-", "–", "—", "－", "~", "to", "a", "y", "到", "至"})
_CJK_STATUS = re.compile(
    r"(?:投稿|稿件|论文|提交)(?:状态|决定|结论|结果)?\s*[:=：为是]\s*(?:已|被)?\s*(?:录取|录用|接收|拒稿|拒绝|接受)"
    r"|(?:投稿|稿件|论文).{0,4}(?:已|被)(?:录取|录用|接收|拒稿|拒绝|接受)"
)
_PREPRINT_MARK = re.compile(r"arxiv|pre[\s-]?print|e-print", re.IGNORECASE)
_PREPRINT_BY = re.compile(r"\b(?:authored\s+by|by)\s+(?:[A-Z]\.\s*)?[A-Z][\w’'-]+")
_PREPRINT_AUTHORS = re.compile(r"\bauthors?\s*[:=：]\s*[A-Z]")
_PREPRINT_LIST = re.compile(r"\([A-Z][\w’'-]+\s+[A-Z][\w’'-]+\s+and\s+[A-Z]")
_PREPRINT_ET_AL = re.compile(r"\bet\s+al\.?", re.IGNORECASE)


def _construct_tokens(text: str) -> list[str]:
    return [token for token in _CONSTRUCT_WORD.findall(text.casefold()) if token]


def _content_follower(token: str) -> bool:
    return bool(token) and token not in _CONTENT_SKIP and token not in _STATUS_HEADS and token not in _STATUS_SUBJECTS


def _submission_status(tokens: list[str], raw: str) -> bool:
    if _CJK_STATUS.search(raw):
        return True
    for index, token in enumerate(tokens):
        left = tokens[max(0, index - 8):index]
        if token in _STATUS_ABBREV and any(word in _STATUS_HEADS for word in left):
            return True
        if token not in _DECISION_WORDS:
            continue
        previous = tokens[index - 1] if index else ""
        nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
        if previous in _STATUS_DEGREES:
            return True
        right = tokens[index + 1:index + 8]
        # "should be accepted" is a recommendation for the leak judge, not a recorded status.
        if any(word in _STATUS_MODALS for word in left):
            continue
        if any(word in _STATUS_HEADS for word in left):
            if _content_follower(nxt):
                continue
            return True
        if any(word in _STATUS_SUBJECTS for word in left) and any(word in _STATUS_BINDERS for word in left):
            if _content_follower(nxt):
                continue
            return True
        if any(word in _STATUS_SUBJECTS for word in right):
            return True
        if "as" in right:
            after = right[right.index("as") + 1:]
            if any(word in {"poster", "oral", "spotlight"} for word in after):
                return True
    return False


def _is_number(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", token))


def _rating_number(tokens: list[str], index: int) -> bool:
    nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
    return nxt in _RATING_TAILS


def _number_after_label(tokens: list[str], index: int) -> bool:
    """A rating number follows the label without an intervening content word."""
    for cursor in range(index + 1, min(len(tokens), index + 6)):
        token = tokens[cursor]
        if _is_number(token):
            return _rating_number(tokens, cursor)
        if token not in {
            ":", "=", "-", "—", "–", "－", "|", "of", "is", "equals", "equal", "out", "/",
            "a", "an", "the", "es", "fue", "为", "是",
        }:
            return False
    return False


def _total_score(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        qualified_score = token in {"score", "scores"} and index and tokens[index - 1] in _SCORE_QUALIFIERS
        direct = token in _SCORE_LABELS
        if not direct and not qualified_score and token not in {"overall", "total"}:
            continue
        if _number_after_label(tokens, index):
            return True
    return False


def _range_anchor(window: list[str]) -> bool:
    labels = _SCORE_LABELS | {"score", "scores"}
    if any(word in labels for word in window):
        return True
    return bool(window) and window[-1] in {"overall", "total"}


def _planned_before(tokens: list[str], index: int) -> bool:
    return any(word in _PLAN_CUES for word in tokens[max(0, index - 12):index])


_RANGE_METRICS = frozenset({
    "loss", "accuracy", "error", "threshold", "similarity", "distance", "bleu", "f1",
    "metric", "metrics", "measure", "measurement", "latency", "perplexity", "precision",
    "recall", "auc", "reconstruction", "calibration", "calibrated",
    "gradient", "gradients", "slope", "slopes", "derivative", "derivatives",
    "curvature", "hessian", "norm", "norms", "entropy", "divergence",
})
_REVIEW_RANGE_ANCHORS = _SCORE_LABELS | _SCORE_QUALIFIERS | {"overall", "total"}


def _score_range(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        window = tokens[max(0, index - 6):index]
        span = tokens[max(0, index - 8):index + 6]
        # A review anchor keeps the range blocked. A nearby scientific metric
        # does not need a plan cue: the metric is what makes the range scientific.
        metric_near = any(word in _RANGE_METRICS for word in span)
        review_anchor = any(word in _REVIEW_RANGE_ANCHORS for word in window)
        if _planned_before(tokens, index) or (metric_near and not review_anchor):
            continue
        if token in {"range", "between", "entre"} and _range_anchor(window):
            if any(_is_number(word) for word in tokens[index:index + 8]):
                return True
        if (
            _is_number(token)
            and index + 2 < len(tokens)
            and tokens[index + 1] in _RANGE_MARKS
            and _is_number(tokens[index + 2])
            and _range_anchor(window)
        ):
            return True
    return False


_PRIOR_WORK = re.compile(
    r"(?i)\b(?:earlier|prior|previous|independent)\b|先前|已有研究|trabajo\s+previo|\banterior\b|\bindependiente\b"
)
_OURS = re.compile(r"(?i)\bour\b")
_EARLIER_WORD = re.compile(r"(?i)\b(?:earlier|prior|previous)\b")
_REVIEW_STATUS = re.compile(
    r"(?i)\b(?:review|reviewer|meta-?review)\s*(?:status|state|decision)\s*[:=]\s*\S"
    r"|\b(?:review\s+)?(?:status|decision)\s*[:=]\s*(?:major|minor)\s+revision\b"
    r"|\b(?:manuscript|submission|paper)\s+status\s*[:=：]\s*(?:under|in|pending|desk|major|minor|awaiting)\b"
    r"|\bestado\s+(?:de\s+)?(?:la\s+)?(?:revision|manuscrito|articulo)\s*[:：]\s*\S"
    r"|审稿(?:意见)?(?:状态|结论)\s*[:：]\s*\S"
    r"|(?:评审|审稿)(?:状态|结论|意见)\s*[:：为是]\s*(?:大修|小修|待审|修改|拒稿|录用)"
)
_CJK_DECISION = re.compile(
    r"(?:投稿|稿件|论文|提交|本稿|本文).{0,8}(?:已|被)(?:录取|录用|拒稿|拒绝|收录|刊登)"
    r"|(?:决定|结论|状态|结果|终审)\s*[:：=＝为是]\s*(?:已|被|不予)?(?:录取|录用|拒稿|拒绝|接受|接收|收录|刊登)"
    r"|(?:录取|录用|拒稿)(?:决定|结论)"
)
_CJK_SCORE = re.compile(
    r"(?:总体|最终|审稿|评审|元评审|综合|平均|总)?(?:总分|总评)\s*[:：为是]?\s*\d"
    r"|(?:总体|最终|审稿|评审|综合|平均|总|本稿|本文|终审)(?:评分|分数|得分|评级|成绩)\s*[:：为是]?\s*\d"
)
_CJK_RANGE = re.compile(r"(?:评分|总分|总评|评级).{0,12}\d\s*(?:到|至|—|-)\s*\d")
_IDENTITY_LABEL = re.compile(
    r"(?i)\b(?:affiliation|institution|university|universidad|instituto|department|orcid)\s*[:：]\s*\S"
    r"|\b(?:e-?mail|correo(?:\s+electronico)?)\s*[:：]\s*\S"
    r"|\b(?:phone|telephone)\s*[:：.]?\s*\+?\d[\d\s().-]{6,}"
    r"|\b(?:tel|mobile)\s*[:：.]\s*\+?\d[\d\s().-]{6,}"
    r"|(?:单位|机构|邮箱|电话|大学|签名|署名人|联系方式|通讯作者|通讯地址)\s*[:：]\s*\S"
    r"|(?:作者|署名)\s*[:：]\s*\S"
    r"|\b(?:authors?|autores|autoria|byline)\s*[:：—–\-]\s*\S"
)
_SIGNATURE_LABEL = re.compile(
    r"(?i:\b(?:signature|signed|firma|firmado|contacto|institucion)\b)\s*[:：=]\s*[A-Z\u3400-\u9fff]"
)
_LABEL_SEPARATORS = frozenset({":", "=", "|", "-", "—", "–", "－"})
_LABEL_VALUES = _DECISION_WORDS | _STATUS_ABBREV | {"major", "minor", "大修", "小修", "待审"}
_DECISION_PHRASES = frozenset({
    "major revision", "minor revision", "weak accept", "strong accept", "weak reject",
    "strong reject", "conditional accept", "desk reject", "camera ready", "under review",
    "in review", "pending review", "accept as poster", "accept as oral", "accept as spotlight",
    "revision mayor", "revision menor", "aceptado condicional", "修改后录用", "弱接收", "强接收",
    "revision requested", "cambios mayores", "cambios menores", "decline publication",
    "publicacion aprobada",
})
_OUTCOME_WORDS = _DECISION_WORDS | frozenset({
    "publish", "published", "decline", "declined", "selected",
    "admitido", "admitida", "aprobado", "aprobada",
    "收录", "刊登", "不予录用", "已收录",
})
_LABEL_HEADS = _STATUS_HEADS | frozenset({
    "disposition", "ruling", "resolution", "veredicto", "editorial",
    "referee", "editor", "state",
})
_LABEL_QUALIFIERS = frozenset({
    "final", "global", "oficial", "official", "overall", "total", "meta", "reviewer",
})


def _value_phrase(tokens: list[str], start: int) -> str:
    words = []
    for token in tokens[start:start + 3]:
        if token in _LABEL_SEPARATORS or token in _CONTENT_SKIP:
            break
        words.append(token)
        if " ".join(words) in _DECISION_PHRASES or token in _LABEL_VALUES or _is_number(token):
            return " ".join(words)
    return " ".join(words)


_CJK_IDENTITY_MARK = ("通讯作者", "通讯地址", "联系方式", "署名", "作者", "单位", "机构")
_CJK_SCORE_MARK = ("总分", "总评", "得分", "评分", "评级")
_CJK_STATUS_MARK = ("终审结果", "审稿意见", "评审意见", "状态", "决定", "结论", "结果")
_CJK_REVIEW_HINT = ("审", "稿", "终", "评", "本稿", "本文", "论文", "投稿")


def _cjk_kind(token: str) -> str | None:
    """A review or identity head glued to the front of a CJK word."""
    if not token or len(token) > 16 or not any("\u3400" <= char <= "\u9fff" for char in token):
        return None
    for mark in _CJK_IDENTITY_MARK:
        if token.endswith(mark):
            return "identity"
    hinted = any(hint in token for hint in _CJK_REVIEW_HINT)
    for mark in _CJK_SCORE_MARK:
        if token.endswith(mark) and (token == mark or hinted):
            return "score"
    for mark in _CJK_STATUS_MARK:
        if token.endswith(mark) and (token == mark or any(hint in token for hint in ("审", "稿", "终", "投稿", "本稿", "论文"))):
            return "status"
    return None


def _label_kinds(token: str) -> set[str]:
    kinds = set()
    if token in _SCORE_LABELS or token in {"overall", "total"}:
        kinds.add("score")
    if token in {"score", "scores"}:
        kinds.add("bare")
    if token in _LABEL_HEADS:
        kinds.add("status")
    if token in _IDENTITY_LABEL_TOKENS:
        kinds.add("identity")
    kind = _cjk_kind(token)
    if kind:
        kinds.add(kind)
    return kinds


def _outcome_text(tokens: list[str]) -> bool:
    words = [token for token in tokens if token not in _CONTENT_SKIP]
    if not words:
        return False
    if any(word in _OUTCOME_WORDS or word in _STATUS_ABBREV or word in _STATUS_DEGREES for word in words):
        return True
    text = " ".join(words)
    return any(phrase in text for phrase in _DECISION_PHRASES)


def _score_text(tokens: list[str]) -> bool:
    words = [token for token in tokens if token not in _CONTENT_SKIP]
    if not words:
        return False
    if _is_number(words[0]) or words[0] in _OUTCOME_WORDS:
        return True
    if re.fullmatch(r"[a-f][+-]?", words[0]):
        return True
    if any("星" in word or word in {"star", "stars"} for word in words[:4]):
        return True
    if len(words) >= 3 and _is_number(words[0]) and words[1] == "/" and _is_number(words[2]):
        return True
    return _outcome_text(words)


def _labeled_fact(tokens: list[str]) -> bool:
    """A review or identity label, a separator, and a value on that line.

    The label may be a few words before the separator, and a CJK head may be
    glued to the front of the word. A bare ``score`` still needs a review
    qualifier, and a scientific measure beside that score stays a measurement.
    """
    for index, token in enumerate(tokens):
        if token not in _LABEL_SEPARATORS:
            continue
        left = tokens[max(0, index - 8):index]
        right = tokens[index + 1:index + 12]
        if token == "-" and index:
            left = tokens[index - 1:index]
        if not left or not right:
            continue
        kinds: set[str] = set()
        for word in left:
            kinds |= _label_kinds(word)
        qualified = bool(set(left) & _SCORE_QUALIFIERS)
        metric = bool(set(left) & (_RANGE_METRICS | _SCIENTIFIC_MEASURES))
        if "bare" in kinds and not qualified:
            kinds.discard("bare")
        if metric and not qualified:
            kinds.discard("bare")
            kinds.discard("score")
        if ("score" in kinds or "bare" in kinds) and _score_text(right):
            return True
        if "status" in kinds and _outcome_text(right):
            return True
        if "identity" in kinds and any(word not in _CONTENT_SKIP for word in right):
            return True
    return False


_IDENTITY_LABEL_TOKENS = frozenset({
    "affiliation", "institution", "university", "universidad", "instituto", "department",
    "orcid", "email", "phone", "telephone", "mobile", "author", "authors", "autores",
    "autoria", "corresponding", "byline", "单位", "机构", "邮箱", "电话", "大学",
    "作者", "署名", "通讯作者", "通讯地址",
})


def _preprint_authorship(text: str) -> bool:
    """A name bound to a preprint marker in the same short span.

    A preprint attributed to earlier independent work is not this proposal's byline.
    ``our preprint`` stays a byline unless it is also marked earlier or prior.
    """
    for match in _PREPRINT_MARK.finditer(text):
        span = text[max(0, match.start() - 80):match.end() + 80]
        earlier = _PRIOR_WORK.search(span) is not None
        ours = _OURS.search(span) is not None and _EARLIER_WORD.search(span) is None
        if earlier and not ours:
            continue
        if (
            _PREPRINT_BY.search(span)
            or _PREPRINT_AUTHORS.search(span)
            or _PREPRINT_LIST.search(span)
            or _PREPRINT_ET_AL.search(span)
        ):
            return True
    return False


_CELL_LABELS = (
    _STATUS_HEADS | _LABEL_HEADS | _SCORE_LABELS | _IDENTITY_LABEL_TOKENS | _SCORE_QUALIFIERS
    | {"score", "scores", "author", "authors", "autor", "autores", "afiliacion", "correo"}
)
_HTML_CELL = re.compile(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>")
_BRACKETED_REVIEW = re.compile(
    r"(?i)(?P<label>overall|total|final|reviewer|meta|official|rating|ratings|grade|grades|"
    r"score|scores|puntuacion|calificacion|puntaje|nota|总分|总评|评分)"
    r"[^.\n]{0,48}"
    r"[\[(（【]\s*(?P<a>\d+(?:\.\d+)?)"
    r"(?:\s*(?:[-–—~,，]|to|a|y|到|至)\s*(?P<b>\d+(?:\.\d+)?))?"
    r"\s*[\]）】\]]"
)
_METRIC_NEAR = re.compile(
    r"(?i)(?:loss|accuracy|error|threshold|similarity|distance|bleu|f1|metric|measure|"
    r"latency|perplexity|precision|recall|auc|reconstruction|calibration)"
)


def _clean_cell(cell: str) -> str:
    return re.sub(r"^[\s\"'`]+|[\s\"'`.,;:：]+$", "", cell.casefold())


def _cell_label(cell: str) -> str:
    parts = [part for part in re.split(r"[\s:=：/_-]+", _clean_cell(cell)) if part]
    for part in reversed(parts):
        if part in _CELL_LABELS or _cjk_kind(part):
            return part
    return ""


def _review_cell_value(cell: str) -> bool:
    folded = _clean_cell(cell)
    if not folded:
        return False
    if (
        _is_number(folded)
        or re.fullmatch(r"[a-f][+-]?", folded)
        or re.fullmatch(r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?", folded)
        or folded in _LABEL_VALUES
        or folded in _OUTCOME_WORDS
        or folded in _STATUS_ABBREV
        or folded in _STATUS_DEGREES
        or folded in _DECISION_PHRASES
    ):
        return True
    words = [part for part in re.split(r"[\s,;]+", folded) if part]
    if any(word in _OUTCOME_WORDS for word in words):
        return True
    return any(phrase in folded for phrase in _DECISION_PHRASES)


def _identity_cell_value(cell: str) -> bool:
    folded = _clean_cell(cell)
    return len(folded) >= 2 and folded not in _CONTENT_SKIP


def _cell_parts(cell: str) -> set[str]:
    return {part for part in re.split(r"[\s:=：/_-]+", _clean_cell(cell)) if part}


def _pair_is_fact(label_cell: str, value_cell: str) -> bool:
    """A label beside an exact decision, status, score, or identity value.

    A bare score next to a decimal, or a score named by a scientific measure,
    stays a measurement. A review qualifier or an integer next to a bare score
    does not.
    """
    label = _cell_label(label_cell)
    if not label:
        return False
    parts = _cell_parts(label_cell)
    identity = label in _IDENTITY_LABEL_TOKENS or label in {
        "author", "authors", "autor", "autores", "afiliacion", "correo",
    }
    if identity:
        return _identity_cell_value(value_cell)
    if label in {"score", "scores"} and not (parts & _SCORE_QUALIFIERS):
        if parts & (_RANGE_METRICS | _SCIENTIFIC_MEASURES):
            return False
        folded = _clean_cell(value_cell)
        if _is_number(folded) and "." in folded:
            return False
    return _review_cell_value(value_cell)


def _row_has_fact(row: list[str]) -> bool:
    return any(
        _pair_is_fact(cell, row[index + 1])
        for index, cell in enumerate(row[:-1])
    )


_DT_DD = re.compile(r"(?is)<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>")
_SETEXT = re.compile(r"(?m)^([^\n]{1,80})\n[ \t]*[-=]{3,}[ \t]*\n")


def _layout_text(text: str) -> str:
    """Turn a definition item, heading, bracket label, or description pair into a label line.

    A trailing LaTeX rule is not a cell. A bracket that holds a review or
    identity head becomes that label; a numeric bracket stays a score span.
    """
    def promote(match: re.Match) -> str:
        inner = match.group(1).strip()
        folded = _plain_text(inner).casefold()
        if any(_label_kinds(token) for token in _construct_tokens(folded)):
            return f"{inner}: "
        return match.group(0)

    text = re.sub(r"[【\[]([^【】\[\]\n]{1,40})[】\]]", promote, text)
    text = _DT_DD.sub(
        lambda match: f"{_without_markup(match.group(1)).strip()}: {_without_markup(match.group(2)).strip()}\n",
        text,
    )
    text = _SETEXT.sub(r"\1: ", text)
    text = re.sub(r"\\item\s*\[([^\]]{1,80})\]", r"\1: ", text)
    return re.sub(r"\\\\+(?=\s|$)", " ", text)


def _table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    html_cells = [_without_markup(cell) for cell in _HTML_CELL.findall(text)]
    if len(html_cells) >= 2:
        rows.append(html_cells)
    for line in _layout_text(text).splitlines():
        if not any(mark in line for mark in ("|", "\t", "&")):
            continue
        cells = [cell.strip() for cell in re.split(r"[|\t&]", line)]
        cells = [cell for cell in cells if cell and not re.fullmatch(r"[:\-\s]+", cell)]
        if cells:
            rows.append(cells)
    return rows


def _cell_has_labeled_value(cell: str) -> bool:
    """A one-cell row can still carry ``Label: value`` inside the cell."""
    parts = [part.strip() for part in _LINE_SPLIT.split(cell) if part.strip()]
    if len(parts) >= 2 and _pair_is_fact(parts[0], parts[1]):
        return True
    return _labeled_fact(_construct_tokens(cell))


def _tabular_fact(text: str) -> bool:
    """A label cell beside a decision, score, status, or identity value.

    A one-column Markdown table still aligns the header with the next row.
    A single cell that itself contains a label and a value is the same shape.
    """
    rows = _table_rows(text)
    if any(_row_has_fact(row) for row in rows):
        return True
    if any(_cell_has_labeled_value(cell) for row in rows for cell in row):
        return True
    for header, body in zip(rows, rows[1:]):
        width = min(len(header), len(body))
        for index in range(width):
            if _pair_is_fact(header[index], body[index]):
                return True
    return False


_LINE_SPLIT = re.compile(r"\s*(?:[:：=]|->|=>|→|—|–|－|\t|\s{2,})\s*")


def _line_facts(text: str) -> bool:
    """A label and a value on one line, or a label whose value is the next line."""
    lines = [line.strip() for line in _layout_text(text).splitlines()]
    for index, line in enumerate(lines):
        if not line or any(mark in line for mark in ("|", "&")):
            continue
        parts = [part.strip() for part in _LINE_SPLIT.split(line) if part.strip()]
        if len(parts) >= 2 and _pair_is_fact(parts[0], parts[1]):
            return True
        if index + 1 >= len(lines):
            continue
        nxt = lines[index + 1]
        if nxt.startswith(":"):
            nxt = nxt[1:].strip()
        if nxt and _pair_is_fact(line, nxt):
            return True
    return False


def _bracketed_review(text: str) -> bool:
    """A review label next to a bracketed number or range.

    ``similarity score (0.2)`` and a planned metric range stay scientific.
    ``overall rating [6, 8]`` and ``score (6-8)`` do not.
    """
    for match in _BRACKETED_REVIEW.finditer(text):
        window = text[max(0, match.start() - 48):match.end() + 8]
        label = match.group("label").casefold()
        ranged = match.group("b") is not None
        reviewish = label not in {"score", "scores", "评分"}
        metric = _METRIC_NEAR.search(window) is not None
        earlier = _construct_tokens(text[:match.start()])
        planned = any(word in _PLAN_CUES or word.startswith("计划") for word in earlier) or "计划" in text[:match.start()]
        if metric and planned and not reviewish:
            continue
        if not reviewish and not ranged:
            continue
        if metric and not reviewish:
            continue
        return True
    return False


def _explicit_construct(text: str) -> bool:
    """Submission status, total score, score range, or inline preprint authorship.

    The decision is the shape of the normalized tokens. A following content word
    keeps ``accepted practice`` and ``acceptance rate`` scientific, and a metric
    named ``similarity score`` is not an overall rating.
    """
    if not text:
        return False
    tokens = _construct_tokens(text)
    planned = any(word in _PLAN_CUES or word.startswith("计划") for word in tokens) or "计划" in text
    if _CJK_RANGE.search(text) and not planned:
        return True
    return (
        _submission_status(tokens, text)
        or _total_score(tokens)
        or _score_range(tokens)
        or _preprint_authorship(text)
        or _labeled_fact(tokens)
        or _tabular_fact(text)
        or _line_facts(text)
        or _bracketed_review(text)
        or _REVIEW_STATUS.search(text) is not None
        or _CJK_DECISION.search(text) is not None
        or _CJK_SCORE.search(text) is not None
        or _IDENTITY_LABEL.search(text) is not None
        or _SIGNATURE_LABEL.search(text) is not None
    )


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
    parts = [text.strip() for text in flat.values() if isinstance(text, str) and text.strip()]
    if len(parts) > 1 and factual_leak("\n".join(parts)):
        raise ValueError(f"proposal {LEAK_MESSAGE}")
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
