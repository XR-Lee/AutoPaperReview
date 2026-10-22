"""Related-work search that runs before novelty and prior-work scores.

The search is not a copy of the manuscript bibliography and not one ad-hoc query.
Keyword calls receive generic topic tokens. Bibliography lookups send a
reference title, a batched arXiv id list, or a DOI, and no manuscript prose. A candidate is
kept only when a provider payload contains its id and title. Papers are never
invented.

Cap numbers follow ``apr-iclr-cap-v2``. ``unfair_baseline`` is venue cap 5
with an experimental-soundness ceiling of 4. ``missing_closest_neighbor`` is
venue cap 6 with a prior-work ceiling of 2 and an originality ceiling of 4.
This module does not write ``OverallScore``. It emits blocking issues that
``rate_venue()`` reads through ``active_caps()``. A degraded search, including
a search where every provider failed, emits no cap. A partial outage can emit
``missing_closest_neighbor`` only from a provider that succeeded, and it still
emits ``unfair_baseline`` when the manuscript baseline anchor resolves. See
``docs/related-work-search.md``.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import logging
import os
import random
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contributions import extract_listed_contributions
from .models import (
    Anchor,
    AnchorKind,
    CommentIntent,
    EvidenceRecord,
    FatalClass,
    FirstReadNote,
    LocalizedText,
    QueryPerspective,
    RelatedWorkQuery,
    ReviewIssue,
    Severity,
    SourceRecord,
)
from .venues import VenueDeadlines, load_venue_deadlines

logger = logging.getLogger("autopaperreview.related_work")

COEFFICIENT_SET = "apr-iclr-cap-v2"
ICLR_BINS = (1, 3, 5, 6, 8, 10)
# Mirror of rating.WEIGHTS / snap_down / CLASS_CAP. Kept local so this branch
# does not import the rating module. A drift here is a bug.
_WEIGHTS = {
    "experimental_soundness": 0.30,
    "claims_supported": 0.25,
    "originality": 0.15,
    "importance_of_research_question": 0.12,
    "prior_work_contextualization": 0.08,
    "writing_clarity": 0.05,
    "community_value": 0.05,
}
UNFAIR_BASELINE_CAP = 5
UNFAIR_BASELINE_CEILINGS = {"experimental_soundness": 4.0}
MISSING_NEIGHBOR_CAP = 6
MISSING_NEIGHBOR_CEILINGS = {
    "prior_work_contextualization": 2.0,
    "originality": 4.0,
}
MANUSCRIPT_SOURCE_ID = "S-manuscript"
MANUSCRIPT_BASELINE_EVIDENCE_ID = "E-manuscript-baselines"
MAX_MUST_COMPARE = 3
DEFAULT_NAMED_METHOD_LIMIT = 15
_MAX_429_RETRIES = 2
_MAX_RETRY_AFTER_SECONDS = 2.0
_S2_429_RETRIES = 5
_S2_MAX_RETRY_AFTER_SECONDS = 60.0
_S2_MIN_INTERVAL = 1.0
# Without S2_API_KEY a 429 must not spend a minute on every query.
S2_UNAUTH_RETRIES = 2
S2_UNAUTH_BUDGET_SECONDS = 8.0
RETRIEVAL_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
BIBLIOGRAPHY_LOOKUP_LIMIT = 60
_ARXIV_ID_BATCH = 40
_CACHEABLE_STATUSES = frozenset({200, 400})
_RETRIEVAL_CACHE: dict[str, "HttpResult"] = {}

INTEGRATION_NOTE = (
    "Cap signals become blocking, decision-relevant issues. "
    "fatal_class unfair_baseline uses the apr-iclr-cap-v2 cap of 5 and an "
    "experimental_soundness ceiling of 4 (clause es-4). "
    "fatal_class missing_closest_neighbor uses venue cap 6, "
    "prior_work_contextualization ceiling 2 (pw-2), and originality ceiling 4 (or-4). "
    "Only a verified candidate dated on or before the venue deadline can support "
    "missing_closest_neighbor. unfair_baseline may cap at 5 from the manuscript's own "
    "experiment lines without a verified candidate, and it always cites the manuscript "
    "baseline anchor. Concurrent work is discussion only. status=degraded (every provider "
    "failed, a timeout, or no provider) emits no cap and must not lower the score. "
    "status=partial lists the failed providers. It may emit missing_closest_neighbor "
    "only for a confirmed neighbour verified by a provider that succeeded, and it still "
    "emits unfair_baseline when the manuscript baseline anchor resolves. Live retrieval and "
    "confirmation compose. With no confirmation, status stays awaiting_confirmation and the "
    "retrieval cap does not fire. A failed provider "
    "never creates or strengthens a retrieval cap. rate_venue() remains the only writer of OverallScore. "
    "Agent mode follows AgentFindingsInspector: the host writes a JSON drop after it "
    "runs the search, and a candidate is kept only when its provider payload parses to "
    "the same id and title."
)

_STOP = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
        "for", "from", "has", "have", "in", "into", "is", "it", "its", "of", "on",
        "or", "our", "that", "the", "their", "these", "this", "those", "to", "was",
        "we", "were", "with", "using", "via", "than", "then", "which", "not", "no",
        "paper", "method", "proposed", "novel", "system", "approach", "based",
        "given", "rather", "instead", "between", "across", "under", "over", "per",
        "into", "within", "without", "also", "only", "such", "each", "both",
    }
)
_GLUE = frozenset(
    {
        "baseline", "baselines", "benchmark", "input", "inputs", "methods",
        "survey", "alternative", "formulation", "formulations", "comparison",
        "estimation", "standard", "scope", "adjacent", "task", "tasks",
        "technique", "model", "models",
    }
)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
_LABEL = re.compile(r"^([A-Za-z][A-Za-z \-]{0,48})\s*:\s*(.+)$")
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)
_ARXIV = re.compile(
    r"\barxiv:\s*([0-9]{4}\.[0-9]{4,5}|[a-z-]+(?:\.[a-z-]+)?/[0-9]{7})\b",
    re.I,
)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_QUOTED = re.compile(r"\"([^\"]{2,})\"")
_CITE = re.compile(r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}")
_COMPACT_METHOD = re.compile(
    r"\b(?:"
    r"[A-Z][A-Za-z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
    r"|[A-Z][a-z0-9]*[a-z][A-Z][A-Za-z0-9]*"
    r"|[A-Z][A-Za-z]*\d+[A-Za-z0-9]*"
    r")\b"
)
_KNOWN_HEADINGS = frozenset(
    {
        "abstract",
        "problem",
        "contributions",
        "experiments",
        "references",
        "bibliography",
        "methods",
        "method",
        "results",
        "conclusion",
        "conclusions",
        "introduction",
        "related work",
        "prior work",
        "background",
        "discussion",
        "appendix",
        "acknowledgements",
        "acknowledgments",
        "approach",
        "evaluation",
        "implementation",
        "limitations",
        "supplementary",
        "supplementary material",
    }
)
_HEADING_CONNECTORS = frozenset(
    {"and", "of", "the", "for", "in", "on", "with", "to", "a", "an", "vs", "via"}
)
_METHOD_LABELS = frozenset({"closest method", "named method", "named methods", "method"})
_CLOSEST_LABELS = frozenset(
    {
        "closest method",
        "closest methods",
        "closest work",
        "closest prior",
        "most related",
        "most related method",
        "most related work",
        "most similar",
        "most similar method",
    }
)
_CLOSEST_PHRASE = re.compile(
    r"\b(?:closest method|closest work|closest prior|most related(?: method| work)?|most similar(?: method)?)\b",
    re.I,
)
_NON_METHOD_ZH = {
    "survey": "综述",
    "review": "综述",
    "dataset": "数据集",
    "benchmark": "基准",
    "generator": "生成器",
}
_PRIVILEGED = re.compile(r"ground[- ]truth scale|oracle scale|privileged", re.I)
_DIFFERENT_TASK = re.compile(r"different task|rather than|instead of", re.I)

_LABEL_KEYS = {
    "task": "task",
    "inputs": "inputs",
    "input": "inputs",
    "outputs": "outputs",
    "output": "outputs",
    "assumptions": "assumptions",
    "assumption": "assumptions",
    "setting": "setting",
    "technique": "technique",
    "extra modality": "extra_modality",
    "extra-modality": "extra_modality",
    "sub-problem": "subproblem",
    "subproblem": "subproblem",
    "adjacent tasks": "adjacent_tasks",
    "adjacent task": "adjacent_tasks",
    "decision metric": "decision_metric",
    "compared baselines": "compared",
    "privileged baselines": "privileged",
    "different-task baselines": "different_task",
    "different task baselines": "different_task",
}

DateRelation = Literal["prior", "concurrent", "unknown"]
CandidateTier = Literal["must-compare", "should-discuss", "optional"]
SearchStatus = Literal["complete", "partial", "degraded", "not_run"]
ConfirmationStatus = Literal["confirmed", "awaiting_confirmation", "not_required"]
LogStatus = Literal[
    "ok",
    "offline",
    "rate_limited",
    "http_error",
    "timeout",
    "skipped",
    "dropped",
    "unconfigured",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemFormulation(Strict):
    task: str
    inputs: str
    outputs: str
    assumptions: list[str] = Field(default_factory=list)
    setting: str
    technique: str = ""
    extra_modality: str | None = None
    subproblem: str = ""
    adjacent_tasks: list[str] = Field(default_factory=list)
    contributions: list[str] = Field(default_factory=list)
    complete: bool
    source: Literal["labeled", "heuristic"]


class BibliographyEntry(Strict):
    marker: str
    title: str | None = None
    year: int | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    raw: str


class ExperimentRecord(Strict):
    decision_metric: str | None = None
    compared: list[str] = Field(default_factory=list)
    privileged: list[str] = Field(default_factory=list)
    different_task: list[str] = Field(default_factory=list)


class RetrievalLogEntry(Strict):
    provider: str
    query_id: str | None = None
    status: LogStatus
    detail: str


class RelatedCandidate(Strict):
    id: str
    title: str
    venue: str | None = None
    year: int | None = None
    link: str
    external_id: str
    summary: str
    cited: bool
    published: str | None = None
    date_relation: DateRelation
    required: bool
    tier: CandidateTier
    reason_en: str
    reason_zh: str
    provider: str = ""
    relevance: float = 0.0
    confirmed: bool = False
    confirmation_reason: str = ""
    query_ids: list[str] = Field(default_factory=list)


class MustCompareConfirmation(Strict):
    """Judging-agent decision for one must-compare candidate."""

    external_id: str = ""
    title: str = ""
    confirmed: bool = False
    reason: str = ""


MustCompareHook = Callable[["RelatedCandidate"], tuple[bool, str]]


class BaselineGap(Strict):
    candidate_id: str | None = None
    title: str
    kind: Literal["missing_baseline", "privileged_baseline", "different_task"]
    detail_en: str
    detail_zh: str


class ClauseAdvice(Strict):
    dimension: str
    clause_id: str
    score: float
    reason_en: str
    reason_zh: str


class CapSignal(Strict):
    id: str
    fatal_class: Literal["unfair_baseline", "missing_closest_neighbor"]
    venue_cap: int
    coefficient_set: str = COEFFICIENT_SET
    dimension_ceilings: dict[str, float]
    clause_ids: dict[str, str]
    blocking: bool = True
    decision_relevant: bool = True
    reason_en: str
    reason_zh: str
    candidate_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RelatedWorkJudgment(Strict):
    formulation: ProblemFormulation
    queries: list[RelatedWorkQuery]
    candidates: list[RelatedCandidate]
    gaps: list[BaselineGap]
    clause_advice: list[ClauseAdvice]
    cap_signals: list[CapSignal]
    effective_venue_cap: int | None
    logs: list[RetrievalLogEntry]
    weakness_en: str
    weakness_zh: str
    rebuttal_en: str
    rebuttal_zh: str
    venue_profile: str
    submission_deadline: str
    decision_metric: str | None = None
    status: SearchStatus
    failed_providers: list[str] = Field(default_factory=list)
    confirmation: ConfirmationStatus = "not_required"
    confirmation_packet: dict[str, Any] = Field(default_factory=dict)
    integration: str = INTEGRATION_NOTE


@dataclass
class RawHit:
    title: str
    summary: str
    venue: str | None
    year: int | None
    published: str | None
    arxiv_id: str | None
    doi: str | None
    openalex_id: str | None
    s2_id: str | None
    link: str
    provider: str
    query_ids: list[str] = field(default_factory=list)

    def external_id(self) -> str:
        if self.doi:
            return f"doi:{norm_doi(self.doi)}"
        if self.arxiv_id:
            return f"arxiv:{norm_arxiv(self.arxiv_id)}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id.rsplit('/', 1)[-1]}"
        if self.s2_id:
            return f"s2:{self.s2_id}"
        return ""


class HttpResult:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}


class RetrievalFailure(Exception):
    def __init__(self, status: LogStatus, detail: str) -> None:
        self.status = status
        super().__init__(detail)


Transport = Callable[[str], HttpResult]


def norm_doi(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.I)
    return text.lower().rstrip(" .)")


def norm_arxiv(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", text, flags=re.I)
    text = re.sub(r"^arxiv:", "", text, flags=re.I)
    text = text.split("v")[0] if re.search(r"v\d+$", text) else text
    return text.strip()


def _normalize_title(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def keywords(text: str, *, limit: int = 8) -> list[str]:
    found: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token in _STOP or token in found:
            continue
        found.append(token)
        if len(found) >= limit:
            break
    return found


def _join_unique(parts: Sequence[str]) -> str:
    found: list[str] = []
    for part in parts:
        for token in part.split():
            if token not in found:
                found.append(token)
    return " ".join(found)


def manuscript_title(text: str) -> str:
    match = re.search(r"(?im)^title\s*:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def _heading_text(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^#{1,6}\s+", "", stripped)
    stripped = re.sub(r"^\d+(?:\.\d+)*[.)]?\s+", "", stripped)
    return stripped.strip()


def _is_real_heading(line: str) -> bool:
    """A section ends at the next real heading, not only at a short name list."""
    text = _heading_text(line)
    if not text or _LABEL.match(text):
        return False
    if text.endswith((".", "?", "!", ";", ",")):
        return False
    words = text.split()
    if not words or len(words) > 8 or len(text) > 80:
        return False
    if not re.search(r"[A-Za-z]", text):
        return False
    if text.casefold() in _KNOWN_HEADINGS:
        return True
    return all(word[:1].isupper() or word.casefold() in _HEADING_CONNECTORS for word in words)


def _section(text: str, name: str) -> str:
    lines = text.splitlines()
    wanted = name.casefold()
    start: int | None = None
    for index, line in enumerate(lines):
        if _heading_text(line).casefold() == wanted:
            start = index + 1
            break
    if start is None:
        return ""
    kept: list[str] = []
    for line in lines[start:]:
        if _is_real_heading(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _labeled_fields(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _is_real_heading(stripped):
            continue
        match = _LABEL.match(stripped)
        if not match:
            continue
        key = _LABEL_KEYS.get(match.group(1).strip().lower())
        if key and key not in found:
            found[key] = match.group(2).strip()
    return found


def _split_items(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s*;\s*", value) if part.strip()]


def derive_formulation(manuscript: str) -> ProblemFormulation:
    """Read task, inputs, outputs, assumptions, and setting from labeled lines."""
    fields = _labeled_fields(manuscript)
    contributions = [str(item["text"]) for item in extract_listed_contributions(manuscript)]
    task = fields.get("task", "")
    inputs = fields.get("inputs", "")
    outputs = fields.get("outputs", "")
    setting = fields.get("setting", "")
    assumptions = _split_items(fields.get("assumptions", ""))
    complete = bool(task and inputs and outputs and setting and assumptions)
    return ProblemFormulation(
        task=task or "unspecified",
        inputs=inputs or "unspecified",
        outputs=outputs or "unspecified",
        assumptions=assumptions,
        setting=setting or "unspecified",
        technique=fields.get("technique", ""),
        extra_modality=fields.get("extra_modality") or None,
        subproblem=fields.get("subproblem", ""),
        adjacent_tasks=_split_items(fields.get("adjacent_tasks", "")),
        contributions=contributions,
        complete=complete,
        source="labeled" if complete else "heuristic",
    )


def allow_tokens(formulation: ProblemFormulation) -> set[str]:
    chunks = [
        formulation.task,
        formulation.inputs,
        formulation.outputs,
        formulation.setting,
        formulation.technique,
        formulation.extra_modality or "",
        formulation.subproblem,
        " ".join(formulation.assumptions),
        " ".join(formulation.adjacent_tasks),
    ]
    tokens = {token for chunk in chunks for token in keywords(chunk, limit=32)}
    return tokens | set(_GLUE)


def query_is_public(query: str, allow: set[str]) -> bool:
    tokens = [token.lower() for token in _TOKEN.findall(query)]
    return bool(tokens) and all(token in allow for token in tokens)


def _sentence_spans(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if part.strip()]


def _is_compact_method_name(name: str) -> bool:
    """Hyphenated, camel-case, or digit-containing method tokens such as Foo-BAR or Abc3D."""
    token = " ".join(name.split())
    if not token or not re.search(r"[A-Za-z]", token):
        return False
    for piece in token.split():
        if "-" in piece and re.search(r"[A-Za-z]", piece):
            return True
        if re.search(r"[A-Za-z]", piece) and re.search(r"\d", piece):
            return True
        if re.search(r"[a-z]", piece) and re.search(r"[A-Z].*[A-Z]", piece):
            return True
    return False


def _remember_name(
    records: dict[str, dict[str, Any]],
    name: str,
    *,
    title: str,
    explicit: bool = False,
    in_experiments: bool = False,
    cited_body: bool = False,
    distance: int | None = None,
) -> None:
    normalized = _normalize_title(name)
    compact = _is_compact_method_name(name)
    if not normalized or (len(normalized.split()) < 2 and not compact):
        return
    if title and normalized == title:
        return
    row = records.get(normalized)
    if row is None:
        row = {
            "name": " ".join(name.split()),
            "explicit": False,
            "in_experiments": False,
            "cited_body": False,
            "distance": None,
        }
        records[normalized] = row
    if explicit:
        row["explicit"] = True
    if in_experiments:
        row["in_experiments"] = True
    if cited_body:
        row["cited_body"] = True
    if distance is not None and (row["distance"] is None or distance < row["distance"]):
        row["distance"] = distance


def named_methods_from_manuscript(
    manuscript: str,
    *,
    limit: int = DEFAULT_NAMED_METHOD_LIMIT,
) -> list[str]:
    """Method names the manuscript itself uses, highest priority first.

    Explicit closest or most-related methods are always included, as are
    references cited in the body. Baselines and experiment-table names come
    next, then related-work sentences nearest the paper's own method, then
    names mentioned more often. Hyphenated, camel-case, and digit-containing
    names are kept. The submission title is omitted. ``limit`` caps the rest;
    it does not drop an explicit closest method or a cited reference.
    """
    if limit < 1:
        raise ValueError("named method limit must be at least 1")
    title = _normalize_title(manuscript_title(manuscript))
    records: dict[str, dict[str, Any]] = {}
    experiments = parse_experiments(manuscript)
    for item in [*experiments.compared, *experiments.privileged, *experiments.different_task]:
        _remember_name(records, _baseline_name(item), title=title, in_experiments=True)
    related = _section(manuscript, "Related Work") or _section(manuscript, "Prior Work")
    for line in related.splitlines():
        labeled = _LABEL.match(line.strip())
        if not labeled:
            continue
        label = labeled.group(1).strip().lower()
        if label not in _METHOD_LABELS and label not in _CLOSEST_LABELS:
            continue
        for piece in _split_items(labeled.group(2)):
            _remember_name(records, piece, title=title, explicit=label in _CLOSEST_LABELS)
    for entry in parse_bibliography(manuscript):
        if entry.title:
            _remember_name(
                records,
                entry.title,
                title=title,
                cited_body=_bibliography_rank(manuscript, entry) < 3,
            )
    technique = _normalize_title(_labeled_fields(_section(manuscript, "Problem")).get("technique", ""))
    sentences = _sentence_spans(related)
    own_indexes = [
        index
        for index, sentence in enumerate(sentences)
        if re.search(r"\b(?:our|we)\b", sentence, re.I)
        or (technique and technique in _normalize_title(sentence))
    ]
    introduced_pattern = re.compile(
        r"(?:closest method|closest work|most related(?: method| work)?|most similar(?: method)?)"
        r"\s*(?:is|:)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,6})"
    )
    for index, sentence in enumerate(sentences):
        distance = min((abs(index - own) for own in own_indexes), default=index)
        explicit_sentence = _CLOSEST_PHRASE.search(sentence) is not None
        introduced = introduced_pattern.search(sentence)
        if introduced:
            _remember_name(records, introduced.group(1), title=title, explicit=True, distance=distance)
        for match in _COMPACT_METHOD.finditer(sentence):
            token = match.group(0)
            if len(token) < 4 and "-" not in token:
                continue
            _remember_name(
                records,
                token,
                title=title,
                explicit=explicit_sentence,
                distance=distance,
            )
        normalized_sentence = _normalize_title(sentence)
        for normalized, row in list(records.items()):
            if normalized in normalized_sentence:
                _remember_name(
                    records,
                    row["name"],
                    title=title,
                    explicit=explicit_sentence,
                    distance=distance,
                )
    lowered = manuscript.lower()
    ranked = []
    for normalized, row in records.items():
        frequency = lowered.count(normalized)
        ranked.append((row, frequency))
    ranked.sort(
        key=lambda item: (
            0 if item[0]["explicit"] else 1,
            0 if item[0]["cited_body"] else 1,
            0 if item[0]["in_experiments"] else 1,
            item[0]["distance"] if item[0]["distance"] is not None else 10**9,
            -item[1],
            _normalize_title(item[0]["name"]),
        )
    )
    forced = [row["name"] for row, _frequency in ranked if row["explicit"] or row["cited_body"]]
    rest = [row["name"] for row, _frequency in ranked if not row["explicit"] and not row["cited_body"]]
    return (forced + rest)[: max(limit, len(forced))]


def generate_search_queries(
    formulation: ProblemFormulation,
    named_methods: Sequence[str] = (),
) -> list[RelatedWorkQuery]:
    """Build one keyword query per family, plus name-only lookups for named methods."""
    if not formulation.complete:
        return []
    allow = allow_tokens(formulation)
    task = keywords(formulation.task)
    inputs = keywords(formulation.inputs)
    outputs = keywords(formulation.outputs)
    technique = keywords(formulation.technique)
    modality = keywords(formulation.extra_modality or "")
    subproblem = keywords(formulation.subproblem or formulation.task)
    adjacent = keywords(" ".join(formulation.adjacent_tasks))
    specs: list[tuple[str, QueryPerspective, list[str]]] = [
        ("same_problem", QueryPerspective.same_problem, [*task, *inputs, *outputs]),
        ("same_technique", QueryPerspective.same_technique, [*technique, *task]),
        (
            "alternative_formulation",
            QueryPerspective.alternative_formulation,
            [*task, *modality, "input"],
        ),
        (
            "standard_baselines",
            QueryPerspective.standard_baselines,
            [*subproblem, "baseline", "benchmark"],
        ),
        ("adjacent_tasks", QueryPerspective.adjacent_tasks, [*adjacent, "survey"]),
    ]
    queries: list[RelatedWorkQuery] = []
    for family, perspective, tokens in specs:
        text = _join_unique(tokens)
        if len(text.split()) < 2:
            continue
        if not query_is_public(text, allow):
            raise RuntimeError(f"refusing to emit a non-public {family} query")
        queries.append(
            RelatedWorkQuery(
                id=f"Q-{family}-1",
                perspective=perspective,
                query=text,
                generated_from="problem_formulation",
                metadata={"family": family, "specificity": "mid", "outbound": "keywords-only"},
            )
        )
    seen_names: set[str] = set()
    name_index = 0
    for name in named_methods:
        normalized = _normalize_title(name)
        if not normalized or normalized in seen_names:
            continue
        if len(normalized.split()) < 2 and not _is_compact_method_name(name):
            continue
        seen_names.add(normalized)
        name_index += 1
        queries.append(
            RelatedWorkQuery(
                id=f"Q-named-{name_index}",
                perspective=QueryPerspective.baselines,
                query=" ".join(name.split()),
                generated_from="manuscript_method_name",
                metadata={"family": "named_method", "specificity": "name", "outbound": "name-only"},
            )
        )
    return queries


def assert_outbound_safe(
    query: str,
    manuscript: str,
    allow: set[str],
    *,
    named: set[str] | None = None,
) -> None:
    title = manuscript_title(manuscript)
    normalized_query = _normalize_title(query)
    if title and _normalize_title(title) and _normalize_title(title) in normalized_query:
        raise RuntimeError("refusing to send the submission title to an external service")
    if named and normalized_query in named:
        abstract = _normalize_title(_section(manuscript, "Abstract"))
        if abstract and len(normalized_query.split()) >= 8 and normalized_query in abstract:
            raise RuntimeError("refusing to send manuscript text to an external service")
        return
    if not query_is_public(query, allow):
        raise RuntimeError("refusing to send manuscript text to an external service")


_NUMERIC_ENTRY = re.compile(r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d{1,3})\.)(?=\s|$)")
_YEAR_ONLY_LINE = re.compile(r"^\s*(?:19|20)\d{2}[a-z]?\s*[.,;:]?\s*$", re.I)
_YEAR_LEAD_LINE = re.compile(r"^\s*(?:19|20)\d{2}[a-z]?\b", re.I)
_AUTHOR_YEAR_START = re.compile(
    r"^\s*[A-Z][A-Za-z'’\-]+"
    r"(?:\s*,\s*(?:[A-Z]\.|[A-Z][A-Za-z'’\-]+)|(?:\s+[A-Z]\.)+|\s+et\s+al\.?|\s+and\s+[A-Z])",
)
_QUOTED_TITLE = re.compile(r"[\"“]([^\"”]{2,})[\"”]")
_BRACKET_CITE = re.compile(r"\[([0-9][0-9\s,;–—\-]*)\]")
_AUTHOR_YEAR_CITE = re.compile(
    r"\b([A-Z][A-Za-z'’\-]+)\s+et\s+al\.?,?\s+\(?((?:19|20)\d{2})\)?"
    r"|"
    r"\b([A-Z][A-Za-z'’\-]+)\s+and\s+[A-Z][A-Za-z'’\-]+,?\s+\(?((?:19|20)\d{2})\)?"
)
_ARXIV_URL = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z-]+(?:\.[a-z-]+)?/[0-9]{7})",
    re.I,
)


def _reference_body(manuscript: str) -> str:
    """References through the next known heading. Wrapped title lines stay inside."""
    lines = manuscript.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if _heading_text(line).casefold() in {"references", "bibliography"}:
            start = index + 1
            break
    if start is None:
        return ""
    kept: list[str] = []
    for line in lines[start:]:
        heading = _heading_text(line).casefold()
        if heading in _KNOWN_HEADINGS and heading not in {"references", "bibliography"}:
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _numeric_marker(line: str) -> str | None:
    match = _NUMERIC_ENTRY.match(line)
    if not match:
        return None
    return next(group for group in match.groups() if group)


def _plausible_title(title: str | None) -> bool:
    if not title:
        return False
    cleaned = title.strip(" \t,.;:\"'“”")
    if len(cleaned) < 3 or not re.search(r"[A-Za-z]", cleaned):
        return False
    if re.fullmatch(r"(?:19|20)\d{2}", cleaned):
        return False
    if cleaned.casefold().startswith("in proceedings") or cleaned.casefold().startswith("in proc"):
        return False
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", cleaned)
    if len(words) >= 2:
        return True
    return _is_compact_method_name(cleaned)


def _strip_reference_marker(raw: str) -> tuple[str | None, str]:
    match = re.match(r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d{1,3})\.)\s*", raw)
    if not match:
        return None, raw.strip()
    marker = next(group for group in match.groups() if group)
    return marker, raw[match.end() :].strip()


def _author_fragment(segment: str) -> bool:
    """True when a period-separated chunk is an author list, not a title."""
    text = " ".join(segment.strip(" ,;").split())
    if not text:
        return True
    name = r"(?:[A-Z]\.?\s+)*[A-Z][A-Za-z'’\-]*"
    if re.fullmatch(rf"{name}(?:\s*(?:,\s*(?:and\s+)?|and\s+){name})*", text):
        return True
    if re.fullmatch(r"(?:[A-Z]\.\s*)+", text):
        return True
    return False


def _title_sentence(text: str) -> str | None:
    rest = re.sub(r"^(?:19|20)\d{2}[a-z]?[.)]\s+", "", text.strip())
    sentence = re.split(r"\.\s+", rest, maxsplit=1)[0].strip(" ,.;:")
    if _plausible_title(sentence) and not _author_fragment(sentence):
        return sentence
    return None


def _extract_reference_title(body: str) -> str | None:
    """Quoted titles, the sentence after the authors, or the sentence after the year."""
    quoted = _QUOTED_TITLE.search(body)
    if quoted:
        title = quoted.group(1).strip(" \t,.;:")
        if _plausible_title(title):
            return title
    cleaned = re.sub(r"\barxiv:\s*\S+", " ", body, flags=re.I)
    cleaned = _ARXIV_URL.sub(" ", cleaned)
    cleaned = _DOI.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    year_title = re.search(r"(?:19|20)\d{2}[a-z]?[.)]\s+(.+?)\.\s", cleaned + " ")
    if year_title:
        title = year_title.group(1).strip(" ,.;:")
        if _plausible_title(title) and not _author_fragment(title):
            return title
    author_block = re.match(
        r"(?:(?:[A-Z]\.\s*){0,4}[A-Z][A-Za-z'’\-]*|[A-Z][A-Za-z'’\-]+,\s*(?:[A-Z]\.\s*)+)"
        r"(?:\s*,\s*(?:and\s+)?(?:(?:[A-Z]\.\s*){0,4}[A-Z][A-Za-z'’\-]*|[A-Z][A-Za-z'’\-]+)"
        r"|\s+and\s+(?:(?:[A-Z]\.\s*){0,4}[A-Z][A-Za-z'’\-]*|[A-Z][A-Za-z'’\-]+)"
        r"|\s+et\s+al\.?)*"
        r"\.\s+",
        cleaned,
    )
    if author_block:
        title = _title_sentence(cleaned[author_block.end() :])
        if title:
            return title
    for segment in re.split(r"\.\s+", cleaned):
        title = segment.strip(" ,.;:")
        if _author_fragment(title) or not _plausible_title(title):
            continue
        return title
    return None


def _first_surname(raw: str) -> str | None:
    _marker, body = _strip_reference_marker(raw)
    patterns = (
        r"([A-Z][A-Za-z'’\-]+)\s*,",
        r"(?:[A-Z]\.\s+)+([A-Z][A-Za-z'’\-]+)",
        r"([A-Z][A-Za-z'’\-]+)\s+et\s+al",
        r"([A-Z][A-Za-z'’\-]+)\s+and\s+",
    )
    for pattern in patterns:
        match = re.match(pattern, body)
        if match and match.group(1).casefold() not in {"in", "the", "proceedings", "proc"}:
            return match.group(1).casefold()
    return None


def _split_reference_entries(body: str) -> list[str]:
    """Group wrapped reference lines. A year line continues the previous entry."""
    entries: list[list[str]] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            entries.append(current)
            current = []

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        year_continuation = bool(_YEAR_ONLY_LINE.match(stripped) or _YEAR_LEAD_LINE.match(stripped))
        new_numeric = _numeric_marker(stripped) is not None
        new_author = _AUTHOR_YEAR_START.match(stripped) is not None and not year_continuation
        if current and new_numeric:
            flush()
        elif current and new_author:
            joined = " ".join(current)
            if _YEAR.search(joined) or len(joined) > 80:
                flush()
        current.append(stripped)
    flush()
    normalized: list[str] = []
    for chunk in entries:
        text = " ".join(piece for line in chunk for piece in line.split())
        if text:
            normalized.append(text)
    return normalized


def parse_bibliography(manuscript: str) -> list[BibliographyEntry]:
    body = _reference_body(manuscript)
    if not body:
        return []
    entries: list[BibliographyEntry] = []
    for index, raw in enumerate(_split_reference_entries(body), start=1):
        if len(raw) < 8:
            continue
        marker, remainder = _strip_reference_marker(raw)
        years = [int(year) for year in _YEAR.findall(raw)]
        year = years[-1] if years else None
        if marker is None:
            surname = _first_surname(raw)
            marker = f"{surname}-{year}" if surname and year else f"ay-{index}"
        arxiv = _ARXIV.search(raw) or _ARXIV_URL.search(raw)
        doi = _DOI.search(raw)
        title = _extract_reference_title(remainder if marker else raw)
        entries.append(
            BibliographyEntry(
                marker=marker,
                title=title,
                year=year,
                arxiv_id=norm_arxiv(arxiv.group(1)) if arxiv else None,
                doi=norm_doi(doi.group(1)) if doi else None,
                raw=raw[:500],
            )
        )
    return entries


def _expand_bracket(body: str) -> set[str]:
    text = body.strip()
    range_match = re.fullmatch(r"(\d+)\s*[–—\-]\s*(\d+)", text)
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        if start > end:
            start, end = end, start
        if end - start > 40:
            return set()
        return {str(number) for number in range(start, end + 1)}
    return {piece for piece in re.split(r"\s*[,;]\s*", text) if piece.isdigit()}


def _citation_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for match in _CITE.finditer(text or ""):
        for piece in match.group(1).split(","):
            key = piece.strip()
            if key:
                keys.add(key)
    for match in _BRACKET_CITE.finditer(text or ""):
        keys.update(_expand_bracket(match.group(1)))
    return keys


def _author_year_cites(text: str) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for match in _AUTHOR_YEAR_CITE.finditer(text or ""):
        surname = match.group(1) or match.group(3)
        year = match.group(2) or match.group(4)
        if surname and year:
            found.add((surname.casefold(), int(year)))
    return found


def _bibliography_sections(manuscript: str) -> list[tuple[int, str]]:
    """Related work, then experiments, then the method description."""
    related = _section(manuscript, "Related Work") or _section(manuscript, "Prior Work") or ""
    experiments = _section(manuscript, "Experiments") or _section(manuscript, "Evaluation") or ""
    method = "\n".join(
        part
        for part in (
            _section(manuscript, "Method") or "",
            _section(manuscript, "Methods") or "",
            _section(manuscript, "Approach") or "",
            _section(manuscript, "Problem") or "",
        )
        if part
    )
    return [(0, related), (1, experiments), (2, method)]


def _prose_before_references(manuscript: str) -> str:
    match = re.search(r"(?im)^(references|bibliography)\s*$", manuscript)
    if not match:
        return manuscript
    return manuscript[: match.start()]


def _entry_is_cited(entry: BibliographyEntry, text: str) -> bool:
    if entry.marker in _citation_keys(text):
        return True
    surname = _first_surname(entry.raw)
    if surname and entry.year and (surname, entry.year) in _author_year_cites(text):
        return True
    return False


def _bibliography_rank(manuscript: str, entry: BibliographyEntry) -> int:
    """0 related work, 1 experiments, 2 method, 3 otherwise."""
    best = 3
    for rank, body in _bibliography_sections(manuscript):
        if _entry_is_cited(entry, body):
            best = min(best, rank)
    return best


def _method_offset(manuscript: str) -> int:
    for name in ("Method", "Methods", "Approach", "Problem"):
        match = re.search(rf"(?im)^{re.escape(name)}\s*$", manuscript)
        if match:
            return match.start()
    return 0


def _cite_distance(manuscript: str, entry: BibliographyEntry) -> int:
    """How far the nearest in-body citation sits from the method description."""
    prose = _prose_before_references(manuscript)
    anchor = _method_offset(manuscript)
    offsets: list[int] = []
    if entry.marker.isdigit():
        for match in _BRACKET_CITE.finditer(prose):
            if entry.marker in _expand_bracket(match.group(1)):
                offsets.append(match.start())
        for match in _CITE.finditer(prose):
            keys = {piece.strip() for piece in match.group(1).split(",")}
            if entry.marker in keys:
                offsets.append(match.start())
    surname = _first_surname(entry.raw)
    if surname and entry.year:
        pattern = re.compile(
            rf"\b{re.escape(surname)}\b(?:\s+et\s+al\.?|\s+and\s+[A-Z][A-Za-z'’\-]+),?\s+\(?{entry.year}\)?"
        )
        offsets.extend(match.start() for match in pattern.finditer(prose))
    if not offsets:
        return 10**9
    return min(abs(offset - anchor) for offset in offsets)


def _cited_query(
    index: int,
    *,
    provider: str,
    lookup: str,
    text: str,
    marker: str,
    cited_in_body: bool,
) -> RelatedWorkQuery:
    return RelatedWorkQuery(
        id=f"Q-cited-{index}",
        perspective=QueryPerspective.baselines,
        query=text,
        generated_from="manuscript_bibliography",
        metadata={
            "family": "cited_reference",
            "specificity": "arxiv_id" if lookup == "arxiv_id_list" else lookup,
            "outbound": "bibliography-metadata",
            "providers": [provider],
            "lookup": lookup,
            "marker": marker,
            "cited_in_body": cited_in_body,
        },
    )


def cited_reference_queries(
    manuscript: str,
    entries: Sequence[BibliographyEntry],
    *,
    limit: int = BIBLIOGRAPHY_LOOKUP_LIMIT,
) -> list[RelatedWorkQuery]:
    """Resolve bibliography entries by title, arXiv id, and DOI. Metadata only.

    Entries cited in related work, the experiments, or the method section come
    first, then the ones nearest the method description. Entries without a
    plausible title are skipped and do not use a slot. The default budget is
    60. arXiv ids are sent as batched ``id_list`` lookups. Semantic Scholar is
    not queried.
    """
    if limit < 1:
        raise ValueError("bibliography lookup limit must be at least 1")
    ranked = sorted(
        (entry for entry in entries if _plausible_title(entry.title)),
        key=lambda entry: (
            _bibliography_rank(manuscript, entry),
            _cite_distance(manuscript, entry),
            int(entry.marker) if entry.marker.isdigit() else 10**9,
            entry.marker,
        ),
    )
    cited = [entry for entry in ranked if _bibliography_rank(manuscript, entry) < 3]
    uncited = [entry for entry in ranked if _bibliography_rank(manuscript, entry) >= 3]
    selected = [*cited, *uncited][:limit]
    queries: list[RelatedWorkQuery] = []
    index = 0
    for entry in selected:
        cited_in_body = _bibliography_rank(manuscript, entry) < 3
        if entry.title:
            index += 1
            queries.append(
                _cited_query(
                    index,
                    provider="arxiv",
                    lookup="title",
                    text=entry.title,
                    marker=entry.marker,
                    cited_in_body=cited_in_body,
                )
            )
            index += 1
            queries.append(
                _cited_query(
                    index,
                    provider="openalex",
                    lookup="title",
                    text=entry.title,
                    marker=entry.marker,
                    cited_in_body=cited_in_body,
                )
            )
        if entry.doi:
            index += 1
            queries.append(
                _cited_query(
                    index,
                    provider="openalex",
                    lookup="doi",
                    text=entry.doi,
                    marker=entry.marker,
                    cited_in_body=cited_in_body,
                )
            )
    id_entries = [entry for entry in selected if entry.arxiv_id]
    for start in range(0, len(id_entries), _ARXIV_ID_BATCH):
        batch = id_entries[start : start + _ARXIV_ID_BATCH]
        index += 1
        queries.append(
            _cited_query(
                index,
                provider="arxiv",
                lookup="arxiv_id_list",
                text=",".join(entry.arxiv_id or "" for entry in batch),
                marker=batch[0].marker,
                cited_in_body=any(_bibliography_rank(manuscript, entry) < 3 for entry in batch),
            )
        )
    return queries


def _baseline_name(item: str) -> str:
    return item.split("|", 1)[0].strip()


def parse_experiments(manuscript: str) -> ExperimentRecord:
    fields = _labeled_fields(_section(manuscript, "Experiments") or manuscript)
    body = _section(manuscript, "Experiments")
    privileged = _split_items(fields.get("privileged", ""))
    different = _split_items(fields.get("different_task", ""))
    compared = _split_items(fields.get("compared", ""))
    if body:
        for sentence in re.split(r"(?<=[.])\s+", " ".join(body.split())):
            if _PRIVILEGED.search(sentence) and not any(sentence.startswith(item) for item in privileged):
                if not any(_PRIVILEGED.search(item) for item in privileged):
                    privileged.append(sentence.strip())
            if _DIFFERENT_TASK.search(sentence) and not any(_DIFFERENT_TASK.search(item) for item in different):
                different.append(sentence.strip())
    return ExperimentRecord(
        decision_metric=fields.get("decision_metric"),
        compared=compared,
        privileged=privileged,
        different_task=different,
    )


def _mentions(name: str, title: str) -> bool:
    left = set(keywords(name, limit=12))
    right = set(keywords(title, limit=12))
    if not left or not right:
        return False
    if left <= right or right <= left:
        return True
    return len(left & right) / min(len(left), len(right)) >= 0.6


def _titles_match(left: str, right: str) -> bool:
    """Normalized title equality. A long title may contain the other as a phrase."""
    first = _normalize_title(left)
    second = _normalize_title(right)
    if not first or not second:
        return False
    if first == second:
        return True
    short, long = (first, second) if len(first) <= len(second) else (second, first)
    if len(short.split()) < 4:
        return False
    return f" {short} " in f" {long} "


def is_cited(hit: RawHit, entries: Sequence[BibliographyEntry]) -> bool:
    arxiv = norm_arxiv(hit.arxiv_id) if hit.arxiv_id else None
    doi = norm_doi(hit.doi) if hit.doi else None
    for entry in entries:
        if arxiv and entry.arxiv_id and arxiv == entry.arxiv_id:
            return True
        if doi and entry.doi and doi == entry.doi:
            return True
        if entry.title and _titles_match(entry.title, hit.title):
            return True
    return False


def classify_date(published: str | None, year: int | None, deadline: date) -> DateRelation:
    if published:
        try:
            stamp = date.fromisoformat(published[:10])
        except ValueError:
            stamp = None
        if stamp is not None:
            return "concurrent" if stamp > deadline else "prior"
    if year is None:
        return "unknown"
    if year < deadline.year:
        return "prior"
    if year > deadline.year:
        return "concurrent"
    return "unknown"


def _covers(text: str, phrase: str) -> bool:
    needed = keywords(phrase, limit=6)
    if not needed:
        return False
    hay = {token.lower() for token in _TOKEN.findall(text)}
    hits = sum(1 for token in needed if token in hay)
    return hits >= max(1, (len(needed) + 1) // 2)


def _non_method_kind(hit: RawHit) -> str | None:
    """Surveys, reviews, datasets, benchmarks, and generators are not methods."""
    title = hit.title or ""
    lead = (hit.summary or "")[:240]
    checks = (
        ("survey", r"\bsurveys?\b"),
        ("review", r"\breviews?\b"),
        ("dataset", r"\bdatasets?\b"),
        ("benchmark", r"\bbenchmarks?\b"),
        ("generator", r"\bgenerators?\b"),
    )
    for kind, pattern in checks:
        if re.search(pattern, title, re.I):
            return kind
    presented = (
        ("survey", r"\b(?:a|an|this)\s+(?:systematic\s+|literature\s+)?survey\b"),
        ("review", r"\b(?:a|an|this)\s+(?:systematic\s+|literature\s+)?review\b"),
        ("dataset", r"\b(?:a|an|this)\s+datasets?\b"),
        ("benchmark", r"\b(?:a|an|this)\s+benchmarks?\b"),
        ("generator", r"\b(?:a|an|this)\s+(?:\w+\s+){0,3}generators?\b"),
    )
    for kind, pattern in presented:
        if re.search(pattern, lead, re.I):
            return kind
    return None


def _overlap(blob: str, phrase: str) -> float:
    needed = keywords(phrase, limit=8)
    if not needed:
        return 0.0
    hay = {token.lower() for token in _TOKEN.findall(blob)}
    return sum(1 for token in needed if token in hay) / len(needed)


def relevance_score(hit: RawHit, formulation: ProblemFormulation) -> float:
    """Task overlap weighted above technique and setting. Modality alone adds nothing."""
    blob = f"{hit.title} {hit.summary}"
    task = _overlap(blob, formulation.task)
    technique = _overlap(blob, formulation.technique) if formulation.technique else 0.0
    setting = _overlap(blob, formulation.setting)
    return round(0.5 * task + 0.3 * technique + 0.2 * setting, 4)


def _base_tier(
    hit: RawHit,
    formulation: ProblemFormulation,
) -> tuple[CandidateTier, str, str]:
    blob = f"{hit.title} {hit.summary}"
    kind = _non_method_kind(hit)
    if kind:
        zh = _NON_METHOD_ZH[kind]
        return (
            "should-discuss",
            f"This is a {kind}, so it can be discussed and is not a must-compare method.",
            f"这是{zh}，可以讨论，但不是必须比较的方法。",
        )
    task_ok = _covers(blob, formulation.task)
    technique_ok = bool(formulation.technique) and _covers(blob, formulation.technique)
    setting_ok = _covers(blob, formulation.setting)
    if task_ok and (technique_ok or setting_ok):
        return (
            "must-compare",
            "Same task and the same technique or setting as the submission.",
            "与投稿的任务相同，并且技术路线或设定相同。",
        )
    if formulation.adjacent_tasks and any(_covers(blob, item) for item in formulation.adjacent_tasks):
        return (
            "optional",
            "Adjacent task used to delimit scope, not a required baseline.",
            "用于划定范围的相邻任务，不是必须的基线。",
        )
    return (
        "optional",
        "Retrieved, and only loosely related to the formulated task.",
        "已检索到，但与所表述的任务只有松散关系。",
    )


def build_candidate(
    hit: RawHit,
    *,
    formulation: ProblemFormulation,
    entries: Sequence[BibliographyEntry],
    deadline: date,
    index: int,
) -> RelatedCandidate:
    relation = classify_date(hit.published, hit.year, deadline)
    tier, reason_en, reason_zh = _base_tier(hit, formulation)
    cited = is_cited(hit, entries)
    required = tier == "must-compare" and relation == "prior"
    if relation == "concurrent" and tier == "must-compare":
        tier = "should-discuss"
        required = False
        reason_en += " Published after the submission deadline, so it is concurrent and not required."
        reason_zh += "发表日期晚于投稿截止日，视为同期工作，不要求比较。"
    elif relation == "unknown" and tier == "must-compare":
        tier = "should-discuss"
        required = False
        reason_en += " Publication date did not resolve, so it is not a required baseline."
        reason_zh += "发表日期未能确认，因此不作为必须的基线。"
    elif cited and tier == "optional":
        tier = "should-discuss"
        reason_en = "Cited in the bibliography, and not otherwise a required baseline."
        reason_zh = "出现在参考文献中，但除此之外不是必须的基线。"
    summary = hit.summary.strip() or "Verified metadata record; the provider returned no abstract."
    if len(summary) > 280:
        summary = summary[:277].rstrip() + "..."
    return RelatedCandidate(
        id=f"C{index}",
        title=hit.title.strip(),
        venue=hit.venue,
        year=hit.year,
        link=hit.link,
        external_id=hit.external_id(),
        summary=summary,
        cited=cited,
        published=hit.published,
        date_relation=relation,
        required=required,
        tier=tier,
        reason_en=reason_en,
        reason_zh=reason_zh,
        provider=hit.provider,
        relevance=relevance_score(hit, formulation),
        query_ids=list(dict.fromkeys(hit.query_ids)),
    )


def _limit_must_compare(candidates: list[RelatedCandidate]) -> list[RelatedCandidate]:
    """Keep at most MAX_MUST_COMPARE confirmed neighbours, highest relevance first.

    Runs after confirmation so an unconfirmed neighbour does not consume a slot.
    """
    ranked = sorted(
        (item for item in candidates if item.required),
        key=lambda item: (-item.relevance, _normalize_title(item.title), item.external_id),
    )
    keep = {item.id for item in ranked[:MAX_MUST_COMPARE]}
    for item in candidates:
        if item.required and item.id not in keep:
            item.required = False
            item.tier = "should-discuss"
            item.reason_en += " Ranked below the must-compare cap, so it is discussion-only."
            item.reason_zh += "相关性排在必须比较的名额之外，因此只作为讨论。"
    return candidates


def _apply_confirmations(
    candidates: list[RelatedCandidate],
    confirmations: Sequence[MustCompareConfirmation],
    hook: MustCompareHook | None,
) -> list[RelatedCandidate]:
    """Confirm must-compare items before the numeric cap. Unconfirmed items stay discussion-only."""
    by_external = {item.external_id.strip(): item for item in confirmations if item.external_id.strip()}
    by_title = {_normalize_title(item.title): item for item in confirmations if item.title.strip()}
    for item in candidates:
        if not item.required:
            continue
        confirmed = False
        reason = ""
        record = by_external.get(item.external_id) or by_title.get(_normalize_title(item.title))
        if record is not None and record.reason.strip():
            reason = record.reason.strip()
            confirmed = bool(record.confirmed)
        if hook is not None:
            accepted, why = hook(item)
            why_text = why.strip()
            if accepted:
                confirmed = True
                if why_text:
                    reason = why_text
            else:
                confirmed = False
                reason = why_text
        if confirmed and reason:
            item.confirmed = True
            item.confirmation_reason = reason
            if reason not in item.reason_en:
                item.reason_en += f" Confirmed: {reason}"
            if reason not in item.reason_zh:
                item.reason_zh += f"已确认：{reason}"
            continue
        item.required = False
        item.tier = "should-discuss"
        item.confirmed = False
        if reason:
            item.confirmation_reason = reason
            if reason not in item.reason_en:
                item.reason_en += f" {reason}"
            if reason not in item.reason_zh:
                item.reason_zh += f" {reason}"
        else:
            item.reason_en += " The judging agent did not confirm it, so it is discussion-only."
            item.reason_zh += "判断模型没有确认它，因此只作为讨论。"
    return candidates


def _named_in(title: str, items: Sequence[str]) -> bool:
    return any(_mentions(_baseline_name(item), title) or _mentions(title, _baseline_name(item)) for item in items)


def compare_experiments(
    candidates: Sequence[RelatedCandidate],
    experiments: ExperimentRecord,
) -> list[BaselineGap]:
    gaps: list[BaselineGap] = []
    pool = [*experiments.compared, *experiments.privileged, *experiments.different_task]
    for candidate in candidates:
        if not candidate.required:
            continue
        if _named_in(candidate.title, pool):
            continue
        gaps.append(
            BaselineGap(
                candidate_id=candidate.id,
                title=candidate.title,
                kind="missing_baseline",
                detail_en=(
                    f"{candidate.title} ({candidate.external_id}) is a required must-compare work "
                    "and is missing from the experiment baselines."
                ),
                detail_zh=(
                    f"{candidate.title}（{candidate.external_id}）是必须比较的工作，但没有出现在实验基线中。"
                ),
            )
        )
    for item in experiments.privileged:
        name = _baseline_name(item)
        gaps.append(
            BaselineGap(
                candidate_id=None,
                title=name,
                kind="privileged_baseline",
                detail_en=f"{name} is given privileged information and is not a fair decision comparison.",
                detail_zh=f"{name}获得了额外特权信息，不能作为公平的决定性比较。",
            )
        )
    for item in experiments.different_task:
        name = _baseline_name(item)
        gaps.append(
            BaselineGap(
                candidate_id=None,
                title=name,
                kind="different_task",
                detail_en=f"{name} solves a different task from the one the claim uses.",
                detail_zh=f"{name}解决的是另一个任务，与主张所使用的任务不同。",
            )
        )
    return gaps


def _clause(
    dimension: str,
    clause_id: str,
    score: float,
    reason_en: str,
    reason_zh: str,
) -> ClauseAdvice:
    return ClauseAdvice(
        dimension=dimension,
        clause_id=clause_id,
        score=score,
        reason_en=reason_en,
        reason_zh=reason_zh,
    )


def build_clause_advice(
    *,
    gaps: Sequence[BaselineGap],
    candidates: Sequence[RelatedCandidate],
    status: SearchStatus,
) -> list[ClauseAdvice]:
    if status == "not_run":
        return []
    missing = [gap for gap in gaps if gap.kind == "missing_baseline"]
    unfair = [gap for gap in gaps if gap.kind in {"privileged_baseline", "different_task"}]
    required = [item for item in candidates if item.required]
    advice: list[ClauseAdvice] = []
    if missing:
        advice.append(
            _clause(
                "prior_work_contextualization",
                "pw-2",
                2.0,
                "The decision comparison's closest verified neighbor is missing.",
                "决定性比较所依赖的、已核实的最近相关工作缺失。",
            )
        )
        advice.append(
            _clause(
                "originality",
                "or-4",
                4.0,
                "A verified neighbor already covers the task, and the submission is not compared with it.",
                "已核实的相关工作已经覆盖该任务，投稿却没有与它比较。",
            )
        )
    elif required and not unfair:
        advice.append(
            _clause(
                "prior_work_contextualization",
                "pw-8",
                8.0,
                "The required neighbors are the comparison the experiments use.",
                "必须比较的相关工作就是实验所使用的比较。",
            )
        )
    elif required and unfair:
        advice.append(
            _clause(
                "prior_work_contextualization",
                "pw-6",
                6.0,
                "The right neighbors are present, and the decision comparison is unfair or off-task.",
                "相关工作已经列出，但决定性比较不公平或答非所问。",
            )
        )
    elif candidates and not required:
        advice.append(
            _clause(
                "prior_work_contextualization",
                "pw-4",
                4.0,
                "Prior work is named and not used as the comparison the claim depends on.",
                "点名了已有工作，但没有把它用作主张所依赖的比较。",
            )
        )
    if unfair:
        advice.append(
            _clause(
                "experimental_soundness",
                "es-4",
                4.0,
                "A privileged input or a different task sits on the decision comparison.",
                "决定性比较使用了特权输入，或比较的是另一个任务。",
            )
        )
    return advice


def evidence_id_for_candidate(candidate_id: str) -> str:
    return f"E-RW-{candidate_id}"


def _eligible_cap_candidate(candidate: RelatedCandidate) -> bool:
    """A retrieval cap cites a confirmed prior must-compare paper."""
    return (
        bool(candidate.external_id)
        and candidate.date_relation == "prior"
        and candidate.required
        and candidate.confirmed
    )


def _provider_sets(logs: Sequence[RetrievalLogEntry]) -> tuple[list[str], list[str]]:
    """Providers that logged a success, and providers that logged a failure.

    ``agent`` and ``local`` are not external providers. A provider that returned
    records and later failed is in both lists: the verified records can support
    a cap, and the provider is still reported as failed.
    """
    successful: list[str] = []
    failed: list[str] = []
    for entry in logs:
        if entry.provider in {"local", "agent"}:
            continue
        if entry.status == "ok":
            if entry.provider not in successful:
                successful.append(entry.provider)
        elif entry.status == "skipped" and "already unavailable" not in entry.detail:
            if entry.provider not in failed:
                failed.append(entry.provider)
        elif entry.status in _DEGRADED_LOGS and entry.provider not in failed:
            failed.append(entry.provider)
    return successful, failed


def _missing_neighbor_allowed(
    candidate: RelatedCandidate,
    *,
    status: SearchStatus,
    successful_providers: set[str],
) -> bool:
    if not _eligible_cap_candidate(candidate):
        return False
    if status == "complete":
        return True
    if status != "partial":
        return False
    return bool(candidate.provider) and candidate.provider in successful_providers


def manuscript_anchor_resolves(excerpt: str) -> bool:
    """A manuscript cap anchor is a non-empty quote of the paper's own lines."""
    text = excerpt.strip()
    return bool(text) and not text.startswith("No labeled baseline")


