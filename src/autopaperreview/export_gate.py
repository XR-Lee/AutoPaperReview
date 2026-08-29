"""Refuse to export a review package that fails minimum audit budgets."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import ReviewIssue, ReviewPackage, RouteKind, Severity


class ExportGateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_issues: int = Field(default=0, ge=0)
    min_claims: int = Field(default=0, ge=0)
    min_anchored_issues: int = Field(default=0, ge=0)
    min_literature_snapshots: int = Field(default=0, ge=0)
    min_novelty_assessments: int = Field(default=0, ge=0)
    require_excerpt_on_snapshots: bool = False
    require_ledger: bool = False
    require_integrity: bool = False
    require_literature_if_literature_route: bool = False
    require_anchor_on_major: bool = False


def _is_major(issue: ReviewIssue) -> bool:
    return issue.severity in {Severity.critical, Severity.major}


def export_gate_errors(
    package: ReviewPackage,
    params: ExportGateParams,
    *,
    route_kinds: Sequence[RouteKind] = (),
) -> list[str]:
    errors: list[str] = []
    if len(package.issues) < params.min_issues:
        errors.append(f"export gate requires at least {params.min_issues} issues")
    if len(package.claims) < params.min_claims:
        errors.append(f"export gate requires at least {params.min_claims} claims")
    anchored = sum(1 for issue in package.issues if issue.anchor is not None)
    if anchored < params.min_anchored_issues:
        errors.append(f"export gate requires at least {params.min_anchored_issues} anchored issues")
    if len(package.retrieved_snapshots) < params.min_literature_snapshots:
        errors.append(
            f"export gate requires at least {params.min_literature_snapshots} retrieved snapshots"
        )
    if len(package.novelty_assessments) < params.min_novelty_assessments:
        errors.append(
            f"export gate requires at least {params.min_novelty_assessments} novelty assessments"
        )
    if params.require_excerpt_on_snapshots:
        missing = [item.id for item in package.retrieved_snapshots if not (item.excerpt and item.excerpt.strip())]
        if missing:
            errors.append(f"export gate requires snapshot excerpts; missing: {missing}")
    if params.require_ledger and not package.ledger_claims:
        errors.append("export gate requires a claim-evidence-risk ledger")
    if params.require_integrity and not package.integrity_records:
        errors.append("export gate requires integrity records")
    if params.require_literature_if_literature_route:
        has_literature_route = RouteKind.literature in route_kinds or any(
            route.kind == RouteKind.literature for route in package.routes
        )
        if has_literature_route and not package.retrieved_snapshots:
            errors.append("export gate requires snapshots when a literature route is declared")
    if params.require_anchor_on_major:
        missing = [issue.id for issue in package.issues if _is_major(issue) and issue.anchor is None]
        if missing:
            errors.append(f"export gate requires anchors on major/critical issues; missing: {missing}")
    if not package.issues and not package.claims and (
        params.min_issues or params.min_claims or params.require_ledger
    ):
        errors.append("export gate refuses an empty review package")
    return errors
