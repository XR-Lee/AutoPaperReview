"""Deterministic JSON and Markdown report rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from .i18n import (
    MissingTranslationError,
    ensure_package_languages,
    normalize_languages,
    require_language,
)
from .markers import EvidenceMarker, assign_display_numbers, body_citation_ids, build_markers
from .models import (
    DimensionScore,
    IntegrityRecord,
    LedgerClaim,
    LocalizedText,
    NoveltyAssessment,
    OverallScoreMethod,
    ReviewClaimKind,
    ReviewIssue,
    ReviewPackage,
    Severity,
    VenueKind,
)
from .readability import (
    dimension_label,
    how_to_read,
    intent_heading,
    issue_quote,
)
from .related_work import render_related_work_brief, render_related_work_section
from .status_notes import ClassifiedNote, StatusNote, classify_status_note


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
    "How to Read This Review": "如何阅读本审稿",
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
    "Evidence Index": "证据索引",
    "Acceptance Gate": "接受条件",
    "Claim Ledger": "主张台账",
    "Listed Contributions": "作者贡献逐条",
    "Investigation Agenda": "核查议程",
    "Integrity": "完整性核验",
    "Novelty": "新颖性",
    "Related Work": "相关工作",
    "Evidence Catalog": "证据目录",
    "Evidence markers": "证据标记",
    "Score justification": "分数说明",
    "Marker numbers": "标记编号",
    "Clause codes": "条款编号",
    "Appendix": "附录",
    "Appendix summary": "附录摘要",
    "Format and Visual Checks": "版式与视觉检查",
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

_VISUAL_STATUS = {
    "unchecked": ("unchecked", "未检查"),
    "format_only": ("format only", "仅版式"),
    "checked": ("checked", "已检查"),
}


def _rating_lines(package: ReviewPackage, language: str) -> list[str]:
    score = package.overall_score
    if (
        score is None
        or score.method != OverallScoreMethod.declared_formula
        or not score.coefficient_set
    ):
        return [NO_OVERALL_ZH if language == "zh-Hans" else NO_OVERALL_EN]
    meta = score.metadata or {}
    cap_ids = meta.get("cap_ids") or []
    exemption_ids = meta.get("exemption_ids") or []
    status_key = str(meta.get("visual_status") or "unchecked")
    status = _VISUAL_STATUS.get(status_key, (status_key, status_key))
    status_label = status[1] if language == "zh-Hans" else status[0]
    none = "无" if language == "zh-Hans" else "none"
    if language == "zh-Hans":
        return [
            f"已声明评分 `{score.coefficient_set}`：ICLR 档 **{score.value:.0f}**。"
            "这不是拟合回归。",
            f"- 加权平均：{meta.get('weighted')}",
            f"- 门控 min(主张, 可靠性)：{meta.get('gate')}",
            f"- 上限：{', '.join(cap_ids) if cap_ids else none}",
            f"- 豁免：{', '.join(exemption_ids) if exemption_ids else none}",
            f"- 视觉状态：{status_label}",
            f"- 信心：{meta.get('confidence')}",
        ]
    return [
        f"Declared rating `{score.coefficient_set}`: ICLR bin **{score.value:.0f}**. "
        "This is not a fitted regression.",
        f"- Weighted mean: {meta.get('weighted')}",
        f"- Gate min(claims, soundness): {meta.get('gate')}",
        f"- Caps: {', '.join(cap_ids) if cap_ids else none}",
        f"- Exemptions: {', '.join(exemption_ids) if exemption_ids else none}",
        f"- Visual status: {status_label}",
        f"- Confidence: {meta.get('confidence')}",
    ]


def sort_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.id))


def record_anchor(record_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", record_id).strip("-").lower()
    return f"record-{slug}"


def _linked_ids(record_ids: list[str], *, empty: str = "none") -> str:
    if not record_ids:
        return empty
    return ", ".join(f"[{item}](#{record_anchor(item)})" for item in record_ids)


def _evidence_suffix(evidence_ids: list[str]) -> str:
    if not evidence_ids:
        return ""
    return " [" + _linked_ids(evidence_ids) + "]"


def _body_cite(evidence_ids: list[str], markers: dict[str, EvidenceMarker]) -> str:
    """Visible citation `[E3, p.5]` linking to the compact marker index."""
    parts: list[str] = []
    for evidence_id in evidence_ids:
        marker = markers.get(evidence_id)
        if marker is None or marker.external:
            continue
        parts.append(f"[[{marker.cite}](#{marker.anchor})]")
    if not parts:
        return ""
    return " " + " ".join(parts)


def _evidence_index_lines(package: ReviewPackage) -> list[str]:
    if not package.evidence:
        return []
    lines = []
    for item in sorted(package.evidence, key=lambda record: record.id):
        excerpt = (item.excerpt or "").strip()
        detail = excerpt or item.claim
        if excerpt and excerpt != item.claim:
            detail = f"{item.claim} — {excerpt}"
        lines.append(
            f'- <a id="{record_anchor(item.id)}"></a>`{item.id}` '
            f"({item.source_id}; {item.locator}) {detail}"
        )
    return lines


def _issue_location(issue: ReviewIssue) -> str:
    if issue.anchor is not None:
        return f"{issue.anchor.display} ({issue.anchor.kind.value})"
    return issue.location


_ID_NUM = re.compile(r"^([A-Za-z]+[-]?)(\d+)$")


def _quoted_passage(issue: ReviewIssue, package: ReviewPackage) -> str | None:
    return issue_quote(issue, package)


def _render_how_to_read(languages: Sequence[str]) -> list[str]:
    lines = [_heading("How to Read This Review", languages), ""]
    if len(languages) == 1:
        lines.extend([how_to_read(languages[0]), ""])
        return lines
    lines.extend(
        _language_blocks(
            languages,
            {language: [how_to_read(language)] for language in languages},
        )
    )
    return lines


def _after_ceiling(package: ReviewPackage, score: DimensionScore) -> float | None:
    overall = package.overall_score
    if overall is None:
        return None
    bands = overall.metadata.get("bands_after_ceiling")
    if not isinstance(bands, dict):
        return None
    value = bands.get(score.dimension.value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _dimension_numbers(package: ReviewPackage, score: DimensionScore) -> str:
    """Assigned score and clause beside the post-ceiling band, when rating has run."""
    clause = f" `{score.clause_id}`" if score.clause_id else ""
    assigned = f"{score.score:.1f}/10{clause}"
    after = _after_ceiling(package, score)
    if after is None:
        return assigned
    return f"assigned {assigned}; after ceiling {after:.1f}/10"


def _quote_block(quote: str, *, bilingual: bool) -> list[str]:
    collapsed = re.sub(r"\s+", " ", quote).strip()
    label = "**Quoted passage / 原文摘录.**" if bilingual else "**Quoted passage.**"
    return [label, "", f"> {collapsed}", ""]


def _render_first_read_mono(
    issue: ReviewIssue, package: ReviewPackage, language: str
) -> list[str]:
    lines: list[str] = []
    quote = _quoted_passage(issue, package)
    heading = intent_heading(issue, language)
    lines.append(f"- **Kind:** {heading}")
    if issue.first_read is not None:
        lines.append("")
        lines.append(
            f"**What the paper said.** "
            f"{require_language(issue.first_read.paper_said, language, field=f'{issue.id}.first_read.paper_said')}"
        )
        lines.append("")
        if quote:
            lines.extend(_quote_block(quote, bilingual=False))
        lines.append(
            f"**Why this matters.** "
            f"{require_language(issue.first_read.explanation, language, field=f'{issue.id}.first_read.explanation')}"
        )
        lines.append("")
    elif quote:
        lines.append("")
        lines.extend(_quote_block(quote, bilingual=False))
    return lines


def _render_first_read_bilingual(
    issue: ReviewIssue, package: ReviewPackage, languages: Sequence[str]
) -> list[str]:
    lines: list[str] = []
    quote = _quoted_passage(issue, package)
    kinds = " / ".join(intent_heading(issue, language) for language in languages)
    lines.append(f"- **Kind:** {kinds}")
    lines.append("")
    if issue.first_read is not None:
        lines.append("**What the paper said / 论文写了什么.**")
        lines.extend(
            _prose_items(
                issue.first_read.paper_said,
                languages,
                field=f"{issue.id}.first_read.paper_said",
            )
        )
        lines.append("")
        if quote:
            lines.extend(_quote_block(quote, bilingual=True))
        lines.append("**Why this matters / 为何重要.**")
        lines.extend(
            _prose_items(
                issue.first_read.explanation,
                languages,
                field=f"{issue.id}.first_read.explanation",
            )
        )
        lines.append("")
    elif quote:
        lines.extend(_quote_block(quote, bilingual=True))
    return lines


def _render_evidence_catalog(package: ReviewPackage) -> list[str]:
    if not package.evidence:
        return []
    lines = ["## Evidence Catalog", ""]
    for item in package.evidence:
        excerpt = (item.excerpt or "").strip()
        lines.append(f"- `{item.id}` {item.locator}: {item.claim}")
        if excerpt:
            lines.append(f"  Quote: {excerpt}")
    lines.append("")
    return lines


def _compact_ids(ids: Sequence[str]) -> str:
    cleaned = list(dict.fromkeys(item for item in ids if item))
    if len(cleaned) <= 2:
        return ", ".join(cleaned)
    matches = [_ID_NUM.match(item) for item in cleaned]
    if all(matches) and len({item.group(1) for item in matches if item}) == 1:
        nums = [int(item.group(2)) for item in matches if item]
        if nums == list(range(nums[0], nums[0] + len(nums))):
            prefix = matches[0].group(1) if matches[0] else ""
            return f"{prefix}{nums[0]}–{prefix}{nums[-1]}"
    return ", ".join(cleaned)


def _legend_line(status: StatusNote, languages: Sequence[str], params: str | None = None) -> str:
    extra = f" ({params})" if params else ""
    if len(languages) == 1:
        language = languages[0]
        badge = status.badge_zh if language == "zh-Hans" else status.code
        legend = status.legend_zh if language == "zh-Hans" else status.legend_en
        return f"**{badge}**{extra} — {legend}"
    return (
        f"**{status.code}** {status.badge_zh}{extra} — "
        f"{status.legend_en} / {status.legend_zh}"
    )


def _classify_note(text: LocalizedText | None, metadata: dict) -> ClassifiedNote:
    primary = text.primary if text else ""
    return classify_status_note(primary, metadata)


def _prose_note_lines(text: LocalizedText, languages: Sequence[str], *, indent: str = "  ") -> list[str]:
    if len(languages) == 1:
        return [f"{indent}- {text.resolve(languages[0])}"]
    return [f"{indent}- **{language}:** {text.resolve(language)}" for language in languages]


def _ordered_groups(items: Sequence, key_fn):
    groups: list[tuple[object, list]] = []
    index: dict[object, int] = {}
    for item in items:
        key = key_fn(item)
        if key not in index:
            index[key] = len(groups)
            groups.append((key, []))
        groups[index[key]][1].append(item)
    return groups


def _render_ledger_records(
    records: Sequence[LedgerClaim],
    languages: Sequence[str],
    *,
    quote_once: bool,
) -> list[str]:
    lines: list[str] = []
    classified = [_classify_note(item.risk, item.metadata) for item in records]
    status_keys = {
        (item.status.code, item.params)
        for item in classified
        if item.status is not None
    }
    shared = all(item.status is not None for item in classified) and len(status_keys) == 1
    if shared and records:
        first = classified[0]
        assert first.status is not None
        lines.append(f"- {_legend_line(first.status, languages, first.params)}")
        for item in records:
            if quote_once:
                claim = item.claim.primary
                lines.append(f"- `{item.id}` {claim}{_evidence_suffix(item.in_paper_evidence_ids)}")
            elif len(languages) == 1:
                lines.append(
                    f"- `{item.id}` {item.claim.resolve(languages[0])}"
                    f"{_evidence_suffix(item.in_paper_evidence_ids)}"
                )
            else:
                lines.append(f"- `{item.id}`{_evidence_suffix(item.in_paper_evidence_ids)}")
                for language in languages:
                    lines.append(f"  - **{language}:** {item.claim.resolve(language)}")
        return lines
    for item, note in zip(records, classified):
        if quote_once:
            lines.append(
                f"- `{item.id}` {item.claim.primary}{_evidence_suffix(item.in_paper_evidence_ids)}"
            )
        elif len(languages) == 1:
            lines.append(
                f"- `{item.id}` {item.claim.resolve(languages[0])}"
                f"{_evidence_suffix(item.in_paper_evidence_ids)}"
            )
        else:
            lines.append(f"- `{item.id}`{_evidence_suffix(item.in_paper_evidence_ids)}")
            for language in languages:
                lines.append(f"  - **{language}:** {item.claim.resolve(language)}")
        if note.status is not None:
            lines.append(f"  - {_legend_line(note.status, languages, note.params)}")
        elif item.risk:
            if quote_once or len(languages) > 1:
                lines.extend(_prose_note_lines(item.risk, languages, indent="  "))
            else:
                lines.append(f"  - Risk: {item.risk.resolve(languages[0])}")
    return lines


def _integrity_group_key(item: IntegrityRecord) -> tuple:
    note = _classify_note(item.notes, item.metadata)
    if note.status is not None:
        return ("status", item.kind.value, item.verdict.value, note.status.code, note.params or "")
    primary = item.notes.primary if item.notes else ""
    return ("prose", item.kind.value, item.verdict.value, primary)


def _render_integrity_records(records: Sequence[IntegrityRecord], languages: Sequence[str]) -> list[str]:
    lines: list[str] = []
    for key, items in _ordered_groups(records, _integrity_group_key):
        kind, = (key[1],)
        verdict = key[2]
        if key[0] == "status":
            status = classify_status_note(None, {"note_code": key[3]}).status
            assert status is not None
            params = key[4] or None
            lines.append(
                f"- {_legend_line(status, languages, params)} · `{kind}` {verdict}"
            )
            for item in items:
                lines.append(f"  - `{item.id}` {item.subject}")
            continue
        if len(items) == 1:
            item = items[0]
            lines.append(
                f"- `{item.id}` {item.kind.value}: {item.verdict.value} — {item.subject}"
            )
            if item.notes:
                if len(languages) == 1:
                    lines.append(f"  - {item.notes.resolve(languages[0])}")
                else:
                    lines.extend(_prose_note_lines(item.notes, languages, indent="  "))
            continue
        lines.append(f"- `{kind}` {verdict}")
        if items[0].notes:
            if len(languages) == 1:
                lines.append(f"  - {items[0].notes.resolve(languages[0])}")
            else:
                lines.extend(_prose_note_lines(items[0].notes, languages, indent="  "))
        for item in items:
            lines.append(f"  - `{item.id}` {item.subject}")
    return lines


def _novelty_group_key(item: NoveltyAssessment) -> tuple:
    note = _classify_note(item.notes, item.metadata)
    setting = "matched" if item.matched_setting else "not-matched"
    if note.status is not None:
        return ("status", item.tag.value, setting, note.status.code, note.params or "")
    primary = item.notes.primary if item.notes else ""
    return ("prose", item.tag.value, setting, primary)


def _novelty_cells(items: Sequence[NoveltyAssessment]) -> str:
    by_claim: dict[str, list[str]] = {}
    for item in items:
        by_claim.setdefault(item.claim_id, []).append(item.snapshot_id)
    return "; ".join(
        f"{claim_id} x {_compact_ids(snapshot_ids)}"
        for claim_id, snapshot_ids in by_claim.items()
    )


def _render_novelty_assessments(
    records: Sequence[NoveltyAssessment],
    languages: Sequence[str],
) -> list[str]:
    lines: list[str] = []
    for key, items in _ordered_groups(records, _novelty_group_key):
        tag, setting = key[1], key[2]
        if key[0] == "status":
            status = classify_status_note(None, {"note_code": key[3]}).status
            assert status is not None
            params = key[4] or None
            lines.append(
                f"- {_legend_line(status, languages, params)} · `{tag}` {setting}"
            )
            if len(items) == 1:
                item = items[0]
                lines.append(
                    f"  - `{item.id}` claim {item.claim_id} · snapshot {item.snapshot_id}"
                )
            else:
                lines.append(f"  - {_novelty_cells(items)}")
            continue
        for item in items:
            lines.append(
                f"- `{item.id}` {item.tag.value} ({setting}; claim {item.claim_id}; snapshot {item.snapshot_id})"
            )
            if item.notes:
                if len(languages) == 1:
                    lines.append(f"  - {item.notes.resolve(languages[0])}")
                else:
                    lines.extend(_prose_note_lines(item.notes, languages, indent="  "))
    return lines


def _render_audit_sections(package: ReviewPackage, language: str) -> list[str]:
    return _render_audit(package, [language])


def _render_audit_sections_multilingual(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    return _render_audit(package, languages)


def _render_audit(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    lines: list[str] = []
    if package.ledger_claims:
        listed = [item for item in package.ledger_claims if item.metadata.get("kind") == "listed_contribution"]
        other = [item for item in package.ledger_claims if item.metadata.get("kind") != "listed_contribution"]
        if listed:
            lines.extend([_heading("Listed Contributions", languages), ""])
            lines.extend(_render_ledger_records(listed, languages, quote_once=len(languages) > 1))
            lines.append("")
        if other:
            lines.extend([_heading("Claim Ledger", languages), ""])
            lines.extend(_render_ledger_records(other, languages, quote_once=False))
            lines.append("")
    if package.agenda:
        lines.extend([_heading("Investigation Agenda", languages), ""])
        for item in package.agenda:
            perspective = item.perspective.value if item.perspective else "unspecified"
            lines.append(f"- `{item.id}` ({perspective}): {item.question}")
        lines.append("")
    if package.integrity_records:
        lines.extend([_heading("Integrity", languages), ""])
        lines.extend(_render_integrity_records(package.integrity_records, languages))
        lines.append("")
    if package.novelty_assessments:
        lines.extend([_heading("Novelty", languages), ""])
        lines.extend(_render_novelty_assessments(package.novelty_assessments, languages))
        lines.append("")
    return lines


def _render_related_work(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    raw = package.metadata.get("related_work")
    if not raw:
        return []
    return render_related_work_section(raw, languages)


def _heading(title: str, languages: Sequence[str]) -> str:
    chinese = SECTION_ZH.get(title)
    if list(languages) == ["zh-Hans"] and chinese:
        return f"## {chinese}"
    if len(languages) == 1 or not chinese:
        return f"## {title}"
    return f"## {title} / {chinese}"


def _render_venue_block_monolingual(
    package: ReviewPackage,
    language: str,
    markers: dict[str, EvidenceMarker],
) -> list[str]:
    lines = ["## Venue Conclusions", "", *_rating_lines(package, language), ""]
    if not package.venue_conclusions:
        return lines
    conferences = [item for item in package.venue_conclusions if item.kind == VenueKind.conference]
    journals = [item for item in package.venue_conclusions if item.kind == VenueKind.journal]
    for title, items in (("Conferences", conferences), ("Journals", journals)):
        if not items:
            continue
        lines.extend([f"### {title}", ""])
        for item in items:
            label = require_language(item.label, language, field=f"{item.id}.label")
            rationale = require_language(item.rationale, language, field=f"{item.id}.rationale")
            lines.extend(
                [
                    f"#### {item.id}",
                    "",
                    f"- **Outcome:** {item.outcome.value} — {label}{_body_cite(item.evidence_ids, markers)}",
                    "",
                    rationale,
                    "",
                ]
            )
    return lines


def _render_venue_block_bilingual(
    package: ReviewPackage,
    languages: Sequence[str],
    markers: dict[str, EvidenceMarker],
) -> list[str]:
    lines = [_heading("Venue Conclusions", languages), ""]
    lines.extend(
        _language_blocks(
            languages,
            {language: _rating_lines(package, language) for language in languages},
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
            lines.append(f"- **Outcome:** `{item.outcome.value}`{_body_cite(item.evidence_ids, markers)}")
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


def _render_format_and_visual(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    if not package.format_checks and not package.visual_findings:
        return []
    lines = [_heading("Format and Visual Checks", languages), ""]
    for item in package.format_checks:
        status = "pass" if item.passed else "fail"
        clause = f" `{item.clause_id}`" if item.clause_id else ""
        fatal = f" `{item.fatal_class.value}`" if item.fatal_class else ""
        lines.append(f"- **{item.id}** `{item.check}` — {status}{clause}{fatal}")
        if item.detail and len(languages) > 1:
            lines.extend(
                _prose_items(item.detail, languages, field=f"{item.id}.detail")
            )
        elif item.detail:
            lines.append(f"  - {item.detail.resolve(languages[0])}")
    for finding in package.visual_findings:
        fatal = f" `{finding.fatal_class.value}`" if finding.fatal_class else ""
        clause = f" `{finding.clause_id}`" if finding.clause_id else ""
        figure = f" {finding.figure_id}" if finding.figure_id else ""
        severity = f" {finding.severity.value}" if finding.severity else ""
        lines.append(
            f"- **{finding.id}** page {finding.page}{figure} `{finding.kind.value}`"
            f"{severity}{clause}{fatal} — `{finding.page_image_sha256[:12]}`"
        )
    lines.append("")
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


def _string_list(mapping: dict[str, list[str]], language: str, field: str) -> list[str]:
    """Return one language's list, or fail when another language would be substituted."""
    values = mapping.get(language)
    if values:
        return list(values)
    if not any(mapping.values()):
        return []
    raise MissingTranslationError(
        f"{field} has no {language} text; silent monolingual fallback is forbidden"
    )


