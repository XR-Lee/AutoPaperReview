"""Refuse to export a review package that fails minimum audit budgets."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .figures import caption_inventory
from .models import OverallScoreMethod, ReviewIssue, ReviewPackage, RouteKind, Severity
from .readability import first_read_gate_errors, is_major
from .rubrics import COEFFICIENT_SET, DIMENSION_BANDS
from .visual import finding_matches_caption


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
    min_figure_citations: int = Field(default=0, ge=0)
    require_figures_if_visual_route: bool = False
    require_contribution_coverage: bool = False
    require_first_read_on_major: bool = False
    require_quote_on_major: bool = False
    min_explanation_chars: int = Field(default=0, ge=0)
    require_rubric_bands: bool = False
    require_rating: bool = False
    require_fatal_class_on_critical: bool = False
    require_decision_relevant_on_major: bool = False
    require_page_image_on_visual: bool = False
    require_related_work_confirmation: bool = False


def _is_major(issue: ReviewIssue) -> bool:
    return is_major(issue)


def _uncovered_contributions(package: ReviewPackage) -> list[str]:
    listed = [
        item for item in package.ledger_claims if item.metadata.get("kind") == "listed_contribution"
    ]
    if not listed:
        return []
    blob = " ".join(
        [
            *[issue.location for issue in package.issues],
            *[issue.evidence.primary for issue in package.issues],
            *[issue.title.primary for issue in package.issues],
        ]
    ).lower()
    missing: list[str] = []
    for item in listed:
        index = item.metadata.get("index")
        markers = [item.id.lower(), f"contribution {index}", f"contribution ({index})"]
        if not any(marker in blob for marker in markers):
            missing.append(item.id)
    return missing


def _cited_figures(package: ReviewPackage) -> set[str]:
    parts: list[str] = []
    for issue in package.issues:
        parts.append(issue.location)
        parts.append(issue.evidence.primary)
        if issue.anchor is not None:
            parts.append(issue.anchor.display)
    for record in package.evidence:
        parts.append(record.locator)
        parts.append(record.claim)
        if record.excerpt:
            parts.append(record.excerpt)
    return set(caption_inventory("\n".join(parts)))


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
    cited_figures = _cited_figures(package)
    required_figures = params.min_figure_citations
    if params.require_figures_if_visual_route and RouteKind.visual in route_kinds:
        required_figures = max(required_figures, 1)
    if len(cited_figures) < required_figures:
        errors.append(
            f"export gate requires at least {required_figures} distinct figure/table citations; "
            f"found {sorted(cited_figures) or 'none'}"
        )
    if params.require_contribution_coverage:
        missing_contrib = _uncovered_contributions(package)
        if missing_contrib:
            errors.append(
                "export gate requires an issue covering each listed contribution; "
                f"missing: {missing_contrib}"
            )
    if params.require_rubric_bands:
        for score in package.dimension_scores:
            if not score.clause_id:
                errors.append(f"export gate requires a rubric clause on {score.dimension.value}")
            elif float(score.score) not in DIMENSION_BANDS:
                errors.append(f"export gate requires an anchored band on {score.dimension.value}")
    if params.require_rating:
        score = package.overall_score
        if (
            score is None
            or score.method != OverallScoreMethod.declared_formula
            or score.coefficient_set != COEFFICIENT_SET
        ):
            errors.append(f"export gate requires declared formula {COEFFICIENT_SET}")
    if params.require_fatal_class_on_critical:
        missing = [
            issue.id
            for issue in package.issues
            if issue.severity == Severity.critical and issue.fatal_class is None
        ]
        if missing:
            errors.append(f"export gate requires fatal_class on critical issues; missing: {missing}")
    if params.require_decision_relevant_on_major:
        majors = [issue for issue in package.issues if issue.severity in {Severity.critical, Severity.major}]
        if majors and not any(issue.decision_relevant for issue in package.issues):
            errors.append("export gate requires a decision-relevant issue when a major or critical issue exists")
    if params.require_page_image_on_visual and RouteKind.visual in route_kinds:
        cited = _cited_figures(package)
        covered = {item.figure_id for item in package.visual_findings if item.figure_id}
        missing = sorted(cited - covered)
        if missing:
            errors.append(
                "export gate requires a page-image visual finding for each cited figure; "
                f"missing: {missing}"
            )
        paraphrases = [item.id for item in package.visual_findings if finding_matches_caption(item)]
        if paraphrases:
            errors.append(f"export gate rejects caption paraphrases: {paraphrases}")
        unhashed = [item.id for item in package.visual_findings if not item.page_image_sha256]
        if unhashed:
            errors.append(f"export gate requires page image hashes; missing: {unhashed}")
    if params.require_related_work_confirmation:
        related = package.metadata.get("related_work")
        if isinstance(related, dict) and related.get("confirmation") == "awaiting_confirmation":
            errors.append(
                "export gate requires related-work confirmation; status is awaiting_confirmation"
            )
    errors.extend(
        first_read_gate_errors(
            package,
            require_first_read_on_major=params.require_first_read_on_major,
            require_quote_on_major=params.require_quote_on_major,
            min_explanation_chars=params.min_explanation_chars,
        )
    )
    if not package.issues and not package.claims and (
        params.min_issues or params.min_claims or params.require_ledger
    ):
        errors.append("export gate refuses an empty review package")
    return errors
