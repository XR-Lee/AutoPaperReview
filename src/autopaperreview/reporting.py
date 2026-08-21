"""Deterministic JSON and Markdown report rendering."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import ReviewIssue, ReviewPackage, Severity


SEVERITY_ORDER = {
    Severity.critical: 0,
    Severity.major: 1,
    Severity.moderate: 2,
    Severity.minor: 3,
    Severity.editorial: 4,
}


def sort_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.id))


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
    if package.recommendation:
        lines.extend(["## Recommendation", "", package.recommendation.resolve(language), ""])

    strengths = package.strengths.get(language) or package.strengths.get("en") or []
    if strengths:
        lines.extend(["## Strengths", ""])
        lines.extend(f"- {value}" for value in strengths)
        lines.append("")

    current_severity: Severity | None = None
    for issue in sort_issues(package.issues):
        if issue.severity != current_severity:
            current_severity = issue.severity
            lines.extend([f"## {issue.severity.value.title()} Issues", ""])
        routes = ", ".join(issue.route_ids) or "unassigned"
        sources = ", ".join(issue.source_ids) or "manuscript only"
        lines.extend(
            [
                f"### {issue.id} {issue.title.resolve(language)}",
                "",
                f"- **Location:** {issue.location}",
                f"- **Confidence:** {issue.confidence:.2f}",
                f"- **Routes:** {routes}",
                f"- **Sources:** {sources}",
                "",
                f"**Evidence.** {issue.evidence.resolve(language)}",
                "",
                f"**Impact.** {issue.impact.resolve(language)}",
                "",
                f"**Required action.** {issue.required_action.resolve(language)}",
                "",
            ]
        )

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