def _prepare_languages(
    package: ReviewPackage,
    languages: Sequence[str],
    *,
    fill_missing: bool,
) -> ReviewPackage:
    if not fill_missing or list(languages) == ["en"]:
        return package
    return ensure_package_languages(package, languages)


def _score_phrase(package: ReviewPackage, score: DimensionScore) -> str:
    """Score text for the body. Clause codes stay in the appendix."""
    assigned = f"assigned {score.score:.1f}/10"
    after = _after_ceiling(package, score)
    if after is None:
        return assigned
    return f"{assigned}; after ceiling {after:.1f}/10"


def _concise_venue(package: ReviewPackage, language: str) -> str:
    score = package.overall_score
    if (
        score is None
        or score.method != OverallScoreMethod.declared_formula
        or not score.coefficient_set
    ):
        return NO_OVERALL_ZH if language == "zh-Hans" else NO_OVERALL_EN
    if language == "zh-Hans":
        return f"会议档位：{score.value:.0f}。"
    return f"Venue bin: {score.value:.0f}."


def _text_line(text: LocalizedText, language: str, languages: Sequence[str], field: str) -> str:
    if len(languages) == 1:
        return require_language(text, language, field=field)
    return require_language(text, language, field=field)


def _marker_legend(
    package: ReviewPackage,
    languages: Sequence[str],
    markers: dict[str, EvidenceMarker],
) -> list[str]:
    numbered = [marker for marker in markers.values() if marker.label]
    numbered.sort(key=lambda marker: int(marker.label[1:]))
    if not numbered:
        return []
    lines = [_heading("Evidence markers", languages), ""]
    for marker in numbered:
        titles = [
            issue.title
            for issue in sort_issues(package.issues)
            if marker.evidence_id in issue.evidence_ids
        ]
        if len(languages) == 1:
            pointer = ""
            if titles:
                pointer = " " + require_language(titles[0], languages[0], field=marker.label)
            lines.append(f'- <a id="{marker.anchor}"></a>`[{marker.cite}]`{pointer}')
            continue
        lines.append(f'- <a id="{marker.anchor}"></a>`[{marker.cite}]`')
        for language in languages:
            if not titles:
                continue
            pointer = require_language(titles[0], language, field=marker.label)
            lines.append(f"  - **{language}:** {pointer}")
    lines.append("")
    return lines


