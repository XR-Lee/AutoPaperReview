"""Deterministic JSON and Markdown report rendering."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .i18n import ensure_package_languages, normalize_languages, require_language, translate_text
from .models import LocalizedText, ReviewClaimKind, ReviewIssue, ReviewPackage, Severity


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

SECTION_ZH = {
    "Summary": "摘要",
    "Recommendation": "审稿建议",
    "Dimension Scores": "维度评分",
    "Overall Assessment": "总体评价",
    "Strengths": "优点",
    "Weaknesses": "不足",
    "Questions": "问题",
    "Detailed Comments": "详细评论",
    "Related-Work Queries": "相关工作查询",
    "Retrieved Sources": "检索到的来源",
    "Acceptance Gate": "接受条件",
}

SEVERITY_ZH = {
    Severity.critical: "严重问题",
    Severity.major: "主要问题",
    Severity.moderate: "中等问题",
    Severity.minor: "次要问题",
    Severity.editorial: "编辑问题",
}

NO_OVERALL_EN = (
    "No overall score is assigned. AutoPaperReview does not emit a raw LLM 0–10 "
    "by default, and this repository does not ship a fitted ICLR regression."
)
NO_OVERALL_ZH = (
    "未给出总体分数。AutoPaperReview 默认不输出未经校准的 LLM 0–10 分，本仓库也不附带拟合的 ICLR 回归。"
)


def sort_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.id))


def _evidence_suffix(evidence_ids: list[str]) -> str:
    if not evidence_ids:
        return ""
    return " [" + ", ".join(evidence_ids) + "]"


def _heading(title: str, languages: Sequence[str]) -> str:
    chinese = SECTION_ZH.get(title)
    if len(languages) == 1 or not chinese:
        return f"## {title}"
    return f"## {title} / {chinese}"


def _language_blocks(languages: Sequence[str], bodies: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    for language in languages:
        lines.append(f"### {language}")
        lines.append("")
        body = bodies.get(language) or []
        lines.extend(body)
        if body and body[-1] != "":
            lines.append("")
    return lines


def _prose_items(
    text: LocalizedText,
    languages: Sequence[str],
    *,
    field: str,
    suffix: str = "",
) -> list[str]:
    return [f"- **{language}:** {require_language(text, language, field=field)}{suffix}" for language in languages]


def render_markdown(
    package: ReviewPackage,
    *,
    language: str | None = None,
    languages: Sequence[str] | None = None,
    bilingual: bool = False,
    fill_missing: bool = True,
) -> str:
    langs = normalize_languages(
        language=language,
        languages=languages,
        bilingual=bilingual,
        default_language="en",
    )
    if len(langs) == 1:
        return _render_monolingual(package, langs[0])
    prepared = ensure_package_languages(package, langs) if fill_missing else package
    return _render_multilingual(prepared, langs)


def _render_monolingual(package: ReviewPackage, language: str) -> str:
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
    summaries = [claim for claim in package.claims if claim.kind == ReviewClaimKind.summary]
    if summaries:
        summary_text = " ".join(
            f"{claim.text.resolve(language)}{_evidence_suffix(claim.evidence_ids)}"
            for claim in summaries
        )
    elif package.summary:
        summary_text = package.summary.resolve(language)
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
                NO_OVERALL_EN,
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


def _render_multilingual(package: ReviewPackage, languages: Sequence[str]) -> str:
    counts = Counter(issue.severity.value for issue in package.issues)
    lines = [
        f"# Review Report: {package.project_id}",
        "",
        f"- Source SHA-256: `{package.manuscript.sha256}`",
        f"- Issues: {len(package.issues)}",
        f"- Languages: {', '.join(languages)}",
        f"- Critical: {counts['critical']}",
        f"- Major: {counts['major']}",
        f"- Moderate: {counts['moderate']}",
        f"- Minor: {counts['minor']}",
        f"- Editorial: {counts['editorial']}",
        "",
    ]

    claims_by_kind = {kind: [] for kind in CLAIM_SECTION}
    for claim in package.claims:
        claims_by_kind[claim.kind].append(claim)

    summaries = claims_by_kind[ReviewClaimKind.summary]
    if summaries or package.summary:
        lines.extend([_heading("Summary", languages), ""])
        bodies: dict[str, list[str]] = {}
        for language in languages:
            if summaries:
                bodies[language] = [
                    " ".join(
                        f"{require_language(claim.text, language, field=claim.id)}"
                        f"{_evidence_suffix(claim.evidence_ids)}"
                        for claim in summaries
                    )
                ]
            elif package.summary:
                bodies[language] = [require_language(package.summary, language, field="summary")]
        lines.extend(_language_blocks(languages, bodies))

    if package.recommendation:
        lines.extend([_heading("Recommendation", languages), ""])
        lines.extend(
            _language_blocks(
                languages,
                {
                    language: [require_language(package.recommendation, language, field="recommendation")]
                    for language in languages
                },
            )
        )

    if package.dimension_scores:
        lines.extend([_heading("Dimension Scores", languages), ""])
        for score in sorted(package.dimension_scores, key=lambda item: item.dimension.value):
            lines.append(
                f"- **{score.dimension.value}:** {score.score:.1f}{_evidence_suffix(score.evidence_ids)}"
            )
            if score.rationale:
                for language in languages:
                    rationale = require_language(
                        score.rationale, language, field=f"{score.dimension.value}.rationale"
                    )
                    lines.append(f"  - **{language}:** {rationale}")
        lines.append("")

    lines.extend([_heading("Overall Assessment", languages), ""])
    if package.overall_score is None:
        overall_text = {
            "en": NO_OVERALL_EN,
            "zh-Hans": NO_OVERALL_ZH,
        }
        bodies = {}
        for language in languages:
            if language in overall_text:
                bodies[language] = [overall_text[language]]
            else:
                bodies[language] = [translate_text(NO_OVERALL_EN, source="en", target=language)]
        lines.extend(_language_blocks(languages, bodies))
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

    def _claim_section(kind: ReviewClaimKind, title: str) -> None:
        claims = claims_by_kind[kind]
        if not claims:
            return
        lines.append(_heading(title, languages))
        lines.append("")
        lines.extend(
            _language_blocks(
                languages,
                {
                    language: [
                        f"- {require_language(claim.text, language, field=claim.id)}"
                        f"{_evidence_suffix(claim.evidence_ids)}"
                        for claim in claims
                    ]
                    for language in languages
                },
            )
        )

    if claims_by_kind[ReviewClaimKind.strength]:
        _claim_section(ReviewClaimKind.strength, "Strengths")
    else:
        if any(package.strengths.get(language) for language in languages):
            lines.extend([_heading("Strengths", languages), ""])
            lines.extend(
                _language_blocks(
                    languages,
                    {
                        language: [f"- {value}" for value in package.strengths.get(language, [])]
                        for language in languages
                    },
                )
            )

    _claim_section(ReviewClaimKind.weakness, "Weaknesses")
    _claim_section(ReviewClaimKind.question, "Questions")
    _claim_section(ReviewClaimKind.comment, "Detailed Comments")

    current_severity: Severity | None = None
    for issue in sort_issues(package.issues):
        if issue.severity != current_severity:
            current_severity = issue.severity
            english_heading = f"{issue.severity.value.title()} Issues"
            lines.extend([f"## {english_heading} / {SEVERITY_ZH[issue.severity]}", ""])
        routes = ", ".join(issue.route_ids) or "unassigned"
        sources = ", ".join(issue.source_ids) or "manuscript only"
        evidence_ids = ", ".join(issue.evidence_ids) or "none"
        lines.extend(
            [
                f"### {issue.id}",
                "",
                f"- **Location:** {issue.location}",
                f"- **Confidence:** {issue.confidence:.2f}",
                f"- **Routes:** {routes}",
                f"- **Sources:** {sources}",
                f"- **Evidence IDs:** {evidence_ids}",
                "",
            ]
        )
        lines.extend(_prose_items(issue.title, languages, field=f"{issue.id}.title"))
        lines.append("")
        lines.append("**Evidence / 证据.**")
        lines.extend(_prose_items(issue.evidence, languages, field=f"{issue.id}.evidence"))
        lines.append("")
        lines.append("**Impact / 影响.**")
        lines.extend(_prose_items(issue.impact, languages, field=f"{issue.id}.impact"))
        lines.append("")
        lines.append("**Required action / 需采取的行动.**")
        lines.extend(_prose_items(issue.required_action, languages, field=f"{issue.id}.required_action"))
        lines.append("")

    if package.related_work_queries:
        lines.extend([_heading("Related-Work Queries", languages), ""])
        for query in package.related_work_queries:
            lines.append(f"- `{query.id}` ({query.perspective.value}): {query.query}")
        lines.append("")

    if package.retrieved_snapshots:
        lines.extend([_heading("Retrieved Sources", languages), ""])
        for snapshot in package.retrieved_snapshots:
            arxiv = f"; arXiv {snapshot.arxiv_id}" if snapshot.arxiv_id else ""
            lines.append(
                f"- `{snapshot.id}` {snapshot.title} "
                f"({snapshot.content_kind.value}{arxiv}; {snapshot.retrieved_at.date().isoformat()}; "
                f"queries {', '.join(snapshot.query_ids)})"
            )
        lines.append("")

    if any(package.acceptance_gate.get(language) for language in languages):
        lines.extend([_heading("Acceptance Gate", languages), ""])
        lines.extend(
            _language_blocks(
                languages,
                {
                    language: [f"- [ ] {value}" for value in package.acceptance_gate.get(language, [])]
                    for language in languages
                },
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def write_package_json(package: ReviewPackage, path: Path) -> None:
    path.write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
