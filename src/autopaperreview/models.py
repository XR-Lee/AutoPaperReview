"""Versioned canonical models for review findings, evidence, and runs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Severity(str, Enum):
    critical = "critical"
    major = "major"
    moderate = "moderate"
    minor = "minor"
    editorial = "editorial"


class RouteKind(str, Enum):
    prompt = "prompt"
    deterministic = "deterministic"
    executable = "executable"
    literature = "literature"
    visual = "visual"
    human = "human"


class NetworkPolicy(str, Enum):
    deny = "deny"
    metadata_only = "metadata_only"
    allow = "allow"


class StageStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    skipped = "skipped"
    failed = "failed"


class ReviewDecision(str, Enum):
    open = "open"
    accepted = "accepted"
    rejected = "rejected"
    superseded = "superseded"


class LocalizedText(StrictModel):
    primary: str = Field(min_length=1)
    language: str = Field(default="en", min_length=2)
    translations: dict[str, str] = Field(default_factory=dict)

    @field_validator("primary")
    @classmethod
    def strip_primary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("localized text cannot be blank")
        return value

    def resolve(self, language: str | None = None) -> str:
        if language is None or language == self.language:
            return self.primary
        return self.translations.get(language, self.primary)


class Artifact(StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    role: str = Field(min_length=1)
    media_type: str | None = None
    generated_by: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewRoute(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1)
    kind: RouteKind
    prompt_version: str | None = None
    model: str | None = None
    manuscript_access: bool = True
    network_access: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceRecord(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    kind: str = Field(min_length=1)
    title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    doi: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    accessed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    source_id: str
    locator: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    excerpt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retrieval_query: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewIssue(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    severity: Severity
    category: str = Field(min_length=1)
    title: LocalizedText
    location: str = Field(min_length=1)
    evidence: LocalizedText
    impact: LocalizedText
    required_action: LocalizedText
    confidence: float = Field(ge=0.0, le=1.0)
    route_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    consensus_key: str | None = None
    support_count: int = Field(default=1, ge=1)
    decision: ReviewDecision = ReviewDecision.open
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("route_ids", "source_ids", "evidence_ids", "tags")
    @classmethod
    def unique_strings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ReviewPackage(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    project_id: str = Field(min_length=1)
    manuscript: Artifact
    recommendation: LocalizedText | None = None
    routes: list[ReviewRoute] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    strengths: dict[str, list[str]] = Field(default_factory=dict)
    acceptance_gate: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "ReviewPackage":
        collections = {
            "issue": [issue.id for issue in self.issues],
            "route": [route.id for route in self.routes],
            "source": [source.id for source in self.sources],
            "evidence": [item.id for item in self.evidence],
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} IDs must be unique")

        route_ids = {route.id for route in self.routes}
        source_ids = {source.id for source in self.sources}
        evidence_ids = {item.id for item in self.evidence}

        missing = sorted({rid for issue in self.issues for rid in issue.route_ids} - route_ids)
        if missing:
            raise ValueError(f"issues reference unknown routes: {missing}")
        missing = sorted({sid for issue in self.issues for sid in issue.source_ids} - source_ids)
        if missing:
            raise ValueError(f"issues reference unknown sources: {missing}")
        evidence_missing = sorted({item.source_id for item in self.evidence} - source_ids)
        if evidence_missing:
            raise ValueError(f"evidence references unknown sources: {evidence_missing}")
        missing = sorted({eid for issue in self.issues for eid in issue.evidence_ids} - evidence_ids)
        if missing:
            raise ValueError(f"issues reference unknown evidence: {missing}")
        return self


class StageRecord(StrictModel):
    id: str
    type: str
    status: StageStatus
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attempt: int = Field(default=1, ge=1)
    outputs: list[Artifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class RunManifest(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    framework_version: str
    run_id: str
    project_id: str
    config_path: str
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_snapshot: Artifact | None = None
    source: Artifact
    network_policy: NetworkPolicy
    started_at: datetime
    ended_at: datetime | None = None
    stages: list[StageRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def stage(self, stage_id: str) -> StageRecord | None:
        return next((stage for stage in self.stages if stage.id == stage_id), None)

    @model_validator(mode="after")
    def validate_stage_ids(self) -> "RunManifest":
        stage_ids = [stage.id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("stage IDs must be unique")
        return self
