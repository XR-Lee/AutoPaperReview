"""First-read review helpers: labels, quotes, and optional export-gate checks.

The reader of a review is often seeing the manuscript for the first time
(an area chair, an author, or a reviewer who has not yet internalized the
paper's terms). Open venue guides and open reviewers all write for that
reader. This module keeps those checks deterministic and LLM-free.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from .models import (
    CommentIntent,
    ReviewIssue,
    ReviewPackage,
    SarDimension,
    Severity,
)


FIRST_READ_PROMPT = "first_read_v1.md"

HOW_TO_READ_EN = (
    "This review is written for someone who is seeing the manuscript for the "
    "first time — an area chair, an author, or a reviewer who has not yet "
    "internalized the paper's terms. Each finding restates the relevant claim "
    "in plain language, quotes the passage when one exists, explains why the "
    "point matters, and says what would change the assessment. Internal "
    "evidence IDs are audit handles; they are not a substitute for the argument."
)
HOW_TO_READ_ZH = (
    "本审稿面向第一次读这篇稿件的人：领域主席、作者，以及尚未熟悉文中术语的审稿人。"
    "每条意见都会用平实语言复述相关主张，在有原文时引用对应段落，解释为何重要，"
    "并说明怎样修改才会改变评价。内部证据编号只是核验把手，不能代替论述本身。"
)

DIMENSION_LABELS: dict[SarDimension, tuple[str, str]] = {
    SarDimension.originality: ("Originality", "原创性"),
    SarDimension.importance_of_research_question: (
        "Importance of the research question",
        "研究问题的重要性",
    ),
    SarDimension.claims_supported: ("Whether claims are supported", "主张是否得到支持"),
    SarDimension.experimental_soundness: ("Experimental soundness", "实验可靠性"),
    SarDimension.writing_clarity: ("Writing clarity", "写作清晰度"),
    SarDimension.community_value: ("Community value", "对社区的价值"),
    SarDimension.prior_work_contextualization: (
        "Contextualization of prior work",
        "对已有工作的定位",
    ),
}

INTENT_LABELS: dict[CommentIntent, tuple[str, str]] = {
    CommentIntent.praise: ("Praise", "优点"),
    CommentIntent.issue: ("Issue", "问题"),
    CommentIntent.suggestion: ("Suggestion", "建议"),
    CommentIntent.question: ("Question", "疑问"),
    CommentIntent.note: ("Note", "说明"),
}


def packaged_prompt_text(name: str) -> str:
    return files("autopaperreview").joinpath("prompts", name).read_text(encoding="utf-8")


def resolve_first_read_contract(root: Path | None = None) -> str:
    if root is not None:
        candidate = Path(root) / "prompts" / FIRST_READ_PROMPT
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return packaged_prompt_text(FIRST_READ_PROMPT)


def how_to_read(language: str) -> str:
    return HOW_TO_READ_ZH if language == "zh-Hans" else HOW_TO_READ_EN


def dimension_label(dimension: SarDimension, language: str) -> str:
    english, chinese = DIMENSION_LABELS[dimension]
    return chinese if language == "zh-Hans" else english


def issue_quote(issue: ReviewIssue, package: ReviewPackage | None = None) -> str | None:
    if issue.first_read and issue.first_read.quote and issue.first_read.quote.strip():
        return issue.first_read.quote.strip()
    if issue.anchor and issue.anchor.excerpt and issue.anchor.excerpt.strip():
        return issue.anchor.excerpt.strip()
    if package is None:
        return None
    by_id = {item.id: item for item in package.evidence}
    for evidence_id in issue.evidence_ids:
        item = by_id.get(evidence_id)
        if item and item.excerpt and item.excerpt.strip():
            return item.excerpt.strip()
    return None


def issue_intent(issue: ReviewIssue) -> CommentIntent:
    if issue.first_read is not None:
        return issue.first_read.intent
    return CommentIntent.issue


def issue_blocking(issue: ReviewIssue) -> bool | None:
    if issue.first_read is not None and issue.first_read.blocking is not None:
        return issue.first_read.blocking
    if issue.severity in {Severity.critical, Severity.major}:
        return True
    if issue.severity in {Severity.minor, Severity.editorial}:
        return False
    return None


def intent_heading(issue: ReviewIssue, language: str) -> str:
    intent = issue_intent(issue)
    label = INTENT_LABELS[intent][1 if language == "zh-Hans" else 0]
    blocking = issue_blocking(issue)
    if blocking is True:
        suffix = "（必须处理）" if language == "zh-Hans" else " (blocking)"
    elif blocking is False:
        suffix = "（不阻止接受）" if language == "zh-Hans" else " (non-blocking)"
    else:
        suffix = ""
    return f"{label}{suffix}"


def is_major(issue: ReviewIssue) -> bool:
    return issue.severity in {Severity.critical, Severity.major}


def first_read_gate_errors(
    package: ReviewPackage,
    *,
    require_first_read_on_major: bool = False,
    require_quote_on_major: bool = False,
    min_explanation_chars: int = 0,
) -> list[str]:
    errors: list[str] = []
    majors = [issue for issue in package.issues if is_major(issue)]
    if require_first_read_on_major:
        missing = [issue.id for issue in majors if issue.first_read is None]
        if missing:
            errors.append(
                "export gate requires first-read notes on major/critical issues; "
                f"missing: {missing}"
            )
    if require_quote_on_major:
        missing = [issue.id for issue in majors if not issue_quote(issue, package)]
        if missing:
            errors.append(
                "export gate requires a quoted passage on major/critical issues; "
                f"missing: {missing}"
            )
    if min_explanation_chars:
        short: list[str] = []
        for issue in package.issues:
            text = (
                issue.first_read.explanation.primary
                if issue.first_read is not None
                else issue.impact.primary
            )
            if len(text.strip()) < min_explanation_chars:
                short.append(issue.id)
        if short:
            errors.append(
                "export gate requires issue explanations of at least "
                f"{min_explanation_chars} characters; short: {short}"
            )
    return errors