def _render_concise(
    package: ReviewPackage,
    languages: Sequence[str],
    markers: dict[str, EvidenceMarker],
) -> str:
    """OpenReview-style body. Internal ids and the audit trail stay in the appendix."""
    bilingual = len(languages) > 1
    lines = [f"# Review: {package.project_id}", ""]
    if bilingual:
        lines.extend([f"- Languages: {', '.join(languages)}", ""])

    def emit(title: str, bodies: dict[str, list[str]]) -> None:
        if not any(bodies.get(language) for language in languages):
            return
        lines.append(_heading(title, languages))
        lines.append("")
        if bilingual:
            lines.extend(_language_blocks(languages, bodies))
            return
        language = languages[0]
        lines.extend(bodies.get(language, []))
        if bodies.get(language) and bodies[language][-1] != "":
            lines.append("")

    summaries = [claim for claim in package.claims if claim.kind == ReviewClaimKind.summary]
    summary_bodies: dict[str, list[str]] = {}
    for language in languages:
        if summaries:
            text = " ".join(
                f"{_text_line(claim.text, language, languages, claim.id)}"
                f"{_body_cite(claim.evidence_ids, markers)}"
                for claim in summaries
            )
        elif package.summary:
            text = _text_line(package.summary, language, languages, "summary")
        else:
            text = ""
        if text:
            summary_bodies[language] = [text]
    emit("Summary", summary_bodies)

    strength_claims = [claim for claim in package.claims if claim.kind == ReviewClaimKind.strength]
    strength_bodies: dict[str, list[str]] = {}
    for language in languages:
        bullets = [
            f"- {_text_line(claim.text, language, languages, claim.id)}"
            f"{_body_cite(claim.evidence_ids, markers)}"
            for claim in strength_claims
        ]
        if not bullets:
            bullets = [f"- {value}" for value in _string_list(package.strengths, language, "strengths")]
        if bullets:
            strength_bodies[language] = bullets
    emit("Strengths", strength_bodies)

    weakness_claims = [claim for claim in package.claims if claim.kind == ReviewClaimKind.weakness]
    weakness_bodies: dict[str, list[str]] = {}
    for language in languages:
        bullets = [
            f"- {_text_line(claim.text, language, languages, claim.id)}"
            f"{_body_cite(claim.evidence_ids, markers)}"
            for claim in weakness_claims
        ]
        action_label = "需采取的行动：" if language == "zh-Hans" else "Required action: "
        for issue in sort_issues(package.issues):
            if issue.severity == Severity.editorial:
                continue
            title = _text_line(issue.title, language, languages, f"{issue.id}.title")
            action = _text_line(issue.required_action, language, languages, f"{issue.id}.required_action")
            bullets.append(f"- {title} {action_label}{action}{_body_cite(issue.evidence_ids, markers)}")
        if bullets:
            weakness_bodies[language] = bullets
    emit("Weaknesses", weakness_bodies)

    question_claims = [claim for claim in package.claims if claim.kind == ReviewClaimKind.question]
    question_bodies: dict[str, list[str]] = {}
    for language in languages:
        bullets = [
            f"- {_text_line(claim.text, language, languages, claim.id)}"
            f"{_body_cite(claim.evidence_ids, markers)}"
            for claim in question_claims
        ]
        if bullets:
            question_bodies[language] = bullets
    emit("Questions", question_bodies)

    score_bodies: dict[str, list[str]] = {}
    for language in languages:
        rows: list[str] = []
        if package.recommendation:
            rows.append(_text_line(package.recommendation, language, languages, "recommendation"))
        rows.append(_concise_venue(package, language))
        for score in sorted(package.dimension_scores, key=lambda item: item.dimension.value):
            label = dimension_label(score.dimension, language)
            rationale = ""
            if score.rationale:
                rationale = " " + _text_line(
                    score.rationale,
                    language,
                    languages,
                    f"{score.dimension.value}.rationale",
                )
            rows.append(
                f"- {label}: {_score_phrase(package, score)}.{rationale}"
                f"{_body_cite(score.evidence_ids, markers)}"
            )
        if rows:
            score_bodies[language] = rows
    emit("Score justification", score_bodies)

    lines.extend(render_related_work_brief(package.metadata.get("related_work"), languages) if package.metadata.get("related_work") else [])
    lines.extend(_marker_legend(package, languages, markers))
    gate_bodies = {
        language: [f"- [ ] {value}" for value in _string_list(package.acceptance_gate, language, "acceptance_gate")]
        for language in languages
    }
    emit("Acceptance Gate", gate_bodies)
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(
    package: ReviewPackage,
    *,
    language: str | None = None,
    languages: Sequence[str] | None = None,
    bilingual: bool = False,
    fill_missing: bool = True,
    markers: dict[str, EvidenceMarker] | None = None,
) -> str:
    """Review body only. Appendix material is `render_appendix`."""
    langs = normalize_languages(
        language=language,
        languages=languages,
        bilingual=bilingual,
        default_language="en",
    )
    prepared = _prepare_languages(package, langs, fill_missing=fill_missing)
    raw = markers if markers is not None else build_markers(prepared)
    marker_map = assign_display_numbers(prepared, raw)
    return _render_concise(prepared, langs, marker_map)


