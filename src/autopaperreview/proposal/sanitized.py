"""The only proposal object a model call may receive.

Construction copies an allowlist of source fields into a flat mapping.
A raw dict, a nested object, or a score field is rejected here, so it cannot
be serialized to a model. Factual leaks are refused after normalization.
Paraphrase and cross-field implication are not decided in this module.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

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
        "recommendation",
        "recommendations",
        "decision",
        "decisions",
        "confidence",
        "soundness",
        "presentation",
        "award",
        "awards",
        "authors",
        "authorids",
        "author_ids",
        "email",
        "emails",
        "venue",
        "venueid",
        "final_rating",
        "accept",
        "meta_review",
    }
)
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e]")
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
# Host and path are split so the source does not contain a live forum URL.
_OPENREVIEW = re.compile(
    r"openreview\s*\.\s*net\s*/\s*(?:forum|pdf|note)\s*\?\s*id\s*=\s*[a-z0-9]{10}"
    r"|forum\s*\?\s*id\s*=\s*[a-z0-9]{10}"
)
_ARXIV = re.compile(
    r"(?:export\s*\.\s*)?arxiv\s*\.\s*org\s*/\s*(?:abs|pdf|html|ftp)\s*/\s*\d{4}\s*\.\s*\d{4,5}"
    r"|arxiv\s*:\s*\d{4}\s*\.\s*\d{4,5}"
)
_SCORE = re.compile(
    r"\b(?:overall|final|meta|reviewer|my|our|average)\s+(?:ratings?|scores?|recommendations?)\b"
    r"|\b(?:ratings?|scores?)\s*(?::\s*|\sof\s+)\d+"
    r"|\b\d+\s+out\s+of\s+10\b"
)
_DECISION = re.compile(
    r"\bdecision\s*:\s*(?:accept|reject)\w*"
    r"|\b(?:accept|reject)(?:ed|ance)?\s+as\s+(?:an?\s+)?(?:oral|spotlight|poster)\b"
    r"|\b(?:strong|weak|clear|marginal)\s+(?:accept|reject)(?:ance)?\b"
    r"|\bcamera[\s\-]*ready\b"
    r"|\brecommend(?:ation)?\s+(?:to\s+)?(?:accept|reject)\w*"
    r"|\bvote\s+to\s+(?:accept|reject)\b"
)
_AWARD = re.compile(r"\b(?:best|outstanding)\s+paper\b|\bpaper\s+award\b")
_KEY_ID = re.compile(r"[a-z0-9]{10}")
LEAK_MESSAGE = "contains a rating, decision, award, acceptance, or identity leak"


class SanitizedProposal(dict):
    """Immutable JSON object that has already passed the structural and factual gates."""

    def __init__(self, fields: dict):
        super().__init__()
        dict.update(self, fields)
        self._sealed = True

    def __setitem__(self, key, value):
        if getattr(self, "_sealed", False):
            raise TypeError("sanitized proposal cannot be modified")
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        raise TypeError("sanitized proposal cannot be modified")

    def update(self, *args, **kwargs):
        raise TypeError("sanitized proposal cannot be modified")

    def pop(self, *args, **kwargs):
        raise TypeError("sanitized proposal cannot be modified")

    def clear(self):
        raise TypeError("sanitized proposal cannot be modified")

    def setdefault(self, *args, **kwargs):
        raise TypeError("sanitized proposal cannot be modified")


def normalize_text(text: str) -> str:
    """NFKC, strip zero-width characters, then casefold."""
    folded = unicodedata.normalize("NFKC", text)
    folded = _ZERO_WIDTH.sub("", folded)
    return folded.casefold()


def fold_facts(text: str) -> str:
    """Normalize, then assemble obfuscated emails and spelled-out score words."""
    folded = normalize_text(text)
    folded = _OBFUSCATED_EMAIL.sub(r"\1@\2.\3", folded)
    folded = _SPELLED_EMAIL.sub(r"\1@\2.\3", folded)
    return _SPELLED_NUMBER.sub(lambda match: _NUMBER_WORDS[match.group(0)], folded)


def score_field(key: object) -> bool:
    return str(key).strip().casefold().replace(" ", "_") in SCORE_FIELDS


def factual_leak(text: str, *, key: bool = False) -> bool:
    """True for an email, paper id, explicit score, decision keyword, or award name.

    Indirect wording such as "should be accepted" is not a fact. A mixed
    ten-character key is treated as an OpenReview id. A ten-letter word is not.
    """
    if not isinstance(text, str) or not text:
        return False
    folded = fold_facts(text)
    if (
        _EMAIL.search(folded)
        or _OPENREVIEW.search(folded)
        or _ARXIV.search(folded)
        or _SCORE.search(folded)
        or _DECISION.search(folded)
        or _AWARD.search(folded)
    ):
        return True
    if key and _KEY_ID.fullmatch(folded) and not folded.isalpha():
        return True
    return False


def contains_factual_leak(value: Any) -> bool:
    """Walk keys and values. A leak in a key is the same failure as a leak in text."""
    if isinstance(value, str):
        return factual_leak(value)
    if isinstance(value, dict):
        return any(
            score_field(key) or factual_leak(str(key), key=True) or contains_factual_leak(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_factual_leak(item) for item in value)
    return False


def factual_patterns() -> dict[str, str]:
    return {
        "email": _EMAIL.pattern,
        "openreview": _OPENREVIEW.pattern,
        "arxiv": _ARXIV.pattern,
        "score": _SCORE.pattern,
        "decision": _DECISION.pattern,
        "award": _AWARD.pattern,
    }


def _allowed_key(key: str) -> bool:
    return key in PROPOSAL_FIELDS or _DYNAMIC_FIELD.fullmatch(key) is not None


def _flat_proposal(value: SanitizedProposal) -> bool:
    return all(
        isinstance(key, str) and _allowed_key(key) and (item is None or isinstance(item, str))
        for key, item in value.items()
    )


def sanitize_proposal(source: Any) -> SanitizedProposal:
    """Build the flat proposal object from an allowlist.

    A string becomes the ``proposal`` field. One ``{"text": ...}`` section
    wrapper is flattened. Any other nested object, unknown key, or factual
    leak raises. An existing sanitized flat proposal is returned as-is.
    """
    if isinstance(source, SanitizedProposal):
        if _flat_proposal(source):
            return source
        raise TypeError("model input must be a sanitized proposal")
    if isinstance(source, str):
        source = {"proposal": source.strip()}
    if not isinstance(source, dict):
        raise TypeError("expected proposal text or JSON object")
    # A proposal key holds the document: a string, a field map, or None after
    # a screen removed the only sentence. Sibling record keys such as id are
    # not proposal fields and are not copied.
    if isinstance(source.get("proposal"), dict):
        fields = source["proposal"]
    elif isinstance(source.get("proposal"), str):
        fields = {"proposal": source["proposal"].strip()}
    elif set(source) == {"proposal"} and source.get("proposal") is None:
        fields = {"proposal": None}
    else:
        fields = source
    if not isinstance(fields, dict):
        raise TypeError("invalid proposal object")
    flat: dict[str, str | None] = {}
    for key, value in fields.items():
        if not isinstance(key, str) or not _allowed_key(key):
            raise ValueError("provide proposal fields only")
        if isinstance(value, dict):
            if set(value) - {"text"}:
                raise ValueError("nested proposal field")
            value = value.get("text")
        if value is None:
            flat[key] = None
            continue
        if not isinstance(value, str):
            raise ValueError("invalid proposal section")
        if factual_leak(key, key=True) or factual_leak(value):
            raise ValueError(f"proposal {LEAK_MESSAGE}")
        flat[key] = value
    return SanitizedProposal(flat)


def _copy_checked(value: Any) -> Any:
    if isinstance(value, str):
        if factual_leak(value):
            raise ValueError(f"model input {LEAK_MESSAGE}")
        return value
    if isinstance(value, dict):
        copied = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("model input keys must be strings")
            if score_field(key) or factual_leak(key, key=True):
                raise ValueError(f"model input {LEAK_MESSAGE}")
            copied[key] = _copy_checked(item)
        return copied
    if isinstance(value, (list, tuple)):
        return [_copy_checked(item) for item in value]
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("model input has a non-finite number")
        return value
    raise TypeError("model input must be JSON data")


def seal(document: Any) -> SanitizedProposal:
    """Copy a composed model payload. Keys and values are both checked.

    A plain dict is not returned. ``invoke`` accepts only the sealed object.
    """
    if isinstance(document, SanitizedProposal):
        return document
    if not isinstance(document, (dict, list)):
        raise TypeError("model input must be a SanitizedProposal")
    copied = _copy_checked(document)
    if not isinstance(copied, dict):
        raise TypeError("model input must be a JSON object")
    return SanitizedProposal(copied)


def require_model_input(value: Any) -> SanitizedProposal:
    if not isinstance(value, SanitizedProposal):
        raise TypeError("model input must be a SanitizedProposal")
    return value