def _cap_evidence_ids(candidate_ids: Sequence[str]) -> list[str]:
    return [
        MANUSCRIPT_BASELINE_EVIDENCE_ID,
        *[evidence_id_for_candidate(candidate_id) for candidate_id in candidate_ids],
    ]


def build_cap_signals(
    *,
    gaps: Sequence[BaselineGap],
    status: SearchStatus,
    candidates: Sequence[RelatedCandidate] = (),
    logs: Sequence[RetrievalLogEntry] = (),
) -> list[CapSignal]:
    """Emit retrieval caps from successful providers, and manuscript caps on partial searches.

    ``unfair_baseline`` is manuscript evidence. It fires on a complete or partial
    search when the manuscript anchor resolves, and it does not need a verified
    candidate. ``missing_closest_neighbor`` cites only a confirmed prior neighbour
    verified by a provider that succeeded. A failed provider never adds a cap.
    """
    if status not in {"complete", "partial"}:
        return []
    successful = set(_provider_sets(logs)[0])
    by_id = {item.id: item for item in candidates}
    signals: list[CapSignal] = []
    missing_ids: list[str] = []
    for gap in gaps:
        if gap.kind != "missing_baseline" or not gap.candidate_id:
            continue
        candidate = by_id.get(gap.candidate_id)
        if candidate is None or not _missing_neighbor_allowed(
            candidate,
            status=status,
            successful_providers=successful,
        ):
            continue
        missing_ids.append(candidate.id)
    unfair = [gap for gap in gaps if gap.kind in {"privileged_baseline", "different_task"}]
    if missing_ids:
        signals.append(
            CapSignal(
                id="CAP-missing-neighbor",
                fatal_class="missing_closest_neighbor",
                venue_cap=MISSING_NEIGHBOR_CAP,
                dimension_ceilings=dict(MISSING_NEIGHBOR_CEILINGS),
                clause_ids={"prior_work_contextualization": "pw-2", "originality": "or-4"},
                reason_en=(
                    "A verified must-compare paper is absent from the baselines. "
                    f"Venue cap {MISSING_NEIGHBOR_CAP} leaves room for 1/3/6 under apr-iclr-cap-v2."
                ),
                reason_zh=(
                    "已核实且必须比较的论文没有进入基线。"
                    f"会场上限为 {MISSING_NEIGHBOR_CAP}，在 apr-iclr-cap-v2 下仍可落到 1/3/6。"
                ),
                candidate_ids=missing_ids,
                evidence_ids=_cap_evidence_ids(missing_ids),
            )
        )
    if unfair and status in {"complete", "partial"}:
        matched: list[str] = []
        for gap in unfair:
            for candidate in candidates:
                if not _eligible_cap_candidate(candidate):
                    continue
                if _mentions(candidate.title, gap.title) or _mentions(gap.title, candidate.title):
                    matched.append(candidate.id)
        matched_ids = list(dict.fromkeys(matched))
        signals.append(
            CapSignal(
                id="CAP-unfair-baseline",
                fatal_class="unfair_baseline",
                venue_cap=UNFAIR_BASELINE_CAP,
                dimension_ceilings=dict(UNFAIR_BASELINE_CEILINGS),
                clause_ids={"experimental_soundness": "es-4"},
                reason_en=(
                    "A baseline is given privileged information or solves a different task. "
                    f"This is the apr-iclr-cap-v2 unfair_baseline cap of {UNFAIR_BASELINE_CAP}."
                ),
                reason_zh=(
                    "某个基线获得了特权信息，或解决的是另一个任务。"
                    f"这对应 apr-iclr-cap-v2 的 unfair_baseline 上限 {UNFAIR_BASELINE_CAP}。"
                ),
                candidate_ids=matched_ids,
                evidence_ids=_cap_evidence_ids(matched_ids),
            )
        )
    return signals


