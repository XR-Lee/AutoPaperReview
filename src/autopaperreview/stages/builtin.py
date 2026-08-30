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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..adapters import ReviewRequest, load_review_adapter
from ..artifacts import artifact_from_path
from ..contributions import extract_listed_contributions
from ..export_gate import ExportGateParams, export_gate_errors
from ..figures import caption_inventory, sibling_pdf
from ..hashing import hash_json, sha256_file
from ..i18n import ensure_package_languages, normalize_languages
from ..integrity import build_integrity_records, collect_inventory
from ..ledger import build_agenda, build_ledger_claims
from ..literature import (
    generate_related_work_queries,
    load_retriever,
    load_snapshot_fixture,
    snapshots_to_records,
)
from ..migration import load_review_input
from ..models import (
    AgendaQuestion,
    Artifact,
    DimensionScore,
    EvidenceRecord,
    IntegrityRecord,
    LedgerClaim,
    LocalizedText,
    NetworkPolicy,
    NoveltyAssessment,
    QueryPerspective,
    RelatedWorkQuery,
    RetrievedSourceSnapshot,
    ReviewClaim,
    ReviewIssue,
    ReviewPackage,
    ReviewRoute,
    RouteKind,
    VenueConclusion,
)
from ..novelty import assess_novelty
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


def _figure_packet_lines(source_path: Path, source_text: str, route_kind: RouteKind) -> list[str]:
    pdf = sibling_pdf(source_path)
    inventory = caption_inventory(source_text) if source_text else []
    if pdf is None and not inventory and route_kind != RouteKind.visual:
        return []
    lines = [
        "",
        "## Figures (mandatory inspection)",
        "",
        "Open the original PDF page image for every numbered figure and table graphic. "
        "A caption restated from Source Text is not inspection. Each figure-backed issue "
        "must name the figure id, the page, and one visible mark that is not in the caption.",
        "",
    ]
    if pdf is not None:
        lines.append(f"- PDF: `{pdf.name}`")
        lines.append(f"- PDF SHA-256: `{sha256_file(pdf)}`")
    else:
        lines.append("- PDF: missing sibling `*.pdf`. Do not invent visual evidence from captions.")
    if inventory:
        lines.append("- Caption inventory from extracted text: " + ", ".join(inventory))
    else:
        lines.append("- Caption inventory from extracted text: none")
    if route_kind == RouteKind.visual:
        lines.append("- This visual route must cover every inventoried Figure. Skipping a teaser or qualitative panel is a review defect.")
    return lines


def _contribution_packet_lines(source_text: str) -> list[str]:
    listed = extract_listed_contributions(source_text) if source_text else []
    lines = [
        "",
        "## Listed contributions (audit each one)",
        "",
        "Confirm the official 3–4 contributions from the PDF Contributions paragraph. "
        "Extracted candidates below may be garbled by two-column layout. For EACH item: "
        "(1) name the table/figure/experiment that tests THIS claim, "
        "(2) name the matched-setting comparator for THIS claim, "
        "(3) state missing evidence. Do not write one novelty paragraph for the whole paper.",
        "",
    ]
    if not listed:
        lines.append("- Extracted candidates: none. Still inventory the PDF Contributions list and audit each item.")
        return lines
    for item in listed:
        lines.append(f"- `C{item['index']}`: {item['text']}")
    return lines


def _load_dependency_packages(context: StageContext) -> list[ReviewPackage]:
    packages: list[ReviewPackage] = []
    for stage_id in context.stage.depends_on:
        stage_dir = context.dependency_dir(stage_id)
        for filename in ("consensus.json", "package.json", "review_package.json"):
            candidate = stage_dir / filename
            if candidate.is_file():
                packages.append(ReviewPackage.model_validate_json(candidate.read_text(encoding="utf-8")))
                break
    return packages


def _first_dependency_package(context: StageContext) -> ReviewPackage | None:
    packages = _load_dependency_packages(context)
    return packages[0] if packages else None


