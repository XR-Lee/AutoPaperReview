"""OpenAI-compatible model execution.

The model returns JSON for one judgment at a time. This module checks the
endpoint, stores the raw exchange, and validates the payload. It does not
replace a failed call with the deterministic manuscript adapter.

`OpenAICompatibleAdapter` is the `execute_review` entry point. Text judgments
are contributions, ledger and agenda items, the seven rating dimensions, and
fatal classification. An optional `holistic` judgment adds K samples of a
1–6 decision, a ladder placement against synthetic references, and a 1.0–10.0
venue-scale mean. The shipped K is one. Excerpts on that rating are optional.
`OpenAICompatibleVisionInspector` is the `visual_inspect` entry point and sends
rendered page images to the same chat API.

Loopback endpoints are allowed by default. A hostname that resolves only to
loopback addresses is loopback. Any other host is remote: the stage must set
`allow_remote`, the project network policy must be `allow`, and the configured
environment variable must hold an API key. The key is sent as a bearer token
and is never written to the request body, the stage metadata, or an error
string. The key value is not part of the stage signature.

Connection errors, timeouts, HTTP 429, and HTTP 5xx are retried up to two
times, with a 0.5s then 1s backoff. Any other HTTP 4xx fails immediately.
Those transport retries are separate from the one schema retry.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import time
import unicodedata
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from .i18n import script_matches

from .adapters import ReviewRequest, ReviewResult, _merge_literature
from .anchors import (
    ANCHOR_IDS,
    LOCKED_HOLISTIC_SAMPLES,
    SYNTHETIC_ANCHORS,
    anchor_ladder_text,
    describe_placement,
)
from .hashing import sha256_bytes
from .models import (
    AgendaQuestion,
    Anchor,
    AnchorKind,
    Artifact,
    CommentIntent,
    DimensionScore,
    EvidenceRecord,
    FatalClass,
    FirstReadNote,
    LedgerClaim,
    LocalizedText,
    QueryPerspective,
    ReviewIssue,
    ReviewPackage,
    ReviewRoute,
    SarDimension,
    Severity,
    SourceRecord,
    VisualFinding,
)
from .rubrics import RUBRIC_ID, clause_dimension, clause_score
from .visual import VisualPageRequest

Transport = Callable[[urllib.request.Request, float], bytes]

TEXT_JUDGMENTS = ("contributions", "ledger", "dimensions", "fatal")
OPTIONAL_JUDGMENTS = ("holistic",)
KNOWN_JUDGMENTS = TEXT_JUDGMENTS + OPTIONAL_JUDGMENTS
# One xhigh reasoning call is often several minutes. 60s aborted every paper.
DEFAULT_MODEL_TIMEOUT_SECONDS = 900.0
TRANSPORT_RETRIES = 2
TRANSPORT_BACKOFF_SECONDS = (0.5, 1.0)
_UNRESOLVED_ANCHOR = "excerpt does not occur in the manuscript or a retrieved snapshot"
_HINTS: dict[str, tuple[str, ...]] = {
    "contributions": ("contribution",),
    "ledger": ("ledger", "agenda", "closed-book", "closed_book"),
    "dimensions": ("sar", "dimension", "band"),
    "fatal": ("fatal", "metric", "protocol"),
    "holistic": ("closed", "review", "overall"),
    "visual": ("visual", "figure", "page image"),
}


class ModelExecutionError(RuntimeError):
    """Schema or transport failure. `stage_metadata` is copied onto the stage record."""

    def __init__(self, message: str, *, stage_metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.stage_metadata = stage_metadata or {}


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContributionItem(StrictPayload):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    tests: str = Field(min_length=1)
    comparator: str = Field(min_length=1)
    missing: str = Field(min_length=1)
    severity: Severity = Severity.major
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ContributionJudgment(StrictPayload):
    items: list[ContributionItem] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "ContributionJudgment":
        identifiers = [item.id for item in self.items]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("contribution ids must be unique")
        return self


class LedgerItem(StrictPayload):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    risk: str = Field(min_length=1)


class AgendaItem(StrictPayload):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    question: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    perspective: QueryPerspective | None = None


class LedgerJudgment(StrictPayload):
    ledger: list[LedgerItem] = Field(default_factory=list, min_length=1)
    agenda: list[AgendaItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> "LedgerJudgment":
        ledger_ids = [item.id for item in self.ledger]
        agenda_ids = [item.id for item in self.agenda]
        if len(ledger_ids) != len(set(ledger_ids)):
            raise ValueError("ledger ids must be unique")
        if len(agenda_ids) != len(set(agenda_ids)):
            raise ValueError("agenda ids must be unique")
        return self


class DimensionItem(StrictPayload):
    dimension: SarDimension
    clause_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def clause_matches_dimension(self) -> "DimensionItem":
        if clause_dimension(self.clause_id) != self.dimension.value:
            raise ValueError(f"clause {self.clause_id} does not belong to {self.dimension.value}")
        return self


class DimensionJudgment(StrictPayload):
    dimensions: list[DimensionItem] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def each_dimension_once(self) -> "DimensionJudgment":
        found = {item.dimension for item in self.dimensions}
        missing = [item.value for item in SarDimension if item not in found]
        if missing or len(found) != len(self.dimensions):
            raise ValueError(f"dimensions judgment must include each dimension once; missing {missing}")
        return self


class FatalItem(StrictPayload):
    issue_id: str = Field(min_length=1)
    fatal_class: FatalClass
    decision_relevant: bool = False
    excerpt: str = Field(min_length=1)


class FatalJudgment(StrictPayload):
    classifications: list[FatalItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_issues(self) -> "FatalJudgment":
        identifiers = [item.issue_id for item in self.classifications]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("fatal classifications must name each issue once")
        return self


class FatalVerdict(StrictPayload):
    issue_id: str = Field(min_length=1)
    holds: bool = False


class FatalVerifyJudgment(StrictPayload):
    verdicts: list[FatalVerdict] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_verdicts(self) -> "FatalVerifyJudgment":
        identifiers = [item.issue_id for item in self.verdicts]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("fatal verdicts must name each issue once")
        return self


_RELATION_ALIASES = {
    "better": "better",
    "above": "better",
    "higher": "better",
    "worse": "worse",
    "below": "worse",
    "lower": "worse",
    "tie": "tie",
    "tied": "tie",
    "same": "tie",
    "equal": "tie",
}
_LEAN_ALIASES = {
    "low": "low",
    "lower": "low",
    "mid": "mid",
    "middle": "mid",
    "high": "high",
    "higher": "high",
}


class AnchorComparison(StrictPayload):
    """One manuscript-to-reference vote. The reference is synthetic."""

    anchor_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def canonicalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        updated = dict(value)
        anchor_id = updated.get("anchor_id")
        relation = updated.get("relation")
        if isinstance(anchor_id, str):
            updated["anchor_id"] = anchor_id.strip().lower()
        if isinstance(relation, str):
            updated["relation"] = _RELATION_ALIASES.get(relation.strip().lower(), relation.strip().lower())
        return updated


class HolisticJudgment(StrictPayload):
    """One sample: ladder votes, a 1–6 decision, and a venue-scale mean.

    The placement is derived from the votes. The decision sets the ICLR bin.
    The venue mean is recorded and is not the rank. Excerpts are optional.
    """

    rationale: str = Field(min_length=1)
    rating: float = Field(ge=1, le=6)
    predicted_mean: float = Field(ge=1, le=10)
    comparisons: list[AnchorComparison] = Field(min_length=5, max_length=5)
    gap_lean: str = Field(min_length=1)
    strength_excerpt: str = ""
    weakness_excerpt: str = ""

    @model_validator(mode="before")
    @classmethod
    def accept_comparison_map(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        raw = value.get("comparisons")
        if not isinstance(raw, dict):
            updated = dict(value)
        else:
            rows = []
            for key, item in raw.items():
                if isinstance(item, str):
                    rows.append({"anchor_id": key, "relation": item})
                else:
                    rows.append(item)
            updated = {**value, "comparisons": rows}
        lean = updated.get("gap_lean")
        if isinstance(lean, str):
            updated["gap_lean"] = _LEAN_ALIASES.get(lean.strip().lower(), lean.strip().lower())
        return updated

    @model_validator(mode="after")
    def one_of_each_reference(self) -> "HolisticJudgment":
        found = [item.anchor_id for item in self.comparisons]
        if len(found) != len(set(found)) or set(found) != set(ANCHOR_IDS):
            raise ValueError(
                "comparisons must name each reference once: " + ", ".join(ANCHOR_IDS)
            )
        if self.gap_lean not in _LEAN_ALIASES.values():
            raise ValueError("gap_lean must be low, mid, or high")
        for item in self.comparisons:
            if item.relation not in {"better", "tie", "worse"}:
                raise ValueError(
                    f"relation for {item.anchor_id} must be better, tie, or worse"
                )
        return self


class VisualJudgment(StrictPayload):
    findings: list[VisualFinding] = Field(default_factory=list)


def _require_zh(value: str, label: str) -> None:
    if not script_matches(value, "zh-Hans"):
        raise ValueError(f"{label} must contain Simplified Chinese text")


class ZhIssueText(StrictPayload):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    title: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    required_action: str = Field(min_length=1)
    paper_said: str = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def chinese_fields(self) -> "ZhIssueText":
        for name in ("title", "evidence", "impact", "required_action", "paper_said", "explanation"):
            _require_zh(getattr(self, name), f"{self.id}.{name}")
        return self


class ZhLedgerText(StrictPayload):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    claim: str = Field(min_length=1)
    risk: str = Field(min_length=1)

    @model_validator(mode="after")
    def chinese_fields(self) -> "ZhLedgerText":
        _require_zh(self.claim, f"{self.id}.claim")
        _require_zh(self.risk, f"{self.id}.risk")
        return self


class ZhDimensionText(StrictPayload):
    dimension: SarDimension
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def chinese_rationale(self) -> "ZhDimensionText":
        _require_zh(self.rationale, f"{self.dimension.value}.rationale")
        return self


class ZhHansJudgment(StrictPayload):
    issues: list[ZhIssueText] = Field(default_factory=list)
    ledger: list[ZhLedgerText] = Field(default_factory=list)
    dimensions: list[ZhDimensionText] = Field(default_factory=list)

    @model_validator(mode="after")
    def matches_accepted(self, info: ValidationInfo) -> "ZhHansJudgment":
        expected = None if info.context is None else info.context.get("zh_hans")
        if not isinstance(expected, dict):
            return self
        _same_ids([item.id for item in self.issues], list(expected.get("issue_ids") or []), "issue")
        _same_ids([item.id for item in self.ledger], list(expected.get("ledger_ids") or []), "ledger")
        _same_ids(
            [item.dimension.value for item in self.dimensions],
            list(expected.get("dimensions") or []),
            "dimension",
        )
        return self


def _same_ids(found: list[str], expected: list[str], label: str) -> None:
    if len(found) != len(set(found)):
        raise ValueError(f"zh-Hans {label} ids must be unique")
    if set(found) != set(expected):
        missing = [item for item in expected if item not in set(found)]
        extra = [item for item in found if item not in set(expected)]
        raise ValueError(
            f"zh-Hans {label} ids must match accepted records; missing {missing}; extra {extra}"
        )


_SCHEMAS: dict[str, type[BaseModel]] = {
    "contributions": ContributionJudgment,
    "ledger": LedgerJudgment,
    "dimensions": DimensionJudgment,
    "fatal": FatalJudgment,
    "fatal-verify": FatalVerifyJudgment,
    "holistic": HolisticJudgment,
    "visual": VisualJudgment,
    "zh-Hans": ZhHansJudgment,
}

_SCHEMA_HINTS = {
    "contributions": (
        '{"items":[{"id":"C1","claim":"...","excerpt":"exact substring",'
        '"tests":"...","comparator":"...","missing":"...","severity":"major","confidence":0.5}]} '
        "items must contain at least one contribution."
    ),
    "ledger": (
        '{"ledger":[{"id":"L1","claim":"...","excerpt":"exact substring","risk":"..."}],'
        '"agenda":[{"id":"Q1","question":"...","claim_ids":["C1","L1"]}]} '
        "ledger must contain at least one claim."
    ),
    "dimensions": (
        '{"dimensions":[{"dimension":"experimental_soundness","clause_id":"<clause from the rubric>",'
        '"rationale":"...","excerpt":"copied passage"}]} '
        "Include each of the seven SAR dimensions once. "
        "clause_id is one of cs-2, cs-4, cs-6, cs-8, cs-10, es-2, es-4, es-6, es-8, es-10, "
        "or-2, or-4, or-6, or-8, or-10, im-2, im-4, im-6, im-8, im-10, "
        "wc-2, wc-4, wc-6, wc-8, wc-10, cv-2, cv-4, cv-6, cv-8, cv-10, "
        "pw-2, pw-4, pw-6, pw-8, pw-10. "
        "The rubric text is in the user message. None of those clauses is a default. "
        "The pipeline sets the numeric score from the clause id."
    ),
    "fatal": (
        '{"classifications":[{"issue_id":"I-C1","fatal_class":"leakage","decision_relevant":true,'
        '"excerpt":"copied passage"}]} '
        "fatal_class is one of invalid_protocol, leakage, wrong_metric, unfair_baseline, "
        "unsupported_claim, format_desk_reject. "
        "Omit an issue when the excerpt does not state that class. "
        "decision_relevant is true only when that stated condition decides the result."
    ),
    "fatal-verify": (
        '{"verdicts":[{"issue_id":"I-C1","holds":false}]} '
        "holds is true only when the excerpt itself states the class. "
        "One verdict per issue in the user message."
    ),
    "holistic": (
        "Keys: rationale, rating, predicted_mean, comparisons, gap_lean, strength_excerpt, weakness_excerpt. "
        "rating is an integer 1, 2, 3, 4, 5, or 6. "
        "predicted_mean is a number from 1.0 to 10.0 with one decimal. "
        "comparisons has one object per reference, with anchor_id and relation. "
        "anchor_id is one of weak-reject, ordinary-reject, split-decision, ordinary-accept, strong-accept. "
        "relation is better, tie, or worse. "
        "gap_lean is low, mid, or high. "
        "There is no example number to copy. "
        "1 strong reject, 2 reject, 3 borderline reject, 4 borderline accept, "
        "5 accept, 6 strong accept. 3 and 4 are opposite decisions. "
        "predicted_mean is a side estimate, not the placement and not a rescaling of rating. "
        "strength_excerpt and weakness_excerpt are optional and may be empty. "
        "No other keys."
    ),
    "visual": (
        '{"findings":[{"id":"V1","page":1,"bbox":[0,0,1,1],'
        '"page_image_sha256":"<sha256 of this page image>","kind":"legibility",'
        '"visible_mark":"a mark that is not the caption"}]}'
    ),
    "zh-Hans": (
        '{"issues":[{"id":"I-C1","title":"...","evidence":"...","impact":"...",'
        '"required_action":"...","paper_said":"...","explanation":"..."}],'
        '"ledger":[{"id":"C1","claim":"...","risk":"..."}],'
        '"dimensions":[{"dimension":"experimental_soundness","rationale":"..."}]} '
        "Write Simplified Chinese. Every string must contain Chinese characters. "
        "Ids and dimension names must match the accepted English records."
    ),
}


def _resolves_only_to_loopback(host: str) -> bool:
    """True when every DNS answer is a loopback address. Failure is not loopback."""
    previous = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(2.0)
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(previous)
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            return False
        ip = str(sockaddr[0]).split("%", 1)[0]
        try:
            addresses.append(ipaddress.ip_address(ip))
        except ValueError:
            return False
    return bool(addresses) and all(address.is_loopback for address in addresses)


def endpoint_kind(base_url: str) -> str:
    """Return `loopback` or `remote`. Remote is every host that is not loopback.

    A hostname is resolved. It is loopback only when every address is loopback.
    A resolution failure, or any non-loopback address, is remote.
    """
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("model base_url must be an http or https URL with a host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("model base_url must not include userinfo, a query, or a fragment")
    host = parts.hostname
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return "loopback"
    except ValueError:
        if _resolves_only_to_loopback(host):
            return "loopback"
    return "remote"


def public_base_url(base_url: str) -> str:
    """API root without userinfo or a query. A trailing `/chat/completions` is removed."""
    endpoint_kind(base_url)
    parts = urlsplit(base_url.strip())
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    path = parts.path.rstrip("/")
    suffix = "/chat/completions"
    if path.endswith(suffix):
        path = path[: -len(suffix)]
    return urlunsplit((parts.scheme, netloc, path, "", ""))


def chat_url(base_url: str) -> str:
    return public_base_url(base_url) + "/chat/completions"


def _env_secret(name: str) -> str:
    return os.environ.get(name, "").strip()


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


def _redact_bytes(payload: bytes, secret: str) -> bytes:
    if not secret:
        return payload
    token = secret.encode("utf-8")
    if token in payload:
        return payload.replace(token, b"[redacted]")
    return payload


def _system_prompt(judgment: str) -> str:
    return (
        f"judgment={judgment}\n"
        "Return one JSON object and no other text.\n"
        f"Schema: {_SCHEMA_HINTS[judgment]}\n"
        "Every excerpt must be copied from the manuscript or from a retrieved snapshot in the packet. "
        "A line break may be written as a space. Do not paraphrase. Do not invent a locator. "
        "Unknown fields are rejected."
    )


def _brief(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        text = exc.json()
    else:
        text = str(exc)
    return text[:2000]


def _parse_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not a JSON object: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("model output is not a JSON object")
    return value


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("chat response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("chat response is missing a message")
    content = message.get("content")
    if isinstance(content, list):
        parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("chat response content is empty")
    return content


def _urlopen_transport(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# Below this similarity, a fuzzy window is a different sentence and is dropped.
EXCERPT_FUZZY_THRESHOLD = 0.90
EXCERPT_FUZZY_MIN_CHARS = 24

_DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)
_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u00ab": '"',
        "\u00bb": '"',
    }
)
_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}


def _normalize_with_origins(text: str) -> tuple[str, list[int]]:
    """NFKC, ligatures, line-break hyphens, and collapsed whitespace.

    Each output character keeps the index of the source character it came from.
    A dropped soft hyphen or a hyphen at a line break has no output character.
    """
    raw = text.translate(_DASH_TRANSLATION).translate(_QUOTE_TRANSLATION)
    expanded: list[str] = []
    expanded_origins: list[int] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "-" and index + 1 < len(raw):
            cursor = index + 1
            while cursor < len(raw) and raw[cursor].isspace():
                cursor += 1
            if cursor > index + 1 and cursor < len(raw) and raw[cursor].isalpha():
                index = cursor
                continue
        if char == "\u00ad":
            index += 1
            continue
        piece = _LIGATURES.get(char) or unicodedata.normalize("NFKC", char)
        for out_char in piece:
            expanded.append(out_char)
            expanded_origins.append(index)
        index += 1
    chars: list[str] = []
    origins: list[int] = []
    pending: int | None = None
    for char, origin in zip(expanded, expanded_origins):
        if char.isspace():
            if chars and pending is None:
                pending = origin
            continue
        if pending is not None:
            chars.append(" ")
            origins.append(pending)
            pending = None
        chars.append(char)
        origins.append(origin)
    return "".join(chars), origins


def _fuzzy_normalized_span(
    needle: str,
    source: str,
    origins: list[int],
) -> tuple[int, int] | None:
    if len(needle) < EXCERPT_FUZZY_MIN_CHARS:
        return None
    needle_words = needle.split(" ")
    if len(needle_words) < 4:
        return None
    words: list[tuple[str, int, int]] = []
    cursor = 0
    for part in source.split(" "):
        if not part:
            continue
        start = source.find(part, cursor)
        words.append((part, start, start + len(part)))
        cursor = start + len(part)
    width = len(needle_words)
    best: tuple[float, int, int] | None = None
    for size in range(max(4, width - 1), width + 2):
        if size > len(words):
            continue
        for offset in range(len(words) - size + 1):
            window = " ".join(word for word, _start, _end in words[offset : offset + size])
            ratio = SequenceMatcher(None, needle, window).ratio()
            if ratio < EXCERPT_FUZZY_THRESHOLD:
                continue
            span = (words[offset][1], words[offset + size - 1][2])
            if best is None or ratio > best[0] or (ratio == best[0] and span[0] < best[1]):
                best = (ratio, span[0], span[1])
    if best is None:
        return None
    start, end = best[1], best[2]
    if end <= start or end > len(origins):
        return None
    return origins[start], origins[end - 1] + 1


def _resolve_span(excerpt: str, source: str) -> tuple[int, int, bool] | None:
    """Return source offsets and whether the excerpt was an exact substring."""
    if excerpt in source:
        start = source.find(excerpt)
        return start, start + len(excerpt), True
    needle, _needle_origins = _normalize_with_origins(excerpt.strip())
    normalized, origins = _normalize_with_origins(source)
    if needle and needle in normalized and origins:
        start = normalized.find(needle)
        end = start + len(needle)
        return origins[start], origins[end - 1] + 1, False
    fuzzy = _fuzzy_normalized_span(needle, normalized, origins)
    if fuzzy is None:
        return None
    return fuzzy[0], fuzzy[1], False


def locate_excerpt(
    excerpt: str,
    *,
    manuscript: str,
    manuscript_sha: str,
    snapshots: list[Any],
) -> Anchor | None:
    """Resolve an excerpt against the manuscript, then retrieved snapshots.

    Exact text wins. Otherwise the matcher normalizes Unicode, ligatures,
    line-break hyphenation, and whitespace, then accepts a fuzzy word window
    at or above ``EXCERPT_FUZZY_THRESHOLD``. The stored excerpt is the source
    span, so later checks see the manuscript rather than the model's line breaks.
    """
    if not excerpt or not excerpt.strip():
        return None
    span = _resolve_span(excerpt, manuscript)
    if span is not None:
        start, end, exact = span
        canonical = excerpt if exact else manuscript[start:end]
        return Anchor(
            kind=AnchorKind.text_offset,
            display=f"manuscript[{start}:{end}]",
            artifact_sha256=manuscript_sha,
            encoding="utf-8",
            start=start,
            end=end,
            excerpt=canonical,
        )
    for snapshot in snapshots:
        body = getattr(snapshot, "excerpt", None) or ""
        span = _resolve_span(excerpt, body)
        if span is None:
            continue
        start, end, exact = span
        return Anchor(
            kind=AnchorKind.external_snapshot,
            display=snapshot.id,
            snapshot_id=snapshot.id,
            artifact_sha256=snapshot.sha256,
            excerpt=excerpt if exact else body[start:end],
        )
    return None


def select_packet(
    judgment: str,
    packets: dict[str, str],
    routes: list[ReviewRoute],
    overrides: dict[str, str],
) -> tuple[str | None, str]:
    """Pick the prompt packet for a judgment. Overrides win. One packet is reused."""
    if judgment in overrides:
        key = overrides[judgment]
        if key not in packets:
            raise ModelExecutionError(f"judgment {judgment} packet {key} is not in the stage inputs")
        return key, packets[key]
    if len(packets) == 1:
        key, text = next(iter(packets.items()))
        return key, text
    hints = _HINTS.get(judgment, ())
    best_key: str | None = None
    best_score = 0
    names = {route.id: f"{route.name} {route.kind.value}".lower() for route in routes}
    for key, text in packets.items():
        blob = f"{key} {names.get(key, '')} {text[:4000]}".lower()
        score = sum(1 for hint in hints if hint in blob)
        if score > best_score:
            best_score = score
            best_key = key
    if best_key is None:
        raise ModelExecutionError(
            f"no prompt packet matched judgment {judgment}; set packet_routes.{judgment}"
        )
    return best_key, packets[best_key]


def _schema_key(judgment: str) -> str:
    if judgment in _SCHEMA_HINTS:
        return judgment
    return judgment.split("-", 1)[0]


def _http_status(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _transport_disposition(exc: BaseException) -> str:
    """Return `retry` or `fail`. Schema retries are a separate path."""
    status = _http_status(exc)
    if status is not None:
        if status == 429 or 500 <= status <= 599:
            return "retry"
        return "fail"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError, urllib.error.URLError, OSError)):
        return "retry"
    return "fail"


def _error_body(exc: BaseException) -> bytes:
    read = getattr(exc, "read", None)
    if not callable(read):
        return b""
    try:
        payload = read()
    except Exception:
        return b""
    return payload if isinstance(payload, bytes) else b""


class ModelSession:
    """One stage's chat calls, raw files, and egress note."""

    transport: Transport | None = None
    sleeper: Callable[[float], None] = time.sleep

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str,
        allow_remote: bool,
        network_policy: str,
        timeout_seconds: float,
        temperature: float,
        json_object: bool,
        scratch: Path,
        stage_signature: str,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = public_base_url(base_url)
        self.model_name = model
        self.api_key_env = api_key_env or "APR_MODEL_API_KEY"
        self.allow_remote = allow_remote
        self.network_policy = network_policy
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.json_object = json_object
        self.scratch = scratch
        self.stage_signature = stage_signature
        self._transport = transport or type(self).transport or _urlopen_transport
        self.egress = endpoint_kind(self.base_url)
        self.manuscript_left_machine = False
        self.exchanges: list[dict[str, Any]] = []
        self.rejected: list[dict[str, str]] = []
        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._checked = False
        self._attempt_counts: dict[str, int] = {}
        self._last_messages: list[dict[str, Any]] = []
        self._last_text = ""
        self._last_judgment = ""

    def authorize(self) -> None:
        if self._checked:
            return
        self._checked = True
        secret = _env_secret(self.api_key_env)
        if self.egress == "remote":
            if not self.allow_remote:
                raise PermissionError(
                    "remote model endpoint is denied; set allow_remote = true "
                    "to send manuscript content off this machine"
                )
            if self.network_policy != "allow":
                raise PermissionError("remote model endpoint requires network_policy = allow")
            if not secret:
                raise PermissionError(
                    "remote model endpoint requires an API key in environment variable "
                    f"{self.api_key_env}"
                )
            self.manuscript_left_machine = True
            return
        self.manuscript_left_machine = False

    def metadata(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "base_url": self.base_url,
            "egress": self.egress,
            "manuscript_left_machine": self.manuscript_left_machine,
            "stage_signature": self.stage_signature,
            "result_cache": "stage_signature",
            "model_exchanges": list(self.exchanges),
            "rejected_count": len(self.rejected),
            "drop_counts": _drop_counts(self.rejected),
            "usage_totals": dict(self.usage_totals),
        }

    def reject(
        self,
        judgment: str,
        item_id: str,
        reason: str,
        *,
        category: str | None = None,
    ) -> None:
        row = {"judgment": judgment, "id": item_id, "reason": reason}
        if category:
            row["category"] = category
        self.rejected.append(row)

    def write_index(self) -> None:
        directory = self.scratch / "model"
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / "exchanges.json", self.metadata())
        _write_json(directory / "rejected.json", self.rejected)

    def artifact_names(self) -> list[str]:
        directory = self.scratch / "model"
        if not directory.is_dir():
            return []
        names: list[str] = []
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                names.append(path.relative_to(self.scratch).as_posix())
        return names

    def fail(self, message: str) -> None:
        self.write_index()
        secret = _env_secret(self.api_key_env)
        raise ModelExecutionError(_redact(message, secret), stage_metadata=self.metadata())

    def _next_attempt(self, judgment: str) -> int:
        attempt = self._attempt_counts.get(judgment, 0) + 1
        self._attempt_counts[judgment] = attempt
        return attempt

    def complete(
        self,
        judgment: str,
        user_content: str | list[dict[str, Any]],
        schema: type[BaseModel],
        *,
        context: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> BaseModel:
        self.authorize()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(_schema_key(judgment))},
            {"role": "user", "content": user_content},
        ]
        text = self._exchange(
            judgment,
            messages,
            attempt=self._next_attempt(judgment),
            temperature=temperature,
        )
        try:
            model = schema.model_validate(_parse_object(text), context=context)
        except (ValueError, ValidationError) as first_error:
            messages = [
                *messages,
                {"role": "assistant", "content": text[:8000]},
                {
                    "role": "user",
                    "content": (
                        "Validation failed. Return one JSON object that matches the schema "
                        f"and nothing else.\n{_brief(first_error)}"
                    ),
                },
            ]
            text = self._exchange(
                judgment,
                messages,
                attempt=self._next_attempt(judgment),
                temperature=temperature,
            )
            try:
                model = schema.model_validate(_parse_object(text), context=context)
            except (ValueError, ValidationError) as second_error:
                self.fail(
                    f"judgment {judgment} failed schema validation after one retry: {_brief(second_error)}"
                )
        self._last_messages = messages
        self._last_text = text
        self._last_judgment = judgment
        return model

    def continue_with_error(
        self,
        judgment: str,
        error: str,
        schema: type[BaseModel],
        *,
        context: dict[str, Any] | None = None,
    ) -> BaseModel:
        """One follow-up after a schema-valid payload still failed a pipeline check.

        This is the single retry for that check. It does not start another schema retry.
        """
        self.authorize()
        if self._last_judgment != judgment or not self._last_messages:
            self.fail(f"judgment {judgment} has no completed exchange to retry")
        messages = [
            *self._last_messages,
            {"role": "assistant", "content": self._last_text[:8000]},
            {"role": "user", "content": error},
        ]
        text = self._exchange(judgment, messages, attempt=self._next_attempt(judgment))
        try:
            model = schema.model_validate(_parse_object(text), context=context)
        except (ValueError, ValidationError) as exc:
            self.fail(
                f"judgment {judgment} failed schema validation after one retry: {_brief(exc)}\n{error}"
            )
        self._last_messages = messages
        self._last_text = text
        return model

    def _exchange(
        self,
        judgment: str,
        messages: list[dict[str, Any]],
        *,
        attempt: int,
        temperature: float | None = None,
    ) -> str:
        secret = _env_secret(self.api_key_env)
        body: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature if temperature is None else temperature,
            "messages": messages,
        }
        if self.json_object:
            body["response_format"] = {"type": "json_object"}
        wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        wire = _redact_bytes(wire, secret)
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        request = urllib.request.Request(
            chat_url(self.base_url),
            data=wire,
            headers=headers,
            method="POST",
        )
        directory = self.scratch / "model"
        directory.mkdir(parents=True, exist_ok=True)
        tries = TRANSPORT_RETRIES + 1
        last_detail = "request failed"
        for index in range(tries):
            transport_try = index + 1
            request_name, response_name = _exchange_names(judgment, attempt, transport_try)
            request_path = directory / request_name
            response_path = directory / response_name
            request_path.write_bytes(wire)
            try:
                raw = self._transport(request, self.timeout_seconds)
            except ModelExecutionError:
                raise
            except Exception as exc:
                detail = _redact(f"{type(exc).__name__}: {exc}", secret)[:500]
                status = _http_status(exc)
                if status is not None:
                    detail = f"HTTP {status}: {detail}"
                body = _redact_bytes(_error_body(exc), secret) or detail.encode("utf-8")
                response_path.write_bytes(_redact_bytes(body, secret))
                self._record_exchange(
                    judgment,
                    attempt,
                    transport_try,
                    request_name,
                    response_name,
                    request_path,
                    response_path,
                )
                last_detail = detail
                retryable = _transport_disposition(exc) == "retry"
                if not retryable or index == tries - 1:
                    if retryable and index > 0:
                        self.fail(
                            f"judgment {judgment} request failed after {TRANSPORT_RETRIES} "
                            f"transport retries: {last_detail}"
                        )
                    self.fail(f"judgment {judgment} request failed: {last_detail}")
                type(self).sleeper(TRANSPORT_BACKOFF_SECONDS[index])
                continue
            if not isinstance(raw, bytes):
                self.fail(f"judgment {judgment} transport did not return bytes")
            stored = _redact_bytes(raw, secret)
            response_path.write_bytes(stored)
            self._record_exchange(
                judgment,
                attempt,
                transport_try,
                request_name,
                response_name,
                request_path,
                response_path,
            )
            try:
                payload = json.loads(stored.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return f"invalid chat envelope: {exc}"
            if not isinstance(payload, dict):
                return "invalid chat envelope: response is not an object"
            usage = payload.get("usage")
            if isinstance(usage, dict):
                for key in self.usage_totals:
                    raw_tokens = usage.get(key)
                    if isinstance(raw_tokens, int):
                        self.usage_totals[key] += raw_tokens
            try:
                return _message_text(payload)
            except ValueError as exc:
                return str(exc)
        self.fail(f"judgment {judgment} request failed after {TRANSPORT_RETRIES} transport retries: {last_detail}")

    def _record_exchange(
        self,
        judgment: str,
        attempt: int,
        transport_try: int,
        request_name: str,
        response_name: str,
        request_path: Path,
        response_path: Path,
    ) -> None:
        self.exchanges.append(
            {
                "judgment": judgment,
                "attempt": attempt,
                "transport_try": transport_try,
                "request_sha256": sha256_bytes(request_path.read_bytes()),
                "response_sha256": sha256_bytes(response_path.read_bytes()),
                "request_path": f"model/{request_name}",
                "response_path": f"model/{response_name}",
            }
        )


def _safe_name(judgment: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-._" else "-" for character in judgment)
    return cleaned or "judgment"


def _exchange_names(judgment: str, attempt: int, transport_try: int) -> tuple[str, str]:
    stem = _safe_name(judgment)
    suffix = "" if transport_try == 1 else f".transport-{transport_try}"
    base = f"{stem}.attempt-{attempt}{suffix}"
    return f"{base}.request.json", f"{base}.response.json"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _localized(text: str) -> LocalizedText:
    return LocalizedText(primary=text.strip(), language="en")


def _settings_from_mapping(metadata: dict[str, Any]) -> dict[str, Any]:
    base_url = str(metadata.get("base_url") or "").strip()
    model = str(metadata.get("model") or "").strip()
    if not base_url or not model:
        raise ModelExecutionError("OpenAI-compatible adapter requires base_url and model")
    judgments = list(metadata.get("judgments") or TEXT_JUDGMENTS)
    unknown = [item for item in judgments if item not in KNOWN_JUDGMENTS]
    if unknown:
        raise ModelExecutionError(
            f"unknown judgments {unknown}; visual findings use visual_inspect "
            "with OpenAICompatibleVisionInspector"
        )
    if len(judgments) != len(set(judgments)):
        raise ModelExecutionError("judgments must be unique")
    packet_routes = metadata.get("packet_routes") or {}
    if not isinstance(packet_routes, dict):
        raise ModelExecutionError("packet_routes must be a table of judgment to route id")
    return {
        "base_url": base_url,
        "model": model,
        "api_key_env": str(metadata.get("api_key_env") or "APR_MODEL_API_KEY"),
        "allow_remote": bool(metadata.get("allow_remote")),
        "network_policy": str(metadata.get("network_policy") or "deny"),
        "timeout_seconds": float(metadata.get("timeout_seconds") or DEFAULT_MODEL_TIMEOUT_SECONDS),
        "temperature": float(metadata.get("temperature") or 0),
        "json_object": bool(metadata.get("json_object", True)),
        "judgments": resolve_review_judgments(judgments, cheap=bool(metadata.get("cheap"))),
        "cheap": bool(metadata.get("cheap")),
        "holistic_samples": _bounded_int(
            metadata.get("holistic_samples"),
            default=DEFAULT_HOLISTIC_SAMPLES,
            low=1,
            high=8,
        ),
        "holistic_temperature": _bounded_float(
            metadata.get("holistic_temperature"),
            default=DEFAULT_HOLISTIC_TEMPERATURE,
            low=0.0,
            high=2.0,
        ),
        "packet_routes": {str(key): str(value) for key, value in packet_routes.items()},
        "stage_signature": str(metadata.get("stage_signature") or ""),
        "bilingual": bool(metadata.get("bilingual")),
    }


_CHEAP_SKIP = frozenset({"contributions", "ledger", "dimensions", "fatal"})


def resolve_review_judgments(requested: list[str], *, cheap: bool) -> list[str]:
    """Cheap mode keeps the holistic rating and drops the passes that do not rank."""
    if not cheap:
        return requested
    base = requested or list(TEXT_JUDGMENTS)
    kept = [item for item in base if item not in _CHEAP_SKIP]
    if "holistic" not in kept:
        kept.append("holistic")
    return kept


def _bounded_int(value: object, *, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))


def _bounded_float(value: object, *, default: float, low: float, high: float) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))


