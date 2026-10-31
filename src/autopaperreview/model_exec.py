"""OpenAI-compatible model execution.

The model returns JSON for one judgment at a time. This module checks the
endpoint, stores the raw exchange, and validates the payload. It does not
replace a failed call with the deterministic manuscript adapter.

`OpenAICompatibleAdapter` is the `execute_review` entry point. Text judgments
are contributions, ledger and agenda items, the seven rating dimensions, and
fatal classification. An optional `holistic` judgment ranks the paper. In the
locked `matched` mode it is K matched-panel calls, run together: each sees the
manuscript's main text only (the reference list and the appendix are cut from
the packet and the text is capped), simulates the four topic-matched reviewers
of mixed seniority the venue would assign, states the venue's rating norms,
writes their reviews, and has each submit an official rating; the per-call
panel mean is the mean of those four ratings and the shipped rank is the mean
of the panel means. In `panel` mode it is K mixed-role panels on the full
manuscript. The model's own estimate of the mean after writing a panel is
diagnostic in both. In `expert` mode it is K
expert reads, run together: each names the contribution type, writes down the
closest prior work and the field context the model recalls, fills the venue
review form, and then estimates the mean official rating on 1.0–10.0. The
1–6 decision on the same calls sets the bin and is not the rank. In `audited`
mode it is one audit call, whose pass, fail, and flaw excerpts must resolve in
the manuscript, followed by one scoring call that sees the verified audit. In
`mean` mode it is K venue-mean calls with no audit, run together and averaged.
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
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from .i18n import script_matches

from .adapters import ReviewRequest, ReviewResult, _merge_literature
from .anchors import (
    AUDIT_CHECK_IDS,
    AUDIT_VERDICTS,
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    CONTRIBUTION_TYPE_IDS,
    FLAW_SEVERITIES,
    FORECAST_PANEL_SIZE,
    FORECAST_SEAT_IDS,
    FORM_MAX,
    FORM_MIN,
    FULL_TEXT_MAX_CHARS,
    HOLISTIC_MODES,
    LOCKED_CANDIDATE,
    LOCKED_HOLISTIC_MODE,
    LOCKED_HOLISTIC_SAMPLES,
    LOCKED_HOLISTIC_TEMPERATURE,
    MAIN_TEXT_MAX_CHARS,
    MAIN_TEXT_MIN_SHARE,
    MATCHED_SEAT_IDS,
    MAX_AUDIT_FLAWS,
    MAX_FIELD_CONTEXT_LINES,
    MAX_PRIOR_WORK,
    MAX_REVIEW_POINTS,
    OFFICIAL_RATINGS,
    PANEL_ROLE_IDS,
    PANEL_SIZE,
    RATING_MAX,
    RATING_MIN,
    REBUTTAL_PANEL_SIZE,
    REBUTTAL_SEAT_IDS,
    audit_check_prompt_lines,
    checks_score,
    contribution_type_prompt_lines,
    expected_rating,
    forecast_mean,
    forecast_seat_prompt_lines,
    form_score,
    matched_seat_prompt_lines,
    normalize_contribution_type,
    normalize_forecast_seat,
    normalize_matched_seat,
    normalize_rebuttal_seat,
    normalize_reviewer_role,
    panel_mean,
    panel_role_prompt_lines,
    rebuttal_seat_prompt_lines,
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
# A full manuscript on a reasoning model can sit silent for half an hour.
# 900s aborted those calls. This value is the socket silence budget, not a
# total-time cap: streaming bytes renew it. There is no upper bound.
DEFAULT_MODEL_TIMEOUT_SECONDS = 7200.0
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


class HolisticJudgment(StrictPayload):
    """One venue-mean estimate plus the 1–6 decision. The decision is not the rank."""

    rationale: str = Field(min_length=1)
    rating: float = Field(ge=1, le=6)
    predicted_mean: float = Field(ge=1, le=10)

    @model_validator(mode="before")
    @classmethod
    def reject_bool_scores(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("rating", "predicted_mean"):
            if isinstance(value.get(key), bool):
                raise ValueError(f"{key} must be a number")
        return value


class ExpertPriorWork(StrictPayload):
    """One existing work the expert recalls as close to the submission. Not verified."""

    name: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    cited: bool = False
    sure: bool = False


class ExpertJudgment(StrictPayload):
    """One expert read: the context first, then the review form, then the mean and the decision."""

    area: str = Field(min_length=1)
    contribution_type: str = Field(min_length=1)
    prior_work: list[ExpertPriorWork] = Field(default_factory=list, max_length=MAX_PRIOR_WORK)
    field_context: list[str] = Field(default_factory=list, max_length=MAX_FIELD_CONTEXT_LINES)
    soundness: int = Field(ge=FORM_MIN, le=FORM_MAX)
    presentation: int = Field(ge=FORM_MIN, le=FORM_MAX)
    contribution: int = Field(ge=FORM_MIN, le=FORM_MAX)
    strengths: list[str] = Field(default_factory=list, max_length=MAX_REVIEW_POINTS)
    weaknesses: list[str] = Field(default_factory=list, max_length=MAX_REVIEW_POINTS)
    rationale: str = Field(min_length=1)
    predicted_mean: float = Field(ge=1, le=10)
    rating: float = Field(ge=1, le=6)

    @model_validator(mode="before")
    @classmethod
    def plain_numbers_and_lists(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("soundness", "presentation", "contribution", "predicted_mean", "rating"):
            if isinstance(value.get(key), bool):
                raise ValueError(f"{key} must be a number")
        for key in ("field_context", "strengths", "weaknesses"):
            if isinstance(value.get(key), str):
                value = {**value, key: [value[key]]}
        return value

    @model_validator(mode="after")
    def known_contribution_type(self) -> "ExpertJudgment":
        self.contribution_type = normalize_contribution_type(self.contribution_type)
        return self


class PanelReview(StrictPayload):
    """One simulated reviewer's review and official rating. The label is free text."""

    reviewer: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    rating: int = Field(ge=RATING_MIN, le=RATING_MAX)
    confidence: int = Field(ge=CONFIDENCE_MIN, le=CONFIDENCE_MAX)

    @model_validator(mode="before")
    @classmethod
    def plain_numbers_and_lists(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("rating", "confidence"):
            if isinstance(value.get(key), bool):
                raise ValueError(f"{key} must be a number")
        for key in ("strengths", "weaknesses"):
            if isinstance(value.get(key), str):
                value = {**value, key: [value[key]]}
        return value


class PanelJudgment(StrictPayload):
    """One simulated panel: exactly four reviews, then the chair's mean and decision."""

    reviews: list[PanelReview] = Field(min_length=PANEL_SIZE, max_length=PANEL_SIZE)
    discussion: str = Field(min_length=1)
    predicted_mean: float = Field(ge=1, le=10)
    rating: float = Field(ge=1, le=6)

    @model_validator(mode="before")
    @classmethod
    def reject_bool_scores(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("rating", "predicted_mean"):
            if isinstance(value.get(key), bool):
                raise ValueError(f"{key} must be a number")
        return value


class MatchedJudgment(PanelJudgment):
    """One simulated matched panel. Same shape as a panel; the reviewer labels are the seats."""


class RebuttalUpdate(StrictPayload):
    """One reviewer's rating at the end of the discussion, with what the response settled."""

    reviewer: str = Field(min_length=1)
    change: str = Field(min_length=1)
    final_rating: int = Field(ge=RATING_MIN, le=RATING_MAX)

    @model_validator(mode="before")
    @classmethod
    def plain_number_and_text(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("final_rating"), bool):
            raise ValueError("final_rating must be a number")
        if isinstance(value.get("change"), list):
            value = {**value, "change": "; ".join(str(item) for item in value["change"])}
        return value


class RebuttalJudgment(StrictPayload):
    """One simulated review through the discussion: three reviews, the response, three updates, the chair."""

    reviews: list[PanelReview] = Field(min_length=REBUTTAL_PANEL_SIZE, max_length=REBUTTAL_PANEL_SIZE)
    response: str = Field(min_length=1)
    updates: list[RebuttalUpdate] = Field(min_length=REBUTTAL_PANEL_SIZE, max_length=REBUTTAL_PANEL_SIZE)
    discussion: str = Field(min_length=1)
    predicted_mean: float = Field(ge=1, le=10)
    rating: float = Field(ge=1, le=6)

    @model_validator(mode="before")
    @classmethod
    def reject_bool_scores_and_join_lists(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("rating", "predicted_mean"):
            if isinstance(value.get(key), bool):
                raise ValueError(f"{key} must be a number")
        if isinstance(value.get("response"), list):
            value = {**value, "response": "; ".join(str(item) for item in value["response"])}
        return value


class ForecastReview(PanelReview):
    """One simulated reviewer's review, most likely official rating, and probability over the official ratings.

    ``probabilities`` holds six non-negative numbers in the order of the
    official ratings 1, 3, 5, 6, 8, 10. A dictionary keyed by the rating is
    reordered; a sum away from one is normalised later and counted, not
    retried. A wrong count, a negative entry, or an all-zero list fails.
    """

    probabilities: list[float] = Field(min_length=len(OFFICIAL_RATINGS), max_length=len(OFFICIAL_RATINGS))

    @model_validator(mode="before")
    @classmethod
    def probabilities_as_a_list(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        raw = value.get("probabilities")
        if isinstance(raw, dict):
            ordered: list[Any] = []
            for rating in OFFICIAL_RATINGS:
                for key in (rating, str(rating), float(rating), f"{rating}.0"):
                    if key in raw:
                        ordered.append(raw[key])
                        break
                else:
                    ordered.append(0.0)
            value = {**value, "probabilities": ordered}
        return value

    @field_validator("probabilities", mode="before")
    @classmethod
    def plain_numbers(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise ValueError("each probability must be a number")
        return value

    @field_validator("probabilities")
    @classmethod
    def non_negative_with_mass(cls, value: list[float]) -> list[float]:
        if any(item != item or item < 0 for item in value):
            raise ValueError("probabilities must be non-negative")
        if sum(value) <= 0:
            raise ValueError("probabilities must not all be zero")
        return value


class ForecastJudgment(StrictPayload):
    """One simulated forecast panel: exactly three reviews with probabilities, then the chair's mean and decision."""

    reviews: list[ForecastReview] = Field(min_length=FORECAST_PANEL_SIZE, max_length=FORECAST_PANEL_SIZE)
    discussion: str = ""
    predicted_mean: float = Field(ge=1, le=10)
    rating: float = Field(ge=1, le=6)

    @model_validator(mode="before")
    @classmethod
    def reject_bool_scores(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for key in ("rating", "predicted_mean"):
            if isinstance(value.get(key), bool):
                raise ValueError(f"{key} must be a number")
        if value.get("discussion") is None:
            value = {**value, "discussion": ""}
        return value


class AuditCheckAnswer(StrictPayload):
    """One answer to a fixed audit question. The pipeline checks the excerpt."""

    id: str = Field(min_length=1)
    verdict: str = Field(min_length=1)
    excerpt: str = ""
    note: str = ""

    @model_validator(mode="after")
    def known_id_and_verdict(self) -> "AuditCheckAnswer":
        if self.id not in AUDIT_CHECK_IDS:
            raise ValueError(f"unknown audit check id {self.id}")
        verdict = self.verdict.strip().lower()
        if verdict not in AUDIT_VERDICTS:
            raise ValueError("verdict must be pass, fail, or unclear")
        self.verdict = verdict
        return self


class AuditFlaw(StrictPayload):
    """One problem a reviewer would weigh, with the passage that shows it."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    why: str = Field(min_length=1)
    severity: str = Field(min_length=1)

    @model_validator(mode="after")
    def known_severity(self) -> "AuditFlaw":
        severity = self.severity.strip().lower()
        if severity not in FLAW_SEVERITIES:
            raise ValueError("severity must be blocking, major, or minor")
        self.severity = severity
        return self


class AuditJudgment(StrictPayload):
    """Every audit id answered once, plus at most four flaws. No score."""

    checks: list[AuditCheckAnswer] = Field(default_factory=list)
    flaws: list[AuditFlaw] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_checklist(self) -> "AuditJudgment":
        found = [item.id for item in self.checks]
        if sorted(found) != sorted(AUDIT_CHECK_IDS):
            missing = [item for item in AUDIT_CHECK_IDS if item not in found]
            repeated = sorted({item for item in found if found.count(item) > 1})
            raise ValueError(
                "checks must answer each audit id exactly once; "
                f"missing {missing}; repeated {repeated}"
            )
        if len(self.flaws) > MAX_AUDIT_FLAWS:
            raise ValueError(f"flaws must list at most {MAX_AUDIT_FLAWS} items")
        flaw_ids = [item.id for item in self.flaws]
        if len(flaw_ids) != len(set(flaw_ids)):
            raise ValueError("flaw ids must be unique")
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
    "expert": ExpertJudgment,
    "panel": PanelJudgment,
    "matched": MatchedJudgment,
    "forecast": ForecastJudgment,
    "audit": AuditJudgment,
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
        "Keys: rationale, rating, predicted_mean. "
        "rating is an integer 1, 2, 3, 4, 5, or 6. "
        "predicted_mean is a number from 1.0 to 10.0. "
        "There is no example score to copy. "
        "No other keys."
    ),
    "expert": (
        "Keys in this order: area, contribution_type, prior_work, field_context, soundness, "
        "presentation, contribution, strengths, weaknesses, rationale, predicted_mean, rating. "
        f"contribution_type is one of {', '.join(CONTRIBUTION_TYPE_IDS)}. "
        f"prior_work is a list of at most {MAX_PRIOR_WORK} objects with keys name, relation, cited, sure; "
        "cited and sure are booleans; the list may be empty. "
        f"field_context is a list of at most {MAX_FIELD_CONTEXT_LINES} strings. "
        f"soundness, presentation, and contribution are integers from {FORM_MIN} to {FORM_MAX}. "
        f"strengths and weaknesses are lists of at most {MAX_REVIEW_POINTS} strings. "
        "rating is an integer 1, 2, 3, 4, 5, or 6. "
        "predicted_mean is a number from 1.0 to 10.0. "
        "There is no example score to copy. "
        "No other keys."
    ),
    "panel": (
        '{"reviews":[{"reviewer":"<role id>","summary":"...","strengths":["..."],'
        '"weaknesses":["..."],"rating":<an official rating>,"confidence":<an integer>}],'
        '"discussion":"...","predicted_mean":<a number>,"rating":<a decision>} '
        f"reviews has exactly {PANEL_SIZE} objects, one for each reviewer id in the user message, "
        f"in that order: {', '.join(PANEL_ROLE_IDS)}. "
        "Inside a review, rating is that reviewer's official rating, an integer chosen from "
        f"{', '.join(str(item) for item in OFFICIAL_RATINGS)}, and confidence is an integer from "
        f"{CONFIDENCE_MIN} to {CONFIDENCE_MAX}. strengths and weaknesses are lists of short strings. "
        "At the top level, predicted_mean is a number from 1.0 to 10.0 and rating is the integer "
        "decision 1, 2, 3, 4, 5, or 6. "
        "There is no example score to copy. "
        "No other keys."
    ),
    "matched": (
        '{"reviews":[{"reviewer":"<seat id>","summary":"...","strengths":["..."],'
        '"weaknesses":["..."],"rating":<an official rating>,"confidence":<an integer>}],'
        '"discussion":"...","predicted_mean":<a number>,"rating":<a decision>} '
        f"reviews has exactly {PANEL_SIZE} objects, one for each reviewer id in the user message, "
        f"in that order: {', '.join(MATCHED_SEAT_IDS)}. "
        "Inside a review, rating is that reviewer's official rating, an integer chosen from "
        f"{', '.join(str(item) for item in OFFICIAL_RATINGS)}, and confidence is an integer from "
        f"{CONFIDENCE_MIN} to {CONFIDENCE_MAX}. strengths and weaknesses are lists of short strings. "
        "At the top level, predicted_mean is a number from 1.0 to 10.0 and rating is the integer "
        "decision 1, 2, 3, 4, 5, or 6. "
        "There is no example score to copy. "
        "No other keys."
    ),
    "rebuttal": (
        '{"reviews":[{"reviewer":"<reviewer id>","summary":"...","strengths":["..."],'
        '"weaknesses":["..."],"rating":<an official rating>,"confidence":<an integer>}],'
        '"response":"...",'
        '"updates":[{"reviewer":"<reviewer id>","change":"...","final_rating":<an official rating>}],'
        '"discussion":"...","predicted_mean":<a number>,"rating":<a decision>} '
        f"reviews has exactly {REBUTTAL_PANEL_SIZE} objects, one for each reviewer id in the user "
        f"message, in that order: {', '.join(REBUTTAL_SEAT_IDS)}. "
        f"updates has exactly {REBUTTAL_PANEL_SIZE} objects, one per reviewer, in the same order. "
        "Inside a review, rating is that reviewer's first official rating, an integer chosen from "
        f"{', '.join(str(item) for item in OFFICIAL_RATINGS)}, and confidence is an integer from "
        f"{CONFIDENCE_MIN} to {CONFIDENCE_MAX}. Inside an update, final_rating is that reviewer's "
        "official rating at the end of the discussion, an integer chosen from the same list. "
        "strengths and weaknesses are lists of short strings; response and change are strings. "
        "At the top level, predicted_mean is a number from 1.0 to 10.0 and rating is the integer "
        "decision 1, 2, 3, 4, 5, or 6. "
        "There is no example score to copy. "
        "No other keys."
    ),
    "forecast": (
        '{"reviews":[{"reviewer":"<reviewer id>","summary":"...","strengths":["..."],'
        '"weaknesses":["..."],"rating":<an official rating>,"probabilities":[<six numbers>],'
        '"confidence":<an integer>}],'
        '"predicted_mean":<a number>,"rating":<a decision>} '
        f"reviews has exactly {FORECAST_PANEL_SIZE} objects, one for each reviewer id in the user "
        f"message, in that order: {', '.join(FORECAST_SEAT_IDS)}. "
        "Inside a review, rating is the official rating that reviewer would most likely submit, an "
        f"integer chosen from {', '.join(str(item) for item in OFFICIAL_RATINGS)}; probabilities is a "
        f"list of exactly {len(OFFICIAL_RATINGS)} numbers from 0 to 1 that sum to 1, the probability "
        f"that this reviewer's official rating is {', '.join(str(item) for item in OFFICIAL_RATINGS)}, "
        f"in that order; confidence is an integer from {CONFIDENCE_MIN} to {CONFIDENCE_MAX}. "
        "strengths and weaknesses are lists of short strings. "
        "At the top level, predicted_mean is a number from 1.0 to 10.0 and rating is the integer "
        "decision 1, 2, 3, 4, 5, or 6. "
        "There is no example score to copy. "
        "No other keys."
    ),
    "audit": (
        '{"checks":[{"id":"<one of the twelve ids>","verdict":"<pass, fail, or unclear>",'
        '"excerpt":"copied passage, or empty","note":"..."}],'
        '"flaws":[{"id":"F1","title":"...","excerpt":"copied passage","why":"...",'
        '"severity":"<blocking, major, or minor>"}]} '
        "checks has exactly one entry for each of the twelve ids in the user message. "
        "excerpt is empty only when verdict is unclear. flaws may be an empty list. "
        "There is no score in this object. No other keys."
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
    from .stream_transport import urlopen_chat
    return urlopen_chat(request, timeout)


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
        reuse_prior_exchanges: bool = False,
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
        self.reuse_prior_exchanges = reuse_prior_exchanges
        self.egress = endpoint_kind(self.base_url)
        self.manuscript_left_machine = False
        self.exchanges: list[dict[str, Any]] = []
        self.rejected: list[dict[str, str]] = []
        self.usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._checked = False
        self._lock = threading.RLock()
        self._attempt_counts: dict[str, int] = {}
        self._last_messages: list[dict[str, Any]] = []
        self._last_text = ""
        self._last_judgment = ""

    def authorize(self) -> None:
        with self._lock:
            self._authorize_locked()

    def _authorize_locked(self) -> None:
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
        with self._lock:
            return self._metadata_locked()

    def _metadata_locked(self) -> dict[str, Any]:
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
        with self._lock:
            self.rejected.append(row)

    def write_index(self) -> None:
        with self._lock:
            directory = self.scratch / "model"
            directory.mkdir(parents=True, exist_ok=True)
            _write_json(directory / "exchanges.json", self._metadata_locked())
            _write_json(directory / "rejected.json", list(self.rejected))

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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            if self._last_judgment != judgment or not self._last_messages:
                prior = None
                prior_text = ""
            else:
                prior = list(self._last_messages)
                prior_text = self._last_text
        if prior is None:
            self.fail(f"judgment {judgment} has no completed exchange to retry")
        messages = [
            *prior,
            {"role": "assistant", "content": prior_text[:8000]},
            {"role": "user", "content": error},
        ]
        text = self._exchange(judgment, messages, attempt=self._next_attempt(judgment))
        try:
            model = schema.model_validate(_parse_object(text), context=context)
        except (ValueError, ValidationError) as exc:
            self.fail(
                f"judgment {judgment} failed schema validation after one retry: {_brief(exc)}\n{error}"
            )
        with self._lock:
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
            "stream": True,
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
        if self.reuse_prior_exchanges:
            reused = _reused_exchange_bytes(self.scratch, wire)
            if reused is not None:
                request_name, response_name = _exchange_names(judgment, attempt, 1)
                request_path = directory / request_name
                response_path = directory / response_name
                request_path.write_bytes(wire)
                response_path.write_bytes(reused)
                self._record_exchange(
                    judgment,
                    attempt,
                    1,
                    request_name,
                    response_name,
                    request_path,
                    response_path,
                    reused_prior_exchange=True,
                )
                return self._consume_envelope(reused)
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
            return self._consume_envelope(stored)
        self.fail(f"judgment {judgment} request failed after {TRANSPORT_RETRIES} transport retries: {last_detail}")

    def _consume_envelope(self, stored: bytes) -> str:
        try:
            payload = json.loads(stored.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return f"invalid chat envelope: {exc}"
        if not isinstance(payload, dict):
            return "invalid chat envelope: response is not an object"
        usage = payload.get("usage")
        if isinstance(usage, dict):
            with self._lock:
                for key in self.usage_totals:
                    raw_tokens = usage.get(key)
                    if isinstance(raw_tokens, int):
                        self.usage_totals[key] += raw_tokens
        try:
            return _message_text(payload)
        except ValueError as exc:
            return str(exc)

    def _record_exchange(
        self,
        judgment: str,
        attempt: int,
        transport_try: int,
        request_name: str,
        response_name: str,
        request_path: Path,
        response_path: Path,
        *,
        reused_prior_exchange: bool = False,
    ) -> None:
        row = {
            "judgment": judgment,
            "attempt": attempt,
            "transport_try": transport_try,
            "request_sha256": sha256_bytes(request_path.read_bytes()),
            "response_sha256": sha256_bytes(response_path.read_bytes()),
            "request_path": f"model/{request_name}",
            "response_path": f"model/{response_name}",
        }
        if reused_prior_exchange:
            row["reused_prior_exchange"] = True
        with self._lock:
            self.exchanges.append(row)


# Attempt directories are `{stage_id}-{attempt}-{8 hex}` under `run/.attempts`.
_ATTEMPT_DIR_NAME = re.compile(r"^(?P<stage>.+)-(?P<attempt>[1-9][0-9]*)-(?P<nonce>[0-9a-f]{8})$")


def _reusable_chat_bytes(raw: bytes) -> bool:
    """True when `raw` is a chat envelope with assistant text worth replaying."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    try:
        _message_text(payload)
    except ValueError:
        return False
    return True


def _prior_model_directories(scratch: Path) -> list[Path]:
    """Model logs from earlier attempts of this stage, including a promoted success."""
    parent = scratch.parent
    if parent.name != ".attempts":
        return []
    match = _ATTEMPT_DIR_NAME.fullmatch(scratch.name)
    if match is None:
        return []
    stage_id = match.group("stage")
    found: list[Path] = []
    try:
        siblings = list(parent.iterdir())
    except OSError:
        return []
    for sibling in siblings:
        if sibling == scratch or not sibling.is_dir():
            continue
        sibling_match = _ATTEMPT_DIR_NAME.fullmatch(sibling.name)
        if sibling_match is None or sibling_match.group("stage") != stage_id:
            continue
        model_dir = sibling / "model"
        if model_dir.is_dir():
            found.append(model_dir)
    promoted = parent.parent / "stages" / stage_id / "model"
    if promoted.is_dir():
        found.append(promoted)
    return found


def _reused_exchange_bytes(scratch: Path, wire: bytes) -> bytes | None:
    """Newest stored response whose request bytes match `wire`.

    Error bodies are skipped. A timeout leaves the finished judgments on disk
    so the next run does not pay for them again.
    """
    digest = sha256_bytes(wire)
    newest: tuple[float, bytes] | None = None
    for model_dir in _prior_model_directories(scratch):
        try:
            request_paths = list(model_dir.glob("*.request.json"))
        except OSError:
            continue
        for request_path in request_paths:
            try:
                if sha256_bytes(request_path.read_bytes()) != digest:
                    continue
                response_path = request_path.with_name(
                    request_path.name.removesuffix(".request.json") + ".response.json"
                )
                if not response_path.is_file():
                    continue
                raw = response_path.read_bytes()
                stamp = response_path.stat().st_mtime
            except OSError:
                continue
            if not _reusable_chat_bytes(raw):
                continue
            if newest is None or stamp >= newest[0]:
                newest = (stamp, raw)
    if newest is None:
        return None
    return newest[1]


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
        "holistic_mode": _holistic_mode(metadata.get("holistic_mode")),
        "packet_routes": {str(key): str(value) for key, value in packet_routes.items()},
        "stage_signature": str(metadata.get("stage_signature") or ""),
        "bilingual": bool(metadata.get("bilingual")),
        "reuse_prior_exchanges": bool(metadata.get("reuse_prior_exchanges", False)),
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


def _holistic_mode(value: object) -> str:
    """``forecast``, ``rebuttal``, ``matched``, ``panel``, ``expert``, ``audited``, or ``mean``. An empty value is the locked mode."""
    if value is None or value == "":
        return DEFAULT_HOLISTIC_MODE
    mode = str(value).strip().lower()
    if mode not in HOLISTIC_MODES:
        raise ModelExecutionError(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")
    return mode


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
        "holistic_mode",
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


HOLISTIC_TASK = """
## Venue mean
You are predicting the mean official rating of one paper at a top machine-learning conference. Score the manuscript against the scale below. You have not been shown another submission. Do not invent a reference paper, and do not report a distance from one.

predicted_mean is the mean of the official ratings careful reviewers would give. Those ratings are chosen from 1, 3, 5, 6, 8, and 10. The mean of several reviewers often falls between those choices. Report that mean, from 1.0 to 10.0 with one decimal. Do not round it back to the nearest official choice. This number orders papers. It is the reviewers' mean, not your harshest reading, and it is not a rescaling of rating.

Judge the comparison the manuscript reports. An attractive idea whose comparison does not show a gain a reviewer would rely on is a reject. A modest idea whose comparison shows a gain a reviewer would rely on is an accept. Do not save the top of the scale for a paper that changes the field. A paper you would accept and cite sits near 7 or 8.

Place the mean on this scale:
- near 3: a clear reject. The comparison does not test the claim, or it shows no gain.
- near 4: a reject. There is a real attempt, and a gap decides the paper. A typical rejected paper is near 4, not near 1.
- 5.0 to 5.4: borderline reject. The paper is competent, and you would not argue to accept it.
- 5.6 to 6.4: borderline accept. The paper is competent, and you would argue to accept it.
- near 7 or 8: an accept. The gain is one a reviewer would rely on and cite. A typical accepted paper is here, not near 9.
- near 9: a strong accept. Rare. The support is tight and the result matters.

There is no midpoint between a borderline reject and a borderline accept. The reject side stops at 5.4. The accept side starts at 5.6. Use 5.4 or 5.6 only when the paper sits on that line. A paper you would clearly reject is near 4 or below. A paper you would clearly accept is near 7 or above. Do not use 4.0, 5.0, 6.0, or 7.0 as a default. If the paper is not exactly on an anchor, move by a tenth. Use 1 and 10 only for an extreme case.

A paper with a gain a reviewer would rely on scores higher than a paper whose gain is almost reliable. That paper scores higher than a paper whose comparison does not test the claim. Keep that order. Two papers you would both reject still get different means when one is closer to a reliable gain. Two papers you would both accept still get different means when one is closer to a result you would cite. Most submissions are rejected.

rationale is three short clauses, in this order: the comparison the manuscript reports; whether a reviewer would rely on that gain; which side of the line that fact puts the mean on. Set predicted_mean only after those clauses. Do not edit it toward a round favorite.

rating is an integer from 1 to 6, chosen after predicted_mean. It is only the accept or reject decision. It must agree with the mean, and it is not the rank.
1 — strong reject. The main claim is already known, the method does not work, or an ethical problem is left untouched.
2 — reject. A technical flaw, an evaluation that does not test the claim, or missing detail that blocks reproduction is enough to decline.
3 — borderline reject. The paper is competent, and the reasons to decline outweigh the reasons to accept.
4 — borderline accept. The paper is competent, and the reasons to accept outweigh the reasons to decline.
5 — accept. The paper is sound and useful, and you would defend it in a discussion.
6 — strong accept. The result matters and the support is tight.
A mean at or below 5.4 takes rating 1, 2, or 3. A mean at or above 5.6 takes rating 4, 5, or 6.
3 and 4 are opposite decisions. Use 1 and 6 for extreme cases, not as decoration.
Return only the JSON object.
""".strip()


_AUDIT_HEAD = """
## Audit
You are the first reader of one manuscript submitted to a top machine-learning conference. You are not scoring it. Answer twelve fixed questions about what the manuscript shows, then list the flaws a careful reviewer would weigh in the decision.

Answer every question with one verdict:
- pass: the manuscript shows it. excerpt is the passage that shows it.
- fail: the manuscript shows the gap. excerpt is the passage where the gap is visible: the headline result stated without a spread, the results table caption that lists the baselines, the claim that outruns the evidence.
- unclear: the manuscript does not settle it. Leave excerpt empty.
An excerpt is a short span copied from the manuscript, at most two sentences or one table line. Copy it exactly; a line break may be written as a space. An excerpt that is not in the manuscript turns that answer into unclear. Prefer a passage from the method or the results over the abstract. Length, confidence, or polish does not answer any question. note is one short clause on what the excerpt shows.

The twelve questions, by id:
""".strip()

_AUDIT_TAIL = """
flaws are at most four problems a careful reviewer would weigh in the decision: a comparison the paper itself makes necessary and does not run, a gain inside its own noise, a claim the experiments do not test, a protocol that favours the method, a step that cannot be reimplemented. Each flaw has an id (F1, F2, ...), a title, an excerpt copied from the passage that shows it, why a reviewer would weigh it, and a severity: blocking when it alone decides the paper against acceptance, major when a reviewer would press on it and it could decide the paper, minor when it is worth stating and would not decide the paper alone. A missing citation, a writing issue, or a wish for more experiments is not a flaw here. A paper can have none; return an empty list then.
Return only the JSON object.
""".strip()


def audit_task_text() -> str:
    """The audit instructions. The twelve ids come from ``anchors.AUDIT_CHECKS``."""
    return "\n".join([_AUDIT_HEAD, *audit_check_prompt_lines(), "", _AUDIT_TAIL])


_EXPERT_HEAD = """
## Expert read
You are a senior researcher who has published and reviewed for years in the subfield this manuscript belongs to. You are predicting the mean official rating this paper would receive at a top machine-learning conference. Reviewers there bring their knowledge of the field to the paper: whether the idea already exists under another name, whether the baselines are the ones the area now uses, what size of gain is inside the usual noise, and whether anyone will use the result. Bring yours. You have not been shown another submission. Do not invent a reference paper, and do not report a distance from one.

Fill the fields in this order and settle each one before the next.

1. area: the subfield in a few words.
2. contribution_type: one of the ids below. The standard the evidence is held to follows from it.
""".strip()

_EXPERT_STEPS = """
3. prior_work: up to five existing works you recall as the closest to this submission's main idea or setting. For each give name (the way the area refers to it: a method name, or first author and year), relation (one clause on how the submission differs from it or overlaps with it), cited (true when the submission already cites or compares against it), and sure (true when you are confident the work exists as you describe it). Name a work only when you recall it; a line of work you cannot name belongs in field_context. A recalled work the submission does not cite counts against novelty only when sure is true. An empty list is allowed when you recall nothing close.
4. field_context: up to four short lines on what an expert knows about this setting: the standard benchmarks and the baselines a reviewer in the area expects to see in the comparison, whether this paper's baselines are the current strong ones or ones the area has moved past, and what size of gain the area treats as meaningful on these benchmarks against their usual run-to-run noise.
5. soundness, presentation, contribution: integers from 1 to 4 in the sense of the conference review form, where 1 is poor, 2 is fair, 3 is good, and 4 is excellent. soundness is whether the claims are supported by the evidence by the standard of the contribution type above. presentation is whether a reader in the area can follow and check the work. contribution is how much the area gains if the claims hold: novelty against the prior work you recalled, and the importance of the problem and of the result.
6. strengths and weaknesses: up to three clauses each, the ones a reviewer in this area would write. A weakness names something specific to this paper, not a wish for more experiments.
7. rationale: three short clauses, in this order: what the paper adds to what the area already has; whether the evidence meets the standard of its contribution type; where that puts the mean a panel of reviewers in this area would reach.
8. predicted_mean, then rating, chosen last.

Across all types, reviewers reward a result they will use or cite. They mark down an idea that already exists under another name, a comparison against baselines the area has moved past, a gain inside the usual noise on that benchmark, and a claim broader than the evidence. Judge the headline claim against the numbers in the tables, not against the abstract. Length, ambition, and polish are not evidence. A modest scope with a result the area will use is not marked down for being modest.
""".strip()

# The scale paragraphs below are the round 5 and 6 text, unchanged, so the
# expert read moves only the reading and not the output scale. A test checks
# that each paragraph is a verbatim part of HOLISTIC_TASK.
EXPERT_SCALE_PARAGRAPHS = (
    "predicted_mean is the mean of the official ratings careful reviewers would give. Those ratings are chosen from 1, 3, 5, 6, 8, and 10. The mean of several reviewers often falls between those choices. Report that mean, from 1.0 to 10.0 with one decimal. Do not round it back to the nearest official choice. This number orders papers. It is the reviewers' mean, not your harshest reading, and it is not a rescaling of rating.",
    "Place the mean on this scale:\n- near 3: a clear reject. The comparison does not test the claim, or it shows no gain.\n- near 4: a reject. There is a real attempt, and a gap decides the paper. A typical rejected paper is near 4, not near 1.\n- 5.0 to 5.4: borderline reject. The paper is competent, and you would not argue to accept it.\n- 5.6 to 6.4: borderline accept. The paper is competent, and you would argue to accept it.\n- near 7 or 8: an accept. The gain is one a reviewer would rely on and cite. A typical accepted paper is here, not near 9.\n- near 9: a strong accept. Rare. The support is tight and the result matters.",
    "There is no midpoint between a borderline reject and a borderline accept. The reject side stops at 5.4. The accept side starts at 5.6. Use 5.4 or 5.6 only when the paper sits on that line. A paper you would clearly reject is near 4 or below. A paper you would clearly accept is near 7 or above. Do not use 4.0, 5.0, 6.0, or 7.0 as a default. If the paper is not exactly on an anchor, move by a tenth. Use 1 and 10 only for an extreme case.",
    "rating is an integer from 1 to 6, chosen after predicted_mean. It is only the accept or reject decision. It must agree with the mean, and it is not the rank.\n1 — strong reject. The main claim is already known, the method does not work, or an ethical problem is left untouched.\n2 — reject. A technical flaw, an evaluation that does not test the claim, or missing detail that blocks reproduction is enough to decline.\n3 — borderline reject. The paper is competent, and the reasons to decline outweigh the reasons to accept.\n4 — borderline accept. The paper is competent, and the reasons to accept outweigh the reasons to decline.\n5 — accept. The paper is sound and useful, and you would defend it in a discussion.\n6 — strong accept. The result matters and the support is tight.\nA mean at or below 5.4 takes rating 1, 2, or 3. A mean at or above 5.6 takes rating 4, 5, or 6.\n3 and 4 are opposite decisions. Use 1 and 6 for extreme cases, not as decoration.",
)

_EXPERT_ORDER = (
    "Two papers you would both reject still get different means when one is closer to what the area would use. "
    "Two papers you would both accept still get different means when one is closer to a result the area will cite. "
    "Most submissions are rejected."
)


def expert_task_text() -> str:
    """The expert-read instructions. The type ids come from ``anchors.CONTRIBUTION_TYPES``."""
    return "\n".join(
        [
            _EXPERT_HEAD,
            *contribution_type_prompt_lines(),
            _EXPERT_STEPS,
            "",
            EXPERT_SCALE_PARAGRAPHS[0],
            "",
            EXPERT_SCALE_PARAGRAPHS[1],
            "",
            EXPERT_SCALE_PARAGRAPHS[2],
            "",
            _EXPERT_ORDER,
            "",
            EXPERT_SCALE_PARAGRAPHS[3],
            "Return only the JSON object.",
        ]
    )


_PANEL_HEAD = """
## Review panel
You are simulating the review of this manuscript at a top machine-learning conference. The quantity to predict is the mean of the official ratings the paper would receive from the reviewers this venue actually assigns. Those reviewers are not ideal referees. They are competent, busy researchers who read the paper once, bring their own corner of the field to it, and rate it by what reviewers at this venue reward and mark down. Predict them, not a better reviewer. You have not been shown another submission. Do not invent a reference paper, and do not report a distance from one.

Write four reviews, one from each reviewer below, in this order. Each reviewer has read the whole manuscript and has not seen the other reviews.
""".strip()

_PANEL_STEPS = """
Each review has these fields:
- reviewer: the id above.
- summary: one sentence on what the paper claims, in that reviewer's words.
- strengths: up to three clauses that reviewer would write.
- weaknesses: up to three clauses that reviewer would write, each specific to this paper.
- rating: the official rating that reviewer would submit, one of 1, 3, 5, 6, 8, or 10. At this venue an 8 says the reviewer would argue for acceptance and expects to cite the paper. A 6 says they lean accept with reservations. A 5 says the paper is competent and they do not see enough to accept it. A 3 says they see a clear reason to reject: a flaw in the evidence, a comparison that matters and is missing, or little novelty against work they know. 1 and 10 are rare.
- confidence: an integer from 1 to 5, how sure that reviewer is.

Real panels disagree. The same paper often draws a 3 from one reviewer and an 8 from another because they weigh different things. Give each reviewer the rating they would submit from their own reading. Do not pull the four toward each other, and do not save 8 for a paper that changes the field. The reasons reviewers at this venue most often give for a 3 are limited novelty, a missing comparison, and evidence that does not cover the claim. The reasons they most often give for an 8 are a result they want to use or cite and an idea that changes how they think about the problem. Most submissions are rejected.

After the four reviews, write discussion: two or three short clauses on where this panel lands and what decides it. Then set predicted_mean and rating, in that order and last. predicted_mean is your own estimate of the mean official rating after the discussion; it need not equal the mean of the four ratings above.
""".strip()


def panel_task_text() -> str:
    """The panel instructions. The role ids come from ``anchors.PANEL_ROLES``.

    The scale paragraphs are the round 5-7 text, unchanged, so the panel moves
    the reading and not the output scale of the chair's estimate or the decision.
    """
    return "\n".join(
        [
            _PANEL_HEAD,
            *panel_role_prompt_lines(),
            "",
            _PANEL_STEPS,
            "",
            EXPERT_SCALE_PARAGRAPHS[0],
            "",
            EXPERT_SCALE_PARAGRAPHS[1],
            "",
            EXPERT_SCALE_PARAGRAPHS[2],
            "",
            EXPERT_SCALE_PARAGRAPHS[3],
            "Return only the JSON object.",
        ]
    )


_MATCHED_HEAD = """
## Assigned panel
You are predicting the mean official rating this manuscript would receive at a top machine-learning conference, from the reviewers the venue would assign to it. Those reviewers are matched to the paper's topic and vary in seniority. They are competent and busy: each reads the main text once, in one sitting, does not study the appendix, brings their own knowledge of the area to the paper, and rates by what reviewers at this venue reward and mark down. Predict them, not a better reviewer. You have not been shown another submission. Do not invent a reference paper, and do not report a distance from one.

You are shown the manuscript's main text. The reference list and the appendix are not shown; where they were removed, the text says so. When the main text defers a detail, a proof, or an experiment to the appendix, treat it as these reviewers do: note that it is deferred, and do not treat it as missing or as wrong.

Write four reviews, one from each reviewer below, in this order. Each reviewer has read the main text and has not seen the other reviews.
""".strip()

_MATCHED_STEPS = """
Each review has these fields:
- reviewer: the id above.
- summary: one sentence on what the paper claims, in that reviewer's words.
- strengths: up to two clauses that reviewer would write.
- weaknesses: up to two clauses that reviewer would write, each specific to this paper.
- rating: the official rating that reviewer would submit, one of 1, 3, 5, 6, 8, or 10.
- confidence: an integer from 1 to 5, how sure that reviewer is.
""".strip()

# The venue's rating norms, stated as public knowledge of the venue. Rates
# across all submissions, not a quota for one paper.
MATCHED_NORMS = """
How reviewers at this venue use the scale. About one submission in three is accepted. Across all reviews, 5 and 6 are the most common ratings; 3 is next, about one review in five; 8 is about one review in six; 1 and 10 are rare. An 8 says the reviewer would argue for acceptance in the discussion and expects to cite the paper. A 6 says they lean accept with reservations. A 5 says the paper is competent and they do not see enough to accept it. A 3 says they see a clear reason to reject: little novelty against work they know, a comparison that matters and is missing, or evidence that does not cover the claim. An accepted paper usually has a mean above 6 and at least one 8; a rejected paper usually has a mean between 3.5 and 5.5 and at least one 3. These are rates across all submissions, not a quota for one paper: a strong paper draws 8s from more than one of its reviewers, and a weak one draws 3s from more than one.

Real panels disagree. The same paper often draws a 3 from one reviewer and an 8 from another because they weigh different things. Give each reviewer the rating they would submit from their own reading. Do not pull the four toward each other, and do not save 8 for a paper that changes the field: reviewers at this venue give an 8 to a paper they want to use or cite. The reasons they most often give for a 3 are limited novelty, a missing comparison, and evidence that does not cover the claim.

After the four reviews, write discussion: two or three short clauses on where this panel lands and what decides it. Then set predicted_mean and rating, in that order and last. predicted_mean is your own estimate of the mean official rating after the discussion; it need not equal the mean of the four ratings above.
""".strip()


def matched_task_text() -> str:
    """The matched-panel instructions. The seat ids come from ``anchors.MATCHED_SEATS``.

    The scale paragraphs are the round 5-8 text, unchanged, so the matched panel
    moves the reading, the seats and the norms, not the output scale of the
    chair's estimate or the decision.
    """
    return "\n".join(
        [
            _MATCHED_HEAD,
            *matched_seat_prompt_lines(),
            "",
            _MATCHED_STEPS,
            "",
            MATCHED_NORMS,
            "",
            EXPERT_SCALE_PARAGRAPHS[0],
            "",
            EXPERT_SCALE_PARAGRAPHS[1],
            "",
            EXPERT_SCALE_PARAGRAPHS[2],
            "",
            EXPERT_SCALE_PARAGRAPHS[3],
            "Return only the JSON object.",
        ]
    )


_REBUTTAL_HEAD = """
## Review and discussion
You are simulating the review of this manuscript at a top machine-learning conference, through the discussion period. The quantity to predict is the mean of the official ratings as they stand at the end of that period, after the authors have responded and each reviewer has revised or kept their rating. Those are the ratings the venue's forum shows. The reviewers are not ideal referees. They are competent, busy researchers who read the paper once, bring their own corner of the field to it, and rate it by what reviewers at this venue reward and mark down. Predict them, not a better reviewer. You have not been shown another submission. Do not invent a reference paper, and do not report a distance from one.

Stage one: write three reviews, one from each reviewer below, in this order. Each reviewer has read the whole manuscript and has not seen the other reviews.
""".strip()

_REBUTTAL_STEPS = """
Each review has these fields:
- reviewer: the id above.
- summary: one sentence on what the paper claims, in that reviewer's words.
- strengths: up to two clauses that reviewer would write.
- weaknesses: up to three clauses that reviewer would write, each specific to this paper. These are what the authors will answer.
- rating: the official rating that reviewer would submit, one of 1, 3, 5, 6, 8, or 10. At this venue an 8 says the reviewer would argue for acceptance and expects to cite the paper. A 6 says they lean accept with reservations. A 5 says the paper is competent and they do not see enough to accept it. A 3 says they see a clear reason to reject: a flaw in the evidence, a comparison that matters and is missing, or little novelty against work they know. 1 and 10 are rare.
- confidence: an integer from 1 to 5, how sure that reviewer is.

Real panels disagree. The same paper often draws a 3 from one reviewer and an 8 from another because they weigh different things. Give each reviewer the rating they would submit from their own reading. Do not pull the three toward each other, and do not save 8 for a paper that changes the field. The reasons reviewers at this venue most often give for a 3 are limited novelty, a missing comparison, and evidence that does not cover the claim. The reasons they most often give for an 8 are a result they want to use or cite and an idea that changes how they think about the problem. Most submissions are rejected.

Stage two: write response, the authors' reply as competent authors would write it in the discussion period, in two to five clauses. The authors answer every weakness above and concede nothing they can contest. For each weakness, say what they can offer: a clarification or a pointer to something the manuscript already contains, its appendix included; a new experiment, comparison, or ablation they can run and report within the period, with the outcome such authors would plausibly obtain given what the paper already shows; or nothing that would satisfy the reviewer, because the objection is to the contribution itself. Do not invent a result the paper's own evidence does not make plausible.

Stage three: write updates, one per reviewer, in the same order. Each has reviewer (the id), change (one clause: what the response settled for this reviewer, or what still stands), and final_rating, the official rating that reviewer holds at the end of the discussion, one of 1, 3, 5, 6, 8, or 10. Reviewers at this venue raise a rating when the response settles the concern they rated on: a comparison or ablation they asked for that comes out in the paper's favour, an ambiguity cleared up, a claim narrowed to what the evidence shows. They keep it when the concern is the contribution itself: an idea that exists under another name, a gain within the usual noise of the benchmark, a central claim the added evidence still does not cover, a problem too small to matter. A raised rating usually moves one step, 5 to 6 or 6 to 8. A rating rarely falls. Most ratings do not change; the ones that do belong to reviewers whose concern was answerable. A reviewer who gave a 3 for one of those fundamental reasons keeps it. Decide each reviewer's final rating from their own weaknesses and the response, not from the other reviewers.

After the updates, write discussion: two or three short clauses on where this panel lands at the end of the discussion and what decided it. Then set predicted_mean and rating, in that order and last. predicted_mean is your own estimate of the mean official rating at the end of the discussion; it need not equal the mean of the three final ratings above.
""".strip()


def rebuttal_task_text() -> str:
    """The review-and-discussion instructions. The seat ids come from ``anchors.REBUTTAL_SEATS``.

    The scale paragraphs are the round 5-9 text, unchanged, so the discussion
    stage moves the reading and the target, not the output scale of the
    chair's estimate or the decision.
    """
    return "\n".join(
        [
            _REBUTTAL_HEAD,
            *rebuttal_seat_prompt_lines(),
            "",
            _REBUTTAL_STEPS,
            "",
            EXPERT_SCALE_PARAGRAPHS[0],
            "",
            EXPERT_SCALE_PARAGRAPHS[1],
            "",
            EXPERT_SCALE_PARAGRAPHS[2],
            "",
            EXPERT_SCALE_PARAGRAPHS[3],
            "Return only the JSON object.",
        ]
    )


_FORECAST_HEAD = """
## Review forecast
You are simulating the review of this manuscript at a top machine-learning conference. The quantity to predict is the mean of the official ratings the paper would receive from the reviewers this venue actually assigns. Those reviewers are not ideal referees. They are competent, busy researchers who read the paper once, bring their own corner of the field to it, and rate it by what reviewers at this venue reward and mark down. Predict them, not a better reviewer. You have not been shown another submission. Do not invent a reference paper, and do not report a distance from one.

Write three reviews, one from each reviewer below, in this order. Each reviewer has read the whole manuscript and has not seen the other reviews.
""".strip()

_FORECAST_STEPS = """
Each review has these fields:
- reviewer: the id above.
- summary: one sentence on what the paper claims, in that reviewer's words.
- strengths: up to two clauses that reviewer would write.
- weaknesses: up to two clauses that reviewer would write, each specific to this paper.
- rating: the official rating that reviewer would most likely submit, one of 1, 3, 5, 6, 8, or 10. At this venue an 8 is an accept: a solid paper the reviewer would accept. A typical accepted paper lands on 8. An 8 is not reserved for a paper the reviewer would cite as a landmark. A 6 is only just above the acceptance line: the reviewer leans accept and the reservations could flip the decision. A 5 says the paper is competent and they do not see enough to accept it. A 3 says they see a clear reason to reject: a flaw in the evidence, a comparison that matters and is missing, or little novelty against work they know. 1 and 10 are rare. 10 is the rare paper the reviewer would champion.
- probabilities: six numbers that sum to one, the probability that this reviewer's official rating is 1, 3, 5, 6, 8, and 10, in that order. This is your forecast of one reviewer, not a hedge. A reviewer whose reaction to the paper is clear puts most of the mass on one rating. A reviewer who could land on either side of a line splits it between the two ratings on each side of that line. Put mass on 8 when a reviewer with this reading would accept the paper, and mass on 3 when they would have a real chance of finding a reason to reject it. Spread the mass only as far as the reviewer's reaction is uncertain, and no further; a flat spread over all six ratings is almost never right. The expected rating under these probabilities is this reviewer's score, so the probabilities must say what the review says. rating is the rating with the most mass.
- confidence: an integer from 1 to 5, how sure that reviewer is.

Real panels disagree. The same paper often draws a 3 from one reviewer and an 8 from another because they weigh different things. Give each reviewer the rating and the probabilities they would have from their own reading. Do not pull the three toward each other, and do not save 8 for a paper that changes the field. The reasons reviewers at this venue most often give for a 3 are limited novelty, a missing comparison, and evidence that does not cover the claim. The reasons they most often give for an 8 are a solid result they would accept and an idea the area can use. A landmark they would cite is a 10. Most submissions are rejected.

After the three reviews, set predicted_mean and rating, in that order and last. predicted_mean is your own estimate of the mean official rating; it need not equal the mean of the three ratings above.
""".strip()


def forecast_task_text() -> str:
    """The forecast-panel instructions. The seat ids come from ``anchors.FORECAST_SEATS``.

    The scale paragraphs are the round 5-10 text, unchanged, so the forecast
    moves the form of each reviewer's answer and not the output scale of the
    chair's estimate or the decision.
    """
    return "\n".join(
        [
            _FORECAST_HEAD,
            *forecast_seat_prompt_lines(),
            "",
            _FORECAST_STEPS,
            "",
            EXPERT_SCALE_PARAGRAPHS[0],
            "",
            EXPERT_SCALE_PARAGRAPHS[1],
            "",
            EXPERT_SCALE_PARAGRAPHS[2],
            "",
            EXPERT_SCALE_PARAGRAPHS[3],
            "",
            _FORECAST_ACCEPT_CUT,
            "Return only the JSON object.",
        ]
    )


# Forecast-only. Each sentence names its field. The shared scale still says a
# clear accept is near 7 or 8, and the 1-6 decision still follows that mean
# rule. This paragraph does not add a cutoff.
_FORECAST_ACCEPT_CUT = """
On the review field rating, and on the probabilities for that rating, an accept's official rating is 8. On predicted_mean, an accept sits near 7 or 8. On the 1-to-6 decision, the rating chosen after predicted_mean, apply the mean rule above: a predicted_mean near 7 or 8 is decision 5, and decision 6 stays for a rare strong accept.
""".strip()


_SOURCE_TEXT_HEADING = "\n## Source Text\n"
# The packet sections the prompt_packet stage writes after the source text.
# The source section ends at the first of them; a "## " inside the manuscript
# does not end it.
_PACKET_SECTIONS_AFTER_SOURCE = (
    "\n## Figures (mandatory inspection)",
    "\n## Listed contributions (audit each one)",
    "\n## Related-Work Queries",
    "\n## Retrieved Sources",
)
# A reference-list heading on a line of its own, optionally numbered. Small
# caps sometimes extract with a space after the first letter.
_REFERENCES_HEADING = re.compile(
    r"^[ \t]*(?:\d+\.?[ \t]+)?(?:r[ \t]?eferences|b[ \t]?ibliography)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
MAIN_TEXT_END_MARKER = "[Main text ends here. The reference list and any appendix that follow are not shown.]"
MAIN_TEXT_CAP_MARKER = (
    "[The manuscript text is cut here for length. The rest, including the reference list and any "
    "appendix, is not shown.]"
)


def main_text_packet(packet: str, *, max_chars: int = MAIN_TEXT_MAX_CHARS) -> tuple[str, dict[str, Any]]:
    """The packet with its ``## Source Text`` section reduced to the manuscript's main text.

    The source text is cut at the first standalone reference-list heading that
    occurs at or after ``MAIN_TEXT_MIN_SHARE`` of the text, and is capped at
    ``max_chars`` in every case. A marker line says where the cut happened.
    Every other section of the packet is kept. The record holds numbers only:
    the source characters, the characters shown, and the kind of cut
    (``references``, ``cap``, or ``none``).
    """
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    span = _source_span(packet)
    if span is None:
        return packet, {"source_chars": 0, "shown_chars": 0, "cut": "none"}
    body_start, body_end = span
    source = packet[body_start:body_end]
    source_chars = len(source.strip())
    cut = "none"
    shown = source
    floor = int(len(source) * MAIN_TEXT_MIN_SHARE)
    for match in _REFERENCES_HEADING.finditer(source):
        if match.start() >= floor:
            shown = source[: match.start()]
            cut = "references"
            break
    if len(shown.strip()) > max_chars:
        shown = shown.lstrip("\n")[:max_chars]
        cut = "cap"
    shown = shown.strip("\n")
    shown_chars = len(shown.strip())
    if cut == "references":
        shown = shown + "\n\n" + MAIN_TEXT_END_MARKER
    elif cut == "cap":
        shown = shown + "\n\n" + MAIN_TEXT_CAP_MARKER
    rebuilt = packet[:body_start] + "\n" + shown + "\n" + packet[body_end:]
    return rebuilt, {"source_chars": source_chars, "shown_chars": shown_chars, "cut": cut}


def _source_span(packet: str) -> tuple[int, int] | None:
    """Start and end of the packet's ``## Source Text`` body, or ``None`` without one."""
    start = packet.find(_SOURCE_TEXT_HEADING)
    if start < 0:
        return None
    body_start = start + len(_SOURCE_TEXT_HEADING)
    ends = [index for index in (packet.find(marker, body_start) for marker in _PACKET_SECTIONS_AFTER_SOURCE) if index >= 0]
    body_end = min(ends) if ends else len(packet)
    return body_start, body_end


def full_text_packet(packet: str, *, max_chars: int = FULL_TEXT_MAX_CHARS) -> tuple[str, dict[str, Any]]:
    """The packet whole, unless its ``## Source Text`` section is longer than ``max_chars``.

    A manuscript under the cap is returned byte for byte. A longer one keeps
    its first ``max_chars`` characters of source text, reference list and
    appendix included as far as they reach, followed by a marker line; every
    other section of the packet is kept. The record holds numbers only: the
    source characters, the characters shown, and the kind of cut (``cap`` or
    ``none``). No reference-list cut is made.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    span = _source_span(packet)
    if span is None:
        return packet, {"source_chars": 0, "shown_chars": 0, "cut": "none"}
    body_start, body_end = span
    source = packet[body_start:body_end]
    source_chars = len(source.strip())
    if source_chars <= max_chars:
        return packet, {"source_chars": source_chars, "shown_chars": source_chars, "cut": "none"}
    shown = source.lstrip("\n")[:max_chars].strip("\n")
    shown_chars = len(shown.strip())
    rebuilt = packet[:body_start] + "\n" + shown + "\n\n" + MAIN_TEXT_CAP_MARKER + "\n" + packet[body_end:]
    return rebuilt, {"source_chars": source_chars, "shown_chars": shown_chars, "cut": "cap"}


AUDITED_SCORING_RULE = (
    "Treat the audit as the facts a careful reviewer would raise. It is not a score sheet: "
    "do not add or subtract a fixed amount per item. For each failed check and each flaw, "
    "decide whether it would change a reviewer's decision on this paper, and say so in the "
    "rationale before you set the mean. A flaw a reviewer would decline the paper over puts "
    "the mean on the reject side. A passed check does not raise the mean on its own. Length, "
    "ambition, and polish are not evidence. Begin the rationale with the flaw or failed check "
    "that matters most, or say that none changes the decision."
)
_AUDIT_EXCERPT_CHARS = 240


def _clip(text: str) -> str:
    flat = " ".join(str(text).split())
    if len(flat) <= _AUDIT_EXCERPT_CHARS:
        return flat
    return flat[: _AUDIT_EXCERPT_CHARS - 1].rstrip() + "…"


def _audited_block(audit: dict[str, Any]) -> str:
    """The verified audit as the scorer sees it. An unanchored verdict shows as unclear."""
    lines = [
        "## Verified audit",
        "A first pass answered twelve fixed questions about this manuscript. Each pass or fail "
        "below is tied to a passage that was located in the manuscript. An item marked unclear "
        "had no locatable passage and is not evidence either way.",
        "",
    ]
    for check in audit["checks"]:
        verdict = str(check["verdict"])
        if verdict in {"pass", "fail"}:
            lead = f"- {check['id']}: {verdict}"
            note = str(check.get("note") or "").strip()
            if note:
                lead += f" — {note}"
            lines.append(f'{lead} Passage: "{_clip(check["excerpt"])}"')
        else:
            lines.append(f"- {check['id']}: unclear")
    lines.extend(["", "Flaws from that pass. Each passage was located in the manuscript."])
    flaws = list(audit.get("flaws") or [])
    if not flaws:
        lines.append("- none")
    for flaw in flaws:
        lines.append(
            f"- {flaw['id']} [{flaw['severity']}] {flaw['title']}: {flaw['why']} "
            f'Passage: "{_clip(flaw["excerpt"])}"'
        )
    lines.extend(["", AUDITED_SCORING_RULE])
    return "\n".join(lines)


# One forecast-panel call is the locked evaluation. Temperature is per call so
# a stage default of 0 does not force every sample to the same request body.
# In forecast, rebuttal, matched, panel, expert and mean modes the calls run
# together and each sees only its own request.
DEFAULT_HOLISTIC_SAMPLES = LOCKED_HOLISTIC_SAMPLES
DEFAULT_HOLISTIC_TEMPERATURE = LOCKED_HOLISTIC_TEMPERATURE
DEFAULT_HOLISTIC_MODE = LOCKED_HOLISTIC_MODE


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
            audit_task_text(),
            AUDITED_SCORING_RULE,
            expert_task_text(),
            panel_task_text(),
            matched_task_text(),
            rebuttal_task_text(),
            forecast_task_text(),
            "excerpt-normalize-fuzzy-v1",
            "fatal-verify-v1",
            "venue-mean-k3-v1",
            "audited-k1-v1",
            "expert-k2-v1",
            "panel-k2-v1",
            f"matched-k2-v1-main-text-{MAIN_TEXT_MAX_CHARS}",
            f"rebuttal-k2-v1-full-text-{FULL_TEXT_MAX_CHARS}",
            f"forecast-k1-v1-full-text-{FULL_TEXT_MAX_CHARS}",
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


def _sample_holistic(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
    mode: str = DEFAULT_HOLISTIC_MODE,
    request: ReviewRequest | None = None,
    snapshots: list[Any] | None = None,
) -> dict[str, Any]:
    """K holistic samples in the declared mode.

    ``forecast``: K simulated three-reviewer forecast panels on the full
    packet, run together; the locked rank is the first call's mean expected
    rating. ``rebuttal``: K simulated reviews through the discussion period on
    the full packet, run together; ranked by the mean of the first two
    final-rating means. ``matched``: K simulated matched panels on the main
    text, run together. ``panel``: K simulated mixed-role panels on the full
    packet, run together. ``expert``: K expert reads, run together, averaged.
    ``audited``: each sample is one audit call, whose excerpts are verified
    against the manuscript, then one scoring call that sees the verified
    audit. ``mean``: K venue-mean calls, run together, averaged. There is no
    band substitute in any mode. A missing sample fails the paper.
    """
    if samples < 1:
        session.fail("judgment holistic requires at least one sample")
    if mode not in HOLISTIC_MODES:
        session.fail(f"holistic_mode must be one of {', '.join(HOLISTIC_MODES)}")
    session.authorize()
    if mode == "forecast":
        return _forecast_samples(session, packet, samples=samples, temperature=temperature)
    if mode == "rebuttal":
        return _rebuttal_samples(session, packet, samples=samples, temperature=temperature)
    if mode == "matched":
        return _matched_samples(session, packet, samples=samples, temperature=temperature)
    if mode == "panel":
        return _panel_samples(session, packet, samples=samples, temperature=temperature)
    if mode == "expert":
        return _expert_samples(session, packet, samples=samples, temperature=temperature)
    if mode == "mean":
        return _venue_mean_samples(session, packet, samples=samples, temperature=temperature)
    if request is None:
        session.fail("audited holistic mode needs the manuscript to verify excerpts")
    assert request is not None
    return _audited_samples(
        session,
        packet,
        samples=samples,
        temperature=temperature,
        request=request,
        snapshots=list(snapshots or []),
    )


def _venue_mean_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K venue-mean calls, run together. The previous candidate ``mean-k3``."""
    user = packet + "\n\n" + HOLISTIC_TASK
    ratings: list[float | None] = [None] * samples
    means: list[float | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> HolisticJudgment:
        payload = session.complete(
            f"holistic-{index + 1}",
            user,
            HolisticJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, HolisticJudgment):
            session.fail("judgment holistic returned an unexpected payload")
        return payload

    # Three concurrent calls keep the paper's wall clock near one call.
    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            ratings[index] = round(float(payload.rating), 4)
            means[index] = round(float(payload.predicted_mean), 4)
    if errors:
        raise errors[0]
    if any(item is None for item in ratings) or any(item is None for item in means):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    rating_values = [float(item) for item in ratings if item is not None]
    mean_values = [float(item) for item in means if item is not None]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "predicted_mean": round(sum(mean_values) / len(mean_values), 4),
        "mean_samples": mean_values,
        "n": len(mean_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "mean",
    }


def _form_record(payload: ExpertJudgment, *, sample: int) -> dict[str, Any]:
    """The numbers and the type from one expert read. No text leaves the raw exchange."""
    return {
        "sample": sample,
        "contribution_type": payload.contribution_type,
        "soundness": int(payload.soundness),
        "presentation": int(payload.presentation),
        "contribution": int(payload.contribution),
        "form_score": form_score(payload.soundness, payload.presentation, payload.contribution),
        "prior_work": len(payload.prior_work),
        "prior_work_sure": sum(1 for item in payload.prior_work if item.sure),
        "prior_work_uncited_sure": sum(1 for item in payload.prior_work if item.sure and not item.cited),
        "field_context_lines": len(payload.field_context),
    }


def _expert_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K expert reads, run together. The locked candidate ``expert-k2``."""
    user = packet + "\n\n" + expert_task_text()
    ratings: list[float | None] = [None] * samples
    means: list[float | None] = [None] * samples
    forms: list[dict[str, Any] | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> ExpertJudgment:
        payload = session.complete(
            f"expert-{index + 1}",
            user,
            ExpertJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, ExpertJudgment):
            session.fail("judgment expert returned an unexpected payload")
        assert isinstance(payload, ExpertJudgment)
        return payload

    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            ratings[index] = round(float(payload.rating), 4)
            means[index] = round(float(payload.predicted_mean), 4)
            forms[index] = _form_record(payload, sample=index)
    if errors:
        raise errors[0]
    if any(item is None for item in (*ratings, *means, *forms)):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    rating_values = [float(item) for item in ratings if item is not None]
    mean_values = [float(item) for item in means if item is not None]
    form_values = [item for item in forms if item is not None]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "expert_mean": round(sum(mean_values) / len(mean_values), 4),
        "expert_samples": mean_values,
        "form_samples": [float(item["form_score"]) for item in form_values],
        "forms": form_values,
        "n": len(mean_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "expert",
    }


def _panel_record(
    payload: PanelJudgment,
    *,
    sample: int,
    normalize: Callable[[object], str | None] = normalize_reviewer_role,
) -> dict[str, Any]:
    """The numbers and the role ids from one simulated panel. No text leaves the raw exchange."""
    ratings = [int(review.rating) for review in payload.reviews]
    return {
        "sample": sample,
        "reviews": [
            {
                "role": normalize(review.reviewer) or "other",
                "rating": int(review.rating),
                "confidence": int(review.confidence),
            }
            for review in payload.reviews
        ],
        "panel_mean": panel_mean(ratings),
        "high": max(ratings),
        "low": min(ratings),
        "off_scale": sum(1 for value in ratings if value not in OFFICIAL_RATINGS),
        "chair_mean": round(float(payload.predicted_mean), 4),
        "decision": round(float(payload.rating), 4),
    }


def _panel_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K simulated panels, run together. The locked candidate ``panel-k2``."""
    user = packet + "\n\n" + panel_task_text()
    panels: list[dict[str, Any] | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> PanelJudgment:
        payload = session.complete(
            f"panel-{index + 1}",
            user,
            PanelJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, PanelJudgment):
            session.fail("judgment panel returned an unexpected payload")
        assert isinstance(payload, PanelJudgment)
        return payload

    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            panels[index] = _panel_record(payload, sample=index)
    if errors:
        raise errors[0]
    if any(item is None for item in panels):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    panel_values = [item for item in panels if item is not None]
    rating_values = [float(item["decision"]) for item in panel_values]
    mean_values = [float(item["panel_mean"]) for item in panel_values]
    chair_values = [float(item["chair_mean"]) for item in panel_values]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "panel_mean": round(sum(mean_values) / len(mean_values), 4),
        "panel_samples": mean_values,
        "chair_mean": round(sum(chair_values) / len(chair_values), 4),
        "chair_samples": chair_values,
        "high_samples": [float(item["high"]) for item in panel_values],
        "low_samples": [float(item["low"]) for item in panel_values],
        "panels": panel_values,
        "n": len(mean_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "panel",
    }


def _matched_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K simulated matched panels on the main text, run together. The locked candidate ``matched-k2``."""
    shown, main_text = main_text_packet(packet)
    user = shown + "\n\n" + matched_task_text()
    panels: list[dict[str, Any] | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> MatchedJudgment:
        payload = session.complete(
            f"matched-{index + 1}",
            user,
            MatchedJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, MatchedJudgment):
            session.fail("judgment matched returned an unexpected payload")
        assert isinstance(payload, MatchedJudgment)
        return payload

    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            panels[index] = _panel_record(payload, sample=index, normalize=normalize_matched_seat)
    if errors:
        raise errors[0]
    if any(item is None for item in panels):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    panel_values = [item for item in panels if item is not None]
    rating_values = [float(item["decision"]) for item in panel_values]
    mean_values = [float(item["panel_mean"]) for item in panel_values]
    chair_values = [float(item["chair_mean"]) for item in panel_values]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "matched_mean": round(sum(mean_values) / len(mean_values), 4),
        "matched_samples": mean_values,
        "chair_mean": round(sum(chair_values) / len(chair_values), 4),
        "chair_samples": chair_values,
        "high_samples": [float(item["high"]) for item in panel_values],
        "low_samples": [float(item["low"]) for item in panel_values],
        "matched_panels": panel_values,
        "main_text": main_text,
        "n": len(mean_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "matched",
    }


def _rebuttal_record(payload: RebuttalJudgment, *, sample: int) -> dict[str, Any]:
    """The numbers and the seat ids from one simulated discussion. No text leaves the raw exchange."""
    first = [int(review.rating) for review in payload.reviews]
    final = [int(update.final_rating) for update in payload.updates]
    seats = [normalize_rebuttal_seat(review.reviewer) for review in payload.reviews]
    update_seats = [normalize_rebuttal_seat(update.reviewer) for update in payload.updates]
    initial_mean = panel_mean(first, size=REBUTTAL_PANEL_SIZE)
    final_mean = panel_mean(final, size=REBUTTAL_PANEL_SIZE)
    return {
        "sample": sample,
        "reviews": [
            {
                "role": seat or "other",
                "rating": before,
                "confidence": int(review.confidence),
                "final_rating": after,
            }
            for seat, review, before, after in zip(seats, payload.reviews, first, final)
        ],
        "initial_mean": initial_mean,
        "final_mean": final_mean,
        "lift": round(final_mean - initial_mean, 4),
        "raised": sum(1 for before, after in zip(first, final) if after > before),
        "lowered": sum(1 for before, after in zip(first, final) if after < before),
        "high": max(final),
        "low": min(final),
        "off_scale": sum(1 for value in (*first, *final) if value not in OFFICIAL_RATINGS),
        "label_mismatch": sum(
            1
            for seat, update_seat in zip(seats, update_seats)
            if seat is not None and update_seat is not None and seat != update_seat
        ),
        "chair_mean": round(float(payload.predicted_mean), 4),
        "decision": round(float(payload.rating), 4),
    }


def _rebuttal_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K simulated reviews through the discussion period, run together. The locked candidate ``rebuttal-k2``."""
    shown, main_text = full_text_packet(packet, max_chars=FULL_TEXT_MAX_CHARS)
    user = shown + "\n\n" + rebuttal_task_text()
    panels: list[dict[str, Any] | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> RebuttalJudgment:
        payload = session.complete(
            f"rebuttal-{index + 1}",
            user,
            RebuttalJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, RebuttalJudgment):
            session.fail("judgment rebuttal returned an unexpected payload")
        assert isinstance(payload, RebuttalJudgment)
        return payload

    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            panels[index] = _rebuttal_record(payload, sample=index)
    if errors:
        raise errors[0]
    if any(item is None for item in panels):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    panel_values = [item for item in panels if item is not None]
    rating_values = [float(item["decision"]) for item in panel_values]
    final_values = [float(item["final_mean"]) for item in panel_values]
    initial_values = [float(item["initial_mean"]) for item in panel_values]
    chair_values = [float(item["chair_mean"]) for item in panel_values]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "rebuttal_mean": round(sum(final_values) / len(final_values), 4),
        "rebuttal_samples": final_values,
        "initial_mean": round(sum(initial_values) / len(initial_values), 4),
        "initial_samples": initial_values,
        "chair_mean": round(sum(chair_values) / len(chair_values), 4),
        "chair_samples": chair_values,
        "high_samples": [float(item["high"]) for item in panel_values],
        "low_samples": [float(item["low"]) for item in panel_values],
        "rebuttal_panels": panel_values,
        "main_text": main_text,
        "n": len(final_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "rebuttal",
    }


_RENORMALISE_TOLERANCE = 0.01


def _forecast_record(payload: ForecastJudgment, *, sample: int) -> dict[str, Any]:
    """The numbers and the seat ids from one simulated forecast panel. No text leaves the raw exchange."""
    votes = [int(review.rating) for review in payload.reviews]
    reviews: list[dict[str, Any]] = []
    expectations: list[float] = []
    renormalised = 0
    mode_mismatch = 0
    for review, vote in zip(payload.reviews, votes):
        expected, probabilities = expected_rating(review.probabilities)
        if abs(sum(float(item) for item in review.probabilities) - 1.0) > _RENORMALISE_TOLERANCE:
            renormalised += 1
        peak = max(probabilities)
        modal = [rating for rating, weight in zip(OFFICIAL_RATINGS, probabilities) if weight == peak]
        if vote not in modal:
            mode_mismatch += 1
        expectations.append(expected)
        reviews.append(
            {
                "role": normalize_forecast_seat(review.reviewer) or "other",
                "rating": vote,
                "confidence": int(review.confidence),
                "expected": expected,
                "peak": round(peak, 4),
                "probabilities": list(probabilities),
            }
        )
    return {
        "sample": sample,
        "reviews": reviews,
        "forecast_mean": forecast_mean(expectations),
        "vote_mean": panel_mean(votes, size=FORECAST_PANEL_SIZE),
        "high": max(expectations),
        "low": min(expectations),
        "off_scale": sum(1 for value in votes if value not in OFFICIAL_RATINGS),
        "renormalized": renormalised,
        "mode_mismatch": mode_mismatch,
        "chair_mean": round(float(payload.predicted_mean), 4),
        "decision": round(float(payload.rating), 4),
    }


def _forecast_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
) -> dict[str, Any]:
    """K simulated forecast panels on the whole manuscript, run together. The locked candidate ``forecast-k1``."""
    shown, main_text = full_text_packet(packet, max_chars=FULL_TEXT_MAX_CHARS)
    user = shown + "\n\n" + forecast_task_text()
    panels: list[dict[str, Any] | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> ForecastJudgment:
        payload = session.complete(
            f"forecast-{index + 1}",
            user,
            ForecastJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, ForecastJudgment):
            session.fail("judgment forecast returned an unexpected payload")
        assert isinstance(payload, ForecastJudgment)
        return payload

    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            panels[index] = _forecast_record(payload, sample=index)
    if errors:
        raise errors[0]
    if any(item is None for item in panels):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    panel_values = [item for item in panels if item is not None]
    rating_values = [float(item["decision"]) for item in panel_values]
    forecast_values = [float(item["forecast_mean"]) for item in panel_values]
    vote_values = [float(item["vote_mean"]) for item in panel_values]
    chair_values = [float(item["chair_mean"]) for item in panel_values]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "forecast_mean": round(sum(forecast_values) / len(forecast_values), 4),
        "forecast_samples": forecast_values,
        "vote_mean": round(sum(vote_values) / len(vote_values), 4),
        "vote_samples": vote_values,
        "chair_mean": round(sum(chair_values) / len(chair_values), 4),
        "chair_samples": chair_values,
        "high_samples": [float(item["high"]) for item in panel_values],
        "low_samples": [float(item["low"]) for item in panel_values],
        "forecast_panels": panel_values,
        "main_text": main_text,
        "n": len(forecast_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "forecast",
    }


def _audited_samples(
    session: ModelSession,
    packet: str,
    *,
    samples: int,
    temperature: float,
    request: ReviewRequest,
    snapshots: list[Any],
) -> dict[str, Any]:
    """K audit-then-score pairs. Pairs run together; each pair runs in order."""
    audit_user = packet + "\n\n" + audit_task_text()
    ratings: list[float | None] = [None] * samples
    means: list[float | None] = [None] * samples
    audits: list[dict[str, Any] | None] = [None] * samples
    errors: list[BaseException] = []

    def one(index: int) -> tuple[dict[str, Any], HolisticJudgment]:
        payload = session.complete(
            f"audit-{index + 1}",
            audit_user,
            AuditJudgment,
            temperature=temperature,
        )
        if not isinstance(payload, AuditJudgment):
            session.fail("judgment audit returned an unexpected payload")
        assert isinstance(payload, AuditJudgment)
        audit = _verify_audit(session, payload, request=request, snapshots=snapshots, sample=index)
        user = packet + "\n\n" + _audited_block(audit) + "\n\n" + HOLISTIC_TASK
        score = session.complete(
            f"holistic-{index + 1}",
            user,
            HolisticJudgment,
            temperature=temperature,
        )
        if not isinstance(score, HolisticJudgment):
            session.fail("judgment holistic returned an unexpected payload")
        assert isinstance(score, HolisticJudgment)
        return audit, score

    with ThreadPoolExecutor(max_workers=min(samples, 3)) as pool:
        futures = {pool.submit(one, index): index for index in range(samples)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                audit, score = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            audits[index] = audit
            ratings[index] = round(float(score.rating), 4)
            means[index] = round(float(score.predicted_mean), 4)
    if errors:
        raise errors[0]
    if any(item is None for item in (*ratings, *means, *audits)):
        session.fail(
            "judgment holistic produced no rating; refusing to fall back to dimension bands"
        )
    rating_values = [float(item) for item in ratings if item is not None]
    mean_values = [float(item) for item in means if item is not None]
    audit_values = [item for item in audits if item is not None]
    return {
        "rating": round(sum(rating_values) / len(rating_values), 4),
        "samples": rating_values,
        "audited_mean": round(sum(mean_values) / len(mean_values), 4),
        "audited_samples": mean_values,
        "checks_samples": [float(item["checks_score"]) for item in audit_values],
        "audits": audit_values,
        "n": len(mean_values),
        "verified": True,
        "locked_candidate": LOCKED_CANDIDATE,
        "mode": "audited",
    }


def _verify_audit(
    session: ModelSession,
    payload: AuditJudgment,
    *,
    request: ReviewRequest,
    snapshots: list[Any],
    sample: int,
) -> dict[str, Any]:
    """Keep a pass or fail only when its excerpt resolves. Drop an unanchored flaw.

    The answers are stored in the fixed checklist order. ``claimed`` keeps what
    the model said; ``verdict`` is what the scorer sees.
    """

    def anchor_for(excerpt: str) -> Anchor | None:
        if not excerpt.strip():
            return None
        return locate_excerpt(
            excerpt,
            manuscript=request.source_text,
            manuscript_sha=request.manuscript.sha256,
            snapshots=snapshots,
        )

    answers = {item.id: item for item in payload.checks}
    counts = {
        "pass": 0,
        "fail": 0,
        "unclear": 0,
        "unanchored": 0,
        "flaws": 0,
        "flaws_dropped": 0,
        "blocking": 0,
    }
    checks: list[dict[str, Any]] = []
    for check_id in AUDIT_CHECK_IDS:
        item = answers[check_id]
        claimed = item.verdict
        verdict = claimed
        excerpt = ""
        verified = False
        if claimed in {"pass", "fail"}:
            anchor = anchor_for(item.excerpt)
            if anchor is None:
                session.reject("audit", check_id, _UNRESOLVED_ANCHOR, category="audit_excerpt")
                verdict = "unclear"
                counts["unanchored"] += 1
            else:
                verified = True
                excerpt = anchor.excerpt or item.excerpt
        counts[verdict] += 1
        checks.append(
            {
                "id": check_id,
                "verdict": verdict,
                "claimed": claimed,
                "verified": verified,
                "excerpt": excerpt,
                "note": item.note.strip(),
            }
        )
    flaws: list[dict[str, Any]] = []
    for flaw in payload.flaws:
        anchor = anchor_for(flaw.excerpt)
        if anchor is None:
            session.reject("audit", flaw.id, _UNRESOLVED_ANCHOR, category="flaw_excerpt")
            counts["flaws_dropped"] += 1
            continue
        flaws.append(
            {
                "id": flaw.id,
                "title": flaw.title.strip(),
                "severity": flaw.severity,
                "why": flaw.why.strip(),
                "excerpt": anchor.excerpt or flaw.excerpt,
                "verified": True,
            }
        )
        counts["flaws"] += 1
        if flaw.severity == "blocking":
            counts["blocking"] += 1
    return {
        "sample": sample,
        "checks": checks,
        "flaws": flaws,
        "counts": counts,
        "checks_score": checks_score(counts["pass"]),
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
            packet,
            samples=int(settings["holistic_samples"]),
            temperature=float(settings["holistic_temperature"]),
            mode=str(settings.get("holistic_mode") or DEFAULT_HOLISTIC_MODE),
            request=request,
            snapshots=snapshots,
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
        self._reuse_prior_exchanges = False
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
        reuse_prior_exchanges: bool = False,
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
        self._reuse_prior_exchanges = reuse_prior_exchanges
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
                reuse_prior_exchanges=self._reuse_prior_exchanges,
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
