"""Compact codes for canned integrity, novelty, and ledger notes.

These notes are machine status, not argument. Reports should name the code
once and list the subjects, not reprint a bilingual paragraph on every row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NOT_MATCHED_RE = re.compile(
    r"^Snapshot is not a matched setting \(([^)]+)\); not used as overlap evidence\.$"
)


@dataclass(frozen=True)
class StatusNote:
    code: str
    badge_zh: str
    legend_en: str
    legend_zh: str


@dataclass(frozen=True)
class ClassifiedNote:
    status: StatusNote | None
    params: str | None = None


STATUS_NOTES: dict[str, StatusNote] = {
    "fixture": StatusNote(
        "fixture",
        "占位",
        "Placeholder identifier; not a resolved publication.",
        "占位标识符，不视为已解析文献。",
    ),
    "unresolved": StatusNote(
        "unresolved",
        "未解析",
        "Identifier present, not resolved against a live registry.",
        "有标识符，但未对照在线注册库解析。",
    ),
    "no-id": StatusNote(
        "no-id",
        "无标识",
        "No DOI or arXiv ID; reference integrity cannot be established.",
        "没有 DOI 或 arXiv ID，无法建立参考文献完整性。",
    ),
    "match": StatusNote(
        "match",
        "一致",
        "Workspace accuracy matches the manuscript claim.",
        "工作区准确率与稿件主张一致。",
    ),
    "mismatch": StatusNote(
        "mismatch",
        "不一致",
        "Workspace accuracy disagrees with the manuscript claim.",
        "工作区准确率与稿件主张不一致。",
    ),
    "no-results": StatusNote(
        "no-results",
        "无结果文件",
        "No results.json in the inspected workspace.",
        "所检查的工作区中没有 results.json。",
    ),
    "no-CI/seed": StatusNote(
        "no-CI/seed",
        "未报CI/种子",
        "Manuscript does not report both a confidence interval and a seed.",
        "稿件没有同时报告置信区间和随机种子。",
    ),
    "CI+seed": StatusNote(
        "CI+seed",
        "已报CI/种子",
        "Manuscript reports uncertainty and seeds.",
        "稿件报告了不确定性和随机种子。",
    ),
    "no-excerpt": StatusNote(
        "no-excerpt",
        "无摘录",
        "Snapshot has no excerpt; novelty cannot be verified.",
        "快照没有摘录，无法核验新颖性。",
    ),
    "not-matched": StatusNote(
        "not-matched",
        "设定不匹配",
        "Not a matched setting; not used as overlap evidence.",
        "不是匹配设定，不作为重叠证据。",
    ),
    "overlap": StatusNote(
        "overlap",
        "重叠",
        "Matched-setting comparison used the snapshot excerpt.",
        "匹配设定比较使用了快照摘录。",
    ),
    "distinct": StatusNote(
        "distinct",
        "未重复",
        "Matched setting; snapshot does not repeat the manuscript claim.",
        "设定匹配，且快照没有重复稿件主张。",
    ),
    "unchecked": StatusNote(
        "unchecked",
        "尚未核对",
        "Evidence and matched-setting comparator not checked.",
        "文中证据与对照未核。",
    ),
}

_EXACT_EN: dict[str, str] = {
    "Fixture or placeholder identifier; not treated as a resolved publication.": "fixture",
    "Identifier is present but was not resolved against a live registry.": "unresolved",
    "No DOI or arXiv ID; reference integrity cannot be established.": "no-id",
    "Workspace accuracy matches the manuscript claim.": "match",
    "Workspace accuracy disagrees with the manuscript claim.": "mismatch",
    "No results.json was present in the inspected workspace.": "no-results",
    "Manuscript does not report both a confidence interval and a seed.": "no-CI/seed",
    "Manuscript reports uncertainty and seeds.": "CI+seed",
    "Snapshot has no excerpt, so novelty cannot be verified.": "no-excerpt",
    "Matched-setting comparison used the snapshot excerpt.": "overlap",
    "Matched setting, and the snapshot does not repeat the manuscript claim.": "distinct",
    (
        "Listed contribution: in-paper evidence and a matched-setting "
        "comparator have not been checked for this item."
    ): "unchecked",
}

_METADATA_ALIASES = {
    "fixture": "fixture",
    "unresolved": "unresolved",
    "unresolved_id": "unresolved",
    "no-id": "no-id",
    "no_id": "no-id",
    "match": "match",
    "accuracy_match": "match",
    "mismatch": "mismatch",
    "accuracy_mismatch": "mismatch",
    "no-results": "no-results",
    "no_results": "no-results",
    "no-CI/seed": "no-CI/seed",
    "reporting_missing": "no-CI/seed",
    "CI+seed": "CI+seed",
    "reporting_present": "CI+seed",
    "no-excerpt": "no-excerpt",
    "no_excerpt": "no-excerpt",
    "not-matched": "not-matched",
    "not_matched": "not-matched",
    "overlap": "overlap",
    "novelty_overlap": "overlap",
    "distinct": "distinct",
    "novelty_supported": "distinct",
    "unchecked": "unchecked",
    "unchecked_contribution": "unchecked",
}


def classify_status_note(text: str | None, metadata: dict[str, Any] | None = None) -> ClassifiedNote:
    """Map a canned note (or metadata hint) onto a compact status code."""
    stripped = " ".join((text or "").split())
    if stripped in _EXACT_EN:
        return ClassifiedNote(STATUS_NOTES[_EXACT_EN[stripped]])
    matched = _NOT_MATCHED_RE.match(stripped)
    if matched:
        return ClassifiedNote(STATUS_NOTES["not-matched"], matched.group(1))
    meta = metadata or {}
    code = meta.get("note_code")
    if isinstance(code, str):
        mapped = _METADATA_ALIASES.get(code) or _METADATA_ALIASES.get(code.replace("_", "-"))
        if mapped:
            params = meta.get("note_params")
            return ClassifiedNote(
                STATUS_NOTES[mapped],
                str(params) if params else None,
            )
    return ClassifiedNote(None)


def translate_matched_setting_note(text: str, *, source: str, target: str) -> str | None:
    """Translate interpolated matched-setting notes without word-by-word fallback."""
    collapsed = " ".join(text.split())
    if source == "en" and target == "zh-Hans":
        matched = _NOT_MATCHED_RE.match(collapsed)
        if matched:
            return f"快照不是匹配设定（{matched.group(1)}）；不作为重叠证据。"
    if source == "zh-Hans" and target == "en":
        chinese = re.match(
            r"^快照不是匹配设定[（(](.+?)[）)]；不作为重叠证据。$",
            collapsed,
        )
        if chinese:
            return (
                "Snapshot is not a matched setting "
                f"({chinese.group(1)}); not used as overlap evidence."
            )
    return None
