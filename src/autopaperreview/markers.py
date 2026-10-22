"""Compact in-paper evidence markers for the review body.

External retrieved records stay out of the body. The body cites an in-paper
record as `[E3, p.5]`, and the marker index maps that id to the issue or claim
that uses it. Excerpts are kept for PDF search and are not rendered here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import AnchorKind, ReviewPackage

_PAGE = re.compile(r"\b(?:pp?|pages?)\.?\s*(\d+)\b", re.IGNORECASE)


@dataclass
class EvidenceMarker:
    evidence_id: str
    page: int | None
    external: bool
    cite: str
    anchor: str
    excerpt: str | None = None
    bbox: list[float] | None = None
    pointers: dict[str, str] = field(default_factory=dict)

    def with_page(self, page: int | None) -> EvidenceMarker:
        return EvidenceMarker(
            evidence_id=self.evidence_id,
            page=page,
            external=self.external,
            cite=cite_text(self.evidence_id, page),
            anchor=self.anchor,
            excerpt=self.excerpt,
            bbox=self.bbox,
            pointers=dict(self.pointers),
        )


def cite_text(evidence_id: str, page: int | None) -> str:
    if page:
        return f"{evidence_id}, p.{page}"
    return evidence_id


def marker_anchor(evidence_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", evidence_id).strip("-").lower()
    return f"marker-{slug}"


def page_from_text(*parts: str | None) -> int | None:
    for part in parts:
        if not part:
            continue
        match = _PAGE.search(part)
        if match:
            return int(match.group(1))
    return None


def _is_external(package: ReviewPackage, source_id: str, record_anchor_kind: AnchorKind | None, retrieval_query: str | None) -> bool:
    if record_anchor_kind == AnchorKind.external_snapshot:
        return True
    if retrieval_query:
        return True
    source = next((item for item in package.sources if item.id == source_id), None)
    if source is None:
        return False
    return source.kind in {"retrieved-abstract", "retrieved", "snapshot", "external"}


def build_markers(package: ReviewPackage) -> dict[str, EvidenceMarker]:
    """One marker per evidence record. External records are flagged and not cited in the body."""
    issue_pages: dict[str, int] = {}
    for issue in package.issues:
        page = None
        if issue.anchor is not None and issue.anchor.page:
            page = issue.anchor.page
        else:
            page = page_from_text(issue.location, issue.anchor.display if issue.anchor else None)
        if page is None:
            continue
        for evidence_id in issue.evidence_ids:
            issue_pages.setdefault(evidence_id, page)
    markers: dict[str, EvidenceMarker] = {}
    for record in package.evidence:
        anchor = record.anchor
        page = anchor.page if anchor is not None and anchor.page else None
        if page is None:
            page = page_from_text(
                record.locator,
                anchor.display if anchor is not None else None,
            )
        if page is None:
            page = issue_pages.get(record.id)
        excerpt = (record.excerpt or (anchor.excerpt if anchor is not None else None) or "").strip() or None
        bbox = list(anchor.bbox) if anchor is not None and anchor.bbox else None
        external = _is_external(
            package,
            record.source_id,
            anchor.kind if anchor is not None else None,
            record.retrieval_query,
        )
        markers[record.id] = EvidenceMarker(
            evidence_id=record.id,
            page=page,
            external=external,
            cite=cite_text(record.id, page),
            anchor=marker_anchor(record.id),
            excerpt=excerpt,
            bbox=bbox,
        )
    return markers


def body_citation_ids(package: ReviewPackage) -> list[str]:
    """Evidence ids mentioned by the review body, in first-seen order."""
    ordered: list[str] = []

    def add(ids: list[str]) -> None:
        for evidence_id in ids:
            if evidence_id not in ordered:
                ordered.append(evidence_id)

    for claim in package.claims:
        add(claim.evidence_ids)
    if package.summary is None and not any(True for _ in package.claims):
        pass
    for score in package.dimension_scores:
        add(score.evidence_ids)
    for item in package.venue_conclusions:
        add(item.evidence_ids)
    for issue in package.issues:
        add(issue.evidence_ids)
    return ordered