def _package_with(context: StageContext, attribute: str) -> ReviewPackage | None:
    for package in _load_dependency_packages(context):
        if getattr(package, attribute, None):
            return package
    return None


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
    include_literature: bool = False
    max_source_chars: int = Field(default=200_000, ge=1)


class PromptPacketStage(StageHandler):
    type_name = "prompt_packet"
    version = "4"
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
        literature = _package_with(context, "retrieved_snapshots") or _package_with(
            context, "related_work_queries"
        )
        material: dict[str, Any] = {"prompt_hashes": prompt_hashes}
        pdf = sibling_pdf(context.source_path)
        if pdf is not None:
            material["manuscript_pdf_sha256"] = sha256_file(pdf)
        if literature is not None:
            material["literature_sha256"] = hash_json(
                {
                    "queries": [query.model_dump(mode="json") for query in literature.related_work_queries],
                    "snapshots": [item.model_dump(mode="json") for item in literature.retrieved_snapshots],
                }
            )
        return material

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
        literature = _package_with(context, "retrieved_snapshots") or _package_with(
            context, "related_work_queries"
        )
        if params.include_literature and (
            literature is None or not (literature.related_work_queries or literature.retrieved_snapshots)
        ):
            raise FileNotFoundError(
                "include_literature requires a dependency that emits queries or snapshots"
            )

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
                f"- Route kind: `{route.kind.value}`",
                "",
                prompt_text.strip(),
            ]
            if source_text and route.manuscript_access:
                body.extend(["", "## Source Text", "", source_text])
            if route.manuscript_access or route.kind == RouteKind.visual:
                body.extend(_figure_packet_lines(context.source_path, source_text, route.kind))
            if route.manuscript_access and source_text:
                body.extend(_contribution_packet_lines(source_text))
            attach_literature = literature is not None and (
                params.include_literature or route.kind == RouteKind.literature
            )
            if attach_literature and literature is not None:
                body.extend(["", "## Related-Work Queries", ""])
                for query in literature.related_work_queries:
                    body.append(f"- `{query.id}` ({query.perspective.value}): {query.query}")
                body.extend(["", "## Retrieved Sources", ""])
                if not literature.retrieved_snapshots:
                    body.append("- none")
                for snapshot in literature.retrieved_snapshots:
                    excerpt = (snapshot.excerpt or "").strip() or "(no excerpt)"
                    body.append(
                        f"- `{snapshot.id}` {snapshot.title} ({snapshot.content_kind.value})"
                    )
                    body.append(f"  Excerpt: {excerpt}")
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
    version = "2"
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
        venues_by_id: dict[str, VenueConclusion] = {}
        ledger_by_id: dict[str, LedgerClaim] = {}
        agenda_by_id: dict[str, AgendaQuestion] = {}
        integrity_by_id: dict[str, IntegrityRecord] = {}
        novelty_by_id: dict[str, NoveltyAssessment] = {}
        for package in packages:
            for label, values, catalog in (
                ("route", package.routes, routes_by_id),
                ("source", package.sources, sources_by_id),
                ("evidence", package.evidence, evidence_by_id),
                ("claim", package.claims, claims_by_id),
                ("related-work query", package.related_work_queries, queries_by_id),
                ("retrieved snapshot", package.retrieved_snapshots, snapshots_by_id),
                ("venue conclusion", package.venue_conclusions, venues_by_id),
                ("ledger claim", package.ledger_claims, ledger_by_id),
                ("agenda question", package.agenda, agenda_by_id),
                ("integrity record", package.integrity_records, integrity_by_id),
                ("novelty assessment", package.novelty_assessments, novelty_by_id),
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
        residual_risks: list[LocalizedText] = []
        seen_risks: set[str] = set()
        for package in packages:
            for risk in package.residual_risks:
                key = risk.primary
                if key in seen_risks:
                    continue
                seen_risks.add(key)
                residual_risks.append(risk)

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
            venue_conclusions=list(venues_by_id.values()),
            overall_score=overall_score,
            related_work_queries=list(queries_by_id.values()),
            retrieved_snapshots=list(snapshots_by_id.values()),
            ledger_claims=list(ledger_by_id.values()),
            residual_risks=residual_risks,
            agenda=list(agenda_by_id.values()),
            integrity_records=list(integrity_by_id.values()),
            novelty_assessments=list(novelty_by_id.values()),
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
    version = "4"
    params_model = LiteratureGroundingParams

    def _params(self, context: StageContext) -> LiteratureGroundingParams:
        return LiteratureGroundingParams.model_validate(context.stage.params)

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = self._params(context)
        material: dict[str, Any] = {}
        if params.snapshot_path:
            path = context.loaded_config.resolve(params.snapshot_path)
            material["snapshot_sha256"] = sha256_file(path)
        agenda = _package_with(context, "agenda")
        if agenda is not None:
            material["agenda_sha256"] = hash_json([item.model_dump(mode="json") for item in agenda.agenda])
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
        agenda_package = _package_with(context, "agenda")
        if agenda_package is not None:
            existing = {query.query for query in queries}
            for question in agenda_package.agenda:
                if question.question in existing:
                    continue
                queries.append(
                    RelatedWorkQuery(
                        id=f"Q-agenda-{question.id}",
                        perspective=question.perspective or QueryPerspective.agenda,
                        query=question.question,
                        generated_from="agenda",
                        metadata={"agenda_id": question.id, "claim_ids": question.claim_ids},
                    )
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
    languages: list[str] | None = None
    bilingual: bool = False
    min_issues: int = Field(default=0, ge=0)
    min_claims: int = Field(default=0, ge=0)
    min_anchored_issues: int = Field(default=0, ge=0)
    min_literature_snapshots: int = Field(default=0, ge=0)
    min_novelty_assessments: int = Field(default=0, ge=0)
    require_excerpt_on_snapshots: bool = False
    require_ledger: bool = False
    require_integrity: bool = False
    require_literature_if_literature_route: bool = False
    require_anchor_on_major: bool = False
    min_figure_citations: int = Field(default=0, ge=0)
    require_figures_if_visual_route: bool = False
    require_contribution_coverage: bool = False

    @field_validator("languages")
    @classmethod
    def unique_languages(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned:
            raise ValueError("report languages cannot be empty")
        return cleaned


class ReportStage(StageHandler):
    type_name = "report"
    version = "3"
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
        gate = ExportGateParams.model_validate(
            params.model_dump(exclude={"language", "languages", "bilingual"})
        )
        languages = normalize_languages(
            language=params.language,
            languages=params.languages,
            bilingual=params.bilingual,
            default_language=context.loaded_config.config.project.default_language,
        )
        package = self._package(context).model_copy(deep=True)
        gate_errors = export_gate_errors(
            package,
            gate,
            route_kinds=[route.kind for route in context.loaded_config.config.routes],
        )
        if gate_errors:
            raise ValueError("export gate failed:\n- " + "\n- ".join(gate_errors))
        if len(languages) > 1:
            package = ensure_package_languages(package, languages)
        package.metadata = {
            **package.metadata,
            "run_id": context.manifest.run_id,
            "report_languages": list(languages),
        }
        package_path = context.stage_dir / "review_package.json"
        markdown_path = context.stage_dir / "review_report.md"
        summary_path = context.stage_dir / "summary.json"
        write_package_json(package, package_path)
        markdown = render_markdown(package, languages=languages, fill_missing=False)
        markdown_path.write_text(markdown, encoding="utf-8")
        project_dir = context.loaded_config.path.parent
        (project_dir / "review_report.md").write_text(markdown, encoding="utf-8")
        pdf_written = False
        from ..pdf_report import reportlab_available, write_report_pdf

        pdf_path = context.stage_dir / "review_report.pdf"
        if reportlab_available():
            write_report_pdf(
                markdown,
                pdf_path,
                title=f"Review Report: {package.project_id}",
            )
            (project_dir / "review_report.pdf").write_bytes(pdf_path.read_bytes())
            pdf_written = True
        _write_json(
            summary_path,
            {
                "project_id": package.project_id,
                "source_sha256": package.manuscript.sha256,
                "issue_count": len(package.issues),
                "language": languages[0],
                "languages": list(languages),
                "bilingual": len(languages) > 1,
                "pdf": pdf_written,
            },
        )
        artifacts = [
            _artifact(package_path, context, "review.package.release"),
            _artifact(markdown_path, context, "review.report.markdown"),
            _artifact(summary_path, context, "review.summary"),
        ]
        return StageOutcome(
            artifacts=artifacts,
            metadata={
                "issue_count": len(package.issues),
                "language": languages[0],
                "languages": list(languages),
                "bilingual": len(languages) > 1,
                "pdf": pdf_written,
            },
        )


class LedgerParams(ParamsModel):
    pass


class LedgerStage(StageHandler):
    type_name = "ledger"
    version = "3"
    params_model = LedgerParams

    def run(self, context: StageContext) -> StageOutcome:
        try:
            text = context.source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("ledger currently supports text sources only") from exc
        claims = build_ledger_claims(text, manuscript_sha256=context.manifest.source.sha256)
        from ..models import SourceRecord

        source = SourceRecord(
            id="S-manuscript",
            kind="manuscript",
            title="Manuscript",
            locator=context.source_path.name,
            sha256=context.manifest.source.sha256,
        )
        evidence = []
        bound_claims: list[LedgerClaim] = []
        for claim in claims:
            excerpt = claim.claim.primary
            evidence_id = f"E-{claim.id}"
            evidence.append(
                EvidenceRecord(
                    id=evidence_id,
                    source_id="S-manuscript",
                    locator=claim.anchor.display if claim.anchor else "manuscript",
                    claim=excerpt,
                    excerpt=excerpt,
                    artifact_sha256=context.manifest.source.sha256,
                    anchor=claim.anchor,
                )
            )
            bound_claims.append(claim.model_copy(update={"in_paper_evidence_ids": [evidence_id]}))
        residual = [
            LocalizedText(
                primary="In-paper claims have not yet been checked against workspace artifacts.",
                language="en",
                translations={"zh-Hans": "文中主张尚未对照工作区产物核验。"},
            )
        ]
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            sources=[source],
            evidence=evidence,
            ledger_claims=bound_claims,
            residual_risks=residual,
            metadata={"ledger": {"claim_count": len(bound_claims)}},
        )
        ledger_path = context.stage_dir / "ledger.json"
        package_path = context.stage_dir / "package.json"
        _write_json(ledger_path, [item.model_dump(mode="json") for item in bound_claims])
        write_package_json(package, package_path)
        return StageOutcome(
            artifacts=[
                _artifact(ledger_path, context, "review.ledger"),
                _artifact(package_path, context, "review.package.ledger"),
            ],
            metadata={"claim_count": len(bound_claims)},
        )


class AgendaParams(ParamsModel):
    pass


class AgendaStage(StageHandler):
    type_name = "agenda"
    version = "1"
    params_model = AgendaParams

    def run(self, context: StageContext) -> StageOutcome:
        ledger = _package_with(context, "ledger_claims")
        if ledger is None:
            raise FileNotFoundError("agenda stage requires a dependency that emits ledger_claims")
        questions = build_agenda(ledger.ledger_claims)
        package = ledger.model_copy(update={"agenda": questions})
        agenda_path = context.stage_dir / "agenda.json"
        package_path = context.stage_dir / "package.json"
        _write_json(agenda_path, [item.model_dump(mode="json") for item in questions])
        write_package_json(package, package_path)
        return StageOutcome(
            artifacts=[
                _artifact(agenda_path, context, "review.agenda"),
                _artifact(package_path, context, "review.package.agenda"),
            ],
            metadata={"question_count": len(questions)},
        )


class WorkspaceInspectParams(ParamsModel):
    paths: list[str] = Field(default_factory=list)


class WorkspaceInspectStage(StageHandler):
    type_name = "workspace_inspect"
    version = "1"
    params_model = WorkspaceInspectParams

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = WorkspaceInspectParams.model_validate(context.stage.params)
        paths = params.paths or [context.loaded_config.config.project.source]
        hashes: dict[str, str] = {}
        for raw in paths:
            candidate = context.loaded_config.resolve(raw)
            if candidate.is_file():
                hashes[raw] = sha256_file(candidate)
            elif candidate.is_dir():
                for file_path in sorted(p for p in candidate.rglob("*") if p.is_file()):
                    hashes[str(file_path.relative_to(context.workspace))] = sha256_file(file_path)
        return {"inventory_input_hashes": hashes}

    def run(self, context: StageContext) -> StageOutcome:
        params = WorkspaceInspectParams.model_validate(context.stage.params)
        paths = params.paths or [context.loaded_config.config.project.source]
        inventory = collect_inventory(paths, workspace=context.workspace, generated_by=context.stage.id)
        inventory_path = context.stage_dir / "inventory.json"
        _write_json(inventory_path, [item.model_dump(mode="json") for item in inventory])
        return StageOutcome(
            artifacts=[_artifact(inventory_path, context, "workspace.inventory")],
            metadata={"file_count": len(inventory), "read_only": True},
        )


class IntegrityParams(ParamsModel):
    pass


class IntegrityStage(StageHandler):
    type_name = "integrity"
    version = "2"
    params_model = IntegrityParams

    def run(self, context: StageContext) -> StageOutcome:
        try:
            text = context.source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("integrity currently supports text sources only") from exc
        literature = _package_with(context, "retrieved_snapshots")
        ledger = _package_with(context, "ledger_claims")
        inventory: list[Artifact] = []
        for stage_id in context.stage.depends_on:
            candidate = context.dependency_dir(stage_id) / "inventory.json"
            if candidate.is_file():
                inventory = [Artifact.model_validate(item) for item in json.loads(candidate.read_text(encoding="utf-8"))]
                break
        snapshots = literature.retrieved_snapshots if literature else []
        evidence = list(literature.evidence) if literature else []
        if ledger is not None:
            evidence = list({item.id: item for item in [*evidence, *ledger.evidence]}.values())
        records = build_integrity_records(
            manuscript_text=text,
            snapshots=snapshots,
            inventory=inventory,
            evidence=evidence,
            workspace=context.workspace,
        )
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            sources=list(
                {
                    item.id: item
                    for item in [
                        *(literature.sources if literature else []),
                        *(ledger.sources if ledger else []),
                    ]
                }.values()
            ),
            evidence=evidence,
            related_work_queries=list(literature.related_work_queries) if literature else [],
            retrieved_snapshots=snapshots,
            ledger_claims=list(ledger.ledger_claims) if ledger else [],
            integrity_records=records,
            metadata={"integrity": {"record_count": len(records)}},
        )
        records_path = context.stage_dir / "integrity.json"
        package_path = context.stage_dir / "package.json"
        _write_json(records_path, [item.model_dump(mode="json") for item in records])
        write_package_json(package, package_path)
        return StageOutcome(
            artifacts=[
                _artifact(records_path, context, "review.integrity"),
                _artifact(package_path, context, "review.package.integrity"),
            ],
            metadata={"record_count": len(records)},
        )


class NoveltyParams(ParamsModel):
    pass


class NoveltyStage(StageHandler):
    type_name = "novelty"
    version = "1"
    params_model = NoveltyParams

    def run(self, context: StageContext) -> StageOutcome:
        try:
            text = context.source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("novelty currently supports text sources only") from exc
        literature = _package_with(context, "retrieved_snapshots")
        ledger = _package_with(context, "ledger_claims")
        if literature is None or ledger is None:
            raise FileNotFoundError("novelty stage requires ledger_claims and retrieved_snapshots")
        assessments = assess_novelty(
            claims=ledger.ledger_claims,
            snapshots=literature.retrieved_snapshots,
            evidence=literature.evidence,
            manuscript_text=text,
        )
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            sources=list({item.id: item for item in [*ledger.sources, *literature.sources]}.values()),
            evidence=list({item.id: item for item in [*ledger.evidence, *literature.evidence]}.values()),
            related_work_queries=literature.related_work_queries,
            retrieved_snapshots=literature.retrieved_snapshots,
            ledger_claims=ledger.ledger_claims,
            agenda=ledger.agenda,
            novelty_assessments=assessments,
            metadata={"novelty": {"assessment_count": len(assessments)}},
        )
        novelty_path = context.stage_dir / "novelty.json"
        package_path = context.stage_dir / "package.json"
        _write_json(novelty_path, [item.model_dump(mode="json") for item in assessments])
        write_package_json(package, package_path)
        return StageOutcome(
            artifacts=[
                _artifact(novelty_path, context, "review.novelty"),
                _artifact(package_path, context, "review.package.novelty"),
            ],
            metadata={"assessment_count": len(assessments)},
        )


