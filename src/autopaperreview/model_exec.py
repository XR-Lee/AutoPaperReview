"""OpenAI-compatible model execution.

The model returns JSON for one judgment at a time. This module checks the
endpoint, stores the raw exchange, and validates the payload. It does not
replace a failed call with the deterministic manuscript adapter.

`OpenAICompatibleAdapter` is the `execute_review` entry point. Text judgments
are contributions, ledger and agenda items, the seven rating dimensions, and
fatal classification. `OpenAICompatibleVisionInspector` is the `visual_inspect`
entry point and sends rendered page images to the same chat API.

Loopback endpoints are allowed by default. Any other host is remote: the stage
must set `allow_remote`, the project network policy must be `allow`, and the
configured environment variable must hold an API key. The key is sent as a
bearer token and is never written to the request body, the stage metadata, or
an error string.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
from .rubrics import RUBRIC_ID, clause_dimension, clause_score
from .visual import VisualPageRequest

Transport = Callable[[urllib.request.Request, float], bytes]

TEXT_JUDGMENTS = ("contributions", "ledger", "dimensions", "fatal")
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
    items: list[ContributionItem] = Field(default_factory=list)

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
    ledger: list[LedgerItem] = Field(default_factory=list)
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


_SCHEMAS: dict[str, type[BaseModel]] = {
    "contributions": ContributionJudgment,
    "ledger": LedgerJudgment,
    "dimensions": DimensionJudgment,
    "fatal": FatalJudgment,
    "visual": VisualJudgment,
}

_SCHEMA_HINTS = {
    "contributions": (
        '{"items":[{"id":"C1","claim":"...","excerpt":"exact substring",'
        '"tests":"...","comparator":"...","missing":"...","severity":"major","confidence":0.5}]}'
    ),
    "ledger": (
        '{"ledger":[{"id":"L1","claim":"...","excerpt":"exact substring","risk":"..."}],'
        '"agenda":[{"id":"Q1","question":"...","claim_ids":["C1","L1"]}]}'
    ),
    "dimensions": (
        '{"dimensions":[{"dimension":"experimental_soundness","clause_id":"es-4",'
        '"rationale":"...","excerpt":"exact substring"}]} '
        "Include each of the seven SAR dimensions once. "
        "The pipeline sets the numeric score from the clause id."
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
}


def endpoint_kind(base_url: str) -> str:
    """Return `loopback` or `remote`. Remote is every host that is not loopback."""
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
        pass
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


class ModelSession:
    """One stage's chat calls, raw files, and egress note."""

    transport: Transport | None = None

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

    def complete(
        self,
        judgment: str,
        user_content: str | list[dict[str, Any]],
        schema: type[BaseModel],
    ) -> BaseModel:
        self.authorize()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(judgment.split("-", 1)[0])},
            {"role": "user", "content": user_content},
        ]
        text = self._exchange(judgment, messages, attempt=1)
        try:
            return schema.model_validate(_parse_object(text))
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
            retry = self._exchange(judgment, messages, attempt=2)
            try:
                return schema.model_validate(_parse_object(retry))
            except (ValueError, ValidationError) as second_error:
                self.fail(
                    f"judgment {judgment} failed schema validation after one retry: {_brief(second_error)}"
                )
        raise AssertionError("unreachable")

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
        request_name = f"{_safe_name(judgment)}.attempt-{attempt}.request.json"
        response_name = f"{_safe_name(judgment)}.attempt-{attempt}.response.json"
        request_path = directory / request_name
        response_path = directory / response_name
        request_path.write_bytes(wire)
        try:
            raw = self._transport(request, self.timeout_seconds)
        except ModelExecutionError:
            raise
        except Exception as exc:
            detail = _redact(f"{type(exc).__name__}: {exc}", secret)[:500]
            response_path.write_bytes(_redact_bytes(detail.encode("utf-8"), secret))
            self._record_exchange(judgment, attempt, request_name, response_name, request_path, response_path)
            self.fail(f"judgment {judgment} request failed: {detail}")
        if not isinstance(raw, bytes):
            self.fail(f"judgment {judgment} transport did not return bytes")
        stored = _redact_bytes(raw, secret)
        response_path.write_bytes(stored)
        self._record_exchange(judgment, attempt, request_name, response_name, request_path, response_path)
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

    def _record_exchange(
        self,
        judgment: str,
        attempt: int,
        request_name: str,
        response_name: str,
        request_path: Path,
        response_path: Path,
    ) -> None:
        self.exchanges.append(
            {
                "judgment": judgment,
                "attempt": attempt,
                "request_sha256": sha256_bytes(request_path.read_bytes()),
                "response_sha256": sha256_bytes(response_path.read_bytes()),
                "request_path": f"model/{request_name}",
                "response_path": f"model/{response_name}",
            }
        )


def _safe_name(judgment: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-._" else "-" for character in judgment)
    return cleaned or "judgment"


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
            **{key: value for key, value in settings.items() if key not in {"judgments", "packet_routes"}},
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
        for item in payload.dimensions:
            anchor = locate_excerpt(
                item.excerpt,
                manuscript=request.source_text,
                manuscript_sha=request.manuscript.sha256,
                snapshots=snapshots,
            )
            if anchor is None:
                session.reject(
                    "dimensions",
                    item.dimension.value,
                    "excerpt does not occur in the manuscript or a retrieved snapshot",
                )
                continue
            evidence_id = f"E-{item.dimension.value}"
            evidence.append(_evidence(evidence_id, item.excerpt, anchor, request.manuscript))
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
                    f"- page_image_sha256: {request.page_image_sha256}\n"
                    f"- width_px: {request.width_px}\n"
                    f"- height_px: {request.height_px}\n"
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ]
        judgment = f"visual-page-{request.page}"
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
            if finding.page != request.page or finding.page_image_sha256 != request.page_image_sha256:
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