def _route_ids(route_id: str | None, routes: list[ReviewRoute]) -> list[str]:
    if route_id and any(route.id == route_id for route in routes):
        return [route_id]
    return []


def _evidence(
    evidence_id: str,
    excerpt: str,
    anchor: Anchor,
    manuscript: Artifact,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        source_id="S1",
        locator=anchor.display,
        claim=excerpt,
        excerpt=excerpt,
        artifact_sha256=anchor.artifact_sha256 or manuscript.sha256,
        anchor=anchor,
    )


def _first_read(claim: str, explanation: str, excerpt: str, *, blocking: bool) -> FirstReadNote:
    return FirstReadNote(
        paper_said=_localized(claim),
        explanation=_localized(explanation),
        quote=excerpt,
        intent=CommentIntent.issue,
        blocking=blocking,
    )


_SESSION_SETTINGS_EXCLUDED = frozenset(
    {
        "judgments",
        "packet_routes",
        "bilingual",
        "cheap",
        "holistic_samples",
        "holistic_temperature",
    }
)


class OpenAICompatibleAdapter:
    """Call an OpenAI-compatible chat endpoint for the text judgments in one stage.

    A class-level `transport` replaces urllib for tests. Production runs leave it unset.
    """

    transport: Transport | None = None

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else type(self).transport

    def execute(self, request: ReviewRequest, scratch: Path) -> ReviewResult:
        if not request.packets:
            raise ModelExecutionError("model execution requires prompt packets from a prompt_packet dependency")
        settings = _settings_from_mapping(request.metadata)
        session = ModelSession(
            scratch=scratch,
            transport=self._transport,
            **{
                key: value
                for key, value in settings.items()
                if key not in _SESSION_SETTINGS_EXCLUDED
            },
        )
        snapshots = list(request.literature.retrieved_snapshots) if request.literature else []
        try:
            built = _run_judgments(session, request, settings, snapshots)
        except ModelExecutionError as exc:
            session.write_index()
            exc.stage_metadata = session.metadata()
            raise
        session.write_index()
        package = built
        package = _merge_literature(package, request.literature)
        return ReviewResult(
            package=package,
            raw_artifact_names=session.artifact_names(),
            metadata={
                **session.metadata(),
                "adapter_kind": "openai-compatible",
                "judgments": settings["judgments"],
                "issue_count": len(package.issues),
            },
        )