class ExecuteReviewParams(ParamsModel):
    adapter: str
    fixture_path: str | None = None
    requires_network: bool = False
    network_scope: str = "fulltext"


class ExecuteReviewStage(StageHandler):
    type_name = "execute_review"
    version = "1"
    params_model = ExecuteReviewParams

    def _params(self, context: StageContext) -> ExecuteReviewParams:
        return ExecuteReviewParams.model_validate(context.stage.params)

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = self._params(context)
        material: dict[str, Any] = {"adapter": params.adapter}
        if params.fixture_path:
            material["fixture_sha256"] = sha256_file(context.loaded_config.resolve(params.fixture_path))
        packets: dict[str, str] = {}
        for stage_id in context.stage.depends_on:
            for path in sorted(context.dependency_dir(stage_id).glob("*.md")):
                packets[path.name] = sha256_file(path)
        if packets:
            material["packet_hashes"] = packets
        return material

    def run(self, context: StageContext) -> StageOutcome:
        params = self._params(context)
        _enforce_network_declaration(
            context, requires_network=params.requires_network, network_scope=params.network_scope
        )
        try:
            source_text = context.source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source_text = ""
        packets: dict[str, str] = {}
        for stage_id in context.stage.depends_on:
            for path in sorted(context.dependency_dir(stage_id).glob("*.md")):
                packets[path.stem] = path.read_text(encoding="utf-8")
        fixture = context.loaded_config.resolve(params.fixture_path) if params.fixture_path else None
        request = ReviewRequest(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            source_text=source_text,
            packets=packets,
            literature=_package_with(context, "retrieved_snapshots"),
            ledger=_package_with(context, "ledger_claims"),
            fixture_path=str(fixture) if fixture else None,
            routes=[
                ReviewRoute(
                    id=route.id,
                    name=route.name,
                    kind=route.kind,
                    prompt_version=route.prompt_version,
                    model=route.model,
                    manuscript_access=route.manuscript_access,
                    network_access=route.network_access,
                    metadata=route.metadata,
                )
                for route in context.loaded_config.config.routes
            ],
            metadata={"network_scope": params.network_scope},
        )
        adapter = load_review_adapter(params.adapter)
        result = adapter.execute(request, context.stage_dir)
        package = result.package.model_copy(update={"manuscript": context.manifest.source})
        if package.project_id != context.loaded_config.config.project.id:
            raise ValueError("execute_review adapter returned a different project_id")
        package_path = context.stage_dir / "package.json"
        write_package_json(package, package_path)
        artifacts = [_artifact(package_path, context, "review.package.executed")]
        for name in result.raw_artifact_names:
            path = context.stage_dir / name
            if path.is_file():
                artifacts.append(_artifact(path, context, "review.adapter.raw"))
        return StageOutcome(
            artifacts=artifacts,
            metadata={
                "adapter": params.adapter,
                "issue_count": len(package.issues),
                "packet_count": len(packets),
                **result.metadata,
            },
        )


BUILTIN_STAGE_HANDLERS = (
    IngestStage,
    CommandStage,
    ImportIssuesStage,
    PromptPacketStage,
    LiteratureGroundingStage,
    LedgerStage,
    AgendaStage,
    WorkspaceInspectStage,
    IntegrityStage,
    NoveltyStage,
    ExecuteReviewStage,
    ConsensusStage,
    ReportStage,
)
