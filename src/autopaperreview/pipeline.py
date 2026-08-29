"""Explicit local DAG runner with provenance, caching, and resumable manifests."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import artifact_from_path, resolve_artifact_path
from .config import LoadedConfig, StageConfig, load_config
from .hashing import hash_json
from .models import Artifact, RunManifest, StageRecord, StageStatus
from .registry import StageRegistry
from .stages.base import StageContext
from .stages.builtin import BUILTIN_STAGE_HANDLERS


class PipelineError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_registry() -> StageRegistry:
    registry = StageRegistry()
    for handler_type in BUILTIN_STAGE_HANDLERS:
        registry.register(handler_type())
    registry.load_entry_points()
    return registry


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def save_manifest(manifest: RunManifest, path: Path) -> None:
    content = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, content)


def load_manifest(path: Path) -> RunManifest:
    return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _source_artifact(loaded: LoadedConfig, source_path: Path) -> Artifact:
    return artifact_from_path(
        source_path,
        role="manuscript.source",
        base=loaded.workspace,
        base_name="workspace",
    )


def _run_id(loaded: LoadedConfig, source: Artifact) -> str:
    return f"{loaded.config.project.id}-{source.sha256[:12]}-{loaded.sha256[:8]}"


def _stage_signature(
    *,
    loaded: LoadedConfig,
    stage: StageConfig,
    source: Artifact,
    dependencies: dict[str, StageRecord],
    handler_material: dict[str, Any],
) -> str:
    dependency_outputs = {
        stage_id: sorted((artifact.role, artifact.sha256) for artifact in record.outputs)
        for stage_id, record in sorted(dependencies.items())
    }
    return hash_json(
        {
            "framework_version": __version__,
            "stage": stage.model_dump(mode="json"),
            "source_sha256": source.sha256,
            "dependency_outputs": dependency_outputs,
            "handler_material": handler_material,
            "network_policy": loaded.config.project.network_policy.value,
        }
    )


def _cached_outputs_valid(record: StageRecord, *, loaded: LoadedConfig, run_dir: Path) -> bool:
    if not record.outputs:
        return False
    for artifact in record.outputs:
        path = resolve_artifact_path(artifact, workspace=loaded.workspace, run_dir=run_dir)
        if not path.is_file():
            return False
        if artifact_from_path(path, role=artifact.role).sha256 != artifact.sha256:
            return False
    return True


def _replace_stage_record(manifest: RunManifest, record: StageRecord) -> None:
    for index, existing in enumerate(manifest.stages):
        if existing.id == record.id:
            manifest.stages[index] = record
            return
    manifest.stages.append(record)


def _promote_attempt(
    *,
    attempt_dir: Path,
    final_dir: Path,
    outcome_artifacts: list[Artifact],
    context: StageContext,
) -> list[Artifact]:
    mappings: list[tuple[Artifact, Path]] = []
    emitted_paths: set[Path] = set()
    for artifact in outcome_artifacts:
        source_path = resolve_artifact_path(
            artifact,
            workspace=context.workspace,
            run_dir=context.run_dir,
        )
        try:
            relative = source_path.relative_to(attempt_dir)
        except ValueError as exc:
            raise PipelineError(
                f"stage {context.stage.id} emitted an artifact outside its scratch directory: {source_path}"
            ) from exc
        if not source_path.is_file():
            raise PipelineError(f"stage {context.stage.id} declared a missing artifact: {source_path}")
        if relative in emitted_paths:
            raise PipelineError(f"stage {context.stage.id} declared the same artifact twice: {relative}")
        emitted_paths.add(relative)
        mappings.append((artifact, relative))

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = final_dir.with_name(f".{final_dir.name}.{uuid.uuid4().hex}.backup")
    if final_dir.exists():
        os.replace(final_dir, backup_dir)
    try:
        os.replace(attempt_dir, final_dir)
    except Exception:
        if backup_dir.exists() and not final_dir.exists():
            os.replace(backup_dir, final_dir)
        raise
    else:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

    promoted: list[Artifact] = []
    for original, relative in mappings:
        promoted.append(
            artifact_from_path(
                final_dir / relative,
                role=original.role,
                generated_by=original.generated_by,
                base=context.run_dir,
                base_name="run",
                metadata={key: value for key, value in original.metadata.items() if key != "path_base"},
            )
        )
    return promoted


def validate_config_paths(loaded: LoadedConfig, registry: StageRegistry | None = None) -> list[str]:
    registry = registry or default_registry()
    errors: list[str] = []
    source_path = loaded.resolve(loaded.config.project.source)
    if not source_path.is_file():
        errors.append(f"source file does not exist: {source_path}")
    for route in loaded.config.routes:
        if route.prompt and not loaded.resolve(route.prompt).is_file():
            errors.append(f"route {route.id} prompt does not exist: {loaded.resolve(route.prompt)}")
    for stage in loaded.config.stages:
        try:
            handler = registry.get(stage.type)
            handler.validate_params(stage.params)
        except KeyError as exc:
            errors.append(str(exc))
        except ValueError as exc:
            errors.append(f"stage {stage.id} has invalid parameters: {exc}")
        if stage.type == "import_issues" and "path" in stage.params:
            path = loaded.resolve(str(stage.params["path"]))
            if not path.is_file():
                errors.append(f"stage {stage.id} review input does not exist: {path}")
        if stage.type == "prompt_packet":
            wanted = {str(value) for value in stage.params.get("route_ids", [])}
            known_routes = {route.id for route in loaded.config.routes}
            missing_routes = sorted(wanted - known_routes)
            if missing_routes:
                errors.append(f"stage {stage.id} references unknown routes: {missing_routes}")
        if stage.type == "execute_review" and stage.params.get("fixture_path"):
            path = loaded.resolve(str(stage.params["fixture_path"]))
            if not path.is_file():
                errors.append(f"stage {stage.id} review fixture does not exist: {path}")
        if stage.type == "workspace_inspect":
            for raw in stage.params.get("paths", []):
                path = loaded.resolve(str(raw))
                if not path.exists():
                    errors.append(f"stage {stage.id} inspect path does not exist: {path}")
        if stage.type in {"command", "literature_grounding", "execute_review"} and stage.params.get(
            "requires_network"
        ):
            scope = str(stage.params.get("network_scope", "fulltext" if stage.type == "command" else "metadata"))
            policy = loaded.config.project.network_policy
            allowed = policy.value == "allow" or (policy.value == "metadata_only" and scope == "metadata")
            if not allowed:
                errors.append(f"stage {stage.id} requests {scope} network access under policy {policy.value}")
        if stage.type == "literature_grounding" and stage.params.get("snapshot_path"):
            path = loaded.resolve(str(stage.params["snapshot_path"]))
            if not path.is_file():
                errors.append(f"stage {stage.id} snapshot fixture does not exist: {path}")
    try:
        loaded.config.ordered_stages()
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def run_pipeline(
    config_path: str | Path,
    *,
    force: bool = False,
    registry: StageRegistry | None = None,
) -> RunManifest:
    loaded = load_config(config_path)
    registry = registry or default_registry()
    errors = validate_config_paths(loaded, registry)
    if errors:
        raise PipelineError("configuration validation failed:\n- " + "\n- ".join(errors))

    source_path = loaded.resolve(loaded.config.project.source)
    source = _source_artifact(loaded, source_path)
    run_root = loaded.resolve(loaded.config.project.run_root)
    run_dir = run_root / _run_id(loaded, source)
    manifest_path = run_dir / "run_manifest.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    config_snapshot_path = run_dir / "config.snapshot.json"
    _atomic_write(
        config_snapshot_path,
        json.dumps(loaded.raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    config_snapshot = artifact_from_path(
        config_snapshot_path,
        role="config.snapshot",
        generated_by="autopaperreview",
        base=run_dir,
        base_name="run",
    )

    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        if manifest.config_sha256 != loaded.sha256 or manifest.source.sha256 != source.sha256:
            raise PipelineError("existing run manifest does not match the current config/source hashes")
        manifest.config_snapshot = config_snapshot
        manifest.ended_at = None
    else:
        manifest = RunManifest(
            framework_version=__version__,
            run_id=run_dir.name,
            project_id=loaded.config.project.id,
            config_path=str(loaded.path.relative_to(loaded.workspace)),
            config_sha256=loaded.sha256,
            config_snapshot=config_snapshot,
            source=source,
            network_policy=loaded.config.project.network_policy,
            started_at=utc_now(),
        )
    save_manifest(manifest, manifest_path)

    for stage in loaded.config.ordered_stages():
        previous = manifest.stage(stage.id)
        dependencies: dict[str, StageRecord] = {}
        for dependency_id in stage.depends_on:
            dependency = manifest.stage(dependency_id)
            if dependency is None or dependency.status not in {StageStatus.success, StageStatus.skipped}:
                raise PipelineError(f"stage {stage.id} dependency {dependency_id} has not succeeded")
            dependencies[dependency_id] = dependency

        final_stage_dir = run_dir / "stages" / stage.id
        signature_context = StageContext(
            loaded_config=loaded,
            stage=stage,
            run_dir=run_dir,
            stage_dir=final_stage_dir,
            source_path=source_path,
            manifest=manifest,
            dependency_records=dependencies,
        )
        handler = registry.get(stage.type)
        handler_material = {
            "handler_type": handler.type_name,
            "handler_version": str(handler.version),
            **handler.signature_material(signature_context),
        }
        signature = _stage_signature(
            loaded=loaded,
            stage=stage,
            source=source,
            dependencies=dependencies,
            handler_material=handler_material,
        )
        if (
            not force
            and previous is not None
            and previous.status in {StageStatus.success, StageStatus.skipped}
            and previous.signature == signature
            and _cached_outputs_valid(previous, loaded=loaded, run_dir=run_dir)
        ):
            cached = previous.model_copy(deep=True)
            cached.status = StageStatus.skipped
            cached.metadata = {**cached.metadata, "cache_hit": True}
            _replace_stage_record(manifest, cached)
            save_manifest(manifest, manifest_path)
            continue

        attempt = (previous.attempt + 1) if previous else 1
        attempt_dir = run_dir / ".attempts" / f"{stage.id}-{attempt}-{uuid.uuid4().hex[:8]}"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        context = StageContext(
            loaded_config=loaded,
            stage=stage,
            run_dir=run_dir,
            stage_dir=attempt_dir,
            source_path=source_path,
            manifest=manifest,
            dependency_records=dependencies,
        )
        running = StageRecord(
            id=stage.id,
            type=stage.type,
            status=StageStatus.running,
            signature=signature,
            started_at=utc_now(),
            attempt=attempt,
        )
        _replace_stage_record(manifest, running)
        save_manifest(manifest, manifest_path)

        try:
            outcome = handler.run(context)
            promoted = _promote_attempt(
                attempt_dir=attempt_dir,
                final_dir=final_stage_dir,
                outcome_artifacts=outcome.artifacts,
                context=context,
            )
            completed = StageRecord(
                id=stage.id,
                type=stage.type,
                status=StageStatus.success,
                signature=signature,
                started_at=running.started_at,
                ended_at=utc_now(),
                attempt=attempt,
                outputs=promoted,
                metadata={
                    **outcome.metadata,
                    "cache_hit": False,
                    "handler_version": str(handler.version),
                },
            )
            _replace_stage_record(manifest, completed)
            save_manifest(manifest, manifest_path)
        except Exception as exc:
            failed = StageRecord(
                id=stage.id,
                type=stage.type,
                status=StageStatus.failed,
                signature=signature,
                started_at=running.started_at,
                ended_at=utc_now(),
                attempt=attempt,
                error=f"{type(exc).__name__}: {exc}",
                metadata={"attempt_dir": str(attempt_dir.relative_to(run_dir))},
            )
            _replace_stage_record(manifest, failed)
            manifest.ended_at = utc_now()
            save_manifest(manifest, manifest_path)
            raise PipelineError(f"stage {stage.id} failed: {exc}") from exc

    manifest.ended_at = utc_now()
    save_manifest(manifest, manifest_path)
    return manifest