def _bilingual(english: str, chinese: str) -> LocalizedText:
    return LocalizedText(primary=english, language="en", translations={"zh-Hans": chinese})


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def manuscript_baseline_excerpt(manuscript: str) -> str:
    """Labeled baseline lines only, with whitespace collapsed to one line."""
    fields = _labeled_fields(_section(manuscript, "Experiments"))
    lines: list[str] = []
    for key, label in (
        ("decision_metric", "Decision metric"),
        ("compared", "Compared baselines"),
        ("privileged", "Privileged baselines"),
        ("different_task", "Different-task baselines"),
    ):
        value = fields.get(key, "").strip()
        if value:
            lines.append(f"{label}: {value}")
    text = _collapse(" ".join(lines))
    if not text:
        text = "No labeled baseline or related-work lines were found."
    if len(text) > 1200:
        text = text[:1197].rstrip() + "..."
    return text


def _cap_first_read(
    signal: CapSignal,
    excerpt: str,
    cited: Sequence[RelatedCandidate],
) -> FirstReadNote:
    quote = _collapse(excerpt) or None
    if signal.fatal_class == "missing_closest_neighbor":
        names = ", ".join(item.title for item in cited) or "a verified prior method"
        paper_en = f"The experiments do not compare against {names}."
        paper_zh = f"实验没有与{names}比较。"
        explanation_en = (
            "A first-time reader needs that comparison before treating the result as new. "
            f"The venue cap stays {signal.venue_cap} until those prior methods are compared fairly."
        )
        explanation_zh = (
            "第一次读这篇稿件的人，需要先看到这次比较，才能判断结果是不是新的。"
            f"在公平比较这些已有方法之前，会场上限保持在 {signal.venue_cap}。"
        )
    else:
        paper_en = (
            "A listed baseline is given privileged information, such as ground-truth scale, "
            "or it solves a different task."
        )
        paper_zh = "列出的某个基线带有特权信息（例如真实尺度），或者它解决的是另一个任务。"
        explanation_en = (
            "That baseline cannot decide the claim. "
            f"The venue cap stays {signal.venue_cap} until the decision comparison uses the same task and the same inputs."
        )
        explanation_zh = (
            "该基线不能用来决定主张是否成立。"
            f"在决定性比较使用同一任务和同一输入之前，会场上限保持在 {signal.venue_cap}。"
        )
    return FirstReadNote(
        paper_said=_bilingual(paper_en, paper_zh),
        explanation=_bilingual(explanation_en, explanation_zh),
        quote=quote,
        intent=CommentIntent.issue,
        blocking=True,
    )


