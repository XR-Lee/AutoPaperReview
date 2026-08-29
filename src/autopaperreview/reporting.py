"""Deterministic JSON and Markdown report rendering."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import ReviewClaimKind, ReviewIssue, ReviewPackage, Severity


SEVERITY_ORDER = {
    Severity.critical: 0,
    Severity.major: 1,
    Severity.moderate: 2,
    Severity.minor: 3,
    Severity.editorial: 4,
}

CLAIM_SECTION = {
    ReviewClaimKind.summary: "Summary",
    ReviewClaimKind.strength: "Strengths",
    ReviewClaimKind.weakness: "Weaknesses",
    ReviewClaimKind.question: "Questions",
    ReviewClaimKind.comment: "Detailed Comments",
}


def sort_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.id))


def _evidence_suffix(evidence_ids: list[str]) -> str:
    if not evidence_ids:
        return ""
    return " [" + ", ".join(evidence_ids) + "]"


def render_markdown(package: ReviewPackage, *, language: str) -> str:
    counts = Counter(issue.severity.value for issue in package.issues)
    lines = [
        f"# Review Report: {package.project_id}",
        "",
        f"- Source SHA-256: `{package.manuscript.sha256}`",
        f"- Issues: {len(package.issues)}",
        f"- Critical: {counts['critical']}",
        f"- Major: {counts['major']}",
        f"- Moderate: {counts['moderate']}",
        f"- Minor: {counts['minor']}",
        f"- Editorial: {counts['editorial']}",
        "",
    ]

    summary_text = None
    if package.summary:
        summary_text = package.summary.resolve(language)
    else:
        summaries = [claim for claim in package.claims if claim.kind == ReviewClaimKind.summary]
        if summaries:
            summary_text = " ".join(
                f"{claim.text.resolve(language)}{_evidence_suffix(claim.evidence_ids)}"
                for claim in summaries
            )
    if summary_text:
        lines.extend(["## Summary", "", summary_text, ""])

    if package.recommendation:
        lines.extend(["## Recommendation", "", package.recommendation.resolve(language), ""])

    if package.dimension_scores:
        lines.extend(["## Dimension Scores", ""])
        for score in sorted(package.dimension_scores, key=lambda item: item.dimension.value):
            rationale = score.rationale.resolve(language) if score.rationale else ""
            detail = f" — {rationale}" if rationale else ""
            lines.append(
                f"- **{score.dimension.value}:** {score.score:.1f}"
                f"{_evidence_suffix(score.evidence_ids)}{detail}"
            )
        lines.append("")

    lines.extend(["## Overall Assessment", ""])
    if package.overall_score is None:
        lines.extend(
            [
                "No overall score is assigned. AutoPaperReview does not emit a raw LLM 0–10 "
                "by default, and this repository does not ship a fitted ICLR regression.",
                "",
            ]
        )
    else:
        overall = package.overall_score
        lines.extend(
            [
                f"- **Score:** {overall.value:.2f} (scale {overall.scale_min:g}–{overall.scale_max:g})",
                f"- **Method:** `{overall.method.value}`",
                f"- **Scorer:** {overall.scorer or 'unspecified'}",
                f"- **Coefficient set:** {overall.coefficient_set or 'none'}",
                f"- **Model:** {overall.model_identifier or 'none'}",
                f"- **Prompt hash:** {overall.prompt_hash or 'none'}",
                f"- **Evidence:** {', '.join(overall.evidence_ids) or 'none'}",
            ]
        )
        if overall.notes:
            lines.append(f"- **Notes:** {overall.notes}")
        lines.append("")

    claims_by_kind = {kind: [] for kind in CLAIM_SECTION}
    for claim in package.claims:
        claims_by_kind[claim.kind].append(claim)

    if claims_by_kind[ReviewClaimKind.strength]:
        lines.extend(["## Strengths", ""])
        for claim in claims_by_kind[ReviewClaimKind.strength]:
            lines.append(f"- {claim.text.resolve(language)}{_evidence_suffix(claim.evidence_ids)}")
        lines.append("")
    else:
        strengths = package.strengths.get(language) or package.strengths.get("en") or []
        if strengths:
            lines.extend(["## Strengths", ""])
            lines.extend(f"- {value}" for value in strengths)
            lines.append("")

    if claims_by_kind[ReviewClaimKind.weakness]:
        lines.extend(["## Weaknesses", ""])
        for claim in claims_by_kind[ReviewClaimKind.weakness]:
            lines.append(f"- {claim.text.resolve(language)}{_evidence_suffix(claim.evidence_ids)}")
        lines.append("")

    if claims_by_kind[ReviewClaimKind.question]:
        lines.extend(["## Questions", ""])
        for claim in claims_by_kind[ReviewClaimKind.question]:
            lines.append(f"- {claim.text.resolve(language)}{_evidence_suffix(claim.evidence_ids)}")
        lines.append("")

    if claims_by_kind[ReviewClaimKind.comment]:
        lines.extend(["## Detailed Comments", ""])
        for claim in claims_by_kind[ReviewClaimKind.comment]:
            lines.append(f"- {claim.text.resolve(language)}{_evidence_suffix(claim.evidence_ids)}")
        lines.append("")

    current_severity: Severity | None = None
    for issue in sort_issues(package.issues):
        if issue.severity != current_severity:
            current_severity = issue.severity
            lines.extend([f"## {issue.severity.value.title()} Issues", ""])
        routes = ", ".join(issue.route_ids) or "unassigned"
        sources = ", ".join(issue.source_ids) or "manuscript only"
        evidence_ids = ", ".join(issue.evidence_ids) or "none"
        lines.extend(
            [
                f"### {issue.id} {issue.title.resolve(language)}",
                "",
                f"- **Location:** {issue.location}",
                f"- **Confidence:** {issue.confidence:.2f}",
                f"- **Routes:** {routes}",
                f"- **Sources:** {sources}",
                f"- **Evidence IDs:** {evidence_ids}",
                "",
                f"**Evidence.** {issue.evidence.resolve(language)}",
                "",
                f"**Impact.** {issue.impact.resolve(language)}",
                "",
                f"**Required action.** {issue.required_action.resolve(language)}",
                "",
            ]
        )

    if package.related_work_queries:
        lines.extend(["## Related-Work Queries", ""])
        for query in package.related_work_queries:
            lines.append(f"- `{query.id}` ({query.perspective.value}): {query.query}")
        lines.append("")

    if package.retrieved_snapshots:
        lines.extend(["## Retrieved Sources", ""])
        for snapshot in package.retrieved_snapshots:
            arxiv = f"; arXiv {snapshot.arxiv_id}" if snapshot.arxiv_id else ""
            lines.append(
                f"- `{snapshot.id}` {snapshot.title} "
                f"({snapshot.content_kind.value}{arxiv}; {snapshot.retrieved_at.date().isoformat()}; "
                f"queries {', '.join(snapshot.query_ids)})"
            )
        lines.append("")

    gate = package.acceptance_gate.get(language) or package.acceptance_gate.get("en") or []
    if gate:
        lines.extend(["## Acceptance Gate", ""])
        lines.extend(f"- [ ] {value}" for value in gate)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_package_json(package: ReviewPackage, path: Path) -> None:
    path.write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
