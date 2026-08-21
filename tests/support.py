from __future__ import annotations

from typing import Any

from autopaperreview.models import Artifact, LocalizedText, ReviewIssue


TEST_SHA256 = "a" * 64


def make_artifact(**overrides: Any) -> Artifact:
    values: dict[str, Any] = {
        "path": "manuscript.txt",
        "sha256": TEST_SHA256,
        "size_bytes": 12,
        "role": "manuscript.source",
        "media_type": "text/plain",
    }
    values.update(overrides)
    return Artifact(**values)


def localized(value: str) -> LocalizedText:
    return LocalizedText(primary=value, language="en")


def make_issue(**overrides: Any) -> ReviewIssue:
    values: dict[str, Any] = {
        "id": "I001",
        "severity": "major",
        "category": "evaluation",
        "title": localized("Evaluation is incomplete"),
        "location": "Results",
        "evidence": localized("Only one run is reported."),
        "impact": localized("Stability is unknown."),
        "required_action": localized("Report repeated runs."),
        "confidence": 0.75,
    }
    values.update(overrides)
    return ReviewIssue(**values)


def legacy_issue(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "I001",
        "severity": "major",
        "category": "evaluation",
        "title_en": "Evaluation is incomplete",
        "title_zh": "评估不完整",
        "location": "Results",
        "evidence_en": "Only one run is reported.",
        "evidence_zh": "只报告了一次运行。",
        "impact_en": "Stability is unknown.",
        "impact_zh": "稳定性未知。",
        "required_action_en": "Report repeated runs.",
        "required_action_zh": "报告重复运行。",
        "detected_by": ["M0"],
        "sources": ["S1"],
        "confidence": 0.75,
    }
    values.update(overrides)
    return values
