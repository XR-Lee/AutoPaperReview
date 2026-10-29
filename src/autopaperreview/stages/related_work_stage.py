"""Stage that searches related work before novelty and prior-work scores."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import artifact_from_path
from ..awaiting import AwaitingInput
from ..hashing import sha256_file
from ..models import (
    EvidenceRecord,
    NetworkPolicy,
    ReviewPackage,
    RetrievedSourceSnapshot,
    SnapshotContentKind,
    SourceRecord,
)
from ..related_work import (
    MANUSCRIPT_SOURCE_ID,
    RelatedWorkJudgment,
    evidence_id_for_candidate,
    issues_from_cap_signals,
    manuscript_baseline_excerpt,
    manuscript_baseline_records,
    render_related_work_markdown,
    run_related_work_search,
)
from ..reporting import write_package_json
from ..venues import profile_dir
from .base import StageContext, StageHandler, StageOutcome


class ParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _write_json(path: Path, value: Any) -> None:
    import json

    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _enforce_network(context: StageContext, *, requires_network: bool, network_scope: str) -> None:
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


class RelatedWorkSearchParams(ParamsModel):
    venue: str = "iclr2027"
    mode: Literal["agent", "live", "off", "pool"] = "agent"
    findings_path: str | None = None
    requires_network: bool = False
    network_scope: str = "metadata"
    max_results_per_query: int = Field(default=5, ge=1, le=25)
    named_method_limit: int = Field(default=15, ge=1, le=40)
    bibliography_lookup_limit: int = Field(default=60, ge=1, le=200)
    confirmation_path: str | None = None
    confirmation_adapter: str | None = None
    confirmation_base_url: str | None = None
    confirmation_model: str | None = None
    confirmation_api_key_env: str = "APR_MODEL_API_KEY"
    confirmation_allow_remote: bool = False
    providers: list[str] = Field(
        default_factory=lambda: ["arxiv", "openalex", "semantic_scholar"]
    )
    pool_path: str | None = None
    cutoff_date: str | None = None
    blocked_ids: list[str] = Field(default_factory=list)
    pool_summary_chars: int = Field(default=2000, ge=280, le=20000)

    def model_post_init(self, __context: Any) -> None:
        if self.mode == "live" and not self.requires_network:
            raise ValueError("live related-work search requires requires_network = true")
        if self.mode == "pool" and not self.pool_path:
            raise ValueError("pool related-work search requires pool_path")
        if self.mode == "pool" and self.requires_network:
            raise ValueError("pool related-work search does not use the network")
        if self.cutoff_date:
            date.fromisoformat(self.cutoff_date)
        if self.network_scope != "metadata":
            raise ValueError("related-work search only supports metadata network scope")
        unknown = [item for item in self.providers if item not in {"arxiv", "openalex", "semantic_scholar"}]
        if unknown:
            raise ValueError(f"unknown related-work providers: {unknown}")


def _artifact(path: Path, context: StageContext, role: str):
    return artifact_from_path(
        path,
        role=role,
        generated_by=context.stage.id,
        base=context.run_dir,
        base_name="run",
    )


def _snapshots(
    judgment: RelatedWorkJudgment,
    *,
    retrieved_at: datetime,
) -> tuple[list[RetrievedSourceSnapshot], list[SourceRecord], list[EvidenceRecord]]:
    known = {query.id for query in judgment.queries}
    snapshots: list[RetrievedSourceSnapshot] = []
    sources: list[SourceRecord] = []
    evidence: list[EvidenceRecord] = []
    for candidate in judgment.candidates:
        query_ids = [query_id for query_id in candidate.query_ids if query_id in known]
        if not query_ids and judgment.queries:
            query_ids = [judgment.queries[0].id]
        if not query_ids:
            continue
        query_text = next(query.query for query in judgment.queries if query.id == query_ids[0])
        doi = candidate.external_id.split(":", 1)[1] if candidate.external_id.startswith("doi:") else None
        arxiv = candidate.external_id.split(":", 1)[1] if candidate.external_id.startswith("arxiv:") else None
        matched = candidate.required
        snapshot = RetrievedSourceSnapshot(
            id=f"RWC{candidate.id[1:]}",
            query_ids=query_ids,
            title=candidate.title,
            retrieved_at=retrieved_at,
            content_kind=SnapshotContentKind.abstract,
            locator=candidate.link,
            arxiv_id=arxiv,
            doi=doi,
            excerpt=candidate.summary,
            task=judgment.formulation.task if matched else None,
            dataset=judgment.formulation.setting if matched else None,
            metric=judgment.decision_metric if matched else None,
            metadata={
                "tier": candidate.tier,
                "cited": candidate.cited,
                "date_relation": candidate.date_relation,
                "required": candidate.required,
                "external_id": candidate.external_id,
                "provider": candidate.provider,
                "venue": candidate.venue,
                "year": candidate.year,
            },
        )
        snapshots.append(snapshot)
        sources.append(
            SourceRecord(
                id=f"S-{candidate.id}",
                kind="retrieved-abstract",
                title=candidate.title,
                locator=candidate.link,
                doi=doi,
                accessed_at=retrieved_at,
                metadata={"external_id": candidate.external_id, "candidate_id": candidate.id},
            )
        )
        evidence.append(
            EvidenceRecord(
                id=evidence_id_for_candidate(candidate.id),
                source_id=f"S-{candidate.id}",
                locator=candidate.link,
                claim=candidate.summary,
                excerpt=candidate.summary,
                retrieval_query=query_text,
                metadata={
                    "candidate_id": candidate.id,
                    "external_id": candidate.external_id,
                    "venue": candidate.venue,
                    "year": candidate.year,
                    "published": candidate.published,
                    "date_relation": candidate.date_relation,
                    "verified": True,
                    "required": candidate.required,
                    "tier": candidate.tier,
                },
            )
        )
    return snapshots, sources, evidence


class RelatedWorkSearchStage(StageHandler):
    type_name = "related_work_search"
    version = "8"
    params_model = RelatedWorkSearchParams
    reads_source = True

    def _params(self, context: StageContext) -> RelatedWorkSearchParams:
        return RelatedWorkSearchParams.model_validate(context.stage.params)

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = self._params(context)
        material: dict[str, Any] = {"venue": params.venue}
        deadline = profile_dir() / f"{params.venue}.deadlines.json"
        if deadline.is_file():
            material["deadline_sha256"] = sha256_file(deadline)
        if params.findings_path:
            path = context.loaded_config.resolve(params.findings_path)
            if path.is_file():
                material["findings_sha256"] = sha256_file(path)
        material["named_method_limit"] = params.named_method_limit
        material["bibliography_lookup_limit"] = params.bibliography_lookup_limit
        if params.confirmation_path:
            path = context.loaded_config.resolve(params.confirmation_path)
            material["confirmation_path"] = params.confirmation_path
            if path.is_file():
                material["confirmation_sha256"] = sha256_file(path)
        if params.confirmation_adapter:
            material["confirmation_adapter"] = params.confirmation_adapter
            material["confirmation_base_url"] = params.confirmation_base_url
            material["confirmation_model"] = params.confirmation_model
            material["confirmation_api_key_env"] = params.confirmation_api_key_env
            material["confirmation_allow_remote"] = params.confirmation_allow_remote
        if params.pool_path:
            pool = context.loaded_config.resolve(params.pool_path)
            material["pool_path"] = params.pool_path
            if pool.is_file():
                material["pool_sha256"] = sha256_file(pool)
        if params.cutoff_date:
            material["cutoff_date"] = params.cutoff_date
        if params.blocked_ids:
            material["blocked_ids"] = list(params.blocked_ids)
        material["pool_summary_chars"] = params.pool_summary_chars
        return material

    def run(self, context: StageContext) -> StageOutcome:
        params = self._params(context)
        _enforce_network(
            context,
            requires_network=params.requires_network,
            network_scope=params.network_scope,
        )
        try:
            manuscript = context.source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("related_work_search currently supports text sources only") from exc
        findings = context.loaded_config.resolve(params.findings_path) if params.findings_path else None
        if params.mode == "agent" and findings is not None and not findings.is_file():
            # Generate the queries locally so the host agent knows what to search for.
            preview = run_related_work_search(
                manuscript,
                venue_key=params.venue,
                mode="agent",
                findings_path=None,
                named_method_limit=params.named_method_limit,
                bibliography_lookup_limit=params.bibliography_lookup_limit,
                network_policy=context.loaded_config.config.project.network_policy.value,
                cutoff_date=date.fromisoformat(params.cutoff_date) if params.cutoff_date else None,
                blocked_ids=params.blocked_ids,
            )
            raise AwaitingInput(
                "agent related-work inspector requires a findings drop; "
                f"set findings_path to an existing file ({findings.name} is missing)",
                request={
                    "kind": "related_work_findings",
                    "drop_path": params.findings_path,
                    "queries": [
                        {"id": query.id, "perspective": query.perspective.value, "query": query.query}
                        for query in preview.queries
                    ],
                    "notes": [entry.detail for entry in preview.logs],
                },
            )
        confirmation_file = (
            context.loaded_config.resolve(params.confirmation_path) if params.confirmation_path else None
        )
        pool = context.loaded_config.resolve(params.pool_path) if params.pool_path else None
        judgment = run_related_work_search(
            manuscript,
            venue_key=params.venue,
            mode=params.mode,
            findings_path=findings if params.mode == "agent" or findings is not None else None,
            allow_network=params.requires_network,
            max_results=params.max_results_per_query,
            providers=params.providers,
            named_method_limit=params.named_method_limit,
            bibliography_lookup_limit=params.bibliography_lookup_limit,
            confirmation_path=confirmation_file if confirmation_file is not None and confirmation_file.is_file() else None,
            confirmation_adapter=params.confirmation_adapter,
            confirmation_adapter_params={
                "base_url": params.confirmation_base_url,
                "model": params.confirmation_model,
                "api_key_env": params.confirmation_api_key_env,
                "allow_remote": params.confirmation_allow_remote,
            },
            confirmation_scratch=context.stage_dir,
            network_policy=context.loaded_config.config.project.network_policy.value,
            cache_dir=context.workspace / ".autopaperreview" / "related_work_cache",
            pool_path=pool,
            cutoff_date=date.fromisoformat(params.cutoff_date) if params.cutoff_date else None,
            blocked_ids=params.blocked_ids,
            pool_summary_chars=params.pool_summary_chars,
        )
        retrieved_at = datetime.now(timezone.utc)
        snapshots, sources, evidence = _snapshots(judgment, retrieved_at=retrieved_at)
        baseline_source, baseline_evidence = manuscript_baseline_records(
            manuscript,
            sha256=context.manifest.source.sha256,
            locator=context.source_path.name,
        )
        if not any(item.id == MANUSCRIPT_SOURCE_ID for item in sources):
            sources.append(baseline_source)
        excerpt = manuscript_baseline_excerpt(manuscript)
        issues = issues_from_cap_signals(judgment, anchor_excerpt=excerpt)
        if issues and not any(item.id == baseline_evidence.id for item in evidence):
            evidence.append(baseline_evidence)
        package = ReviewPackage(
            project_id=context.loaded_config.config.project.id,
            manuscript=context.manifest.source,
            sources=sources,
            evidence=evidence,
            issues=issues,
            related_work_queries=judgment.queries,
            retrieved_snapshots=snapshots,
            metadata={"related_work": judgment.model_dump(mode="json")},
        )
        queries_path = context.stage_dir / "queries.json"
        candidates_path = context.stage_dir / "candidates.json"
        packet_path = context.stage_dir / "confirmation_packet.json"
        judgment_path = context.stage_dir / "judgment.json"
        log_path = context.stage_dir / "retrieval_log.json"
        markdown_path = context.stage_dir / "related_work.md"
        package_path = context.stage_dir / "package.json"
        _write_json(queries_path, [query.model_dump(mode="json") for query in judgment.queries])
        _write_json(candidates_path, [item.model_dump(mode="json") for item in judgment.candidates])
        _write_json(packet_path, judgment.confirmation_packet)
        _write_json(judgment_path, judgment.model_dump(mode="json"))
        _write_json(log_path, [item.model_dump(mode="json") for item in judgment.logs])
        markdown_path.write_text(render_related_work_markdown(judgment), encoding="utf-8")
        write_package_json(package, package_path)
        return StageOutcome(
            artifacts=[
                _artifact(queries_path, context, "related_work.queries"),
                _artifact(candidates_path, context, "related_work.candidates"),
                _artifact(packet_path, context, "related_work.confirmation_packet"),
                _artifact(judgment_path, context, "related_work.judgment"),
                _artifact(log_path, context, "related_work.log"),
                _artifact(markdown_path, context, "related_work.markdown"),
                _artifact(package_path, context, "review.package.related_work"),
            ],
            metadata={
                "status": judgment.status,
                "confirmation": judgment.confirmation,
                "failed_providers": list(judgment.failed_providers),
                "candidate_count": len(judgment.candidates),
                "query_count": len(judgment.queries),
                "effective_venue_cap": judgment.effective_venue_cap,
                "requires_network": params.requires_network,
                "target_leakage_count": len(judgment.target_leakage),
                "cutoff_date": judgment.cutoff_date,
            },
        )