def manuscript_baseline_records(
    manuscript: str,
    *,
    sha256: str | None,
    locator: str,
) -> tuple[SourceRecord, EvidenceRecord]:
    excerpt = manuscript_baseline_excerpt(manuscript)
    anchor = Anchor(
        kind=AnchorKind.quote,
        display="Experiments / References",
        artifact_sha256=sha256,
        excerpt=excerpt,
    )
    source = SourceRecord(
        id=MANUSCRIPT_SOURCE_ID,
        kind="manuscript",
        title="Manuscript",
        locator=locator,
        sha256=sha256,
    )
    evidence = EvidenceRecord(
        id=MANUSCRIPT_BASELINE_EVIDENCE_ID,
        source_id=MANUSCRIPT_SOURCE_ID,
        locator="Experiments / References",
        claim="Baselines and related-work lines named in the manuscript.",
        excerpt=excerpt,
        artifact_sha256=sha256,
        anchor=anchor,
        metadata={"role": "manuscript_baselines"},
    )
    return source, evidence


def related_work_cap_applies(judgment: RelatedWorkJudgment, issue: ReviewIssue) -> bool:
    """Whether ``rate`` should keep a related-work cap for this search status.

    A failed provider cannot create or strengthen a retrieval cap. On a partial
    search, ``unfair_baseline`` still applies when the manuscript anchor resolves,
    and ``missing_closest_neighbor`` applies only when every cited candidate was
    confirmed and verified by a provider that logged success.
    """
    if issue.metadata.get("source") != "related_work_search":
        return True
    if issue.fatal_class == FatalClass.unfair_baseline:
        if judgment.status not in {"complete", "partial"}:
            return False
        if MANUSCRIPT_BASELINE_EVIDENCE_ID not in issue.evidence_ids:
            return False
        excerpt = issue.anchor.excerpt if issue.anchor is not None else ""
        return manuscript_anchor_resolves(excerpt or "")
    if judgment.status == "complete":
        return True
    if judgment.status != "partial":
        return False
    if issue.fatal_class != FatalClass.missing_closest_neighbor:
        return False
    candidate_ids = issue.metadata.get("candidate_ids") or []
    if not isinstance(candidate_ids, list) or not candidate_ids:
        return False
    successful = set(_provider_sets(judgment.logs)[0])
    by_id = {item.id: item for item in judgment.candidates}
    for candidate_id in candidate_ids:
        if not isinstance(candidate_id, str):
            return False
        candidate = by_id.get(candidate_id)
        if candidate is None or not _missing_neighbor_allowed(
            candidate,
            status="partial",
            successful_providers=successful,
        ):
            return False
    return True