FATAL_CLASS_DEFINITIONS = """
Fatal classes are venue caps. Assign one only when the excerpt states the condition.
- invalid_protocol: the decision comparison is not one experiment. Splits, populations, or protocols are mixed into one number.
- leakage: the evaluation uses a label, an example, or a target the method was not allowed to see.
- wrong_metric: the number used for the decision is not the metric the claim names.
- unfair_baseline: a compared system receives an input, a label, or a task the claimed system does not.
- unsupported_claim: the paper's own reported comparison contradicts the headline in the excerpt.
- format_desk_reject: the excerpt states a broken anonymity rule or a broken page limit.
Do not assign missing_closest_neighbor. Related-work search owns that class.
A suspicion, a missing experiment, or a harsh tone is not one of these classes.
""".strip()


def holistic_task_text() -> str:
    """Decision plus the synthetic ladder. Positions are not in this text."""
    head = """
## Placement, then decision
You are reviewing one paper for a top machine-learning conference. Order it by where it sits relative to the fixed references below. Do not average per-axis marks. The accept or reject decision is a separate number and does not order papers that share a decision.

Use the following only as questions that inform the placement and the decision:
- Is the technical work sound, and do the experiments or arguments actually support the claims?
- Can a careful reader follow the paper and repeat the setup from what is written?
- Does the result matter to the venue, and is it new relative to the prior work the paper itself engages?
- Are limitations and possible harms stated, rather than skipped?

Read the references from weakest to strongest. Make one cut. The manuscript is better than a prefix of the ladder, may tie the next reference, and is worse than every stronger reference. Do not mark a stronger reference better than a weaker one. Decide the cut before choosing any number. The decision does not choose the cut. Two papers you would both decline still sit on different references when one is closer to a minor rename and the other is closer to a split decision. Use tie only when you would not rank the manuscript apart from that one reference.

relation is one of three words:
- better: a careful reviewer would rank this manuscript above that reference
- tie: a careful reviewer would not rank the two apart
- worse: a careful reviewer would rank this manuscript below that reference

gap_lean is where the manuscript sits inside the open gap at that cut. low is closer to the weaker side, high is closer to the stronger side, and mid is between them. If you tie a reference, send mid.

## Decision
rating is an integer from 1 to 6. It is only the accept or reject decision:

1 — strong reject. The main claim is already known, the method does not work, or an ethical problem is left untouched.
2 — reject. A technical flaw, an evaluation that does not test the claim, or missing detail that blocks reproduction is enough to decline.
3 — borderline reject. The paper is competent, and the reasons to decline outweigh the reasons to accept.
4 — borderline accept. The paper is competent, and the reasons to accept outweigh the reasons to decline.
5 — accept. The paper is sound and useful, and you would defend it in a discussion.
6 — strong accept. The result matters and the support is tight.

Most submissions are rejected. A competent paper with a real idea and gaps a reviewer would still ask the authors to fix is a 3 when you would not argue to accept it. A 4 means you would argue to accept it. Do not treat 4 as the ordinary score for a finished paper. 3 and 4 are opposite decisions. Use 1 and 6 for extreme cases, not as decoration.

If the paper is weak and you are unsure, the decision is 3 or below. If the paper is strong and you are unsure, the decision is 4 or above.

predicted_mean is a side estimate of the mean score careful reviewers would give this paper on the venue scale from 1.0 to 10.0, with one decimal. It is not a rescaling of rating, it does not choose the comparisons, and it is not chosen from a short list of favorite values such as 4 or 6.

rationale is one short clause per reference, in ladder order, then which reason wins the decision. The comparisons have to match those clauses.
strength_excerpt and weakness_excerpt are optional. Leave both as empty strings unless you copy a passage from the manuscript.
Return only the JSON object.
""".strip()
    return head + "\n\n## Reference ladder\n" + anchor_ladder_text()