def _marker_index(
    package: ReviewPackage,
    languages: Sequence[str],
    markers: dict[str, EvidenceMarker],
) -> list[str]:
    cited = [evidence_id for evidence_id in body_citation_ids(package) if evidence_id in markers]
    shown = [markers[evidence_id] for evidence_id in cited if not markers[evidence_id].external]
    if not shown:
        return []
    lines = [_heading("Evidence markers", languages), ""]
    for marker in shown:
        lines.append(f'- <a id="{marker.anchor}"></a>`[{marker.cite}]`')
        pointers = _marker_pointers(package, marker.evidence_id, languages)
        lines.extend(pointers)
    lines.append("")
    return lines


def _marker_pointers(package: ReviewPackage, evidence_id: str, languages: Sequence[str]) -> list[str]:
    rows: list[tuple[str, LocalizedText]] = []
    for issue in sort_issues(package.issues):
        if evidence_id in issue.evidence_ids:
            rows.append((issue.id, issue.title))
    for claim in package.claims:
        if evidence_id in claim.evidence_ids:
            rows.append((claim.id, claim.text))
    if not rows:
        return []
    if len(languages) == 1:
        language = languages[0]
        text = "; ".join(
            f"{item_id} {require_language(text, language, field=item_id)}" for item_id, text in rows
        )
        return [f"  - {text}"]
    lines: list[str] = []
    for language in languages:
        text = "; ".join(
            f"{item_id} {require_language(text, language, field=item_id)}"
            for item_id, text in rows
        )
        lines.append(f"  - **{language}:** {text}")
    return lines