def issues_from_cap_signals(
    judgment: RelatedWorkJudgment,
    *,
    anchor_excerpt: str,
) -> list[ReviewIssue]:
    """Blocking issues for active_caps().

    A complete or partial search may emit both caps. Degraded and not-run emit none.
    ``missing_closest_neighbor`` requires a confirmed prior candidate from a
    provider that succeeded. ``unfair_baseline`` is manuscript evidence: it fires
    on a complete or partial search when the manuscript anchor resolves, and it
    never requires a verified candidate.
    """
    if judgment.status not in {"complete", "partial"}:
        return []
    if not manuscript_anchor_resolves(anchor_excerpt):
        return []
    successful = set(_provider_sets(judgment.logs)[0])
    by_id = {item.id: item for item in judgment.candidates}
    anchor = Anchor(
        kind=AnchorKind.quote,
        display="Experiments / References",
        excerpt=anchor_excerpt,
    )
    issues: list[ReviewIssue] = []
    for signal in judgment.cap_signals:
        if signal.fatal_class == "unfair_baseline":
            if judgment.status not in {"complete", "partial"}:
                continue
            if MANUSCRIPT_BASELINE_EVIDENCE_ID not in signal.evidence_ids:
                continue
        cited: list[RelatedCandidate] = []
        for candidate_id in signal.candidate_ids:
            candidate = by_id.get(candidate_id)
            if candidate is None:
                continue
            if signal.fatal_class == "missing_closest_neighbor":
                if not _missing_neighbor_allowed(
                    candidate,
                    status=judgment.status,
                    successful_providers=successful,
                ):
                    continue
            elif not _eligible_cap_candidate(candidate):
                continue
            cited.append(candidate)
        if signal.fatal_class == "missing_closest_neighbor" and not cited:
            continue
        evidence_ids = [MANUSCRIPT_BASELINE_EVIDENCE_ID]
        evidence_ids.extend(evidence_id_for_candidate(item.id) for item in cited)
        external_ids = [item.external_id for item in cited]
        if signal.fatal_class == "missing_closest_neighbor":
            joined = ", ".join(external_ids)
            title_en = "Closest verified neighbor is missing from the baselines"
            title_zh = "已核实的最近相关工作没有进入基线"
            evidence_en = (
                f"Verified prior records {joined} are dated before the submission deadline "
                "and are absent from the manuscript baselines."
            )
            evidence_zh = (
                f"已核实且早于投稿截止日的记录 {joined} 没有出现在稿件基线中。"
            )
            impact_en = (
                f"apr-iclr-cap-v2 caps the venue bin at {signal.venue_cap} "
                "and lowers prior-work and originality."
            )
            impact_zh = (
                f"apr-iclr-cap-v2 将会场档限制在 {signal.venue_cap}，并压低已有工作定位与原创性。"
            )
            action_en = (
                "Compare those prior papers under the same metric, or show why they are not the closest neighbors."
            )
            action_zh = "在同一指标下比较这些已有论文，或说明它们为什么不是最近的相关工作。"
            category = "related work"
        else:
            title_en = "A baseline is privileged or solves a different task"
            title_zh = "某个基线带有特权信息或解决的是另一个任务"
            evidence_en = signal.reason_en
            evidence_zh = signal.reason_zh
            impact_en = (
                f"apr-iclr-cap-v2 caps the venue bin at {signal.venue_cap} "
                "and lowers experimental soundness."
            )
            impact_zh = f"apr-iclr-cap-v2 将会场档限制在 {signal.venue_cap}，并压低实验可靠性。"
            action_en = (
                "Remove privileged inputs and different-task baselines from the decision comparison."
            )
            action_zh = "从决定性比较中去掉特权输入和答非所问的基线。"
            category = "experimental soundness"
        issues.append(
            ReviewIssue(
                id=signal.id,
                severity=Severity.major,
                category=category,
                title=_bilingual(title_en, title_zh),
                location="Experiments / References",
                evidence=_bilingual(evidence_en, evidence_zh),
                impact=_bilingual(impact_en, impact_zh),
                required_action=_bilingual(action_en, action_zh),
                confidence=0.9,
                source_ids=[MANUSCRIPT_SOURCE_ID],
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                tags=["apr-iclr-cap-v2", signal.fatal_class],
                anchor=anchor,
                fatal_class=FatalClass(signal.fatal_class),
                decision_relevant=True,
                first_read=_cap_first_read(signal, anchor_excerpt, cited),
                metadata={
                    "source": "related_work_search",
                    "coefficient_set": COEFFICIENT_SET,
                    "venue_cap": signal.venue_cap,
                    "candidate_ids": [item.id for item in cited],
                    "candidate_external_ids": external_ids,
                    "candidate_providers": [item.provider for item in cited],
                    "manuscript_anchor": MANUSCRIPT_BASELINE_EVIDENCE_ID,
                    "clause_ids": dict(signal.clause_ids),
                },
            )
        )
    return issues


def related_work_comparison(judgment: RelatedWorkJudgment | dict[str, Any] | None) -> dict[str, Any] | None:
    """Baselines versus closest neighbours. Scoring reads this before a score is written."""
    if judgment is None:
        return None
    record = judgment if isinstance(judgment, RelatedWorkJudgment) else RelatedWorkJudgment.model_validate(judgment)
    missing_ids = {gap.candidate_id for gap in record.gaps if gap.kind == "missing_baseline"}
    neighbours = []
    for candidate in record.candidates:
        if candidate.tier == "optional" and not candidate.required:
            continue
        neighbours.append(
            {
                "id": candidate.id,
                "external_id": candidate.external_id,
                "date_relation": candidate.date_relation,
                "required": candidate.required,
                "tier": candidate.tier,
                "verified": True,
                "in_baselines": candidate.id not in missing_ids,
            }
        )
    return {
        "status": record.status,
        "baselines": {
            "missing": [gap.title for gap in record.gaps if gap.kind == "missing_baseline"],
            "privileged": [gap.title for gap in record.gaps if gap.kind == "privileged_baseline"],
            "different_task": [gap.title for gap in record.gaps if gap.kind == "different_task"],
        },
        "closest_neighbours": neighbours,
        "failed_providers": list(record.failed_providers),
        "confirmation": record.confirmation,
    }


def snap_down(value: float) -> int:
    """Largest ICLR bin that does not exceed ``value``. Same cuts as ``rating.snap_down``."""
    chosen = ICLR_BINS[0]
    for bin_value in ICLR_BINS:
        if float(bin_value) <= value:
            chosen = bin_value
    return int(chosen)


def preview_venue_bin(bands: dict[str, float], signals: Sequence[CapSignal]) -> int:
    """Preview the apr-iclr-cap-v2 bin. This does not write a score onto a package."""
    missing = [name for name in _WEIGHTS if name not in bands]
    if missing:
        raise ValueError(f"preview requires all seven dimension scores; missing {missing}")
    adjusted = {name: float(bands[name]) for name in _WEIGHTS}
    for signal in signals:
        for dimension, ceiling in signal.dimension_ceilings.items():
            adjusted[dimension] = min(adjusted[dimension], ceiling)
    weighted = sum(_WEIGHTS[name] * adjusted[name] for name in _WEIGHTS)
    gate = min(adjusted["claims_supported"], adjusted["experimental_soundness"])
    raw = min(weighted, gate + 1.0)
    caps = [signal.venue_cap for signal in signals]
    capped = min([raw, *caps]) if caps else raw
    return snap_down(capped)


def _suggested_text(
    *,
    formulation: ProblemFormulation,
    gaps: Sequence[BaselineGap],
    deadline: str,
    status: SearchStatus,
    metric: str | None,
) -> tuple[str, str, str, str]:
    if status == "not_run":
        return (
            "Prior-work and novelty scores are not supported yet: no external paper was verified.",
            "新颖性和已有工作定位还不能打分：没有任何外部论文被核实。",
            "Supply a findings file of provider payloads, or rerun when metadata APIs are reachable. Scores should wait for that search.",
            "请提供带有数据源原文的检索结果文件，或在元数据接口可用时重跑。打分应等到这次检索完成。",
        )
    missing = [gap.title for gap in gaps if gap.kind == "missing_baseline"]
    privileged = [gap.title for gap in gaps if gap.kind == "privileged_baseline"]
    different = [gap.title for gap in gaps if gap.kind == "different_task"]
    if not missing and not privileged and not different:
        return (
            "The required neighbors that were verified are in the comparison, and no listed baseline is given privileged information or a different task.",
            "已核实且必须比较的相关工作都在比较之中，列出的基线也没有特权信息或答非所问的任务。",
            "No related-work rebuttal is required for baselines. If a reviewer names another neighbor, check its date against the submission deadline before treating it as required.",
            "基线方面不需要相关工作的回应。若审稿人指出另一篇工作，先核对其日期是否晚于投稿截止日，再决定是否必须比较。",
        )
    bits_en: list[str] = []
    bits_zh: list[str] = []
    if missing:
        joined = "; ".join(missing)
        bits_en.append(
            f"The experiments omit {joined}, which are required baselines for {formulation.task}."
        )
        bits_zh.append(f"实验没有比较 {joined}，而这些是{formulation.task}必须比较的基线。")
    if privileged:
        joined = "; ".join(privileged)
        verb = "are" if len(privileged) > 1 else "is"
        bits_en.append(
            f"{joined} {verb} given privileged information, such as ground-truth scale, and cannot decide the claim."
        )
        bits_zh.append(f"{joined}获得了特权信息（例如真实尺度），不能用来决定该主张是否成立。")
    if different:
        joined = "; ".join(different)
        bits_en.append(f"{joined} solves a different task from {formulation.task}.")
        bits_zh.append(f"{joined}解决的任务与{formulation.task}不同。")
    metric_text = metric or formulation.setting
    weakness_en = " ".join(bits_en)
    weakness_zh = "".join(bits_zh)
    rebuttal_en = (
        f"We will add the missing must-compare methods under the same metric ({metric_text}) and the same split, "
        "without ground-truth scale or other oracle inputs. Baselines that solve a different task will be labeled "
        f"and removed from the decision table. Papers dated after {deadline} will be discussed as concurrent work and not treated as required."
    )
    rebuttal_zh = (
        f"我们将在同一指标（{metric_text}）和同一数据划分下补上缺失的必须比较方法，并且不使用真实尺度或其他先知输入。"
        "解决另一任务的基线会被标明，并移出决定性表格。"
        f"晚于 {deadline} 的论文将作为同期工作讨论，不视为必须比较。"
    )
    return weakness_en, weakness_zh, rebuttal_en, rebuttal_zh


def _log(provider: str, status: LogStatus, detail: str, query_id: str | None = None) -> RetrievalLogEntry:
    entry = RetrievalLogEntry(provider=provider, query_id=query_id, status=status, detail=detail)
    if status in {"offline", "rate_limited", "dropped", "http_error", "timeout"}:
        logger.warning(
            "related-work search: provider %s %s%s: %s",
            provider,
            status,
            f" query {query_id}" if query_id else "",
            detail,
        )
    else:
        logger.info("related-work search: provider %s %s", provider, status)
    return entry


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return " ".join(element.text.split())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_arxiv_atom(body: str, *, provider: str = "arxiv") -> list[RawHit]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    entries = [element for element in root.iter() if _local(element.tag) == "entry"]
    hits: list[RawHit] = []
    for entry in entries:
        title = ""
        summary = ""
        published = ""
        link = ""
        arxiv_id = ""
        venue = ""
        for child in list(entry):
            name = _local(child.tag)
            if name == "title":
                title = _text(child)
            elif name == "summary":
                summary = _text(child)
            elif name == "published":
                published = _text(child)
            elif name == "id":
                arxiv_id = norm_arxiv(_text(child))
            elif name == "journal_ref":
                venue = _text(child)
            elif name == "link" and not link:
                link = child.attrib.get("href", "")
        if not title or not arxiv_id:
            continue
        stamp = published[:10] if re.match(r"\d{4}-\d{2}-\d{2}", published) else None
        year = int(stamp[:4]) if stamp else None
        hits.append(
            RawHit(
                title=title,
                summary=summary,
                venue=venue or "arXiv",
                year=year,
                published=stamp,
                arxiv_id=arxiv_id,
                doi=None,
                openalex_id=None,
                s2_id=None,
                link=link or f"https://arxiv.org/abs/{arxiv_id}",
                provider=provider,
            )
        )
    return hits


