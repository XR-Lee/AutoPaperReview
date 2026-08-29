"""Local literature-grounding helpers and an optional retrieval adapter contract.

The core generates multi-perspective search queries from manuscript text and
records retrieved source snapshots as artifacts. Live search is optional and
must be declared. Tavily, OpenAlex, and PaperQA are not core dependencies.
"""

from __future__ import annotations

import importlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    EvidenceRecord,
    QueryPerspective,
    RelatedWorkQuery,
    RetrievedSourceSnapshot,
    SnapshotContentKind,
    SourceRecord,
)


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manuscript_sha256: str
    queries: list[RelatedWorkQuery]
    network_scope: str = "metadata"


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[RetrievedSourceSnapshot] = Field(default_factory=list)
    raw_artifact_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LiteratureRetriever(Protocol):
    def retrieve(self, request: RetrievalRequest, scratch: Path) -> RetrievalResult:
        """Return snapshot records. Write any raw provider payloads into scratch."""


_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "proposed",
        "system",
        "that",
        "the",
        "these",
        "this",
        "those",
        "to",
        "was",
        "we",
        "were",
        "with",
    }
)
_HEADING = re.compile(
    r"^(title|abstract|introduction|methods|results|conclusion|references)\b[:\s]*",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")


def parse_manuscript_text(text: str) -> dict[str, str]:
    lines = [line.rstrip() for line in text.splitlines()]
    title = ""
    sections: dict[str, list[str]] = {}
    current = "body"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        titled = re.match(r"^title\s*:\s*(.+)$", stripped, re.IGNORECASE)
        if titled and not title:
            title = titled.group(1).strip()
            continue
        heading = _HEADING.match(stripped)
        if heading:
            current = heading.group(1).lower()
            remainder = stripped[heading.end() :].strip()
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(remainder)
            continue
        if not title and current == "body":
            title = stripped
            continue
        sections.setdefault(current, []).append(stripped)
    return {
        "title": title or "Untitled manuscript",
        "abstract": " ".join(sections.get("abstract", [])),
        "methods": " ".join(sections.get("methods", [])),
        "body": " ".join(sections.get("body", [])),
    }


def distinctive_terms(*parts: str, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for part in parts:
        for token in _TOKEN.findall(part.lower()):
            if token in _STOPWORDS or token in seen:
                continue
            seen.append(token)
            if len(seen) >= limit:
                return seen
    return seen


def generate_related_work_queries(
    manuscript_text: str,
    *,
    max_per_perspective: int = 1,
) -> list[RelatedWorkQuery]:
    if max_per_perspective < 1:
        raise ValueError("max_per_perspective must be at least 1")
    parsed = parse_manuscript_text(manuscript_text)
    title = parsed["title"]
    terms = distinctive_terms(title, parsed["abstract"], parsed["methods"], parsed["body"])
    problem_terms = " ".join(terms[:4]) or title
    technique_terms = " ".join(terms[:3]) or title
    templates = {
        QueryPerspective.baselines: f"{title} benchmark baseline comparison",
        QueryPerspective.same_problem: f"{title} {problem_terms}".strip(),
        QueryPerspective.related_techniques: f"{title} related methods {technique_terms}".strip(),
    }
    queries: list[RelatedWorkQuery] = []
    for perspective, query in templates.items():
        for index in range(1, max_per_perspective + 1):
            suffix = "" if index == 1 else f" {index}"
            queries.append(
                RelatedWorkQuery(
                    id=f"Q-{perspective.value}-{index}",
                    perspective=perspective,
                    query=f"{query}{suffix}".strip(),
                    generated_from="title+abstract",
                    metadata={"term_count": len(terms)},
                )
            )
    return queries


def load_snapshot_fixture(path: Path) -> list[RetrievedSourceSnapshot]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("snapshots", raw)
    if not isinstance(records, list):
        raise ValueError("snapshot fixture must be a list or an object with a snapshots array")
    return [RetrievedSourceSnapshot.model_validate(item) for item in records]


def snapshots_to_records(
    snapshots: list[RetrievedSourceSnapshot],
    queries: list[RelatedWorkQuery],
) -> tuple[list[SourceRecord], list[EvidenceRecord]]:
    queries_by_id = {query.id: query for query in queries}
    sources: list[SourceRecord] = []
    evidence: list[EvidenceRecord] = []
    for snapshot in snapshots:
        kind = (
            "retrieved-abstract"
            if snapshot.content_kind == SnapshotContentKind.abstract
            else "retrieved-full-text-summary"
        )
        sources.append(
            SourceRecord(
                id=snapshot.id,
                kind=kind,
                title=snapshot.title,
                locator=snapshot.locator,
                doi=snapshot.doi,
                sha256=snapshot.sha256,
                accessed_at=snapshot.retrieved_at,
                metadata={
                    "arxiv_id": snapshot.arxiv_id,
                    "content_kind": snapshot.content_kind.value,
                    "query_ids": snapshot.query_ids,
                    "authors": snapshot.authors,
                },
            )
        )
        retrieval_query = None
        for query_id in snapshot.query_ids:
            if query_id in queries_by_id:
                retrieval_query = queries_by_id[query_id].query
                break
        evidence.append(
            EvidenceRecord(
                id=f"E-{snapshot.id}",
                source_id=snapshot.id,
                locator=snapshot.locator,
                claim=f"{snapshot.content_kind.value} snapshot of {snapshot.title}",
                artifact_sha256=snapshot.sha256,
                retrieval_query=retrieval_query,
                metadata={"snapshot_id": snapshot.id, "arxiv_id": snapshot.arxiv_id},
            )
        )
    return sources, evidence


def load_retriever(spec: str) -> LiteratureRetriever:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not attribute:
        raise ValueError("retriever must be a module:attribute reference")
    module = importlib.import_module(module_name)
    retriever = getattr(module, attribute)
    if isinstance(retriever, type):
        retriever = retriever()
    if not hasattr(retriever, "retrieve"):
        raise TypeError(f"retriever {spec!r} does not provide retrieve()")
    return retriever


def utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
