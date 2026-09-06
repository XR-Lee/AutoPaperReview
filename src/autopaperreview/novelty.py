"""Matched-setting novelty tags. Overlap evidence requires task+dataset+metric."""

from __future__ import annotations

import unicodedata

from .models import (
    EvidenceRecord,
    LedgerClaim,
    LocalizedText,
    NoveltyAssessment,
    NoveltyTag,
    RetrievedSourceSnapshot,
)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def _match(left: str | None, right: str | None) -> bool:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def infer_manuscript_setting(claims: list[LedgerClaim], manuscript_text: str) -> dict[str, str]:
    blob = " ".join([manuscript_text, *[claim.claim.primary for claim in claims]])
    lowered = blob.lower()
    task = "tracking" if "track" in lowered else "unspecified"
    if "sequence" in lowered and "holdout" in lowered:
        dataset = "sequence-level holdout"
    elif "image-level" in lowered or "image level" in lowered:
        dataset = "image-level split"
    else:
        dataset = "unspecified"
    metric = "accuracy" if "accuracy" in lowered or "%" in blob else "unspecified"
    return {"task": task, "dataset": dataset, "metric": metric}


def assess_novelty(
    *,
    claims: list[LedgerClaim],
    snapshots: list[RetrievedSourceSnapshot],
    evidence: list[EvidenceRecord],
    manuscript_text: str,
) -> list[NoveltyAssessment]:
    setting = infer_manuscript_setting(claims, manuscript_text)
    evidence_ids = {item.id for item in evidence}
    assessments: list[NoveltyAssessment] = []
    for claim in claims:
        for snapshot in snapshots:
            matched_task = _match(setting["task"], snapshot.task)
            matched_dataset = _match(setting["dataset"], snapshot.dataset)
            matched_metric = _match(setting["metric"], snapshot.metric)
            comparable = matched_task and matched_dataset and matched_metric
            snapshot_evidence = f"E-{snapshot.id}"
            cited = [snapshot_evidence] if snapshot_evidence in evidence_ids else []
            if not snapshot.excerpt:
                tag = NoveltyTag.unclear
                note = "Snapshot has no excerpt, so novelty cannot be verified."
                note_code = "no-excerpt"
                note_params = None
            elif not comparable:
                tag = NoveltyTag.not_comparable
                note_params = (
                    f"task={snapshot.task or 'none'}, dataset={snapshot.dataset or 'none'}, "
                    f"metric={snapshot.metric or 'none'}"
                )
                note = (
                    "Snapshot is not a matched setting "
                    f"({note_params}); not used as overlap evidence."
                )
                note_code = "not-matched"
            else:
                haystack = " ".join(
                    part for part in (snapshot.title, snapshot.excerpt) if part
                ).lower()
                claim_terms = _normalize(claim.claim.primary)
                overlap = any(term and term in haystack for term in claim_terms.split()[:6])
                tag = NoveltyTag.overlap if overlap else NoveltyTag.supported
                note = (
                    "Matched-setting comparison used the snapshot excerpt."
                    if overlap
                    else "Matched setting, and the snapshot does not repeat the manuscript claim."
                )
                note_code = "overlap" if overlap else "distinct"
                note_params = None
            metadata = {
                "manuscript_setting": setting,
                "comparable": comparable,
                "note_code": note_code,
            }
            if note_params:
                metadata["note_params"] = note_params
            assessments.append(
                NoveltyAssessment(
                    id=f"N-{claim.id}-{snapshot.id}",
                    claim_id=claim.id,
                    snapshot_id=snapshot.id,
                    tag=tag,
                    matched_task=matched_task,
                    matched_dataset=matched_dataset,
                    matched_metric=matched_metric,
                    evidence_ids=cited,
                    notes=LocalizedText(primary=note, language="en"),
                    metadata=metadata,
                )
            )
    return assessments