def _abstract_from_inverted(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    slots: list[tuple[int, str]] = []
    for word, places in index.items():
        for place in places:
            slots.append((int(place), word))
    slots.sort()
    return " ".join(word for _, word in slots)


def _year_from(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.match(r"\d{4}", value):
        return int(value[:4])
    return None


def _published_from(value: Any) -> str | None:
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return None


def parse_openalex(payload: dict[str, Any]) -> list[RawHit]:
    works: list[dict[str, Any]]
    if isinstance(payload.get("results"), list):
        works = [item for item in payload["results"] if isinstance(item, dict)]
    elif payload.get("title") and (payload.get("id") or payload.get("doi")):
        works = [payload]
    else:
        return []
    hits: list[RawHit] = []
    for work in works:
        title = str(work.get("title") or "").strip()
        if not title:
            continue
        ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
        doi = work.get("doi") or ids.get("doi")
        arxiv = ids.get("arxiv")
        oa = str(work.get("id") or "")
        source = ((work.get("primary_location") or {}) if isinstance(work.get("primary_location"), dict) else {}).get("source")
        venue = None
        if isinstance(source, dict):
            venue = source.get("display_name")
        summary = work.get("abstract") or _abstract_from_inverted(work.get("abstract_inverted_index"))
        published = _published_from(work.get("publication_date"))
        year = _year_from(work.get("publication_year")) or (_year_from(published) if published else None)
        doi_norm = norm_doi(str(doi)) if doi else None
        arxiv_norm = norm_arxiv(str(arxiv)) if arxiv else None
        link = ""
        if arxiv_norm:
            link = f"https://arxiv.org/abs/{arxiv_norm}"
        elif doi_norm:
            link = f"https://doi.org/{doi_norm}"
        elif oa:
            link = oa if oa.startswith("http") else f"https://openalex.org/{oa}"
        if not link:
            continue
        hits.append(
            RawHit(
                title=title,
                summary=str(summary or ""),
                venue=str(venue) if venue else None,
                year=year,
                published=published,
                arxiv_id=arxiv_norm,
                doi=doi_norm,
                openalex_id=oa or None,
                s2_id=None,
                link=link,
                provider="openalex",
            )
        )
    return hits


def parse_semantic_scholar(payload: dict[str, Any]) -> list[RawHit]:
    papers: list[dict[str, Any]]
    if isinstance(payload.get("data"), list):
        papers = [item for item in payload["data"] if isinstance(item, dict)]
    elif payload.get("paperId") and payload.get("title"):
        papers = [payload]
    else:
        return []
    hits: list[RawHit] = []
    for paper in papers:
        title = str(paper.get("title") or "").strip()
        paper_id = str(paper.get("paperId") or "").strip()
        if not title or not paper_id:
            continue
        external = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
        arxiv = external.get("ArXiv") or external.get("arxiv")
        doi = external.get("DOI") or external.get("doi")
        arxiv_norm = norm_arxiv(str(arxiv)) if arxiv else None
        doi_norm = norm_doi(str(doi)) if doi else None
        published = _published_from(paper.get("publicationDate"))
        year = _year_from(paper.get("year")) or (_year_from(published) if published else None)
        link = ""
        if arxiv_norm:
            link = f"https://arxiv.org/abs/{arxiv_norm}"
        elif doi_norm:
            link = f"https://doi.org/{doi_norm}"
        else:
            link = str(paper.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}")
        hits.append(
            RawHit(
                title=title,
                summary=str(paper.get("abstract") or ""),
                venue=str(paper.get("venue") or "") or None,
                year=year,
                published=published,
                arxiv_id=arxiv_norm,
                doi=doi_norm,
                openalex_id=None,
                s2_id=paper_id,
                link=link,
                provider="semantic_scholar",
            )
        )
    return hits


def parse_crossref(payload: dict[str, Any]) -> list[RawHit]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    if not isinstance(message, dict) or "DOI" not in message and "title" not in message:
        return []
    if "DOI" not in message:
        return []
    title_field = message.get("title")
    title = title_field[0] if isinstance(title_field, list) and title_field else str(title_field or "")
    title = title.strip()
    if not title:
        return []
    container = message.get("container-title")
    venue = container[0] if isinstance(container, list) and container else None
    parts = ((message.get("issued") or {}) if isinstance(message.get("issued"), dict) else {}).get("date-parts")
    published = None
    year = None
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        nums = parts[0]
        year = int(nums[0])
        if len(nums) >= 3:
            published = f"{int(nums[0]):04d}-{int(nums[1]):02d}-{int(nums[2]):02d}"
    abstract = re.sub(r"<[^>]+>", " ", str(message.get("abstract") or ""))
    abstract = " ".join(abstract.split())
    doi = norm_doi(str(message["DOI"]))
    return [
        RawHit(
            title=title,
            summary=abstract,
            venue=str(venue) if venue else None,
            year=year,
            published=published,
            arxiv_id=None,
            doi=doi,
            openalex_id=None,
            s2_id=None,
            link=f"https://doi.org/{doi}",
            provider="crossref",
        )
    ]


def parse_provider_payload(body: str) -> list[RawHit]:
    stripped = body.strip()
    if not stripped:
        return []
    if stripped.startswith("<"):
        return parse_arxiv_atom(stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    if "message" in payload and isinstance(payload.get("message"), dict):
        return parse_crossref(payload)
    if "paperId" in payload or "data" in payload:
        hits = parse_semantic_scholar(payload)
        if hits:
            return hits
    if "results" in payload or str(payload.get("id", "")).startswith("https://openalex.org/"):
        return parse_openalex(payload)
    if payload.get("title") and payload.get("doi"):
        return parse_openalex(payload)
    return []


def _ids_match(declared: dict[str, Any], hit: RawHit) -> bool:
    checks = (
        ("arxiv_id", hit.arxiv_id, norm_arxiv),
        ("doi", hit.doi, norm_doi),
        ("openalex_id", hit.openalex_id, lambda value: value.rsplit("/", 1)[-1]),
        ("s2_id", hit.s2_id, lambda value: value.strip()),
    )
    saw = False
    for key, parsed, normalize in checks:
        raw = declared.get(key)
        if not raw:
            continue
        saw = True
        if not parsed or normalize(str(raw)) != normalize(str(parsed)):
            return False
    return saw


def load_agent_findings(path: Path) -> tuple[list[RawHit], list[RetrievalLogEntry]]:
    """Accept host-supplied provider payloads. Titles that do not parse are dropped."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("agent findings drop must be a JSON object")
    candidates = raw.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("agent findings drop requires candidates to be a list")
    hits: list[RawHit] = []
    logs: list[RetrievalLogEntry] = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            logs.append(_log("agent", "dropped", f"candidate {index} is not an object"))
            continue
        payload = item.get("provider_payload")
        title = str(item.get("title") or "").strip()
        label = title or f"candidate {index}"
        if not isinstance(payload, str) or not payload.strip():
            logs.append(_log("agent", "dropped", f"{label} has no provider payload; id was not resolved"))
            continue
        parsed = parse_provider_payload(payload)
        matched = next((hit for hit in parsed if _ids_match(item, hit)), None)
        if matched is None:
            logs.append(_log("agent", "dropped", f"{label} id did not resolve in the provider payload"))
            continue
        if title and _normalize_title(title) != _normalize_title(matched.title):
            logs.append(
                _log(
                    "agent",
                    "dropped",
                    f"{label} title does not match the resolved record {matched.title!r}",
                )
            )
            continue
        query_ids = item.get("query_ids") or []
        if not isinstance(query_ids, list) or not all(isinstance(entry, str) for entry in query_ids):
            logs.append(_log("agent", "dropped", f"{matched.title} has invalid query_ids"))
            continue
        matched.query_ids = list(query_ids)
        hits.append(matched)
        logs.append(_log("agent", "ok", f"resolved {matched.external_id()}"))
    return hits, logs


def provider_request_headers(url: str) -> dict[str, str]:
    """Headers for one metadata request. The Semantic Scholar key stays out of the URL and the logs."""
    headers = {"User-Agent": "AutoPaperReview/related-work (metadata)"}
    host = urllib.parse.urlparse(url).netloc
    if host == "api.semanticscholar.org":
        key = os.environ.get("S2_API_KEY", "").strip()
        if key:
            headers["x-api-key"] = key
    return headers


def urllib_transport(url: str, timeout: float = 20.0) -> HttpResult:
    request = urllib.request.Request(url, headers=provider_request_headers(url), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            return HttpResult(
                status=getattr(response, "status", 200),
                body=response.read(),
                headers=response_headers,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        response_headers = {key.lower(): value for key, value in (exc.headers.items() if exc.headers else [])}
        return HttpResult(status=exc.code, body=body, headers=response_headers)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise RetrievalFailure("timeout", str(reason)) from exc
        raise RetrievalFailure("offline", str(reason)) from exc


def _s2_authenticated() -> bool:
    return bool(os.environ.get("S2_API_KEY", "").strip())


def _sanitize_openalex_value(value: str) -> str:
    """Drop commas, colons, quotes, and other characters that break OpenAlex filters."""
    cleaned = re.sub(r"[^\w\s.-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip(" .-")


def _search_url(provider: str, query: str, limit: int, *, lookup: str = "") -> str:
    if provider == "arxiv":
        if lookup == "arxiv_id_list":
            ids = [part.strip() for part in query.split(",") if part.strip()]
            joined = ",".join(urllib.parse.quote(part, safe="") for part in ids)
            return (
                "https://export.arxiv.org/api/query"
                f"?id_list={joined}&start=0&max_results={max(len(ids), 1)}"
            )
        if lookup == "arxiv_id":
            ident = urllib.parse.quote(query, safe="")
            return (
                "https://export.arxiv.org/api/query"
                f"?search_query=id:{ident}&start=0&max_results={limit}"
            )
        if lookup == "title":
            encoded = urllib.parse.quote(f'"{query}"', safe="")
            return (
                "https://export.arxiv.org/api/query"
                f"?search_query=ti:{encoded}&start=0&max_results={limit}"
            )
        quoted = urllib.parse.quote(query)
        return (
            "https://export.arxiv.org/api/query"
            f"?search_query=all:{quoted}&start=0&max_results={limit}"
        )
    if provider == "openalex":
        if lookup == "doi":
            quoted = urllib.parse.quote(query, safe="")
            return f"https://api.openalex.org/works?filter=doi:{quoted}&per-page={limit}"
        if lookup == "title":
            sanitized = _sanitize_openalex_value(query) or "untitled"
            quoted = urllib.parse.quote(sanitized, safe="")
            return f"https://api.openalex.org/works?filter=title.search:{quoted}&per-page={limit}"
        quoted = urllib.parse.quote(query)
        return f"https://api.openalex.org/works?search={quoted}&per-page={limit}"
    if provider == "semantic_scholar":
        quoted = urllib.parse.quote(query)
        fields = "title,year,venue,externalIds,abstract,url,publicationDate"
        return (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={quoted}&limit={limit}&fields={fields}"
        )
    raise KeyError(provider)


def _query_text_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    values = urllib.parse.parse_qs(parsed.query)
    if "filter" in values and values["filter"]:
        text = urllib.parse.unquote(values["filter"][0])
        text = re.sub(r"^(?:title\.search|doi):", "", text)
        return text.strip().strip('"')
    for key in ("search_query", "search", "query"):
        if key in values and values[key]:
            text = urllib.parse.unquote(values[key][0])
            text = re.sub(r"^(?:all|ti|id):", "", text)
            return text.strip().strip('"')
    if "id_list" in values and values["id_list"]:
        return urllib.parse.unquote(values["id_list"][0]).strip()
    return ""


def clear_retrieval_cache() -> None:
    """Drop cached provider metadata. Tests use this to isolate transports."""
    _RETRIEVAL_CACHE.clear()


def _query_cache_key(provider: str, query: str, *, lookup: str = "") -> str:
    # Record that a key was present without storing the key itself.
    # ``lookup`` keeps a name search distinct from a title, id, or DOI search.
    auth = "\nauth" if provider == "semantic_scholar" and _s2_authenticated() else ""
    return hashlib.sha256(f"{provider}\n{lookup}\n{query}{auth}".encode("utf-8")).hexdigest()


def _cache_file(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _load_disk_cache(cache_dir: Path | None, key: str, now: float) -> HttpResult | None:
    """Read one cached HTTP 200 or 400. Expired and unreadable files are misses."""
    if cache_dir is None:
        return None
    path = _cache_file(cache_dir, key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        stored_at = float(payload["stored_at"])
        status = int(payload["status"])
        body = base64.b64decode(payload["body"])
    except (KeyError, TypeError, ValueError):
        return None
    if status not in _CACHEABLE_STATUSES or now - stored_at > RETRIEVAL_CACHE_TTL_SECONDS:
        return None
    headers = payload.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    return HttpResult(status, body, {str(name): str(value) for name, value in headers.items()})


def _store_disk_cache(cache_dir: Path | None, key: str, result: HttpResult, now: float) -> None:
    """Persist one HTTP 200 or a negative 400. The file name is the query hash. The API key is not written."""
    if cache_dir is None or result.status not in _CACHEABLE_STATUSES:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_file(cache_dir, key)
    payload = {
        "stored_at": now,
        "status": result.status,
        "body": base64.b64encode(result.body).decode("ascii"),
        "headers": result.headers,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _retry_after_seconds(result: HttpResult, *, cap: float) -> float:
    raw = result.headers.get("retry-after", "").strip()
    if not raw:
        return 0.0
    try:
        return min(max(float(raw), 0.0), cap)
    except ValueError:
        return 0.0


def _rate_limit_delay(provider: str, attempt: int, result: HttpResult, rng: random.Random) -> float:
    """Semantic Scholar waits with exponential backoff and jitter, and honours Retry-After."""
    if provider == "semantic_scholar":
        exponential = float(min(2**attempt, 32))
        jitter = rng.uniform(0, exponential * 0.25)
        retry_after = _retry_after_seconds(result, cap=_S2_MAX_RETRY_AFTER_SECONDS)
        return min(max(exponential + jitter, retry_after), _S2_MAX_RETRY_AFTER_SECONDS)
    return _retry_after_seconds(result, cap=_MAX_RETRY_AFTER_SECONDS)


def _pace_semantic_scholar(
    last: list[float | None],
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    now = clock()
    if last[0] is not None:
        wait = _S2_MIN_INTERVAL - (now - last[0])
        if wait > 0:
            sleeper(wait)
    last[0] = clock()


def guarded_get(
    url: str,
    *,
    manuscript: str,
    allow: set[str],
    transport: Transport,
    named: set[str] | None = None,
) -> HttpResult:
    query = _query_text_from_url(url)
    assert_outbound_safe(query, manuscript, allow, named=named)
    return transport(url)


def _fetch_provider(
    provider: str,
    query: RelatedWorkQuery,
    *,
    manuscript: str,
    allow: set[str],
    named: set[str],
    transport: Transport,
    limit: int,
    sleeper: Callable[[float], None],
    rng: random.Random,
    clock: Callable[[], float],
    s2_last: list[float | None],
    cache_dir: Path | None = None,
    cache_clock: Callable[[], float] | None = None,
    budget_sleeper: Callable[[float], bool] | None = None,
) -> tuple[HttpResult | None, bool, RetrievalLogEntry | None]:
    """Fetch one query. HTTP 429 retries and does not abort the provider."""
    lookup = str(query.metadata.get("lookup") or "")
    key = _query_cache_key(provider, query.query, lookup=lookup)
    cached = _RETRIEVAL_CACHE.get(key)
    if cached is not None:
        return cached, True, None
    stamp = cache_clock or time.time
    disk = _load_disk_cache(cache_dir, key, stamp())
    if disk is not None:
        _RETRIEVAL_CACHE[key] = disk
        return disk, True, None
    url = _search_url(provider, query.query, limit, lookup=lookup)
    result: HttpResult | None = None
    if provider == "semantic_scholar" and _s2_authenticated():
        retries = _S2_429_RETRIES
    elif provider == "semantic_scholar":
        retries = S2_UNAUTH_RETRIES
    else:
        retries = _MAX_429_RETRIES
    for attempt in range(retries + 1):
        if provider == "semantic_scholar":
            _pace_semantic_scholar(s2_last, sleeper, clock)
        try:
            result = guarded_get(url, manuscript=manuscript, allow=allow, transport=transport, named=named)
        except RetrievalFailure as exc:
            return None, False, _log(provider, exc.status, str(exc), query.id)
        except TimeoutError as exc:
            return None, False, _log(provider, "timeout", str(exc), query.id)
        except RuntimeError as exc:
            return None, False, _log(provider, "dropped", str(exc), query.id)
        if result.status != 429:
            break
        if attempt == retries:
            break
        delay = _rate_limit_delay(provider, attempt, result, rng)
        if delay <= 0:
            continue
        if budget_sleeper is None:
            sleeper(delay)
        elif not budget_sleeper(delay):
            break
    if result is not None and result.status in _CACHEABLE_STATUSES:
        _RETRIEVAL_CACHE[key] = result
        _store_disk_cache(cache_dir, key, result, stamp())
    return result, False, None


def live_search(
    queries: Sequence[RelatedWorkQuery],
    *,
    manuscript: str,
    allow: set[str],
    transport: Transport,
    providers: Sequence[str],
    limit: int,
    named: set[str] | None = None,
    sleeper: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
    clock: Callable[[], float] | None = None,
    cache_dir: Path | None = None,
    cache_clock: Callable[[], float] | None = None,
) -> tuple[list[RawHit], list[RetrievalLogEntry]]:
    hits: list[RawHit] = []
    logs: list[RetrievalLogEntry] = []
    blocked: set[str] = set()
    named_queries = named or set()
    pause = sleeper or time.sleep
    entropy = rng or random.Random()
    now = clock or time.monotonic
    s2_last: list[float | None] = [None]
    for provider in providers:
        spent = [0.0]

        def budget_sleeper(seconds: float, _spent: list[float] = spent) -> bool:
            room = S2_UNAUTH_BUDGET_SECONDS - _spent[0]
            if room <= 0:
                return False
            take = min(seconds, room)
            if take > 0:
                pause(take)
            _spent[0] += take
            return take == seconds

        unauth_s2 = provider == "semantic_scholar" and not _s2_authenticated()
        for query in queries:
            targets = query.metadata.get("providers")
            if isinstance(targets, list) and targets and provider not in targets:
                continue
            if provider in blocked:
                logs.append(_log(provider, "skipped", "provider already unavailable", query.id))
                continue
            result, from_cache, failure = _fetch_provider(
                provider,
                query,
                manuscript=manuscript,
                allow=allow,
                named=named_queries,
                transport=transport,
                limit=limit,
                sleeper=pause,
                rng=entropy,
                clock=now,
                s2_last=s2_last,
                cache_dir=cache_dir,
                cache_clock=cache_clock,
                budget_sleeper=budget_sleeper if unauth_s2 else None,
            )
            if failure is not None:
                logs.append(failure)
                if failure.status in _DEGRADED_LOGS or failure.status == "dropped":
                    blocked.add(provider)
                continue
            assert result is not None
            if result.status == 429:
                if unauth_s2:
                    logs.append(
                        _log(
                            provider,
                            "skipped",
                            f"HTTP 429 without S2_API_KEY after {S2_UNAUTH_RETRIES} retries; skipping this provider",
                            query.id,
                        )
                    )
                    blocked.add(provider)
                else:
                    retries = _S2_429_RETRIES if provider == "semantic_scholar" else _MAX_429_RETRIES
                    logs.append(
                        _log(
                            provider,
                            "rate_limited",
                            f"HTTP 429 after {retries} retries; continuing with later queries",
                            query.id,
                        )
                    )
                continue
            if result.status == 503:
                logs.append(_log(provider, "rate_limited", "HTTP 503; stopping this provider", query.id))
                blocked.add(provider)
                continue
            if result.status != 200:
                logs.append(_log(provider, "http_error", f"HTTP {result.status}", query.id))
                continue
            body = result.body.decode("utf-8", errors="replace")
            if provider == "arxiv":
                parsed = parse_arxiv_atom(body)
            else:
                parsed = parse_provider_payload(body)
            parsed.sort(key=lambda hit: (_normalize_title(hit.title), hit.external_id()))
            for hit in parsed:
                hit.query_ids = [query.id]
                hit.provider = provider
                hits.append(hit)
            detail = f"{len(parsed)} records"
            if from_cache:
                detail += " (cached)"
            logs.append(_log(provider, "ok", detail, query.id))
    return hits, logs


def _id_values(hit: RawHit) -> set[tuple[str, str]]:
    values: set[tuple[str, str]] = set()
    if hit.arxiv_id:
        values.add(("arxiv", norm_arxiv(hit.arxiv_id)))
    if hit.doi:
        values.add(("doi", norm_doi(hit.doi)))
    if hit.openalex_id:
        values.add(("openalex", hit.openalex_id.rsplit("/", 1)[-1]))
    if hit.s2_id:
        values.add(("s2", hit.s2_id.strip()))
    return values


def _dedupe_title(title: str) -> str:
    """Lowercase and drop punctuation, hyphens, and whitespace."""
    folded = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^a-z0-9]+", "", folded)


def _same_work(left: RawHit, right: RawHit) -> bool:
    """One paper can arrive with a DOI from one provider and an arXiv id from another."""
    if _id_values(left) & _id_values(right):
        return True
    left_title = _dedupe_title(left.title)
    right_title = _dedupe_title(right.title)
    if not left_title or left_title != right_title:
        return False
    if left.year is None or right.year is None:
        return False
    return abs(left.year - right.year) <= 1


def _merge_hit(kept: RawHit, extra: RawHit) -> None:
    for query_id in extra.query_ids:
        if query_id not in kept.query_ids:
            kept.query_ids.append(query_id)
    if not kept.arxiv_id and extra.arxiv_id:
        kept.arxiv_id = extra.arxiv_id
    if not kept.doi and extra.doi:
        kept.doi = extra.doi
    if not kept.openalex_id and extra.openalex_id:
        kept.openalex_id = extra.openalex_id
    if not kept.s2_id and extra.s2_id:
        kept.s2_id = extra.s2_id
    if len(extra.summary or "") > len(kept.summary or ""):
        kept.summary = extra.summary
    if not kept.venue and extra.venue:
        kept.venue = extra.venue
    if kept.year is None and extra.year is not None:
        kept.year = extra.year
    if not kept.published and extra.published:
        kept.published = extra.published
    if kept.doi and "doi.org" not in kept.link and extra.doi:
        kept.link = extra.link
    elif not kept.link and extra.link:
        kept.link = extra.link


def _dedupe(hits: Sequence[RawHit]) -> list[RawHit]:
    groups: list[RawHit] = []
    for hit in hits:
        if not hit.title.strip() or not hit.link or not hit.external_id():
            continue
        match = next((existing for existing in groups if _same_work(existing, hit)), None)
        if match is None:
            groups.append(hit)
            continue
        _merge_hit(match, hit)
    groups.sort(
        key=lambda hit: (
            _normalize_title(hit.title),
            hit.year if hit.year is not None else 0,
            hit.external_id(),
        )
    )
    return groups


_TIER_RANK = {"must-compare": 0, "should-discuss": 1, "optional": 2}
_DEGRADED_LOGS = {"offline", "rate_limited", "http_error", "timeout"}


def _status_from(
    mode: str,
    logs: Sequence[RetrievalLogEntry],
    searched: bool,
    *,
    providers_available: bool = True,
) -> SearchStatus:
    """Complete, partial, degraded, or not run.

    Partial means at least one provider logged success and at least one logged
    a failure. Every provider failing, or no provider being available, stays
    degraded and must not produce a cap.
    """
    if not searched:
        return "not_run"
    if not providers_available:
        return "degraded"
    successful, failed = _provider_sets(logs)
    if failed and not successful:
        return "degraded"
    if failed and successful:
        return "partial"
    if any(entry.status in _DEGRADED_LOGS for entry in logs):
        return "degraded"
    return "complete" if mode in {"agent", "live"} else "not_run"


def load_confirmations(path: Path) -> list[MustCompareConfirmation]:
    """Read judging-agent confirmations from an agent findings drop."""
    supplied, items = confirmation_file_state(path)
    if not supplied:
        return []
    return items


def confirmation_file_state(path: Path) -> tuple[bool, list[MustCompareConfirmation]]:
    """Return whether the file supplies a confirmation list, and that list."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "confirmations" not in raw:
        return False, []
    items = raw["confirmations"]
    if not isinstance(items, list):
        raise ValueError("confirmations must be a list")
    confirmations: list[MustCompareConfirmation] = []
    for item in items:
        if isinstance(item, dict):
            confirmations.append(MustCompareConfirmation.model_validate(item))
    return True, confirmations


def build_confirmation_packet(
    candidates: Sequence[RelatedCandidate],
    experiments: ExperimentRecord,
) -> dict[str, Any]:
    """Provisional must-compare candidates plus the manuscript baselines, for the judging agent."""
    rows = [
        {
            "id": item.id,
            "title": item.title,
            "external_id": item.external_id,
            "year": item.year,
            "venue": item.venue,
            "link": item.link,
            "summary": item.summary,
            "relevance": item.relevance,
            "date_relation": item.date_relation,
            "cited": item.cited,
            "provider": item.provider,
        }
        for item in candidates
        if item.required and item.tier == "must-compare"
    ]
    return {
        "candidates": rows,
        "baselines": {
            "decision_metric": experiments.decision_metric,
            "compared": list(experiments.compared),
            "privileged": list(experiments.privileged),
            "different_task": list(experiments.different_task),
        },
    }


def parse_confirmation_payload(payload: Any) -> list[MustCompareConfirmation]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("confirmations", [])
    else:
        raise ValueError("confirmation adapter must return a list or an object with confirmations")
    if not isinstance(items, list):
        raise ValueError("confirmations must be a list")
    return [MustCompareConfirmation.model_validate(item) for item in items if isinstance(item, dict)]


def _confirm_with_model_session(
    session_cls: type,
    packet: dict[str, Any],
    *,
    scratch: Path,
    params: dict[str, Any],
    network_policy: str,
) -> list[MustCompareConfirmation]:
    """Ask the OpenAI-compatible adapter from the model-execution stage, when that module is installed."""
    base_url = str(params.get("base_url") or "").strip()
    model = str(params.get("model") or "").strip()
    if not base_url or not model:
        raise ValueError("OpenAI-compatible confirmation requires base_url and model")
    session = session_cls(
        base_url=base_url,
        model=model,
        api_key_env=str(params.get("api_key_env") or "APR_MODEL_API_KEY"),
        allow_remote=bool(params.get("allow_remote")),
        network_policy=str(params.get("network_policy") or network_policy),
        timeout_seconds=float(params.get("timeout_seconds") or 60),
        temperature=float(params.get("temperature") or 0),
        json_object=True,
        scratch=scratch,
        stage_signature=str(params.get("stage_signature") or "related-work-confirmation"),
    )
    prompt = (
        "Confirm which candidates are must-compare for this manuscript. "
        "Return one JSON object and no other text, shaped as "
        '{"confirmations":[{"external_id":"","title":"","confirmed":true,"reason":"one line"}]}. '
        "confirmed is false when the paper is a survey, a different task, or only shares an input modality. "
        "The reason is one line.\n\n"
        + json.dumps(packet, ensure_ascii=False)
    )
    messages = [
        {"role": "system", "content": "Return one JSON object and no other text."},
        {"role": "user", "content": prompt},
    ]
    text = session._exchange("related_work_confirmation", messages, attempt=1)
    parsed = json.loads(text)
    if hasattr(session, "write_index"):
        session.write_index()
    return parse_confirmation_payload(parsed)


def invoke_confirmation_adapter(
    spec: str,
    packet: dict[str, Any],
    *,
    scratch: Path,
    params: dict[str, Any] | None = None,
    network_policy: str = "deny",
) -> list[MustCompareConfirmation]:
    """Call a judging adapter. A ``confirm_related_work`` method wins; otherwise use the model session."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not attribute:
        raise ValueError("confirmation adapter must be a module:attribute reference")
    module = importlib.import_module(module_name)
    obj = getattr(module, attribute)
    instance = obj() if isinstance(obj, type) else obj
    if hasattr(instance, "confirm_related_work"):
        return parse_confirmation_payload(instance.confirm_related_work(packet))
    session_cls = getattr(module, "ModelSession", None)
    if session_cls is not None and hasattr(session_cls, "_exchange"):
        return _confirm_with_model_session(
            session_cls,
            packet,
            scratch=scratch,
            params=params or {},
            network_policy=network_policy,
        )
    raise TypeError(
        f"confirmation adapter {spec} must provide confirm_related_work() "
        "or be the OpenAI-compatible model adapter"
    )


def _mark_awaiting(candidates: list[RelatedCandidate]) -> None:
    for item in candidates:
        if not item.required or item.tier != "must-compare":
            continue
        item.reason_en += " Awaiting judging-agent confirmation. The retrieval cap cannot cite it yet."
        item.reason_zh += "等待判断模型确认。检索上限还不能引用它。"


def run_related_work_search(
    manuscript: str,
    *,
    venue_key: str = "iclr2027",
    mode: str = "agent",
    findings_path: Path | None = None,
    transport: Transport | None = None,
    allow_network: bool = False,
    max_results: int = 5,
    providers: Sequence[str] | None = None,
    deadlines: VenueDeadlines | None = None,
    confirm_must_compare: MustCompareHook | None = None,
    sleeper: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
    clock: Callable[[], float] | None = None,
    named_method_limit: int = DEFAULT_NAMED_METHOD_LIMIT,
    confirmation_path: Path | None = None,
    confirmation_adapter: str | None = None,
    confirmation_adapter_params: dict[str, Any] | None = None,
    confirmation_scratch: Path | None = None,
    network_policy: str = "deny",
    cache_dir: Path | None = None,
    cache_clock: Callable[[], float] | None = None,
    bibliography_lookup_limit: int = BIBLIOGRAPHY_LOOKUP_LIMIT,
) -> RelatedWorkJudgment:
    if mode not in {"agent", "live", "off"}:
        raise ValueError("mode must be agent, live, or off")
    profile = deadlines or load_venue_deadlines(venue_key)
    formulation = derive_formulation(manuscript)
    entries = parse_bibliography(manuscript)
    method_names = (
        named_methods_from_manuscript(manuscript, limit=named_method_limit) if formulation.complete else []
    )
    queries = generate_search_queries(formulation, method_names)
    if formulation.complete:
        queries.extend(cited_reference_queries(manuscript, entries, limit=bibliography_lookup_limit))
    allow = allow_tokens(formulation)
    named = {
        _normalize_title(query.query)
        for query in queries
        if query.metadata.get("family") in {"named_method", "cited_reference"}
    }
    for query in queries:
        assert_outbound_safe(query.query, manuscript, allow, named=named)
    experiments = parse_experiments(manuscript)
    logs: list[RetrievalLogEntry] = []
    hits: list[RawHit] = []
    searched = False
    providers_available = True
    chosen = list(("arxiv", "openalex", "semantic_scholar") if providers is None else providers)
    unknown = [name for name in chosen if name not in {"arxiv", "openalex", "semantic_scholar"}]
    if unknown:
        raise ValueError(f"unknown related-work providers: {unknown}")

    if mode == "off" or not formulation.complete:
        if not formulation.complete:
            logs.append(
                _log(
                    "local",
                    "unconfigured",
                    "problem formulation is incomplete; refusing to query external services",
                )
            )
        else:
            logs.append(_log("local", "unconfigured", "retrieval mode is off; queries were generated locally"))
    elif mode == "agent":
        if findings_path is None:
            logs.append(
                _log(
                    "agent",
                    "unconfigured",
                    "agent findings file was not provided; no candidates were retrieved",
                )
            )
        else:
            searched = True
            found, agent_logs = load_agent_findings(findings_path)
            hits.extend(found)
            logs.extend(agent_logs)
    elif mode == "live":
        if not allow_network:
            raise PermissionError("live related-work search requires metadata network access")
        searched = True
        if not chosen:
            providers_available = False
            logs.append(_log("local", "unconfigured", "zero providers available"))
        else:
            caller = transport or urllib_transport
            found, live_logs = live_search(
                queries,
                manuscript=manuscript,
                allow=allow,
                transport=caller,
                providers=chosen,
                limit=max_results,
                named=named,
                sleeper=sleeper,
                rng=rng,
                clock=clock,
                cache_dir=cache_dir,
                cache_clock=cache_clock,
            )
            hits.extend(found)
            logs.extend(live_logs)

    known_query_ids = {query.id for query in queries}
    deduped = _dedupe(hits)
    for hit in deduped:
        unknown_ids = [query_id for query_id in hit.query_ids if query_id not in known_query_ids]
        if unknown_ids:
            logs.append(_log("local", "dropped", f"ignored unknown query ids {unknown_ids} on {hit.title}"))
        hit.query_ids = [query_id for query_id in hit.query_ids if query_id in known_query_ids]

    ranked = sorted(
        deduped,
        key=lambda hit: (
            _TIER_RANK[_base_tier(hit, formulation)[0]],
            -relevance_score(hit, formulation),
            _normalize_title(hit.title),
            hit.external_id(),
        ),
    )
    candidates = [
        build_candidate(
            hit,
            formulation=formulation,
            entries=entries,
            deadline=profile.submission_deadline,
            index=index,
        )
        for index, hit in enumerate(ranked, start=1)
    ]
    packet = build_confirmation_packet(candidates, experiments)
    confirmations: list[MustCompareConfirmation] = []
    supplied = confirm_must_compare is not None
    for path in (confirmation_path, findings_path):
        if path is None or not path.is_file():
            continue
        file_supplied, file_items = confirmation_file_state(path)
        if file_supplied:
            supplied = True
            confirmations.extend(file_items)
    if confirmation_adapter and packet["candidates"] and not supplied:
        scratch = confirmation_scratch or Path(".")
        confirmations.extend(
            invoke_confirmation_adapter(
                confirmation_adapter,
                packet,
                scratch=scratch,
                params=confirmation_adapter_params,
                network_policy=network_policy,
            )
        )
        supplied = True
    if supplied:
        candidates = _apply_confirmations(candidates, confirmations, confirm_must_compare)
        candidates = _limit_must_compare(candidates)
        confirmation: ConfirmationStatus = "confirmed"
    elif packet["candidates"]:
        _mark_awaiting(candidates)
        confirmation = "awaiting_confirmation"
    else:
        confirmation = "not_required"
    status = _status_from(
        mode,
        logs,
        searched and formulation.complete,
        providers_available=providers_available,
    )
    _successful, failed_providers = _provider_sets(logs)
    gaps = compare_experiments(candidates, experiments) if formulation.complete else []
    if status == "not_run" or confirmation == "awaiting_confirmation":
        gaps = [gap for gap in gaps if gap.kind != "missing_baseline"]
    advice_candidates = candidates
    if confirmation == "awaiting_confirmation":
        advice_candidates = [
            item.model_copy(update={"required": False}) if item.required and not item.confirmed else item
            for item in candidates
        ]
    advice = build_clause_advice(gaps=gaps, candidates=advice_candidates, status=status)
    signals = build_cap_signals(gaps=gaps, status=status, candidates=candidates, logs=logs)
    effective = min((signal.venue_cap for signal in signals), default=None)
    weakness_en, weakness_zh, rebuttal_en, rebuttal_zh = _suggested_text(
        formulation=formulation,
        gaps=gaps,
        deadline=profile.submission_deadline.isoformat(),
        status=status,
        metric=experiments.decision_metric,
    )
    return RelatedWorkJudgment(
        formulation=formulation,
        queries=queries,
        candidates=candidates,
        gaps=gaps,
        clause_advice=advice,
        cap_signals=signals,
        effective_venue_cap=effective,
        logs=logs,
        weakness_en=weakness_en,
        weakness_zh=weakness_zh,
        rebuttal_en=rebuttal_en,
        rebuttal_zh=rebuttal_zh,
        venue_profile=profile.profile_key,
        submission_deadline=profile.submission_deadline.isoformat(),
        decision_metric=experiments.decision_metric,
        status=status,
        failed_providers=failed_providers,
        confirmation=confirmation,
        confirmation_packet=packet,
    )


def _candidate_sentence(item: RelatedCandidate, *, zh: bool) -> str:
    venue = item.venue or ("未注明出处" if zh else "venue not stated")
    year = item.year if item.year is not None else ("年份未知" if zh else "year unknown")
    cited = "已引用" if item.cited and zh else "cited" if item.cited else "未引用" if zh else "not cited"
    required = "必须" if item.required and zh else "required" if item.required else "不必须" if zh else "not required"
    reason = item.reason_zh if zh else item.reason_en
    return (
        f"{item.tier}; {required}; {item.date_relation}; {cited}. "
        f"{item.title} ({venue}, {year}). {item.external_id}. {item.link}. {item.summary} {reason}"
    )


def render_related_work_section(judgment: RelatedWorkJudgment | dict[str, Any], languages: Sequence[str]) -> list[str]:
    record = judgment if isinstance(judgment, RelatedWorkJudgment) else RelatedWorkJudgment.model_validate(judgment)
    bilingual = len(languages) > 1
    if languages == ["zh-Hans"]:
        heading = "## 相关工作"
    elif bilingual:
        heading = "## Related Work / 相关工作"
    else:
        heading = "## Related Work"
    lines = [heading, ""]
    form = record.formulation
    assumptions_en = "; ".join(form.assumptions) if form.assumptions else "none stated"
    assumptions_zh = "；".join(form.assumptions) if form.assumptions else "未写明"
    contributions = form.contributions or ["No numbered contributions were listed."]
    contributions_zh = form.contributions or ["未列出编号贡献。"]
    problem_en = (
        f"Task: {form.task}. Inputs: {form.inputs}. Outputs: {form.outputs}. "
        f"Assumptions: {assumptions_en}. Setting: {form.setting}."
    )
    problem_zh = (
        f"任务：{form.task}。输入：{form.inputs}。输出：{form.outputs}。"
        f"假设：{assumptions_zh}。设定：{form.setting}。"
    )
    search_en = (
        "Keyword queries use generic topic tokens. Bibliography lookups send a reference title, "
        "arXiv id, or DOI only. Manuscript prose was not sent to an external service. "
        f"Submission deadline: {record.submission_deadline} ({record.venue_profile}). Status: {record.status}."
    )
    search_zh = (
        "关键词检索只用通用主题词。参考文献检索只发送题名、arXiv 编号或 DOI。稿件正文没有发给外部服务。"
        f"投稿截止日：{record.submission_deadline}（{record.venue_profile}）。状态：{record.status}。"
    )
    if record.status == "partial":
        failed = ", ".join(record.failed_providers) if record.failed_providers else "none recorded"
        search_en += f" Failed providers: {failed}. The search may be incomplete."
        search_zh += f"失败的数据源：{failed}。检索可能不完整。"
    if record.confirmation == "awaiting_confirmation":
        search_en += (
            " Confirmation: awaiting_confirmation. "
            "The retrieval cap waits until the judging agent confirms each must-compare paper."
        )
        search_zh += "确认状态：awaiting_confirmation。检索上限要等判断模型逐条确认必须比较的论文之后才生效。"
    query_rows = [(query.id, f"{query.perspective.value}: {query.query}") for query in record.queries]

    def subsection(title_en: str, title_zh: str, en: str, zh: str) -> None:
        if bilingual:
            lines.extend([f"### {title_en} / {title_zh}", "", f"- **en:** {en}", f"- **zh-Hans:** {zh}", ""])
        elif languages == ["zh-Hans"]:
            lines.extend([f"### {title_zh}", "", zh, ""])
        else:
            lines.extend([f"### {title_en}", "", en, ""])

    subsection("Problem", "问题", problem_en, problem_zh)
    subsection("Contributions", "贡献", " ".join(contributions), " ".join(contributions_zh))
    subsection("Search", "检索", search_en, search_zh)
    lines.extend(["### Queries / 检索式" if bilingual else "### Queries", ""])
    if not query_rows:
        subsection("Queries issued", "检索式", "No query was issued.", "没有发出检索式。")
    for query_id, query_text in query_rows:
        lines.append(f"- `{query_id}`")
        lines.append("")
        if bilingual:
            lines.extend([f"- **en:** {query_text}", f"- **zh-Hans:** {query_text}", ""])
        else:
            lines.extend([query_text, ""])
    lines.extend(["### Candidates / 候选" if bilingual else "### Candidates", ""])
    if not record.candidates:
        subsection("Verified papers", "核实论文", "No paper was verified.", "没有核实到论文。")
    for item in record.candidates:
        # The id bullet splits PDF language cards so one candidate cannot overflow a page.
        lines.extend([f"- `{item.id}`", ""])
        if bilingual:
            lines.extend(
                [
                    f"- **en:** {_candidate_sentence(item, zh=False)}",
                    f"- **zh-Hans:** {_candidate_sentence(item, zh=True)}",
                    "",
                ]
            )
        else:
            zh = languages == ["zh-Hans"]
            lines.extend([_candidate_sentence(item, zh=zh), ""])
    if record.gaps:
        for index, gap in enumerate(record.gaps, start=1):
            lines.extend([f"- `{gap.kind}-{index}`", ""])
            if bilingual:
                lines.extend([f"- **en:** {gap.detail_en}", f"- **zh-Hans:** {gap.detail_zh}", ""])
            else:
                lines.extend([gap.detail_zh if languages == ["zh-Hans"] else gap.detail_en, ""])
    else:
        subsection(
            "Experiment comparison",
            "实验对照",
            "No missing must-compare baseline and no unfair baseline.",
            "没有缺失的必须基线，也没有不公平基线。",
        )
    if record.clause_advice:
        for item in record.clause_advice:
            lines.extend([f"- `{item.clause_id}`", ""])
            en = f"{item.dimension} → {item.clause_id} ({item.score:.0f}). {item.reason_en}"
            zh = f"{item.dimension} → {item.clause_id}（{item.score:.0f}）。{item.reason_zh}"
            if bilingual:
                lines.extend([f"- **en:** {en}", f"- **zh-Hans:** {zh}", ""])
            else:
                lines.extend([zh if languages == ["zh-Hans"] else en, ""])
    else:
        subsection("Clauses", "条款", "Search did not run; do not score these clauses yet.", "检索未完成，不建议打分。")
    if record.cap_signals:
        for item in record.cap_signals:
            lines.extend([f"- `{item.id}`", ""])
            en = f"{item.fatal_class} venue cap {item.venue_cap} ({item.coefficient_set}). {item.reason_en}"
            zh = f"{item.fatal_class} 会场上限 {item.venue_cap}（{item.coefficient_set}）。{item.reason_zh}"
            if bilingual:
                lines.extend([f"- **en:** {en}", f"- **zh-Hans:** {zh}", ""])
            else:
                lines.extend([zh if languages == ["zh-Hans"] else en, ""])
        effective = (
            f"Effective venue cap {record.effective_venue_cap}."
            if record.effective_venue_cap is not None
            else "No venue cap."
        )
        effective_zh = (
            f"生效上限 {record.effective_venue_cap}。"
            if record.effective_venue_cap is not None
            else "没有会场上限。"
        )
        subsection("Caps", "上限", effective, effective_zh)
    else:
        subsection("Caps", "上限", "No venue cap.", "没有会场上限。")
    subsection("Suggested weakness", "建议的不足", record.weakness_en, record.weakness_zh)
    subsection("Suggested rebuttal", "建议的回应", record.rebuttal_en, record.rebuttal_zh)
    return lines


def render_related_work_brief(
    judgment: RelatedWorkJudgment | dict[str, Any],
    languages: Sequence[str],
) -> list[str]:
    """Reviewer-facing related-work note. Queries and candidates stay in the appendix."""
    record = judgment if isinstance(judgment, RelatedWorkJudgment) else RelatedWorkJudgment.model_validate(judgment)
    bilingual = len(languages) > 1
    if languages == ["zh-Hans"]:
        heading = "## 相关工作"
    elif bilingual:
        heading = "## Related Work / 相关工作"
    else:
        heading = "## Related Work"
    lines = [heading, ""]

    def subsection(title_en: str, title_zh: str, en: str, zh: str) -> None:
        if bilingual:
            lines.extend([f"### {title_en} / {title_zh}", "", f"- **en:** {en}", f"- **zh-Hans:** {zh}", ""])
        elif languages == ["zh-Hans"]:
            lines.extend([f"### {title_zh}", "", zh, ""])
        else:
            lines.extend([f"### {title_en}", "", en, ""])

    status_en = f"Search status: {record.status}. Confirmation: {record.confirmation}."
    status_zh = f"检索状态：{record.status}。确认：{record.confirmation}。"
    if record.status == "partial":
        failed = ", ".join(record.failed_providers) if record.failed_providers else "none recorded"
        status_en += f" Failed providers: {failed}. The search may be incomplete."
        status_zh += f"失败的数据源：{failed}。检索可能不完整。"
    subsection("Status", "状态", status_en, status_zh)
    if record.gaps:
        for index, gap in enumerate(record.gaps, start=1):
            lines.extend([f"- `{gap.kind}-{index}`", ""])
            if bilingual:
                lines.extend([f"- **en:** {gap.detail_en}", f"- **zh-Hans:** {gap.detail_zh}", ""])
            elif languages == ["zh-Hans"]:
                lines.extend([gap.detail_zh, ""])
            else:
                lines.extend([gap.detail_en, ""])
    else:
        subsection(
            "Experiment comparison",
            "实验对照",
            "No missing must-compare baseline and no unfair baseline.",
            "没有缺失的必须基线，也没有不公平基线。",
        )
    if record.effective_venue_cap is not None:
        subsection(
            "Caps",
            "上限",
            f"Effective venue cap {record.effective_venue_cap}.",
            f"生效上限 {record.effective_venue_cap}。",
        )
    subsection("Suggested weakness", "建议的不足", record.weakness_en, record.weakness_zh)
    return lines


def render_related_work_markdown(judgment: RelatedWorkJudgment, languages: Sequence[str] | None = None) -> str:
    body = render_related_work_section(judgment, list(languages or ("en", "zh-Hans")))
    return "\n".join(body).rstrip() + "\n"
