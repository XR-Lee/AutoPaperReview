"""Pure import adapters for the legacy one-off review JSON formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    Artifact,
    LocalizedText,
    ReviewIssue,
    ReviewPackage,
    ReviewRoute,
    RouteKind,
    SourceRecord,
)


def _localized(record: dict[str, Any], prefix: str) -> LocalizedText:
    english = record.get(f"{prefix}_en")
    chinese = record.get(f"{prefix}_zh") or record.get(f"{prefix}_zh_hans")
    plain = record.get(prefix)
    if english:
        translations = {"zh-Hans": chinese} if chinese else {}
        return LocalizedText(primary=str(english), language="en", translations=translations)
    if chinese:
        return LocalizedText(primary=str(chinese), language="zh-Hans")
    if plain:
        return LocalizedText(primary=str(plain), language="en")
    raise ValueError(f"legacy record is missing {prefix} text")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def migrate_legacy_issue(record: dict[str, Any]) -> ReviewIssue:
    known = {
        "id",
        "severity",
        "category",
        "title_en",
        "title_zh",
        "location",
        "evidence_en",
        "evidence_zh",
        "impact_en",
        "impact_zh",
        "required_action_en",
        "required_action_zh",
        "detected_by",
        "sources",
        "confidence",
    }
    unknown = {key: value for key, value in record.items() if key not in known}
    return ReviewIssue(
        id=str(record["id"]),
        severity=record["severity"],
        category=str(record.get("category") or "uncategorized"),
        title=_localized(record, "title"),
        location=str(record.get("location") or "unspecified"),
        evidence=_localized(record, "evidence"),
        impact=_localized(record, "impact"),
        required_action=_localized(record, "required_action"),
        confidence=float(record.get("confidence", 0.5)),
        route_ids=_string_list(record.get("detected_by")),
        source_ids=_string_list(record.get("sources")),
        metadata={"legacy_v0": unknown} if unknown else {},
    )


def _route_kind(route_id: str, record: dict[str, Any]) -> RouteKind:
    explicit = record.get("kind")
    if explicit:
        try:
            return RouteKind(explicit)
        except ValueError:
            pass
    return {
        "M1": RouteKind.deterministic,
        "M3": RouteKind.literature,
        "M4": RouteKind.executable,
        "M5": RouteKind.executable,
        "M6": RouteKind.visual,
    }.get(route_id, RouteKind.prompt)


def _migrate_routes(records: list[dict[str, Any]]) -> list[ReviewRoute]:
    routes: list[ReviewRoute] = []
    for record in records:
        route_id = str(record.get("id") or f"route-{len(routes) + 1}")
        name = record.get("name_en") or record.get("name") or record.get("name_zh") or route_id
        routes.append(
            ReviewRoute(
                id=route_id,
                name=str(name),
                kind=_route_kind(route_id, record),
                metadata={key: value for key, value in record.items() if key not in {"id", "name", "name_en", "name_zh", "kind"}},
            )
        )
    return routes


def _migrate_sources(records: list[dict[str, Any]]) -> list[SourceRecord]:
    sources: list[SourceRecord] = []
    for record in records:
        source_id = str(record.get("id") or f"source-{len(sources) + 1}")
        title = (
            record.get("title")
            or record.get("name")
            or record.get("citation")
            or record.get("description")
            or source_id
        )
        locator = (
            record.get("doi")
            or record.get("url")
            or record.get("path")
            or record.get("citation")
            or record.get("identifier")
            or f"legacy:{source_id}"
        )
        sources.append(
            SourceRecord(
                id=source_id,
                kind=str(record.get("type") or record.get("kind") or "legacy"),
                title=str(title),
                locator=str(locator),
                doi=record.get("doi"),
                metadata={key: value for key, value in record.items() if key not in {"id", "title", "name", "citation", "description", "identifier", "doi", "url", "path", "type", "kind"}},
            )
        )
    return sources


def _complete_references(
    issues: list[ReviewIssue],
    routes: list[ReviewRoute],
    sources: list[SourceRecord],
) -> tuple[list[ReviewRoute], list[SourceRecord]]:
    known_routes = {route.id for route in routes}
    for route_id in sorted({route_id for issue in issues for route_id in issue.route_ids} - known_routes):
        routes.append(
            ReviewRoute(
                id=route_id,
                name=f"Legacy route {route_id}",
                kind=_route_kind(route_id, {}),
                metadata={"legacy_placeholder": True},
            )
        )

    known_sources = {source.id for source in sources}
    for source_id in sorted({source_id for issue in issues for source_id in issue.source_ids} - known_sources):
        sources.append(
            SourceRecord(
                id=source_id,
                kind="legacy",
                title=f"Legacy source {source_id}",
                locator=f"legacy:{source_id}",
                metadata={"legacy_placeholder": True},
            )
        )
    return routes, sources


def migrate_legacy_package(
    raw: Any,
    *,
    manuscript: Artifact,
    project_id: str,
) -> ReviewPackage:
    if isinstance(raw, dict) and raw.get("schema_version") == "1.0" and "project_id" in raw:
        package = ReviewPackage.model_validate(raw)
        if package.project_id != project_id:
            raise ValueError(
                f"canonical package project_id {package.project_id!r} does not match {project_id!r}"
            )
        if package.manuscript.sha256 != manuscript.sha256:
            raise ValueError("canonical package manuscript hash does not match the supplied source")
        package.manuscript = manuscript
        return package

    if isinstance(raw, list):
        issues = [migrate_legacy_issue(record) for record in raw]
        routes, sources = _complete_references(issues, [], [])
        return ReviewPackage(
            project_id=project_id,
            manuscript=manuscript,
            routes=routes,
            sources=sources,
            issues=issues,
            metadata={"imported_from": "legacy-v0-issue-array"},
        )

    if not isinstance(raw, dict) or "issues" not in raw:
        raise ValueError("review input must be a canonical package, a legacy package, or a legacy issue array")

    manuscript_record = raw.get("manuscript") or {}
    claimed_source_hash = manuscript_record.get("source_sha256") or manuscript_record.get("sha256")
    if claimed_source_hash and str(claimed_source_hash).lower() != manuscript.sha256:
        raise ValueError("legacy package manuscript hash does not match the supplied source")
    recommendation = None
    if manuscript_record.get("recommendation_en") or manuscript_record.get("recommendation_zh"):
        recommendation = _localized(manuscript_record, "recommendation")

    strengths: dict[str, list[str]] = {}
    if raw.get("strengths_en"):
        strengths["en"] = [str(value) for value in raw["strengths_en"]]
    if raw.get("strengths_zh"):
        strengths["zh-Hans"] = [str(value) for value in raw["strengths_zh"]]

    acceptance_gate: dict[str, list[str]] = {}
    if raw.get("acceptance_gate_en"):
        acceptance_gate["en"] = [str(value) for value in raw["acceptance_gate_en"]]
    if raw.get("acceptance_gate_zh"):
        acceptance_gate["zh-Hans"] = [str(value) for value in raw["acceptance_gate_zh"]]

    issues = [migrate_legacy_issue(record) for record in raw["issues"]]
    routes, sources = _complete_references(
        issues,
        _migrate_routes(raw.get("methods", [])),
        _migrate_sources(raw.get("sources", [])),
    )
    known_top_level = {
        "manuscript",
        "methods",
        "sources",
        "issues",
        "strengths_en",
        "strengths_zh",
        "acceptance_gate_en",
        "acceptance_gate_zh",
    }
    return ReviewPackage(
        project_id=project_id,
        manuscript=manuscript,
        recommendation=recommendation,
        routes=routes,
        sources=sources,
        issues=issues,
        strengths=strengths,
        acceptance_gate=acceptance_gate,
        metadata={
            "imported_from": "legacy-v0-package",
            "legacy_manuscript": manuscript_record,
            "legacy_top_level": {
                key: value for key, value in raw.items() if key not in known_top_level
            },
        },
    )


def load_review_input(path: Path, *, manuscript: Artifact, project_id: str) -> ReviewPackage:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return migrate_legacy_package(raw, manuscript=manuscript, project_id=project_id)
