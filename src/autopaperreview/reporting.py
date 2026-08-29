"""Deterministic JSON and Markdown report rendering."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .i18n import ensure_package_languages, normalize_languages, require_language, translate_text
from .models import (
    LocalizedText,
    ReviewClaimKind,
    ReviewIssue,
    ReviewPackage,
    Severity,
    VenueKind,
)


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
    "Venue Conclusions": "会议与期刊结论",
    "Conferences": "会议",
    "Journals": "期刊",
    "Overall Assessment": "总体评价",
    "Strengths": "优点",
    "Weaknesses": "不足",
    "Questions": "问题",
    "Detailed Comments": "详细评论",
    "Related-Work Queries": "相关工作查询",
    "Retrieved Sources": "检索到的来源",
    "Acceptance Gate": "接受条件",
    "Claim Ledger": "主张台账",
    "Investigation Agenda": "核查议程",
    "Integrity": "完整性核验",
    "Novelty": "新颖性",
}

SEVERITY_ZH = {
    Severity.critical: "严重问题",
    Severity.major: "主要问题",
    Severity.moderate: "中等问题",
    Severity.minor: "次要问题",
    Severity.editorial: "编辑问题",
}

NO_OVERALL_EN = (
    "No overall score is assigned. Each of the seven dimensions is scored 1–10; "
    "conclusions are venue-specific and are not a fitted conference mapping."
)
NO_OVERALL_ZH = (
    "未给出总体分数。七个维度各自按 1–10 打分；结论按会议或期刊分别给出，不是拟合的总分映射。"
)


def sort_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.id))


def _evidence_suffix(evidence_ids: list[str]) -> str:
    if not evidence_ids:
        return ""
    return " [" + ", ".join(evidence_ids) + "]"


def _issue_location(issue: ReviewIssue) -> str:
    if issue.anchor is not None:
        return f"{issue.anchor.display} ({issue.anchor.kind.value})"
    return issue.location


def _render_audit_sections(package: ReviewPackage, language: str) -> list[str]:
    lines: list[str] = []
    if package.ledger_claims:
        lines.extend(["## Claim Ledger", ""])
        for item in package.ledger_claims:
            risk = item.risk.resolve(language) if item.risk else ""
            lines.append(f"- `{item.id}` {item.claim.resolve(language)}{_evidence_suffix(item.in_paper_evidence_ids)}")
            if risk:
                lines.append(f"  - Risk: {risk}")
        lines.append("")
    if package.agenda:
        lines.extend(["## Investigation Agenda", ""])
        for item in package.agenda:
            perspective = item.perspective.value if item.perspective else "unspecified"
            lines.append(f"- `{item.id}` ({perspective}): {item.question}")
        lines.append("")
    if package.integrity_records:
        lines.extend(["## Integrity", ""])
        for item in package.integrity_records:
            note = item.notes.resolve(language) if item.notes else ""
            lines.append(
                f"- `{item.id}` {item.kind.value}: {item.verdict.value} — {item.subject}"
            )
            if note:
                lines.append(f"  - {note}")
        lines.append("")
    if package.novelty_assessments:
        lines.extend(["## Novelty", ""])
        for item in package.novelty_assessments:
            note = item.notes.resolve(language) if item.notes else ""
            setting = "matched" if item.matched_setting else "not-matched"
            lines.append(
                f"- `{item.id}` {item.tag.value} ({setting}; claim {item.claim_id}; snapshot {item.snapshot_id})"
            )
            if note:
                lines.append(f"  - {note}")
        lines.append("")
    return lines


def _render_audit_sections_multilingual(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    lines: list[str] = []
    if package.ledger_claims:
        lines.extend([_heading("Claim Ledger", languages), ""])
        for item in package.ledger_claims:
            lines.append(f"- `{item.id}`{_evidence_suffix(item.in_paper_evidence_ids)}")
            for language in languages:
                lines.append(f"  - **{language}:** {item.claim.resolve(language)}")
                if item.risk:
                    lines.append(f"    - Risk: {item.risk.resolve(language)}")
        lines.append("")
    if package.agenda:
        lines.extend([_heading("Investigation Agenda", languages), ""])
        for item in package.agenda:
            perspective = item.perspective.value if item.perspective else "unspecified"
            lines.append(f"- `{item.id}` ({perspective}): {item.question}")
        lines.append("")
    if package.integrity_records:
        lines.extend([_heading("Integrity", languages), ""])
        for item in package.integrity_records:
            lines.append(
                f"- `{item.id}` {item.kind.value}: {item.verdict.value} — {item.subject}"
            )
            if item.notes:
                for language in languages:
                    lines.append(f"  - **{language}:** {item.notes.resolve(language)}")
        lines.append("")
    if package.novelty_assessments:
        lines.extend([_heading("Novelty", languages), ""])
        for item in package.novelty_assessments:
            setting = "matched" if item.matched_setting else "not-matched"
            lines.append(
                f"- `{item.id}` {item.tag.value} ({setting}; claim {item.claim_id}; snapshot {item.snapshot_id})"
            )
            if item.notes:
                for language in languages:
                    lines.append(f"  - **{language}:** {item.notes.resolve(language)}")
        lines.append("")
    return lines


def _heading(title: str, languages: Sequence[str]) -> str:
    chinese = SECTION_ZH.get(title)
    if len(languages) == 1 or not chinese:
        return f"## {title}"
    return f"## {title} / {chinese}"


def _render_venue_block_monolingual(package: ReviewPackage, language: str) -> list[str]:
    lines = ["## Venue Conclusions", "", NO_OVERALL_EN if language != "zh-Hans" else NO_OVERALL_ZH, ""]
    if not package.venue_conclusions:
        return lines
    conferences = [item for item in package.venue_conclusions if item.kind == VenueKind.conference]
    journals = [item for item in package.venue_conclusions if item.kind == VenueKind.journal]
    for title, items in (("Conferences", conferences), ("Journals", journals)):
        if not items:
            continue
        lines.extend([f"### {title}", ""])
        for item in items:
            label = item.label.resolve(language)
            rationale = item.rationale.resolve(language)
            lines.extend(
                [
                    f"#### {item.id}",
                    "",
                    f"- **Outcome:** {item.outcome.value} — {label}{_evidence_suffix(item.evidence_ids)}",
                    "",
                    rationale,
                    "",
                ]
            )
    return lines


def _render_venue_block_bilingual(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    lines = [_heading("Venue Conclusions", languages), ""]
    no_overall = {
        "en": NO_OVERALL_EN,
        "zh-Hans": NO_OVERALL_ZH,
    }
    lines.extend(
        _language_blocks(
            languages,
            {
                language: [
                    no_overall.get(language, translate_text(NO_OVERALL_EN, source="en", target=language))
                ]
                for language in languages
            },
        )
    )
    if not package.venue_conclusions:
        return lines
    conferences = [item for item in package.venue_conclusions if item.kind == VenueKind.conference]
    journals = [item for item in package.venue_conclusions if item.kind == VenueKind.journal]
    for title, items in (("Conferences", conferences), ("Journals", journals)):
        if not items:
            continue
        lines.extend([_heading(title, languages).replace("## ", "### "), ""])
        for item in items:
            lines.append(f"#### {item.id}")
            lines.append("")
            lines.append(f"- **Outcome:** `{item.outcome.value}`{_evidence_suffix(item.evidence_ids)}")
            lines.append("")
            lines.extend(
                _language_blocks(
                    languages,
                    {
                        language: [
                            f"**{require_language(item.label, language, field=f'{item.id}.label')}.** "
                            f"{require_language(item.rationale, language, field=f'{item.id}.rationale')}"
                        ]
                        for language in languages
                    },
                )
            )
    return lines


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
                f"- **{score.dimension.value}:** {score.score:.1f}/10"
                f"{_evidence_suffix(score.evidence_ids)}{detail}"
            )
        lines.append("")

    lines.extend(_render_venue_block_monolingual(package, language))

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
                f"- **Location:** {_issue_location(issue)}",
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
            excerpt = (snapshot.excerpt or "").strip()
            lines.append(
                f"- `{snapshot.id}` {snapshot.title} "
                f"({snapshot.content_kind.value}{arxiv}; {snapshot.retrieved_at.date().isoformat()}; "
                f"queries {', '.join(snapshot.query_ids)})"
            )
            if excerpt:
                lines.append(f"  Excerpt: {excerpt}")
        lines.append("")

    lines.extend(_render_audit_sections(package, language))

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
                f"- **{score.dimension.value}:** {score.score:.1f}/10{_evidence_suffix(score.evidence_ids)}"
            )
            if score.rationale:
                for language in languages:
                    rationale = require_language(
                        score.rationale, language, field=f"{score.dimension.value}.rationale"
                    )
                    lines.append(f"  - **{language}:** {rationale}")
        lines.append("")

    lines.extend(_render_venue_block_bilingual(package, languages))

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
                f"- **Location:** {_issue_location(issue)}",
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
            excerpt = (snapshot.excerpt or "").strip()
            lines.append(
                f"- `{snapshot.id}` {snapshot.title} "
                f"({snapshot.content_kind.value}{arxiv}; {snapshot.retrieved_at.date().isoformat()}; "
                f"queries {', '.join(snapshot.query_ids)})"
            )
            if excerpt:
                lines.append(f"  Excerpt: {excerpt}")
        lines.append("")

    lines.extend(_render_audit_sections_multilingual(package, languages))

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
