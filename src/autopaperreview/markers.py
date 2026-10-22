"""Compact in-paper evidence markers for the review body.

External retrieved records stay out of the body. The body cites an in-paper
record as `[E3, p.5]`, and the marker index maps that id to the issue or claim
that uses it. Excerpts are kept for PDF search and are not rendered here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import AnchorKind, ReviewClaimKind, ReviewPackage, Severity

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
    label: str = ""

    def shown(self) -> str:
        return self.label or self.evidence_id

    def with_page(self, page: int | None) -> EvidenceMarker:
        return EvidenceMarker(
            evidence_id=self.evidence_id,
            page=page,
            external=self.external,
            cite=cite_text(self.shown(), page),
            anchor=self.anchor,
            excerpt=self.excerpt,
            bbox=self.bbox,
            pointers=dict(self.pointers),
            label=self.label,
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


def assign_display_numbers(
    package: ReviewPackage,
    markers: dict[str, EvidenceMarker],
) -> dict[str, EvidenceMarker]:
    """Number in-paper citations E1..En in the order the body cites them.

    The raw evidence id stays on `evidence_id` for the appendix. `label` and
    `cite` are what the body and the annotated PDF show.
    """
    updated = dict(markers)
    number = 1
    for evidence_id in body_citation_ids(package):
        marker = updated.get(evidence_id)
        if marker is None or marker.external:
            continue
        label = f"E{number}"
        updated[evidence_id] = EvidenceMarker(
            evidence_id=marker.evidence_id,
            page=marker.page,
            external=False,
            cite=cite_text(label, marker.page),
            anchor=marker_anchor(label),
            excerpt=marker.excerpt,
            bbox=marker.bbox,
            pointers=dict(marker.pointers),
            label=label,
        )
        number += 1
    return updated


_SEVERITY_RANK = {
    Severity.critical: 0,
    Severity.major: 1,
    Severity.moderate: 2,
    Severity.minor: 3,
    Severity.editorial: 4,
}


def body_citation_ids(package: ReviewPackage) -> list[str]:
    """Evidence ids the concise body cites, in the order that body cites them."""
    ordered: list[str] = []

    def add(ids: list[str]) -> None:
        for evidence_id in ids:
            if evidence_id not in ordered:
                ordered.append(evidence_id)

    def claims(kind: ReviewClaimKind) -> None:
        for claim in package.claims:
            if claim.kind == kind:
                add(claim.evidence_ids)

    claims(ReviewClaimKind.summary)
    claims(ReviewClaimKind.strength)
    claims(ReviewClaimKind.weakness)
    issues = sorted(package.issues, key=lambda issue: (_SEVERITY_RANK[issue.severity], issue.id))
    for issue in issues:
        if issue.severity == Severity.editorial:
            continue
        add(issue.evidence_ids)
    claims(ReviewClaimKind.question)
    for score in sorted(package.dimension_scores, key=lambda item: item.dimension.value):
        add(score.evidence_ids)
    return ordered
