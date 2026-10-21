"""OpenAI-compatible model execution.

The model returns JSON for one judgment at a time. This module checks the
endpoint, stores the raw exchange, and validates the payload. It does not
replace a failed call with the deterministic manuscript adapter.

`OpenAICompatibleAdapter` is the `execute_review` entry point. Text judgments
are contributions, ledger and agenda items, the seven rating dimensions, and
fatal classification. `OpenAICompatibleVisionInspector` is the `visual_inspect`
entry point and sends rendered page images to the same chat API.

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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from .i18n import script_matches

from .adapters import ReviewRequest, ReviewResult, _merge_literature
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
from .rubrics import RUBRIC_ID, band_case_errors, clause_dimension, clause_score
from .visual import VisualPageRequest

Transport = Callable[[urllib.request.Request, float], bytes]

TEXT_JUDGMENTS = ("contributions", "ledger", "dimensions", "fatal")
TRANSPORT_RETRIES = 2
TRANSPORT_BACKOFF_SECONDS = (0.5, 1.0)
_UNRESOLVED_ANCHOR = "excerpt does not occur in the manuscript or a retrieved snapshot"
_HINTS: dict[str, tuple[str, ...]] = {
    "contributions": ("contribution",),
    "ledger": ("ledger", "agenda", "closed-book", "closed_book"),
    "dimensions": ("sar", "dimension", "band"),
    "fatal": ("fatal", "metric", "protocol"),
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


class BandEvidence(StrictPayload):
    excerpt: str = Field(min_length=12)
    location: str = Field(min_length=2)
    observable: str = Field(min_length=2)


class DimensionItem(StrictPayload):
    dimension: SarDimension
    supporting: list[BandEvidence] = Field(min_length=1)
    opposing: list[BandEvidence] = Field(min_length=1)
    move_up: str = Field(min_length=40)
    move_down: str = Field(min_length=40)
    clause_id: str = Field(min_length=1)
    rationale: str = Field(min_length=40)

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


class FatalJudgment(StrictPayload):
    classifications: list[FatalItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_issues(self) -> "FatalJudgment":
        identifiers = [item.issue_id for item in self.classifications]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("fatal classifications must name each issue once")
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
        '{"dimensions":[{"dimension":"experimental_soundness",'
        '"supporting":[{"excerpt":"exact substring","location":"Section 4","observable":"single_seed_no_interval"}],'
        '"opposing":[{"excerpt":"a different exact substring","location":"Table 2","observable":"ablation_moves_decision_metric"}],'
        '"move_up":"what observable result would make the next higher band true",'
        '"move_down":"what observable result would make the next lower band true",'
        '"clause_id":"es-4","rationale":"why this clause and not the neighbors"}]} '
        "Write supporting evidence, opposing evidence, move_up, and move_down before clause_id. "
        "Include each of the seven SAR dimensions once. "
        "The pipeline sets the numeric score from the clause id. "
        "A band without both excerpts is rejected. "
        "Band 6 is rejected unless supporting cites a band-6 observable and opposing cites a missing band-8 observable."
    ),
    "fatal": (
        '{"classifications":[{"issue_id":"I-C1","fatal_class":"leakage","decision_relevant":true}]} '
        "fatal_class is one of invalid_protocol, leakage, wrong_metric, unfair_baseline, "
        "unsupported_claim, format_desk_reject."
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
        "Every excerpt must be an exact substring of the manuscript or of a retrieved snapshot "
        "included in the packet. Do not invent a locator. Unknown fields are rejected."
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


def locate_excerpt(
    excerpt: str,
    *,
    manuscript: str,
    manuscript_sha: str,
    snapshots: list[Any],
) -> Anchor | None:
    """Resolve an exact excerpt against the manuscript or a retrieved snapshot."""
    if not excerpt or not excerpt.strip():
        return None
    if excerpt in manuscript:
        start = manuscript.find(excerpt)
        return Anchor(
            kind=AnchorKind.text_offset,
            display=f"manuscript[{start}:{start + len(excerpt)}]",
            artifact_sha256=manuscript_sha,
            encoding="utf-8",
            start=start,
            end=start + len(excerpt),
            excerpt=excerpt,
        )
    for snapshot in snapshots:
        body = getattr(snapshot, "excerpt", None) or ""
        if excerpt in body:
            return Anchor(
                kind=AnchorKind.external_snapshot,
                display=snapshot.id,
                snapshot_id=snapshot.id,
                artifact_sha256=snapshot.sha256,
                excerpt=excerpt,
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
        }

    def reject(self, judgment: str, item_id: str, reason: str) -> None:
        self.rejected.append({"judgment": judgment, "id": item_id, "reason": reason})

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
    ) -> BaseModel:
        self.authorize()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(_schema_key(judgment))},
            {"role": "user", "content": user_content},
        ]
        text = self._exchange(judgment, messages, attempt=self._next_attempt(judgment))
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
            text = self._exchange(judgment, messages, attempt=self._next_attempt(judgment))
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

    def _exchange(self, judgment: str, messages: list[dict[str, Any]], *, attempt: int) -> str:
        secret = _env_secret(self.api_key_env)
        body: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
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
    unknown = [item for item in judgments if item not in TEXT_JUDGMENTS]
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
        "timeout_seconds": float(metadata.get("timeout_seconds") or 60),
        "temperature": float(metadata.get("temperature") or 0),
        "json_object": bool(metadata.get("json_object", True)),
        "judgments": judgments,
        "packet_routes": {str(key): str(value) for key, value in packet_routes.items()},
        "stage_signature": str(metadata.get("stage_signature") or ""),
        "bilingual": bool(metadata.get("bilingual")),
    }


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
                if key not in {"judgments", "packet_routes", "bilingual"}
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


def _anchor_retry_message(missing: list[tuple[str, str]]) -> str:
    lines = [
        "Dimension band rejected. Return one JSON object with all seven dimensions.",
        "Write supporting evidence, opposing evidence, move_up, and move_down before clause_id.",
        "Each excerpt must be an exact substring of the manuscript or a retrieved snapshot.",
        "Band 6 needs a band-6 supporting observable and a missing band-8 opposing observable.",
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
        resolved: list[tuple[str, int, BandEvidence, Anchor]] = []
        item_missing: list[str] = []
        for role, group in (("supporting", item.supporting), ("opposing", item.opposing)):
            for index, ev in enumerate(group, start=1):
                anchor = locate_excerpt(
                    ev.excerpt,
                    manuscript=request.source_text,
                    manuscript_sha=request.manuscript.sha256,
                    snapshots=snapshots,
                )
                if anchor is None:
                    item_missing.append(f"{role}[{index}] excerpt is not in the manuscript")
                else:
                    resolved.append((role, index, ev, anchor))
        structure = band_case_errors(
            dimension=item.dimension.value,
            score=clause_score(item.clause_id),
            supporting=[ev.model_dump() for ev in item.supporting],
            opposing=[ev.model_dump() for ev in item.opposing],
            move_up=item.move_up,
            move_down=item.move_down,
        )
        item_missing.extend(reason.split(": ", 1)[-1] for reason in structure)
        if item_missing:
            missing.append((item.dimension.value, "; ".join(item_missing)))
            continue
        evidence_ids: list[str] = []
        for role, index, ev, anchor in resolved:
            evidence_id = f"E-{item.dimension.value}-{role[0]}{index}"
            evidence_ids.append(evidence_id)
            evidence.append(_evidence(evidence_id, ev.excerpt, anchor, request.manuscript))
        scores.append(
            DimensionScore(
                dimension=item.dimension,
                score=clause_score(item.clause_id),
                rationale=_localized(item.rationale),
                evidence_ids=evidence_ids,
                route_ids=_route_ids(route_id, request.routes),
                rubric_id=RUBRIC_ID,
                clause_id=item.clause_id,
                metadata={
                    "judgment": "dimensions",
                    "supporting": [ev.model_dump() for ev in item.supporting],
                    "opposing": [ev.model_dump() for ev in item.opposing],
                    "move_up": item.move_up,
                    "move_down": item.move_down,
                    "band6_default": False,
                },
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
                )
                continue
            evidence_id = f"E-{item.id}"
            evidence.append(_evidence(evidence_id, item.excerpt, anchor, request.manuscript))
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
                    item.excerpt,
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
                session.reject("ledger", item.id, "ledger id collides with an accepted claim")
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
                )
                continue
            evidence_id = f"E-{item.id}"
            evidence.append(_evidence(evidence_id, item.excerpt, anchor, request.manuscript))
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
        payload = session.complete("dimensions", packet, DimensionJudgment)
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
                    session.reject("dimensions", name, reason)
                detail = "; ".join(f"{name}: {reason}" for name, reason in missing)
                session.fail(
                    "judgment dimensions failed after one retry: unresolved anchors: " + detail
                )
        scores.extend(score_rows)
        evidence.extend(evidence_rows)

    if "fatal" in judgments:
        _route_id, packet = packet_for("fatal")
        known = ", ".join(sorted(known_issues)) or "none"
        user = (
            packet
            + "\n\n## Pipeline context\n"
            + f"Accepted issue ids: {known}\n"
            + "Classify only these ids. The pipeline assigns I-<contribution id>.\n"
        )
        payload = session.complete("fatal", user, FatalJudgment)
        assert isinstance(payload, FatalJudgment)
        updates: dict[str, FatalItem] = {}
        for item in payload.classifications:
            if item.issue_id not in known_issues:
                session.reject("fatal", item.issue_id, "fatal classification names an unknown issue")
                continue
            updates[item.issue_id] = item
        revised: list[ReviewIssue] = []
        for issue in issues:
            item = updates.get(issue.id)
            if item is None:
                revised.append(issue)
                continue
            revised.append(
                issue.model_copy(
                    update={
                        "fatal_class": item.fatal_class,
                        "decision_relevant": item.decision_relevant,
                    }
                )
            )
        issues = revised

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
        self._timeout_seconds = 60.0
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