HOLISTIC_TASK = holistic_task_text()

# One placement call is the locked evaluation. Temperature is per call so a
# stage default of 0 does not force every sample to the same request body.
DEFAULT_HOLISTIC_SAMPLES = LOCKED_HOLISTIC_SAMPLES
DEFAULT_HOLISTIC_TEMPERATURE = 0.7


def rubric_prompt_text() -> str:
    """Packaged sar_bands_v1 clause list. The dimension judgment appends this verbatim."""
    from importlib.resources import files

    return files("autopaperreview").joinpath("prompts", "sar_bands_v1.md").read_text(encoding="utf-8")


def judgment_prompt_fingerprint() -> str:
    """Hash of the text the adapter adds beyond the route packet. A prompt edit busts the cache."""
    from .hashing import digest_excerpt

    blob = "\n".join(
        [
            rubric_prompt_text(),
            FATAL_CLASS_DEFINITIONS,
            HOLISTIC_TASK,
            "excerpt-normalize-fuzzy-v1",
            "fatal-verify-v1",
            "anchor-place-v1",
            ",".join(f"{anchor.id}={anchor.position}" for anchor in SYNTHETIC_ANCHORS),
        ]
    )
    return digest_excerpt(blob)


def _dimensions_user(packet: str) -> str:
    return (
        packet
        + "\n\n## Rubric sar_bands_v1\n"
        + "Choose one clause per dimension from the list below. "
        + "The clause id in the schema is not a default. "
        + "Read every clause and pick the condition the manuscript meets. "
        + "Do not pick a lower clause unless that condition is met.\n\n"
        + rubric_prompt_text()
    )