def _related_brief(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    raw = package.metadata.get("related_work")
    if not raw:
        return []
    return render_related_work_brief(raw, languages)


def _render_monolingual(
    package: ReviewPackage,
    language: str,
    markers: dict[str, EvidenceMarker],
) -> str:
    lines = [
        f"# Review: {package.project_id}",
        "",
    ]
    lines.extend(_render_how_to_read([language]))

    summary_text = None
    summaries = [claim for claim in package.claims if claim.kind == ReviewClaimKind.summary]
    if summaries:
        summary_text = " ".join(
            f"{require_language(claim.text, language, field=claim.id)}"
            f"{_body_cite(claim.evidence_ids, markers)}"
            for claim in summaries
        )
    elif package.summary:
        summary_text = require_language(package.summary, language, field="summary")
    if summary_text:
        lines.extend([_heading("Summary", [language]), "", summary_text, ""])

    if package.recommendation:
        lines.extend(
            [
                _heading("Recommendation", [language]),
                "",
                require_language(package.recommendation, language, field="recommendation"),
                "",
            ]
        )

    if package.dimension_scores:
        lines.extend([_heading("Dimension Scores", [language]), ""])
        for score in sorted(package.dimension_scores, key=lambda item: item.dimension.value):
            rationale = (
                require_language(score.rationale, language, field=f"{score.dimension.value}.rationale")
                if score.rationale
                else ""
            )
            detail = f" — {rationale}" if rationale else ""
            label = dimension_label(score.dimension, language)
            lines.append(
                f"- **{score.dimension.value}:** {label} — {_dimension_numbers(package, score)}"
                f"{_body_cite(score.evidence_ids, markers)}{detail}"
            )
        lines.append("")

    lines.extend(_render_venue_block_monolingual(package, language, markers))
    lines.extend(_render_format_and_visual(package, [language]))

    claims_by_kind = {kind: [] for kind in CLAIM_SECTION}
    for claim in package.claims:
        claims_by_kind[claim.kind].append(claim)

    if claims_by_kind[ReviewClaimKind.strength]:
        lines.extend([_heading("Strengths", [language]), ""])
        for claim in claims_by_kind[ReviewClaimKind.strength]:
            lines.append(
                f"- {require_language(claim.text, language, field=claim.id)}"
                f"{_body_cite(claim.evidence_ids, markers)}"
            )
        lines.append("")
    else:
        strengths = _string_list(package.strengths, language, "strengths")
        if strengths:
            lines.extend([_heading("Strengths", [language]), ""])
            lines.extend(f"- {value}" for value in strengths)
            lines.append("")

    if claims_by_kind[ReviewClaimKind.weakness]:
        lines.extend([_heading("Weaknesses", [language]), ""])
        for claim in claims_by_kind[ReviewClaimKind.weakness]:
            lines.append(
                f"- {require_language(claim.text, language, field=claim.id)}"
                f"{_body_cite(claim.evidence_ids, markers)}"
            )
        lines.append("")

    if claims_by_kind[ReviewClaimKind.question]:
        lines.extend([_heading("Questions", [language]), ""])
        for claim in claims_by_kind[ReviewClaimKind.question]:
            lines.append(
                f"- {require_language(claim.text, language, field=claim.id)}"
                f"{_body_cite(claim.evidence_ids, markers)}"
            )
        lines.append("")

    if claims_by_kind[ReviewClaimKind.comment]:
        lines.extend([_heading("Detailed Comments", [language]), ""])
        for claim in claims_by_kind[ReviewClaimKind.comment]:
            lines.append(
                f"- {require_language(claim.text, language, field=claim.id)}"
                f"{_body_cite(claim.evidence_ids, markers)}"
            )
        lines.append("")

    current_severity: Severity | None = None
    for issue in sort_issues(package.issues):
        if issue.severity != current_severity:
            current_severity = issue.severity
            title = (
                SEVERITY_ZH[issue.severity]
                if language == "zh-Hans"
                else f"{issue.severity.value.title()} Issues"
            )
            lines.extend([f"## {title}", ""])
        cite = _body_cite(issue.evidence_ids, markers)
        lines.extend(
            [
                f"### {issue.id} {require_language(issue.title, language, field=f'{issue.id}.title')}",
                "",
            ]
        )
        lines.extend(_render_first_read_mono(issue, package, language))
        lines.extend(
            [
                f"**Evidence.** {require_language(issue.evidence, language, field=f'{issue.id}.evidence')}{cite}",
                "",
                f"**Impact.** {require_language(issue.impact, language, field=f'{issue.id}.impact')}",
                "",
                f"**Required action.** {require_language(issue.required_action, language, field=f'{issue.id}.required_action')}",
                "",
            ]
        )

    lines.extend(_related_brief(package, [language]))
    lines.extend(_marker_index(package, [language], markers))

    gate = _string_list(package.acceptance_gate, language, "acceptance_gate")
    if gate:
        lines.extend([_heading("Acceptance Gate", [language]), ""])
        lines.extend(f"- [ ] {value}" for value in gate)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_multilingual(
    package: ReviewPackage,
    languages: Sequence[str],
    markers: dict[str, EvidenceMarker],
) -> str:
    lines = [
        f"# Review: {package.project_id}",
        "",
        f"- Languages: {', '.join(languages)}",
        "",
    ]
    lines.extend(_render_how_to_read(languages))

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
                        f"{_body_cite(claim.evidence_ids, markers)}"
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
            labels = " / ".join(dimension_label(score.dimension, language) for language in languages)
            lines.append(
                f"- **{score.dimension.value}:** {labels} — {_dimension_numbers(package, score)}"
                f"{_body_cite(score.evidence_ids, markers)}"
            )
            if score.rationale:
                for language in languages:
                    rationale = require_language(
                        score.rationale, language, field=f"{score.dimension.value}.rationale"
                    )
                    lines.append(f"  - **{language}:** {rationale}")
        lines.append("")

    lines.extend(_render_venue_block_bilingual(package, languages, markers))
    lines.extend(_render_format_and_visual(package, languages))

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
                        f"{_body_cite(claim.evidence_ids, markers)}"
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
        lines.extend(
            [
                f"### {issue.id}",
                "",
            ]
        )
        lines.extend(_render_first_read_bilingual(issue, package, languages))
        lines.extend(_prose_items(issue.title, languages, field=f"{issue.id}.title"))
        lines.append("")
        cite = _body_cite(issue.evidence_ids, markers)
        lines.append(f"**Evidence / 证据.**{cite}")
        lines.extend(_prose_items(issue.evidence, languages, field=f"{issue.id}.evidence"))
        lines.append("")
        lines.append("**Impact / 影响.**")
        lines.extend(_prose_items(issue.impact, languages, field=f"{issue.id}.impact"))
        lines.append("")
        lines.append("**Required action / 需采取的行动.**")
        lines.extend(_prose_items(issue.required_action, languages, field=f"{issue.id}.required_action"))
        lines.append("")

    lines.extend(_related_brief(package, languages))
    lines.extend(_marker_index(package, languages, markers))

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


def render_appendix(
    package: ReviewPackage,
    *,
    language: str | None = None,
    languages: Sequence[str] | None = None,
    bilingual: bool = False,
    level: str = "full",
) -> str:
    """Opt-in audit appendix. `none` is empty. `summary` has counts and ids. `full` has the logs."""
    if level not in {"none", "summary", "full"}:
        raise ValueError("appendix level must be none, summary, or full")
    if level == "none":
        return ""
    langs = normalize_languages(
        language=language,
        languages=languages,
        bilingual=bilingual,
        default_language="en",
    )
    if level == "summary":
        return _render_appendix_summary(package, langs)
    return _render_appendix_full(package, langs)


def _render_appendix_summary(package: ReviewPackage, languages: Sequence[str]) -> str:
    markers = build_markers(package)
    lines = [
        _heading("Appendix summary", languages),
        "",
        f"- Evidence records: {len(package.evidence)}",
        f"- Ledger claims: {len(package.ledger_claims)}",
        f"- Agenda questions: {len(package.agenda)}",
        f"- Novelty assessments: {len(package.novelty_assessments)}",
        f"- Related-work queries: {len(package.related_work_queries)}",
        f"- Retrieved sources: {len(package.retrieved_snapshots)}",
        "",
    ]
    if package.evidence:
        lines.extend(["### Evidence ids", ""])
        for item in package.evidence:
            marker = markers.get(item.id)
            kind = "external" if marker and marker.external else "in-paper"
            page = f" p.{marker.page}" if marker and marker.page else ""
            lines.append(f"- `{item.id}` {kind}{page}")
        lines.append("")
    if package.related_work_queries:
        lines.extend(["### Query ids", ""])
        for query in package.related_work_queries:
            lines.append(f"- `{query.id}` {query.perspective.value}")
        lines.append("")
    if package.retrieved_snapshots:
        lines.extend(["### Retrieved ids", ""])
        for snapshot in package.retrieved_snapshots:
            lines.append(f"- `{snapshot.id}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _marker_crosswalk(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    numbered = assign_display_numbers(package, build_markers(package))
    shown = [marker for marker in numbered.values() if marker.label]
    shown.sort(key=lambda marker: int(marker.label[1:]))
    if not shown:
        return []
    lines = [_heading("Marker numbers", languages), ""]
    for marker in shown:
        page = f", p.{marker.page}" if marker.page else ""
        lines.append(f"- `{marker.label}` = `{marker.evidence_id}`{page}")
    lines.append("")
    return lines


def _clause_table(package: ReviewPackage, languages: Sequence[str]) -> list[str]:
    rows: list[str] = []
    for score in sorted(package.dimension_scores, key=lambda item: item.dimension.value):
        if score.clause_id:
            rows.append(f"- `{score.dimension.value}` `{score.clause_id}`")
    for item in package.format_checks:
        if item.clause_id:
            rows.append(f"- `{item.id}` `{item.clause_id}`")
    for finding in package.visual_findings:
        if finding.clause_id:
            rows.append(f"- `{finding.id}` `{finding.clause_id}`")
    if not rows:
        return []
    return [_heading("Clause codes", languages), "", *rows, ""]


def _render_appendix_full(package: ReviewPackage, languages: Sequence[str]) -> str:
    lines = [_heading("Appendix", languages), ""]
    index_lines = _evidence_index_lines(package)
    if index_lines:
        lines.extend([_heading("Evidence Index", languages), ""])
        lines.extend(index_lines)
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
    lines.extend(_render_related_work(package, languages))
    catalog = _render_evidence_catalog(package)
    if catalog:
        if len(languages) > 1:
            catalog[0] = _heading("Evidence Catalog", languages)
        lines.extend(catalog)
    lines.extend(_render_audit(package, languages))
    lines.extend(_marker_crosswalk(package, languages))
    lines.extend(_clause_table(package, languages))
    raw = build_markers(package)
    try:
        if len(languages) == 1:
            lines.append(_render_monolingual(package, languages[0], raw))
        else:
            lines.append(_render_multilingual(package, languages, raw))
    except MissingTranslationError:
        lines.append(_render_monolingual(package, "en", raw))
    return "\n".join(lines).rstrip() + "\n"


def write_package_json(package: ReviewPackage, path: Path) -> None:
    path.write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
