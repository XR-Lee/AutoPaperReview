"""Local-first built-in pipeline stages."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import artifact_from_path
from ..hashing import hash_json, sha256_file
from ..migration import load_review_input
from ..literature import (
    generate_related_work_queries,
    load_retriever,
    load_snapshot_fixture,
    snapshots_to_records,
)
from ..models import (
    Artifact,
    DimensionScore,
    NetworkPolicy,
    RelatedWorkQuery,
    RetrievedSourceSnapshot,
    ReviewClaim,
    ReviewIssue,
    ReviewPackage,
    ReviewRoute,
)
from ..reporting import render_markdown, write_package_json
from .base import StageContext, StageHandler, StageOutcome


class ParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifact(path: Path, context: StageContext, role: str) -> Artifact:
    return artifact_from_path(
        path,
        role=role,
        generated_by=context.stage.id,
        base=context.run_dir,
        base_name="run",
    )


def _enforce_network_declaration(context: StageContext, *, requires_network: bool, network_scope: str) -> None:
    if not requires_network:
        return
    policy = context.loaded_config.config.project.network_policy
    allowed = policy == NetworkPolicy.allow or (
        policy == NetworkPolicy.metadata_only and network_scope == "metadata"
    )
    if not allowed:
        raise PermissionError(
            f"stage {context.stage.id} requests {network_scope} network access under policy {policy.value}"
        )


class IngestParams(ParamsModel):
    copy_source: bool | None = Field(default=None, alias="copy")


class IngestStage(StageHandler):
    type_name = "ingest"
    params_model = IngestParams

    def run(self, context: StageContext) -> StageOutcome:
        params = IngestParams.model_validate(context.stage.params)
        should_copy = (
            context.loaded_config.config.project.copy_source
            if params.copy_source is None
            else params.copy_source
        )
        source_artifact = context.manifest.source
        artifacts: list[Artifact] = []
        if should_copy:
            copied = context.stage_dir / context.source_path.name
            shutil.copy2(context.source_path, copied)
            source_artifact = _artifact(copied, context, "manuscript.snapshot")
            artifacts.append(source_artifact)
        descriptor = context.stage_dir / "source.json"
        descriptor_artifact = source_artifact.model_copy(deep=True)
        if should_copy:
            descriptor_artifact.path = f"stages/{context.stage.id}/{context.source_path.name}"
            descriptor_artifact.metadata = {
                **descriptor_artifact.metadata,
                "path_base": "run",
            }
        _write_json(descriptor, descriptor_artifact.model_dump(mode="json"))
        artifacts.append(_artifact(descriptor, context, "manuscript.descriptor"))
        return StageOutcome(artifacts=artifacts, metadata={"copied": should_copy})


class CommandParams(ParamsModel):
    command: list[str] = Field(min_length=1)
    cwd: str | None = None
    outputs: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=900, ge=1)
    allowed_return_codes: list[int] = Field(default_factory=lambda: [0])
    requires_network: bool = False
    network_scope: str = "fulltext"
    env: dict[str, str] = Field(default_factory=dict)
    inherit_env: list[str] = Field(
        default_factory=lambda: [
            "PATH",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "TMPDIR",
            "TEMP",
            "TMP",
        ]
    )


class CommandStage(StageHandler):
    type_name = "command"
    params_model = CommandParams

    def _params(self, context: StageContext) -> CommandParams:
        return CommandParams.model_validate(context.stage.params)

    def _cwd(self, context: StageContext, params: CommandParams, values: dict[str, str]) -> Path:
        cwd = context.workspace if params.cwd is None else Path(params.cwd.format_map(values))
        return cwd if cwd.is_absolute() else (context.workspace / cwd).resolve()

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = self._params(context)
        file_hashes: dict[str, str] = {}
        values = {
            "source": str(context.source_path),
            "stage_dir": str(context.stage_dir),
            "run_dir": str(context.run_dir),
            "workspace": str(context.workspace),
        }
        cwd = self._cwd(context, params, values)
        for index, argument in enumerate(params.command):
            if "{stage_dir}" in argument or "{run_dir}" in argument:
                continue
            candidate = Path(argument.format_map(values))
            if not candidate.is_absolute():
                candidate = cwd / candidate
            try:
                candidate.resolve().relative_to(context.run_dir.resolve())
                continue
            except ValueError:
                pass
            if candidate.is_file():
                file_hashes[f"argument:{index}"] = sha256_file(candidate)
        inherited_environment = {
            name: hash_json(os.environ[name])
            for name in sorted(set(params.inherit_env))
            if name in os.environ
        }
        return {
            "command_input_hashes": file_hashes,
            "inherited_environment_hashes": inherited_environment,
            "runtime": {
                "python": list(sys.version_info[:3]),
                "platform": sys.platform,
                "machine": platform.machine(),
            },
        }

    def run(self, context: StageContext) -> StageOutcome:
        params = self._params(context)
        policy = context.loaded_config.config.project.network_policy
        _enforce_network_declaration(
            context, requires_network=params.requires_network, network_scope=params.network_scope
        )

        values = {
            "source": str(context.source_path),
            "stage_dir": str(context.stage_dir),
            "run_dir": str(context.run_dir),
            "workspace": str(context.workspace),
        }
        command = [argument.format_map(values) for argument in params.command]
        cwd = self._cwd(context, params, values)

        environment = {
            name: os.environ[name]
            for name in set(params.inherit_env)
            if name in os.environ
        }
        environment.update(params.env)
        environment["AUTOPAPERREVIEW_NETWORK_POLICY"] = policy.value
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=params.timeout_seconds,
            check=False,
        )
        stdout_path = context.stage_dir / "stdout.log"
        stderr_path = context.stage_dir / "stderr.log"
        command_path = context.stage_dir / "command.json"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        logical_values = {
            "source": "{source}",
            "stage_dir": "{stage_dir}",
            "run_dir": "{run_dir}",
            "workspace": "{workspace}",
        }
        logical_command = [argument.format_map(logical_values) for argument in params.command]
        input_file_hashes = self.signature_material(context)["command_input_hashes"]
        try:
            cwd_relative = cwd.relative_to(context.workspace)
            logical_cwd = "{workspace}" if cwd_relative == Path(".") else f"{{workspace}}/{cwd_relative}"
        except ValueError:
            logical_cwd = f"{{external}}/{cwd.name}"
        _write_json(
            command_path,
            {
                "argv": logical_command,
                "argv_display": shlex.join(logical_command),
                "argv_template": params.command,
                "cwd": logical_cwd,
                "declared_outputs": params.outputs,
                "environment_keys": sorted(
                    {name for name in params.inherit_env if name in os.environ} | set(params.env)
                ),
                "input_file_hashes": input_file_hashes,
                "return_code": completed.returncode,
                "network_policy": policy.value,
                "network_enforcement": "declaration-gate",
            },
        )
        if completed.returncode not in params.allowed_return_codes:
            raise RuntimeError(
                f"command stage {context.stage.id} exited {completed.returncode}; see stderr.log"
            )

        artifacts = [
            _artifact(command_path, context, "command.record"),
            _artifact(stdout_path, context, "command.stdout"),
            _artifact(stderr_path, context, "command.stderr"),
        ]
        for output in params.outputs:
            output_path = Path(output.format_map(values))
            if not output_path.is_absolute():
                output_path = context.stage_dir / output_path
            if not output_path.is_file():
                raise FileNotFoundError(f"declared stage output was not created: {output}")
            artifacts.append(_artifact(output_path, context, "check.result"))
        return StageOutcome(artifacts=artifacts, metadata={"return_code": completed.returncode})


class ImportIssuesParams(ParamsModel):
    path: str


class ImportIssuesStage(StageHandler):
    type_name = "import_issues"
    params_model = ImportIssuesParams

    def _input_path(self, context: StageContext) -> Path:
        params = ImportIssuesParams.model_validate(context.stage.params)
        return context.loaded_config.resolve(params.path)

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        path = self._input_path(context)
        return {"input_sha256": sha256_file(path)}

    def run(self, context: StageContext) -> StageOutcome:
        input_path = self._input_path(context)
        package = load_review_input(
            input_path,
            manuscript=context.manifest.source,
            project_id=context.loaded_config.config.project.id,
        )
        package_path = context.stage_dir / "package.json"
        issues_path = context.stage_dir / "issues.json"
        write_package_json(package, package_path)
        _write_json(issues_path, [issue.model_dump(mode="json") for issue in package.issues])
        return StageOutcome(
            artifacts=[
                _artifact(package_path, context, "review.package.imported"),
                _artifact(issues_path, context, "review.issues.imported"),
            ],
            metadata={"issue_count": len(package.issues), "input_sha256": sha256_file(input_path)},
        )


class PromptPacketParams(ParamsModel):
    route_ids: list[str] = Field(default_factory=list)
    include_source_text: bool = False
    max_source_chars: int = Field(default=200_000, ge=1)


class PromptPacketStage(StageHandler):
    type_name = "prompt_packet"
    params_model = PromptPacketParams

    def _routes(self, context: StageContext, params: PromptPacketParams):
        wanted = set(params.route_ids)
        routes = context.loaded_config.config.routes
        missing = sorted(wanted - {route.id for route in routes})
        if missing:
            raise ValueError(f"prompt packet references unknown routes: {missing}")
        return [route for route in routes if not wanted or route.id in wanted]

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = PromptPacketParams.model_validate(context.stage.params)
        prompt_hashes: dict[str, str] = {}
        for route in self._routes(context, params):
            if route.prompt:
                path = context.loaded_config.resolve(route.prompt)
                prompt_hashes[route.id] = sha256_file(path)
        return {"prompt_hashes": prompt_hashes}

    def run(self, context: StageContext) -> StageOutcome:
        params = PromptPacketParams.model_validate(context.stage.params)
        artifacts: list[Artifact] = []
        routes = self._routes(context, params)
        source_text = ""
        if params.include_source_text and any(route.manuscript_access for route in routes):
            try:
                source_text = context.source_path.read_text(encoding="utf-8")[: params.max_source_chars]
            except UnicodeDecodeError as exc:
                raise ValueError("include_source_text currently supports text sources only") from exc

        for route in routes:
            prompt_text = ""
            if route.prompt:
                prompt_text = context.loaded_config.resolve(route.prompt).read_text(encoding="utf-8")
            packet = context.stage_dir / f"{route.id}.md"
            body = [
                f"# Review Route {route.id}: {route.name}",
                "",
                f"- Source SHA-256: `{context.manifest.source.sha256}`",
                f"- Network policy: `{context.loaded_config.config.project.network_policy.value}`",
                f"- Prompt version: `{route.prompt_version or 'unversioned'}`",
                f"- Manuscript access: `{str(route.manuscript_access).lower()}`",
                "",
                prompt_text.strip(),
            ]
            if source_text and route.manuscript_access:
                body.extend(["", "## Source Text", "", source_text])
            packet.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
            artifacts.append(_artifact(packet, context, "review.prompt_packet"))
        return StageOutcome(artifacts=artifacts, metadata={"route_count": len(artifacts)})


class ConsensusParams(ParamsModel):
    min_independent_routes: int = Field(default=1, ge=1)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in normalized).split())


def _issue_key(issue: ReviewIssue) -> str:
    if issue.consensus_key:
        return issue.consensus_key
    return hash_json(
        {
            "category": _normalize(issue.category),
            "title": _normalize(issue.title.primary),
            "location": _normalize(issue.location),
        }
    )


class ConsensusStage(StageHandler):
    type_name = "consensus"
    params_model = ConsensusParams

    def _packages(self, context: StageContext) -> list[ReviewPackage]:
        packages: list[ReviewPackage] = []
        for stage_id in context.stage.depends_on:
            stage_dir = context.dependency_dir(stage_id)
            for filename in ("consensus.json", "package.json"):
                candidate = stage_dir / filename
                if candidate.is_file():
                    packages.append(ReviewPackage.model_validate_json(candidate.read_text(encoding="utf-8")))
                    break
        if not packages:
            raise FileNotFoundError("consensus stage requires a dependency that emits package.json or consensus.json")
        return packages

    def run(self, context: StageContext) -> StageOutcome:
        params = ConsensusParams.model_validate(context.stage.params)
        packages = self._packages(context)
        for package in packages:
            if package.project_id != context.loaded_config.config.project.id:
                raise ValueError(
                    f"consensus input project_id {package.project_id!r} does not match the current project"
                )
            if package.manuscript.sha256 != context.manifest.source.sha256:
                raise ValueError("consensus input manuscript hash does not match the current source")
        groups: dict[str, list[ReviewIssue]] = {}
        for package in packages:
            for issue in package.issues:
                groups.setdefault(_issue_key(issue), []).append(issue)

        severity_rank = {
            "critical": 4,
            "major": 3,
            "moderate": 2,
            "minor": 1,
            "editorial": 0,
        }
        merged: list[ReviewIssue] = []
        used_issue_ids: set[str] = set()
        for key in sorted(groups):
            issues = groups[key]
            route_ids = sorted({route_id for issue in issues for route_id in issue.route_ids})
            independence_count = len(route_ids) or 1
            if independence_count < params.min_independent_routes:
                continue
            selected = max(
                issues,
                key=lambda item: (severity_rank[item.severity.value], item.confidence, item.id),
            ).model_copy(deep=True)
            selected_id = min(issue.id for issue in issues)
            if selected_id in used_issue_ids:
                selected_id = f"{selected_id}-{key[:8]}"
            used_issue_ids.add(selected_id)
            selected.id = selected_id
            selected.route_ids = route_ids
            selected.source_ids = sorted({source_id for issue in issues for source_id in issue.source_ids})
            selected.evidence_ids = sorted({evidence_id for issue in issues for evidence_id in issue.evidence_ids})
            selected.tags = sorted({tag for issue in issues for tag in issue.tags})
            selected.consensus_key = key
            selected.support_count = independence_count
            selected.metadata = {
                **selected.metadata,
                "consensus": {
                    "member_ids": [issue.id for issue in issues],
                    "independent_routes": route_ids,
                    "rule": "exact-normalized-v1",
                },
            }
            merged.append(selected)

        first = packages[0]
        routes_by_id: dict[str, ReviewRoute] = {}
        sources_by_id = {}
        evidence_by_id = {}
        claims_by_id: dict[str, ReviewClaim] = {}
        queries_by_id: dict[str, RelatedWorkQuery] = {}
        snapshots_by_id: dict[str, RetrievedSourceSnapshot] = {}
        for package in packages:
            for label, values, catalog in (
                ("route", package.routes, routes_by_id),
                ("source", package.sources, sources_by_id),
                ("evidence", package.evidence, evidence_by_id),
                ("claim", package.claims, claims_by_id),
                ("related-work query", package.related_work_queries, queries_by_id),
                ("retrieved snapshot", package.retrieved_snapshots, snapshots_by_id),
            ):
                for value in values:
                    existing = catalog.get(value.id)
                    if existing is not None and existing != value:
                        raise ValueError(f"conflicting {label} definition for ID {value.id}")
                    catalog[value.id] = value

        strengths: dict[str, list[str]] = {}
        acceptance_gate: dict[str, list[str]] = {}
        dimension_by_name: dict[str, DimensionScore] = {}
        summary = None
        overall_score = None
        for package in packages:
            for language, values in package.strengths.items():
                strengths[language] = list(dict.fromkeys([*strengths.get(language, []), *values]))
            for language, values in package.acceptance_gate.items():
                acceptance_gate[language] = list(
                    dict.fromkeys([*acceptance_gate.get(language, []), *values])
                )
            if package.summary is not None:
                if summary is not None and summary != package.summary:
                    raise ValueError("conflicting summary definitions")
                summary = package.summary
            if package.overall_score is not None:
                if overall_score is not None and overall_score != package.overall_score:
                    raise ValueError("conflicting overall_score definitions")
                overall_score = package.overall_score
            for score in package.dimension_scores:
                existing = dimension_by_name.get(score.dimension)
                if existing is not None and existing != score:
                    raise ValueError(f"conflicting dimension score for {score.dimension.value}")
                dimension_by_name[score.dimension] = score
        recommendation = next(
            (package.recommendation for package in packages if package.recommendation is not None),
            None,
        )

        output = ReviewPackage(
            project_id=first.project_id,
            manuscript=context.manifest.source,
            recommendation=recommendation,
            routes=list(routes_by_id.values()),
            sources=list(sources_by_id.values()),
            evidence=list(evidence_by_id.values()),
            issues=sorted(merged, key=lambda item: item.id),
            strengths=strengths,
            acceptance_gate=acceptance_gate,
            summary=summary,
            claims=list(claims_by_id.values()),
            dimension_scores=list(dimension_by_name.values()),
            overall_score=overall_score,
            related_work_queries=list(queries_by_id.values()),
            retrieved_snapshots=list(snapshots_by_id.values()),
            metadata={
                **first.metadata,
                "consensus_rule": "exact-normalized-v1",
                "input_package_count": len(packages),
            },
        )
        output_path = context.stage_dir / "consensus.json"
        write_package_json(output, output_path)
        return StageOutcome(
            artifacts=[_artifact(output_path, context, "review.package.consensus")],
            metadata={"issue_count": len(output.issues)},
        )



class LiteratureGroundingParams(ParamsModel):
    requires_network: bool = False
    network_scope: str = "metadata"
    snapshot_path: str | None = None
    retriever: str | None = None
    max_queries_per_perspective: int = Field(default=1, ge=1)
    include_source_text: bool = True

    def model_post_init(self, __context: Any) -> None:
        if self.retriever and not self.requires_network:
            raise ValueError("a live retriever requires requires_network = true")


class LiteratureGroundingStage(StageHandler):
    type_name = "literature_grounding"
    version = "1"
    params_model = LiteratureGroundingParams

    def _params(self, context: StageContext) -> LiteratureGroundingParams:
        return LiteratureGroundingParams.model_validate(context.stage.params)

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = self._params(context)
        material: dict[str, Any] = {}
        if params.snapshot_path:
            path = context.loaded_config.resolve(params.snapshot_path)
            material["snapshot_sha256"] = sha256_file(path)
        return material

    def run(self, context: StageContext) -> StageOutcome:
        params = self._params(context)
        _enforce_network_declaration(
            context, requires_network=params.requires_network, network_scope=params.network_scope
        )
        if params.include_source_text:
            try:
                manuscript_text = context.source_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("literature_grounding currently supports text sources only") from exc
        else:
            manuscript_text = context.source_path.name
        queries = generate_related_work_queries(
            manuscript_text, max_per_perspective=params.max_queries_per_perspective
        )
        snapshots: list[RetrievedSourceSnapshot] = []
        if params.snapshot_path:
            snapshots.extend(load_snapshot_fixture(context.loaded_config.resolve(params.snapshot_path)))
        if params.retriever:
            from ..literature import RetrievalRequest

            retriever = load_retriever(params.retriever)
            result = retriever.retrieve(
                RetrievalRequest(
                    manuscript_sha256=context.manifest.source.sha256,
                    queries=queries,
                    network_scope=params.network_scope,
                ),
                context.stage_dir,
            )
            snapshots.extend(result.snapshots)
        query_ids = {query.id for query in queries}
        missing = sorted({qid for snapshot in snapshots for qid in snapshot.query_ids} - query_ids)
        if missing:
            raise ValueError(f"retrieved snapshots reference unknown queries: {missing}")
        sources, evidence = snapshots_to_records(snapshots, queries)
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            sources=sources,
            evidence=evidence,
            related_work_queries=queries,
            retrieved_snapshots=snapshots,
            metadata={
                "literature_grounding": {
                    "requires_network": params.requires_network,
                    "network_scope": params.network_scope,
                    "retriever": params.retriever,
                    "snapshot_path": params.snapshot_path,
                }
            },
        )
        queries_path = context.stage_dir / "queries.json"
        snapshots_path = context.stage_dir / "snapshots.json"
        package_path = context.stage_dir / "package.json"
        _write_json(queries_path, [query.model_dump(mode="json") for query in queries])
        _write_json(snapshots_path, [snapshot.model_dump(mode="json") for snapshot in snapshots])
        write_package_json(package, package_path)
        return StageOutcome(
            artifacts=[
                _artifact(queries_path, context, "literature.queries"),
                _artifact(snapshots_path, context, "literature.snapshots"),
                _artifact(package_path, context, "review.package.literature"),
            ],
            metadata={
                "query_count": len(queries),
                "snapshot_count": len(snapshots),
                "requires_network": params.requires_network,
            },
        )


class ReportParams(ParamsModel):
    language: str | None = None


class ReportStage(StageHandler):
    type_name = "report"
    params_model = ReportParams

    def _package(self, context: StageContext) -> ReviewPackage:
        for stage_id in reversed(context.stage.depends_on):
            stage_dir = context.dependency_dir(stage_id)
            for filename in ("consensus.json", "package.json"):
                candidate = stage_dir / filename
                if candidate.is_file():
                    return ReviewPackage.model_validate_json(candidate.read_text(encoding="utf-8"))
        raise FileNotFoundError("report stage requires a dependency that emits consensus.json or package.json")

    def run(self, context: StageContext) -> StageOutcome:
        params = ReportParams.model_validate(context.stage.params)
        language = params.language or context.loaded_config.config.project.default_language
        package = self._package(context).model_copy(deep=True)
        package.metadata = {**package.metadata, "run_id": context.manifest.run_id}
        package_path = context.stage_dir / "review_package.json"
        markdown_path = context.stage_dir / "review_report.md"
        summary_path = context.stage_dir / "summary.json"
        write_package_json(package, package_path)
        markdown_path.write_text(render_markdown(package, language=language), encoding="utf-8")
        _write_json(
            summary_path,
            {
                "project_id": package.project_id,
                "source_sha256": package.manuscript.sha256,
                "issue_count": len(package.issues),
                "language": language,
            },
        )
        return StageOutcome(
            artifacts=[
                _artifact(package_path, context, "review.package.release"),
                _artifact(markdown_path, context, "review.report.markdown"),
                _artifact(summary_path, context, "review.summary"),
            ],
            metadata={"issue_count": len(package.issues), "language": language},
        )


BUILTIN_STAGE_HANDLERS = (
    IngestStage,
    CommandStage,
    ImportIssuesStage,
    PromptPacketStage,
    LiteratureGroundingStage,
    ConsensusStage,
    ReportStage,
)