def _fatal_user(packet: str, known_issue_ids: list[str]) -> str:
    known = ", ".join(known_issue_ids) or "none"
    return (
        packet
        + "\n\n"
        + FATAL_CLASS_DEFINITIONS
        + "\n\n## Pipeline context\n"
        + f"Accepted issue ids: {known}\n"
        + "Classify only these ids. The pipeline assigns I-<contribution id>.\n"
        + "Every classification needs an excerpt copied from the manuscript that states the class.\n"
    )


def _fatal_verify_user(items: list[tuple[str, str, str]]) -> str:
    lines = [
        FATAL_CLASS_DEFINITIONS,
        "",
        "You see only an issue id, a proposed class, and a manuscript excerpt.",
        "holds is true only when that excerpt states the class condition.",
        "A missing experiment, a harsh tone, or a general weakness is not enough.",
        "",
    ]
    for issue_id, fatal_class, excerpt in items:
        lines.append(f"## {issue_id}\nclass: {fatal_class}\nexcerpt:\n{excerpt}\n")
    lines.append(
        'Return {"verdicts":[{"issue_id":"...","holds":false}]} with one verdict for each id above.'
    )
    return "\n".join(lines)


def _optional_holistic_evidence(
    session: ModelSession,
    request: ReviewRequest,
    snapshots: list[Any],
    payload: HolisticJudgment,
    evidence: list[EvidenceRecord],
) -> dict[str, str]:
    """Attach excerpts that resolve. A miss is a counted drop, not a failed rating."""
    found: dict[str, str] = {}
    seen = {item.id for item in evidence}
    for name, excerpt in (
        ("strength", payload.strength_excerpt),
        ("weakness", payload.weakness_excerpt),
    ):
        if not excerpt.strip():
            continue
        anchor = locate_excerpt(
            excerpt,
            manuscript=request.source_text,
            manuscript_sha=request.manuscript.sha256,
            snapshots=snapshots,
        )
        if anchor is None:
            session.reject("holistic", name, _UNRESOLVED_ANCHOR, category="holistic_excerpt")
            continue
        evidence_id = f"E-holistic-{name}"
        if evidence_id not in seen:
            evidence.append(_evidence(evidence_id, anchor.excerpt or excerpt, anchor, request.manuscript))
            seen.add(evidence_id)
        found[name] = evidence_id
    return found


