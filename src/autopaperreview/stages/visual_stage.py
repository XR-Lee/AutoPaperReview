"""Render manuscript pages and run the deterministic venue format checklist."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import artifact_from_path
from ..awaiting import AwaitingInput
from ..figures import sibling_pdf
from ..hashing import sha256_file
from ..models import ReviewPackage, ReviewRoute, RouteKind, VisualFinding
from ..reporting import write_package_json
from ..model_exec import DEFAULT_MODEL_TIMEOUT_SECONDS, public_base_url
from ..visual import (
    finding_matches_caption,
    format_check_pdf,
    load_profile,
    load_visual_inspector,
    profile_path,
    render_pdf_pages,
)
from .base import StageContext, StageHandler, StageOutcome


class ParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _artifact(path: Path, context: StageContext, role: str):
    return artifact_from_path(
        path,
        role=role,
        generated_by=context.stage.id,
        base=context.run_dir,
        base_name="run",
    )


def _visual_route_enabled(context: StageContext) -> bool:
    return any(route.kind == RouteKind.visual for route in context.loaded_config.config.routes)


def _manuscript_pdf(context: StageContext) -> Path | None:
    return sibling_pdf(context.source_path)


class RenderPagesParams(ParamsModel):
    dpi: int | None = Field(default=None, ge=72, le=300)
    profile: str = "iclr2027"
    require_pdf: bool = True
    supplement: str | None = None


def _supplement_pdf(context: StageContext) -> Path | None:
    """Render param wins. Otherwise use the file copied by ingest."""
    params = RenderPagesParams.model_validate(context.stage.params)
    if params.supplement:
        path = context.loaded_config.resolve(params.supplement)
        if not path.is_file():
            raise FileNotFoundError(f"supplement file does not exist: {path}")
        return path
    for stage_id in context.stage.depends_on:
        directory = context.dependency_dir(stage_id)
        meta_path = directory / "supplement.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        filename = str(meta.get("filename") or "")
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


class RenderPagesStage(StageHandler):
    type_name = "render_pages"
    version = "2"
    params_model = RenderPagesParams

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = RenderPagesParams.model_validate(context.stage.params)
        pdf = _manuscript_pdf(context)
        material: dict[str, Any] = {
            "profile_sha256": sha256_file(profile_path(params.profile)),
            "pdf_sha256": None if pdf is None else sha256_file(pdf),
            "dpi": params.dpi,
        }
        if params.supplement:
            material["supplement_sha256"] = sha256_file(context.loaded_config.resolve(params.supplement))
        return material

    def run(self, context: StageContext) -> StageOutcome:
        params = RenderPagesParams.model_validate(context.stage.params)
        profile = load_profile(params.profile)
        pdf = _manuscript_pdf(context)
        if pdf is None:
            if params.require_pdf and _visual_route_enabled(context):
                raise FileNotFoundError(
                    "render_pages requires a sibling manuscript PDF when a visual route is enabled"
                )
            if params.require_pdf:
                raise FileNotFoundError("render_pages requires a manuscript PDF")
            manifest_path = context.stage_dir / "manifest.json"
            manifest_path.write_text('{"pages": [], "skipped": true}\n', encoding="utf-8")
            return StageOutcome(
                artifacts=[_artifact(manifest_path, context, "review.page_manifest")],
                metadata={"skipped": True, "page_count": 0},
            )
        dpi = int(params.dpi or profile.get("render_dpi") or 150)
        page_dir = context.stage_dir / "pages"
        manifest = render_pdf_pages(pdf, page_dir, dpi=dpi)
        supplement = _supplement_pdf(context)
        if supplement is not None:
            extra = render_pdf_pages(
                supplement,
                page_dir,
                dpi=dpi,
                document_name="supplement",
                page_id_prefix="S",
                filename_prefix="supplement",
            )
            manifest = {
                "pdf_sha256": manifest["pdf_sha256"],
                "supplement_sha256": extra["pdf_sha256"],
                "dpi": dpi,
                "page_count": manifest["page_count"],
                "supplement_page_count": extra["page_count"],
                "pages": [*manifest["pages"], *extra["pages"]],
            }
        rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        (page_dir / "manifest.json").write_text(rendered, encoding="utf-8")
        manifest_path = context.stage_dir / "manifest.json"
        manifest_path.write_text(rendered, encoding="utf-8")
        artifacts = [_artifact(manifest_path, context, "review.page_manifest")]
        for item in manifest["pages"]:
            artifacts.append(_artifact(page_dir / item["filename"], context, "review.page_image"))
        return StageOutcome(
            artifacts=artifacts,
            metadata={
                "page_count": manifest["page_count"],
                "supplement_page_count": manifest.get("supplement_page_count", 0),
                "dpi": dpi,
                "pdf_sha256": manifest["pdf_sha256"],
                "page_ids": [str(item.get("page_id") or item["page"]) for item in manifest["pages"]],
            },
        )


class FormatCheckParams(ParamsModel):
    profile: str = "iclr2027"


def check_page_finding(finding: VisualFinding, by_sha: dict[str, dict[str, Any]]) -> None:
    """A finding must name a rendered page by hash, page number, and page id, and not repeat the caption."""
    if finding.page_image_sha256 not in by_sha:
        raise ValueError(f"visual finding {finding.id} page hash is not in the render manifest")
    record = by_sha[finding.page_image_sha256]
    record_id = str(record.get("page_id") or record["page"])
    if int(record["page"]) != finding.page:
        raise ValueError(f"visual finding {finding.id} page does not match the image hash")
    if finding.page_id and finding.page_id != record_id:
        raise ValueError(f"visual finding {finding.id} page id does not match the image hash")
    if finding_matches_caption(finding):
        raise ValueError(f"visual finding {finding.id} repeats the caption")


def _manifest_from(context: StageContext) -> tuple[dict[str, Any], Path]:
    for stage_id in context.stage.depends_on:
        stage_dir = context.dependency_dir(stage_id)
        candidate = stage_dir / "manifest.json"
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8")), stage_dir / "pages"
    raise FileNotFoundError("format_check requires a render_pages manifest")


class FormatCheckStage(StageHandler):
    type_name = "format_check"
    version = "1"
    params_model = FormatCheckParams
    reads_source = True

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = FormatCheckParams.model_validate(context.stage.params)
        return {"profile_sha256": sha256_file(profile_path(params.profile))}

    def run(self, context: StageContext) -> StageOutcome:
        params = FormatCheckParams.model_validate(context.stage.params)
        profile = load_profile(params.profile)
        pdf = _manuscript_pdf(context)
        if pdf is None:
            raise FileNotFoundError("format_check requires a manuscript PDF")
        manifest, page_dir = _manifest_from(context)
        checks = format_check_pdf(pdf, manifest, profile, page_dir=page_dir)
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            format_checks=checks,
            metadata={"format_profile": profile["id"], "visual_status": "format_only"},
        )
        output = context.stage_dir / "package.json"
        write_package_json(package, output)
        failed = [item.id for item in checks if not item.passed]
        return StageOutcome(
            artifacts=[_artifact(output, context, "review.package.format")],
            metadata={"failed": failed, "check_count": len(checks), "profile": profile["id"]},
        )


class VisualInspectParams(ParamsModel):
    adapter: str | None = None
    findings_path: str | None = None
    endpoint: str | None = None
    api_key_env: str = "APR_VISION_API_KEY"
    require_pages_seen: bool = True
    model: str | None = None
    allow_remote: bool = False
    base_url: str | None = None
    packet_route: str | None = None
    timeout_seconds: float = Field(default=DEFAULT_MODEL_TIMEOUT_SECONDS, gt=0)
    temperature: float = 0
    json_object: bool = True


class VisualInspectStage(StageHandler):
    """Optional multimodal adapter. Core records the contract; it does not call a provider."""

    type_name = "visual_inspect"
    version = "2"
    params_model = VisualInspectParams
    reads_source = True

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = VisualInspectParams.model_validate(context.stage.params)
        material: dict[str, Any] = {
            "adapter": params.adapter,
            "endpoint": params.endpoint,
            "api_key_env": params.api_key_env,
        }
        if params.model:
            material["model"] = params.model
        if params.base_url:
            material["base_url"] = public_base_url(params.base_url)
        if params.allow_remote:
            material["allow_remote"] = True
        if params.packet_route:
            material["packet_route"] = params.packet_route
        if params.findings_path:
            path = _resolve_workspace_file(context, params.findings_path)
            material["findings_sha256"] = sha256_file(path) if path.is_file() else None
        return material

    def run(self, context: StageContext) -> StageOutcome:
        params = VisualInspectParams.model_validate(context.stage.params)
        manifest, page_dir = _manifest_from(context)
        findings: list[VisualFinding] = []
        status = "unchecked"
        if params.adapter:
            inspector = load_visual_inspector(params.adapter)
            findings_path = (
                _resolve_workspace_file(context, params.findings_path) if params.findings_path else None
            )
            network_allowed = context.loaded_config.config.project.network_policy.value == "allow"
            if getattr(inspector, "requires_network", False) and params.endpoint and not network_allowed:
                policy = context.loaded_config.config.project.network_policy.value
                raise PermissionError(
                    f"stage {context.stage.id} sends page images and requires network_policy = allow "
                    f"(current policy: {policy})"
                )
            if hasattr(inspector, "bind"):
                inspector.bind(
                    findings_path=findings_path,
                    endpoint=params.base_url or params.endpoint,
                    api_key_env=params.api_key_env,
                    network_allowed=network_allowed,
                )
            if hasattr(inspector, "configure"):
                packets: dict[str, str] = {}
                for stage_id in context.stage.depends_on:
                    for path in sorted(context.dependency_dir(stage_id).glob("*.md")):
                        packets[path.stem] = path.read_text(encoding="utf-8")
                running = context.manifest.stage(context.stage.id)
                inspector.configure(
                    model=params.model,
                    allow_remote=params.allow_remote,
                    base_url=params.base_url or params.endpoint,
                    packets=packets,
                    packet_route=params.packet_route,
                    network_policy=context.loaded_config.config.project.network_policy.value,
                    stage_signature="" if running is None else running.signature,
                    timeout_seconds=params.timeout_seconds,
                    temperature=params.temperature,
                    json_object=params.json_object,
                    routes=[
                        ReviewRoute(
                            id=route.id,
                            name=route.name,
                            kind=route.kind,
                            prompt_version=route.prompt_version,
                            model=route.model,
                            manuscript_access=route.manuscript_access,
                            network_access=route.network_access,
                            metadata=dict(route.metadata),
                        )
                        for route in context.loaded_config.config.routes
                    ],
                )
            page_ids = [str(item.get("page_id") or item["page"]) for item in manifest.get("pages", [])]
            if getattr(inspector, "waits_for_drop", False) and findings_path is not None and not findings_path.is_file():
                reads = [
                    {
                        "id": str(item.get("page_id") or item["page"]),
                        "path": (page_dir / item["filename"]).relative_to(context.run_dir).as_posix(),
                        "sha256": item["sha256"],
                    }
                    for item in manifest.get("pages", [])
                ]
                for stage_id in context.stage.depends_on if params.packet_route else []:
                    packet = context.dependency_dir(stage_id) / f"{params.packet_route}.md"
                    if packet.is_file():
                        reads.insert(0, {"id": params.packet_route, "path": packet.relative_to(context.run_dir).as_posix()})
                raise AwaitingInput(
                    f"visual findings drop does not exist yet: {findings_path}",
                    request={"kind": "visual_findings", "drop_path": params.findings_path, "reads": reads},
                )
            if (
                params.require_pages_seen
                and hasattr(inspector, "assert_pages_seen")
                and findings_path is not None
            ):
                inspector.assert_pages_seen(page_ids)
            by_sha = {item["sha256"]: item for item in manifest.get("pages", [])}
            for item in manifest.get("pages", []):
                request_findings = inspector.inspect(
                    _request(item, page_dir),
                    context.stage_dir,
                )
                for finding in request_findings:
                    check_page_finding(finding, by_sha)
                    findings.append(finding)
            status = getattr(inspector, "status", None) or ("checked" if findings or params.adapter else "unchecked")
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            visual_findings=findings,
            metadata={"visual_status": status},
        )
        output = context.stage_dir / "package.json"
        write_package_json(package, output)
        artifacts = [_artifact(output, context, "review.package.visual")]
        model_dir = context.stage_dir / "model"
        model_meta: dict[str, Any] = {}
        if model_dir.is_dir():
            for path in sorted(model_dir.rglob("*")):
                if path.is_file():
                    artifacts.append(_artifact(path, context, "review.adapter.raw"))
            index = model_dir / "exchanges.json"
            if index.is_file():
                loaded = json.loads(index.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    model_meta = loaded
        return StageOutcome(
            artifacts=artifacts,
            metadata={
                "finding_count": len(findings),
                "visual_status": package.metadata["visual_status"],
                **model_meta,
            },
        )


def _resolve_workspace_file(context: StageContext, relative: str) -> Path:
    from ..integrity import assert_inside_workspace

    candidate = Path(relative)
    root = candidate if candidate.is_absolute() else context.workspace / candidate
    return assert_inside_workspace(root, context.workspace)


def _request(item: dict[str, Any], page_dir: Path):
    from ..visual import VisualPageRequest

    return VisualPageRequest(
        page=int(item["page"]),
        page_id=str(item.get("page_id") or item["page"]),
        document=str(item.get("document") or "main"),
        page_image_sha256=item["sha256"],
        image_path=str(page_dir / item["filename"]),
        width_px=int(item["width_px"]),
        height_px=int(item["height_px"]),
    )
