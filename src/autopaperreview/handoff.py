"""Agent handoff: run status, drop templates, and checked drop submission.

A host agent (a CLI harness such as Claude Code or Codex CLI, or any MCP
client) drives one review in a loop:

1. ``run`` the project. A stage whose agent drop is missing is recorded as
   ``awaiting_input``. Stages that do not depend on it still run.
2. ``run_status`` lists each awaiting request: the files to read, the drop
   path, and for a review judgment the packet digests the drop must echo.
3. ``submit_drop`` checks a drop with the code the stage runs, then writes it
   to the path declared in ``review.toml``.
4. ``run`` again. The stage reads the drop and the stages after it run.

The host agent's model reads the packets, so the manuscript goes wherever that
model runs. APR does not see that egress and does not record it on the manifest.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import load_review_adapter
from .artifacts import resolve_artifact_path
from .config import LoadedConfig, StageConfig, load_config
from .hashing import sha256_file
from .models import RunManifest, SarDimension, Severity, StageStatus, VisualFindingKind
from .pipeline import (
    RunLockedError,
    _atomic_write,
    adapter_waits_for_drop,
    awaiting_requests,
    default_registry,
    load_manifest,
    run_directory,
    run_is_locked,
    run_lock,
)
from .rubrics import CLAUSES, RUBRIC_ID
from .stages.base import StageContext

INSTRUCTIONS = {
    "review_judgment": (
        "Read every file in reads. The packets share the manuscript text, so read it once. Write one JSON "
        "object to drop_path: a review package with issues, evidence, and all seven dimension_scores, and "
        "packet_sha256 copied unchanged from echo.packet_sha256. Copy every excerpt and quote verbatim from "
        "the manuscript. Do not add an overall score. Check the drop with submit_drop, then run again."
    ),
    "visual_findings": (
        "Open every page image in reads. Write {\"pages_seen\": [every page id], \"findings\": [...]} to "
        "drop_path. Each finding carries the page id and the sha256 of the page it marks, a bbox, and the "
        "visible mark (not the caption). Check the drop with submit_drop, then run again."
    ),
    "related_work_findings": (
        "Search for each query. Write {\"candidates\": [{\"title\": ..., \"provider_payload\": ...}]} to "
        "drop_path. provider_payload is the raw arXiv, OpenAlex, or Semantic Scholar response for that "
        "paper; a candidate whose payload does not resolve an id and a title is dropped. Check the drop "
        "with submit_drop, then run again."
    ),
    "related_work_confirmation": (
        "Read the confirmation packet in reads. Write {\"confirmations\": [{\"external_id\": ..., \"title\": "
        "..., \"confirmed\": true or false, \"reason\": one line}]} to drop_path. The retrieval cap stays "
        "off until must-compare candidates are confirmed. Check the drop with submit_drop, then run again."
    ),
}


class HandoffError(ValueError):
    """The request cannot be answered in the current state of the project."""


@dataclass(frozen=True)
class DropSlot:
    stage: StageConfig
    kind: str
    drop_path: str


def drop_slots(loaded: LoadedConfig) -> list[DropSlot]:
    """Agent drops declared in ``review.toml``, in stage order."""
    slots: list[DropSlot] = []
    for stage in loaded.config.ordered_stages():
        params = stage.params
        if stage.type == "execute_review" and params.get("fixture_path"):
            if adapter_waits_for_drop(str(params.get("adapter") or "")):
                slots.append(DropSlot(stage, "review_judgment", str(params["fixture_path"])))
        elif stage.type == "visual_inspect" and params.get("findings_path"):
            if adapter_waits_for_drop(str(params.get("adapter") or "")):
                slots.append(DropSlot(stage, "visual_findings", str(params["findings_path"])))
        elif stage.type == "related_work_search":
            if params.get("mode", "agent") == "agent" and params.get("findings_path"):
                slots.append(DropSlot(stage, "related_work_findings", str(params["findings_path"])))
            if params.get("confirmation_path"):
                slots.append(DropSlot(stage, "related_work_confirmation", str(params["confirmation_path"])))
    return slots


def _manifest(run_dir: Path) -> RunManifest | None:
    path = run_dir / "run_manifest.json"
    return load_manifest(path) if path.is_file() else None


def _present(request: dict[str, Any], *, loaded: LoadedConfig, run_dir: Path, detail: bool) -> dict[str, Any]:
    view = dict(request)
    drop = request.get("drop_path")
    if drop:
        path = loaded.resolve(str(drop))
        view["drop_path_absolute"] = str(path)
        view["drop_exists"] = path.is_file()
    view["reads"] = [
        {**item, "absolute_path": str((run_dir / item["path"]).resolve())} for item in request.get("reads", [])
    ]
    kind = str(request.get("kind") or "")
    if kind in INSTRUCTIONS:
        view["instructions"] = INSTRUCTIONS[kind]
    if detail:
        view["template"] = drop_template(loaded, request)
    return view


def _notices(loaded: LoadedConfig, manifest: RunManifest, run_dir: Path, *, detail: bool) -> list[dict[str, Any]]:
    """Soft waits: the stage succeeded, but a later gate needs an agent answer."""
    notices: list[dict[str, Any]] = []
    declared = {stage.id: stage for stage in loaded.config.stages}
    for record in manifest.stages:
        if record.type != "related_work_search" or record.status not in {StageStatus.success, StageStatus.skipped}:
            continue
        if record.metadata.get("confirmation") != "awaiting_confirmation":
            continue
        stage = declared.get(record.id)
        confirmation_path = stage.params.get("confirmation_path") if stage is not None else None
        request: dict[str, Any] = {
            "stage_id": record.id,
            "stage_type": record.type,
            "kind": "related_work_confirmation",
            "drop_path": confirmation_path,
            "reads": [{"id": "confirmation_packet", "path": f"stages/{record.id}/confirmation_packet.json"}],
        }
        view = _present(request, loaded=loaded, run_dir=run_dir, detail=detail)
        if not confirmation_path:
            view["instructions"] = (
                "Set confirmation_path on this stage in review.toml, then answer the confirmation packet. "
                + INSTRUCTIONS["related_work_confirmation"]
            )
        notices.append(view)
    return notices


def _changes(loaded: LoadedConfig, manifest: RunManifest) -> list[str]:
    changes: list[str] = []
    if manifest.config_sha256 != loaded.sha256:
        changes.append("review.toml changed after the last run")
    source = loaded.resolve(loaded.config.project.source)
    if not source.is_file():
        changes.append(f"the manuscript source is missing: {source}")
    elif sha256_file(source) != manifest.source.sha256:
        changes.append("the manuscript changed after the last run")
    recorded = {stage.id for stage in manifest.stages}
    missing = [stage.id for stage in loaded.config.ordered_stages() if stage.id not in recorded]
    if missing:
        changes.append(f"stages that have not run: {missing}")
    for slot in drop_slots(loaded):
        record = manifest.stage(slot.stage.id)
        path = loaded.resolve(slot.drop_path)
        if record is not None and record.status == StageStatus.awaiting_input and path.is_file():
            changes.append(f"the {slot.kind} drop for stage {slot.stage.id} is written; run again")
    return changes


def _outputs(loaded: LoadedConfig, manifest: RunManifest, run_dir: Path) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for record in manifest.stages:
        if record.type != "report" or record.status not in {StageStatus.success, StageStatus.skipped}:
            continue
        for artifact in record.outputs:
            path = resolve_artifact_path(artifact, workspace=loaded.workspace, run_dir=run_dir)
            outputs.append({"stage_id": record.id, "role": artifact.role, "path": str(path)})
    if outputs:
        for name in ("review_report.md", "review_report.pdf", "annotated_manuscript.pdf"):
            path = loaded.path.parent / name
            if path.is_file():
                outputs.append({"stage_id": None, "role": "project.copy", "path": str(path)})
    return outputs


NEXT_STEPS = {
    "not_started": "Call run to start the review.",
    "running": "A run is in progress. Check status again later.",
    "interrupted": "The last run stopped inside a stage. Call run to resume it.",
    "failed": (
        "Read the failed stage's error. Fix the input it names; for an agent drop, submit a corrected drop. "
        "Then call run."
    ),
    "awaiting_input": "Answer each request in awaiting (read its files, then submit_drop), then call run.",
    "stale": "Inputs changed after the last run. Call run.",
    "complete": "The review is complete. Read the files in outputs.",
}


def run_status(config_path: str | Path, *, detail: bool = False) -> dict[str, Any]:
    """Machine-readable state of one project, read from its run manifest."""
    loaded = load_config(config_path)
    run_dir = run_directory(loaded)
    result: dict[str, Any] = {
        "project_id": loaded.config.project.id,
        "config": str(loaded.path),
        "run_dir": str(run_dir),
    }
    manifest = _manifest(run_dir)
    if manifest is None:
        return {
            **result,
            "state": "not_started",
            "stages": [],
            "awaiting": [],
            "notices": [],
            "changes": [],
            "outputs": [],
            "next": NEXT_STEPS["not_started"],
        }
    statuses = {stage.status for stage in manifest.stages}
    if run_is_locked(run_dir):
        # Between stages every record may read success while the run still rewrites the manifest.
        state = "running"
    elif StageStatus.running in statuses:
        state = "interrupted"
    elif StageStatus.failed in statuses:
        state = "failed"
    elif StageStatus.awaiting_input in statuses:
        state = "awaiting_input"
    else:
        state = "complete"
    changes = _changes(loaded, manifest)
    if state == "complete" and changes:
        state = "stale"
    slot_paths = {slot.stage.id: slot.drop_path for slot in drop_slots(loaded)}
    stages = []
    for record in manifest.stages:
        entry: dict[str, Any] = {
            "id": record.id,
            "type": record.type,
            "status": record.status.value,
            "attempt": record.attempt,
        }
        if record.status == StageStatus.failed:
            entry["error"] = record.error
            if record.id in slot_paths:
                entry["drop_path"] = slot_paths[record.id]
        if record.metadata.get("blocked_by"):
            entry["blocked_by"] = record.metadata["blocked_by"]
        stages.append(entry)
    return {
        **result,
        "state": state,
        "stages": stages,
        "awaiting": [
            _present(request, loaded=loaded, run_dir=run_dir, detail=detail)
            for request in awaiting_requests(manifest)
        ],
        "notices": _notices(loaded, manifest, run_dir, detail=detail),
        "changes": changes,
        "outputs": _outputs(loaded, manifest, run_dir),
        "manuscript_left_machine": bool(manifest.metadata.get("manuscript_left_machine", False)),
        "next": NEXT_STEPS[state],
    }


def _clauses() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for clause_id, (dimension, score, english, _chinese) in CLAUSES.items():
        grouped.setdefault(dimension, []).append({"id": clause_id, "score": score, "text": english})
    for items in grouped.values():
        items.sort(key=lambda item: item["score"])
    return grouped


def drop_template(loaded: LoadedConfig, request: dict[str, Any]) -> dict[str, Any]:
    """A fill-in skeleton for one request. Empty strings and nulls are for the agent to fill."""
    kind = request.get("kind")
    if kind == "review_judgment":
        routes = [route.id for route in loaded.config.routes]
        return {
            "drop": {
                "packet_sha256": dict((request.get("echo") or {}).get("packet_sha256") or {}),
                "summary_en": "",
                "strengths_en": [""],
                "sources": [
                    {
                        "id": "S1",
                        "type": "manuscript",
                        "title": loaded.config.project.title,
                        "path": loaded.config.project.source,
                    }
                ],
                "evidence": [{"id": "E1", "source_id": "S1", "locator": "", "claim": "", "excerpt": ""}],
                "issues": [
                    {
                        "id": "I001",
                        "severity": "",
                        "category": "",
                        "title_en": "",
                        "location": "",
                        "evidence_en": "",
                        "impact_en": "",
                        "required_action_en": "",
                        "paper_said_en": "",
                        "explanation_en": "",
                        "quote": "",
                        "detected_by": [],
                        "sources": ["S1"],
                        "evidence_ids": ["E1"],
                        "confidence": None,
                    }
                ],
                "dimension_scores": [
                    {
                        "dimension": dimension.value,
                        "clause_id": None,
                        "score": None,
                        "rubric_id": RUBRIC_ID,
                        "evidence_ids": ["E1"],
                        "route_ids": [],
                        "rationale": {"primary": "", "language": "en"},
                    }
                    for dimension in SarDimension
                ],
            },
            "help": {
                "severity": [item.value for item in Severity],
                "route_ids": routes,
                "clauses": _clauses(),
                "rules": [
                    "Copy packet_sha256 unchanged. A drop with other digests is rejected.",
                    "Each excerpt and quote is a verbatim span of the manuscript."
                    + (" The stage rejects a span it cannot find." if request.get("verify_excerpts") else ""),
                    "Give all seven dimensions. score is the score of the chosen clause_id.",
                    "Every evidence_ids entry names an evidence record; detected_by and route_ids name routes.",
                    "Major and critical issues need paper_said_en, explanation_en, and quote for the export gate.",
                    "Do not add an overall score. The rate stage assigns the venue bin.",
                    "For a bilingual report, add the matching _zh fields.",
                ],
            },
        }
    if kind == "visual_findings":
        pages = [item for item in request.get("reads", []) if item.get("sha256")]
        return {
            "drop": {
                "pages_seen": [item["id"] for item in pages],
                "findings": [
                    {
                        "id": "V1",
                        "page": None,
                        "page_id": pages[0]["id"] if pages else "",
                        "bbox": [None, None, None, None],
                        "page_image_sha256": pages[0]["sha256"] if pages else "",
                        "kind": "",
                        "visible_mark": "",
                    }
                ],
            },
            "help": {
                "kind": [item.value for item in VisualFindingKind],
                "rules": [
                    "List a page in pages_seen only after opening its image.",
                    "page_image_sha256 is the sha256 listed for that page in reads.",
                    "bbox is [xmin, ymin, xmax, ymax]. visible_mark describes what is on the page, not the caption.",
                    "An empty findings list is valid when the pages show no problem.",
                ],
            },
        }
    if kind == "related_work_findings":
        return {
            "drop": {"candidates": [{"title": "", "provider_payload": ""}]},
            "help": {"queries": request.get("queries", []), "rules": [INSTRUCTIONS["related_work_findings"]]},
        }
    if kind == "related_work_confirmation":
        return {
            "drop": {"confirmations": [{"external_id": "", "title": "", "confirmed": None, "reason": ""}]},
            "help": {"rules": [INSTRUCTIONS["related_work_confirmation"]]},
        }
    return {"drop": {}, "help": {"rules": [f"no template for drop kind {kind!r}"]}}


def _select_slot(
    loaded: LoadedConfig,
    manifest: RunManifest | None,
    *,
    stage_id: str | None,
    kind: str | None,
) -> DropSlot:
    slots = drop_slots(loaded)
    if stage_id:
        slots = [slot for slot in slots if slot.stage.id == stage_id]
    if kind:
        slots = [slot for slot in slots if slot.kind == kind]
    if len(slots) == 1:
        return slots[0]
    if not slots:
        scope = f" for stage {stage_id}" if stage_id else ""
        raise HandoffError(f"review.toml declares no agent drop{scope}" + (f" of kind {kind}" if kind else ""))
    waiting = {(item.get("stage_id"), item.get("kind")) for item in awaiting_requests(manifest)} if manifest else set()
    matching = [slot for slot in slots if (slot.stage.id, slot.kind) in waiting]
    if len(matching) == 1:
        return matching[0]
    names = ", ".join(f"{slot.stage.id}/{slot.kind}" for slot in slots)
    raise HandoffError(f"several agent drops match; pass stage_id and kind ({names})")


def _context(loaded: LoadedConfig, manifest: RunManifest, run_dir: Path, stage: StageConfig, scratch: Path) -> StageContext:
    dependencies = {}
    for dependency_id in stage.depends_on:
        record = manifest.stage(dependency_id)
        if record is None or record.status not in {StageStatus.success, StageStatus.skipped}:
            raise HandoffError(
                f"stage {stage.id} dependency {dependency_id} has not succeeded; run the project until "
                f"{stage.id} awaits input, then submit"
            )
        dependencies[dependency_id] = record
    return StageContext(
        loaded_config=loaded,
        stage=stage,
        run_dir=run_dir,
        stage_dir=scratch,
        source_path=loaded.resolve(loaded.config.project.source),
        manifest=manifest,
        dependency_records=dependencies,
    )


def _check_review(context: StageContext, candidate: Path) -> dict[str, Any]:
    handler = default_registry().get(context.stage.type)
    request = handler.build_request(context, fixture=candidate)
    adapter = load_review_adapter(str(context.stage.params["adapter"]))
    result = adapter.execute(request, context.stage_dir)
    summary: dict[str, Any] = {
        "issue_count": len(result.package.issues),
        "evidence_count": len(result.package.evidence),
        "dimension_count": len(result.package.dimension_scores),
    }
    if "excerpt_check" in result.metadata:
        summary["excerpt_check"] = result.metadata["excerpt_check"]
    return summary


def _check_visual(context: StageContext, candidate: Path) -> dict[str, Any]:
    from .stages.visual_stage import VisualInspectParams, _manifest_from, _request, check_page_finding
    from .visual import load_visual_inspector

    params = VisualInspectParams.model_validate(context.stage.params)
    pages_manifest, page_dir = _manifest_from(context)
    inspector = load_visual_inspector(str(params.adapter))
    inspector.bind(findings_path=candidate)
    pages = pages_manifest.get("pages", [])
    if params.require_pages_seen and hasattr(inspector, "assert_pages_seen"):
        inspector.assert_pages_seen([str(item.get("page_id") or item["page"]) for item in pages])
    by_sha = {item["sha256"]: item for item in pages}
    found = 0
    for item in pages:
        for finding in inspector.inspect(_request(item, page_dir), context.stage_dir):
            check_page_finding(finding, by_sha)
            found += 1
    return {"page_count": len(pages), "finding_count": found}


def _check_related_findings(candidate: Path) -> tuple[dict[str, Any], list[str]]:
    from .related_work import load_agent_findings

    hits, logs = load_agent_findings(candidate)
    warnings = [entry.detail for entry in logs if entry.status == "dropped"]
    return {"candidate_count": len(hits)}, warnings


def _check_confirmation(candidate: Path) -> dict[str, Any]:
    from .related_work import confirmation_file_state

    supplied, items = confirmation_file_state(candidate)
    if not supplied:
        raise ValueError("a confirmation drop is an object with a confirmations list")
    return {"confirmation_count": len(items)}


def submit_drop(
    config_path: str | Path,
    drop: dict[str, Any] | str,
    *,
    stage_id: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Check one agent drop and write it to its declared path.

    The check is the one the stage runs: packet digests, schema, the seven
    dimensions, and, with ``verify_excerpts``, quoted spans; for visual
    findings, the page hash, number, and id. A rejected drop is not written.
    A drop that passes replaces any earlier drop at that path. The run lock is
    held from the check to the write.
    """
    loaded = load_config(config_path)
    run_dir = run_directory(loaded)
    manifest = _manifest(run_dir)
    slot = _select_slot(loaded, manifest, stage_id=stage_id, kind=kind)
    if manifest is None:
        raise HandoffError("the project has not run yet; call run so the stage can say what it needs")
    try:
        # Hold the run lock so a run cannot start between the check and the write.
        with run_lock(run_dir):
            return _submit_locked(loaded, load_manifest(run_dir / "run_manifest.json"), run_dir, slot, drop)
    except RunLockedError as exc:
        raise HandoffError("a run is in progress; submit the drop after it finishes") from exc