def _sample_holistic(
    session: ModelSession,
    request: ReviewRequest,
    snapshots: list[Any],
    packet: str,
    evidence: list[EvidenceRecord],
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K independent placements. A schema failure retries once, then fails the paper.

    There is no dimension-band substitute. The rank is the ladder placement.
    The decision and the venue mean are stored from the same response.
    Optional excerpts are recorded when they resolve and counted when they do not.
    """
    if samples < 1:
        session.fail("judgment holistic requires at least one sample")
    user = packet + "\n\n" + HOLISTIC_TASK
    ratings: list[float] = []
    means: list[float] = []
    places: list[float] = []
    counts: list[float] = []
    placements: list[dict[str, Any]] = []
    attached: dict[str, str] = {}
    for _index in range(samples):
        payload = session.complete(
            "holistic",
            user,
            HolisticJudgment,
            temperature=temperature,
        )
        assert isinstance(payload, HolisticJudgment)
        ratings.append(round(float(payload.rating), 4))
        means.append(round(float(payload.predicted_mean), 4))
        relations = {item.anchor_id: item.relation for item in payload.comparisons}
        described = describe_placement(relations, payload.gap_lean)
        places.append(float(described["place"]))
        counts.append(float(described["count"]))
        placements.append(described)
        attached.update(
            _optional_holistic_evidence(session, request, snapshots, payload, evidence)
        )
    if len(ratings) != samples or len(means) != samples or len(places) != samples:
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    return {
        "rating": round(sum(ratings) / len(ratings), 4),
        "samples": ratings,
        "predicted_mean": round(sum(means) / len(means), 4),
        "mean_samples": means,
        "place": round(sum(places) / len(places), 4),
        "place_samples": places,
        "count": round(sum(counts) / len(counts), 4),
        "count_samples": counts,
        "placements": placements,
        "ladder": [{"id": anchor.id, "position": anchor.position} for anchor in SYNTHETIC_ANCHORS],
        "n": len(ratings),
        "verified": True,
        "strength_evidence_id": attached.get("strength"),
        "weakness_evidence_id": attached.get("weakness"),
    }


def _drop_counts(rejected: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in rejected:
        key = str(item.get("category") or item.get("judgment") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _anchor_retry_message(missing: list[tuple[str, str]]) -> str:
    lines = [
        "Unresolved evidence anchors. Return one JSON object with all seven dimensions.",
        "Each excerpt must be copied from the manuscript or a retrieved snapshot. A line break may be a space.",
    ]
    lines.extend(f"- {name}: {reason}" for name, reason in missing)
    return "\n".join(lines)


def _dimension_rows(
    payload: DimensionJudgment,
    *,
    request: ReviewRequest,
    snapshots: list[Any],
    route_id: str | None,
) -> tuple[list[DimensionScore], list[EvidenceRecord], list[tuple[str, str]]]:
    scores: list[DimensionScore] = []
    evidence: list[EvidenceRecord] = []
    missing: list[tuple[str, str]] = []
    for item in payload.dimensions:
        anchor = locate_excerpt(
            item.excerpt,
            manuscript=request.source_text,
            manuscript_sha=request.manuscript.sha256,
            snapshots=snapshots,
        )
        if anchor is None:
            missing.append((item.dimension.value, _UNRESOLVED_ANCHOR))
            continue
        evidence_id = f"E-{item.dimension.value}"
        quote = anchor.excerpt or item.excerpt
        evidence.append(_evidence(evidence_id, quote, anchor, request.manuscript))
        scores.append(
            DimensionScore(
                dimension=item.dimension,
                score=clause_score(item.clause_id),
                rationale=_localized(item.rationale),
                evidence_ids=[evidence_id],
                route_ids=_route_ids(route_id, request.routes),
                rubric_id=RUBRIC_ID,
                clause_id=item.clause_id,
                metadata={"judgment": "dimensions"},
            )
        )
    return scores, evidence, missing


def _translated(text: LocalizedText, zh: str) -> LocalizedText:
    updated = dict(text.translations)
    updated["zh-Hans"] = zh.strip()
    return text.model_copy(update={"translations": updated})


def _zh_user_content(
    issues: list[ReviewIssue],
    claims: list[LedgerClaim],
    scores: list[DimensionScore],
) -> str:
    payload = {
        "issues": [
            {
                "id": issue.id,
                "title": issue.title.primary,
                "evidence": issue.evidence.primary,
                "impact": issue.impact.primary,
                "required_action": issue.required_action.primary,
                "paper_said": issue.first_read.paper_said.primary if issue.first_read else issue.title.primary,
                "explanation": issue.first_read.explanation.primary if issue.first_read else issue.impact.primary,
            }
            for issue in issues
        ],
        "ledger": [
            {
                "id": claim.id,
                "claim": claim.claim.primary,
                "risk": claim.risk.primary if claim.risk else claim.claim.primary,
            }
            for claim in claims
        ],
        "dimensions": [
            {
                "dimension": score.dimension.value,
                "rationale": score.rationale.primary if score.rationale else score.dimension.value,
            }
            for score in scores
        ],
    }
    return (
        "Translate each English field into Simplified Chinese (zh-Hans). "
        "Keep every id and dimension name. Return one JSON object.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _apply_zh_hans(
    session: ModelSession,
    issues: list[ReviewIssue],
    claims: list[LedgerClaim],
    scores: list[DimensionScore],
) -> tuple[list[ReviewIssue], list[LedgerClaim], list[DimensionScore]]:
    context = {
        "zh_hans": {
            "issue_ids": [issue.id for issue in issues],
            "ledger_ids": [claim.id for claim in claims],
            "dimensions": [score.dimension.value for score in scores],
        }
    }
    payload = session.complete(
        "zh-Hans",
        _zh_user_content(issues, claims, scores),
        ZhHansJudgment,
        context=context,
    )
    assert isinstance(payload, ZhHansJudgment)
    issue_text = {item.id: item for item in payload.issues}
    claim_text = {item.id: item for item in payload.ledger}
    score_text = {item.dimension: item for item in payload.dimensions}
    translated_issues: list[ReviewIssue] = []
    for issue in issues:
        item = issue_text[issue.id]
        first = issue.first_read
        if first is not None:
            first = first.model_copy(
                update={
                    "paper_said": _translated(first.paper_said, item.paper_said),
                    "explanation": _translated(first.explanation, item.explanation),
                }
            )
        translated_issues.append(
            issue.model_copy(
                update={
                    "title": _translated(issue.title, item.title),
                    "evidence": _translated(issue.evidence, item.evidence),
                    "impact": _translated(issue.impact, item.impact),
                    "required_action": _translated(issue.required_action, item.required_action),
                    "first_read": first,
                }
            )
        )
    translated_claims: list[LedgerClaim] = []
    for claim in claims:
        item = claim_text[claim.id]
        translated_claims.append(
            claim.model_copy(
                update={
                    "claim": _translated(claim.claim, item.claim),
                    "risk": _translated(claim.risk, item.risk) if claim.risk is not None else None,
                }
            )
        )
    translated_scores: list[DimensionScore] = []
    for score in scores:
        item = score_text[score.dimension]
        translated_scores.append(
            score.model_copy(
                update={
                    "rationale": _translated(score.rationale, item.rationale) if score.rationale else None,
                }
            )
        )
    return translated_issues, translated_claims, translated_scores


def _run_judgments(
    session: ModelSession,
    request: ReviewRequest,
    settings: dict[str, Any],
    snapshots: list[Any],
) -> ReviewPackage:
    issues: list[ReviewIssue] = []
    claims: list[LedgerClaim] = []
    agenda: list[AgendaQuestion] = []
    evidence: list[EvidenceRecord] = []
    scores: list[DimensionScore] = []
    known_claims: dict[str, LedgerClaim] = {}
    known_issues: dict[str, ReviewIssue] = {}
    holistic_meta: dict[str, Any] | None = None

    def packet_for(judgment: str) -> tuple[str | None, str]:
        return select_packet(judgment, request.packets, request.routes, settings["packet_routes"])

    judgments: list[str] = settings["judgments"]
    if "contributions" in judgments:
        route_id, packet = packet_for("contributions")
        payload = session.complete("contributions", packet, ContributionJudgment)
        assert isinstance(payload, ContributionJudgment)
        for item in payload.items:
            anchor = locate_excerpt(
                item.excerpt,
                manuscript=request.source_text,
                manuscript_sha=request.manuscript.sha256,
                snapshots=snapshots,
            )
            if anchor is None:
                session.reject(
                    "contributions",
                    item.id,
                    "excerpt does not occur in the manuscript or a retrieved snapshot",
                    category="contribution_excerpt",
                )
                continue
            evidence_id = f"E-{item.id}"
            quote = anchor.excerpt or item.excerpt
            evidence.append(_evidence(evidence_id, quote, anchor, request.manuscript))
            claim = LedgerClaim(
                id=item.id,
                claim=_localized(item.claim),
                in_paper_evidence_ids=[evidence_id],
                risk=_localized(item.missing),
                anchor=anchor,
                metadata={
                    "kind": "listed_contribution",
                    "judgment": "contributions",
                    "tests": item.tests,
                    "comparator": item.comparator,
                    "audit": "model",
                },
            )
            issue = ReviewIssue(
                id=f"I-{item.id}",
                severity=item.severity,
                category="contribution_audit",
                title=_localized(item.claim),
                location=f"Contribution {item.id}",
                evidence=_localized(item.tests),
                impact=_localized(item.missing),
                required_action=_localized(item.comparator),
                confidence=item.confidence,
                route_ids=_route_ids(route_id, request.routes),
                source_ids=["S1"],
                evidence_ids=[evidence_id],
                anchor=anchor,
                tags=[item.id],
                first_read=_first_read(
                    item.claim,
                    item.missing,
                    quote,
                    blocking=item.severity in {Severity.critical, Severity.major},
                ),
                metadata={"judgment": "contributions"},
            )
            claims.append(claim)
            issues.append(issue)
            known_claims[claim.id] = claim
            known_issues[issue.id] = issue

    if "ledger" in judgments:
        _route_id, packet = packet_for("ledger")
        accepted = ", ".join(sorted(known_claims)) or "none"
        user = packet + f"\n\n## Pipeline context\nAccepted ledger claim ids: {accepted}\n"
        payload = session.complete("ledger", user, LedgerJudgment)
        assert isinstance(payload, LedgerJudgment)
        for item in payload.ledger:
            if item.id in known_claims:
                session.reject(
                    "ledger",
                    item.id,
                    "ledger id collides with an accepted claim",
                    category="ledger_id",
                )
                continue
            anchor = locate_excerpt(
                item.excerpt,
                manuscript=request.source_text,
                manuscript_sha=request.manuscript.sha256,
                snapshots=snapshots,
            )
            if anchor is None:
                session.reject(
                    "ledger",
                    item.id,
                    "excerpt does not occur in the manuscript or a retrieved snapshot",
                    category="ledger_excerpt",
                )
                continue
            evidence_id = f"E-{item.id}"
            quote = anchor.excerpt or item.excerpt
            evidence.append(_evidence(evidence_id, quote, anchor, request.manuscript))
            claim = LedgerClaim(
                id=item.id,
                claim=_localized(item.claim),
                in_paper_evidence_ids=[evidence_id],
                risk=_localized(item.risk),
                anchor=anchor,
                metadata={"judgment": "ledger"},
            )
            claims.append(claim)
            known_claims[claim.id] = claim
        for item in payload.agenda:
            missing = [claim_id for claim_id in item.claim_ids if claim_id not in known_claims]
            if missing:
                session.reject(
                    "ledger",
                    item.id,
                    "agenda item cites a ledger claim that was not accepted",
                    category="ledger_agenda",
                )
                continue
            agenda.append(
                AgendaQuestion(
                    id=item.id,
                    question=item.question,
                    claim_ids=list(item.claim_ids),
                    perspective=item.perspective,
                    metadata={"judgment": "ledger"},
                )
            )

    if "dimensions" in judgments:
        route_id, packet = packet_for("dimensions")
        payload = session.complete("dimensions", _dimensions_user(packet), DimensionJudgment)
        assert isinstance(payload, DimensionJudgment)
        score_rows, evidence_rows, missing = _dimension_rows(
            payload,
            request=request,
            snapshots=snapshots,
            route_id=route_id,
        )
        if missing:
            payload = session.continue_with_error(
                "dimensions",
                _anchor_retry_message(missing),
                DimensionJudgment,
            )
            assert isinstance(payload, DimensionJudgment)
            score_rows, evidence_rows, missing = _dimension_rows(
                payload,
                request=request,
                snapshots=snapshots,
                route_id=route_id,
            )
            if missing:
                for name, reason in missing:
                    session.reject("dimensions", name, reason, category="dimension_excerpt")
                detail = "; ".join(f"{name}: {reason}" for name, reason in missing)
                session.fail(
                    "judgment dimensions failed after one retry: unresolved anchors: " + detail
                )
        scores.extend(score_rows)
        evidence.extend(evidence_rows)

    if "fatal" in judgments:
        _route_id, packet = packet_for("fatal")
        user = _fatal_user(packet, sorted(known_issues))
        payload = session.complete("fatal", user, FatalJudgment)
        assert isinstance(payload, FatalJudgment)
        anchored: list[tuple[FatalItem, Anchor]] = []
        for item in payload.classifications:
            if item.issue_id not in known_issues:
                session.reject(
                    "fatal",
                    item.issue_id,
                    "fatal classification names an unknown issue",
                    category="fatal_unknown",
                )
                continue
            if item.fatal_class == FatalClass.missing_closest_neighbor:
                session.reject(
                    "fatal",
                    item.issue_id,
                    "related-work search owns missing_closest_neighbor",
                    category="fatal_class",
                )
                continue
            anchor = locate_excerpt(
                item.excerpt,
                manuscript=request.source_text,
                manuscript_sha=request.manuscript.sha256,
                snapshots=snapshots,
            )
            if anchor is None:
                session.reject(
                    "fatal",
                    item.issue_id,
                    "excerpt does not occur in the manuscript or a retrieved snapshot",
                    category="fatal_excerpt",
                )
                continue
            anchored.append((item, anchor))
        verified: dict[str, bool] = {}
        if anchored:
            verify_user = _fatal_verify_user(
                [
                    (item.issue_id, item.fatal_class.value, anchor.excerpt or item.excerpt)
                    for item, anchor in anchored
                ]
            )
            verified_payload = session.complete("fatal-verify", verify_user, FatalVerifyJudgment)
            assert isinstance(verified_payload, FatalVerifyJudgment)
            verified = {item.issue_id: item.holds for item in verified_payload.verdicts}
        updates: dict[str, FatalItem] = {}
        for item, anchor in anchored:
            if not verified.get(item.issue_id, False):
                session.reject(
                    "fatal",
                    item.issue_id,
                    "fatal class was not verified from its excerpt",
                    category="fatal_unverified",
                )
                continue
            evidence_id = f"E-fatal-{item.issue_id}"
            evidence.append(
                _evidence(evidence_id, anchor.excerpt or item.excerpt, anchor, request.manuscript)
            )
            updates[item.issue_id] = item
        revised: list[ReviewIssue] = []
        for issue in issues:
            item = updates.get(issue.id)
            if item is None:
                revised.append(issue)
                continue
            evidence_id = f"E-fatal-{item.issue_id}"
            revised.append(
                issue.model_copy(
                    update={
                        "fatal_class": item.fatal_class,
                        "decision_relevant": item.decision_relevant,
                        "evidence_ids": list(dict.fromkeys([*issue.evidence_ids, evidence_id])),
                    }
                )
            )
        issues = revised
        known_issues = {issue.id: issue for issue in issues}

    if "holistic" in judgments:
        _route_id, packet = packet_for("holistic")
        holistic_meta = _sample_holistic(
            session,
            request,
            snapshots,
            packet,
            evidence,
            samples=int(settings["holistic_samples"]),
            temperature=float(settings["holistic_temperature"]),
        )

    if settings["bilingual"]:
        issues, claims, scores = _apply_zh_hans(session, issues, claims, scores)

    source = SourceRecord(
        id="S1",
        kind="manuscript",
        title="Manuscript",
        locator="manuscript",
        sha256=request.manuscript.sha256,
    )
    return ReviewPackage(
        project_id=request.project_id,
        manuscript=request.manuscript,
        routes=list(request.routes),
        sources=[source],
        evidence=evidence,
        issues=issues,
        ledger_claims=claims,
        agenda=agenda,
        dimension_scores=scores,
        metadata={
            "adapter": "openai-compatible",
            "model": session.model_name,
            "egress": session.egress,
            "manuscript_left_machine": session.manuscript_left_machine,
            **({} if holistic_meta is None else {"holistic_rating": holistic_meta}),
        },
    )


class OpenAICompatibleVisionInspector:
    """Send each rendered page image to the same OpenAI-compatible chat endpoint.

    A class-level `transport` replaces urllib for tests. Production runs leave it unset.
    """

    requires_network = False
    status = "unconfigured"
    transport: Transport | None = None

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport if transport is not None else type(self).transport
        self._endpoint: str | None = None
        self._api_key_env = "APR_MODEL_API_KEY"
        self._network_allowed = False
        self._model: str | None = None
        self._allow_remote = False
        self._packets: dict[str, str] = {}
        self._packet_route: str | None = None
        self._network_policy = "deny"
        self._stage_signature = ""
        self._timeout_seconds = DEFAULT_MODEL_TIMEOUT_SECONDS
        self._temperature = 0.0
        self._json_object = True
        self._routes: list[ReviewRoute] = []
        self.session: ModelSession | None = None

    def bind(
        self,
        *,
        findings_path: Path | None = None,
        endpoint: str | None = None,
        api_key_env: str | None = None,
        network_allowed: bool = False,
    ) -> None:
        del findings_path
        self._endpoint = (endpoint or "").strip() or None
        self._api_key_env = api_key_env or "APR_MODEL_API_KEY"
        self._network_allowed = network_allowed
        self._network_policy = "allow" if network_allowed else self._network_policy
        self.status = "ready" if self._endpoint or self._model else "unconfigured"

    def configure(
        self,
        *,
        model: str | None,
        allow_remote: bool,
        base_url: str | None,
        packets: dict[str, str],
        packet_route: str | None,
        network_policy: str,
        stage_signature: str,
        timeout_seconds: float,
        temperature: float,
        json_object: bool,
        routes: list[ReviewRoute] | None = None,
    ) -> None:
        self._model = (model or "").strip() or None
        self._allow_remote = allow_remote
        if base_url:
            self._endpoint = base_url.strip()
        self._packets = dict(packets)
        self._packet_route = packet_route
        self._network_policy = network_policy
        self._stage_signature = stage_signature
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._json_object = json_object
        self._routes = list(routes or [])
        self.status = "ready"

    def inspect(self, request: VisualPageRequest, scratch: Path) -> list[VisualFinding]:
        if not self._endpoint or not self._model:
            raise ModelExecutionError("visual model inspection requires endpoint and model")
        if not self._packets:
            raise ModelExecutionError("visual model inspection requires a prompt packet")
        if self.session is None:
            self.session = ModelSession(
                base_url=self._endpoint,
                model=self._model,
                api_key_env=self._api_key_env,
                allow_remote=self._allow_remote,
                network_policy=self._network_policy,
                timeout_seconds=self._timeout_seconds,
                temperature=self._temperature,
                json_object=self._json_object,
                scratch=scratch,
                stage_signature=self._stage_signature,
                transport=self._transport,
            )
        overrides = {"visual": self._packet_route} if self._packet_route else {}
        _route_id, packet = select_packet("visual", self._packets, self._routes, overrides)
        image = Path(request.image_path).read_bytes()
        encoded = base64.b64encode(image).decode("ascii")
        user: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{packet}\n\n## Page\n"
                    f"- page: {request.page}\n"
                    f"- page_id: {request.page_id or request.page}\n"
                    f"- document: {request.document}\n"
                    f"- page_image_sha256: {request.page_image_sha256}\n"
                    f"- width_px: {request.width_px}\n"
                    f"- height_px: {request.height_px}\n"
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ]
        judgment = f"visual-page-{request.page_id or request.page}"
        try:
            payload = self.session.complete(judgment, user, VisualJudgment)
        except ModelExecutionError as exc:
            self.session.write_index()
            exc.stage_metadata = self.session.metadata()
            self.status = "failed"
            raise
        assert isinstance(payload, VisualJudgment)
        accepted: list[VisualFinding] = []
        for finding in payload.findings:
            page_id = request.page_id or str(request.page)
            if (
                finding.page != request.page
                or finding.page_image_sha256 != request.page_image_sha256
                or (finding.page_id is not None and finding.page_id != page_id)
            ):
                self.session.reject(
                    judgment,
                    finding.id,
                    "visual finding page image hash does not match the rendered page",
                )
                continue
            xmin, ymin, xmax, ymax = finding.bbox
            inside = 0 <= xmin < xmax <= request.width_px and 0 <= ymin < ymax <= request.height_px
            if not inside:
                self.session.reject(judgment, finding.id, "visual finding bbox is outside the rendered page")
                continue
            accepted.append(finding)
        self.session.write_index()
        self.status = "checked"
        return accepted