def _submit_locked(
    loaded: LoadedConfig,
    manifest: RunManifest,
    run_dir: Path,
    slot: DropSlot,
    drop: dict[str, Any] | str,
) -> dict[str, Any]:
    target = loaded.resolve(slot.drop_path).resolve()
    if target == loaded.path:
        raise HandoffError("the declared drop path is the review config itself")
    base: dict[str, Any] = {"stage_id": slot.stage.id, "kind": slot.kind, "drop_path": str(target)}
    text = drop if isinstance(drop, str) else json.dumps(drop, ensure_ascii=False, indent=2)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {**base, "status": "rejected", "errors": [f"the drop is not valid JSON: {exc}"]}
    if not isinstance(payload, dict):
        return {**base, "status": "rejected", "errors": ["the drop must be a JSON object"]}
    errors: list[str] = []
    warnings: list[str] = []
    if slot.kind in {"review_judgment", "visual_findings"}:
        changes = _changes(loaded, manifest)
        if "the manuscript changed after the last run" in changes:
            raise HandoffError("the manuscript changed after the last run; call run first so the packets are current")
    scratch = run_dir / ".attempts" / f"submit-{slot.stage.id}-{uuid.uuid4().hex[:8]}"
    context = None
    if slot.kind in {"review_judgment", "visual_findings"}:
        # A missing dependency is a state error for the caller, not a fault in the drop.
        context = _context(loaded, manifest, run_dir, slot.stage, scratch / "stage")
    scratch.mkdir(parents=True)
    summary: dict[str, Any] = {}
    try:
        candidate = scratch / "drop.json"
        candidate.write_text(text, encoding="utf-8")
        try:
            if slot.kind == "review_judgment":
                summary = _check_review(context, candidate)
            elif slot.kind == "visual_findings":
                summary = _check_visual(context, candidate)
            elif slot.kind == "related_work_findings":
                summary, warnings = _check_related_findings(candidate)
            else:
                summary = _check_confirmation(candidate)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if errors:
        return {**base, "status": "rejected", "errors": errors, "warnings": warnings}
    replaced = target.is_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, text if text.endswith("\n") else text + "\n")
    return {
        **base,
        "status": "accepted",
        "sha256": sha256_file(target),
        "replaced": replaced,
        "warnings": warnings,
        **summary,
        "next": "Call run. The stage reads this drop.",
    }
