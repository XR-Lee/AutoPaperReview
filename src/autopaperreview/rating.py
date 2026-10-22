"""Declared ICLR rating: dimension bands, fatal-flaw caps, snap-down bins.

This is not a fitted regression. `coefficient_set` is `apr-iclr-cap-v2`.
The core does not call a model. Inputs are records already on the package.
"""

from __future__ import annotations

import math
from typing import Any

from .models import (
    FatalClass,
    OverallScore,
    OverallScoreMethod,
    ReviewPackage,
    SarDimension,
)
from .readability import issue_blocking
from .rubrics import COEFFICIENT_SET, ICLR_BINS, clause_dimension, clause_score

WEIGHTS: dict[SarDimension, float] = {
    SarDimension.experimental_soundness: 0.30,
    SarDimension.claims_supported: 0.25,
    SarDimension.originality: 0.15,
    SarDimension.importance_of_research_question: 0.12,
    SarDimension.prior_work_contextualization: 0.08,
    SarDimension.writing_clarity: 0.05,
    SarDimension.community_value: 0.05,
}

# Venue cap, then dimension ceilings applied while the cap is active.
CLASS_CAP: dict[FatalClass, int] = {
    FatalClass.invalid_protocol: 3,
    FatalClass.leakage: 3,
    FatalClass.wrong_metric: 3,
    FatalClass.unfair_baseline: 5,
    FatalClass.missing_closest_neighbor: 6,
    FatalClass.unsupported_claim: 5,
    FatalClass.format_desk_reject: 1,
}

CLASS_CEILING: dict[FatalClass, dict[SarDimension, float]] = {
    FatalClass.invalid_protocol: {
        SarDimension.experimental_soundness: 4.0,
        SarDimension.claims_supported: 4.0,
    },
    FatalClass.leakage: {SarDimension.experimental_soundness: 2.0},
    FatalClass.wrong_metric: {SarDimension.experimental_soundness: 4.0},
    FatalClass.unfair_baseline: {SarDimension.experimental_soundness: 4.0},
    FatalClass.missing_closest_neighbor: {
        SarDimension.prior_work_contextualization: 2.0,
        SarDimension.originality: 4.0,
    },
    FatalClass.unsupported_claim: {SarDimension.claims_supported: 4.0},
    FatalClass.format_desk_reject: {},
}


def snap_down(value: float) -> int:
    """Largest ICLR bin that does not exceed `value`.

    The scale minimum is 1, so a raw value below 1 still reports 1.
    Every other cut stays on the lower bin: 2, 4, 5.5, 7, and 9 do not
    round up to 3, 5, 6, 8, and 10.
    """
    chosen = ICLR_BINS[0]
    for bin_value in ICLR_BINS:
        if float(bin_value) <= value:
            chosen = bin_value
    return int(chosen)


def _bands(package: ReviewPackage) -> dict[SarDimension, float]:
    found = {score.dimension: float(score.score) for score in package.dimension_scores}
    missing = [item.value for item in SarDimension if item not in found]
    if missing:
        raise ValueError(f"rating requires all seven dimension scores; missing {missing}")
    return found


def _exemption_for(package: ReviewPackage, record_id: str):
    matches = [item for item in package.cap_exemptions if item.issue_id == record_id]
    if len(matches) > 1:
        raise ValueError(f"multiple cap exemptions for {record_id}")
    return matches[0] if matches else None


class ActiveCap:
    def __init__(self, record_id: str, fatal_class: FatalClass, cap: int, exempted: bool) -> None:
        self.record_id = record_id
        self.fatal_class = fatal_class
        self.cap = cap
        self.exempted = exempted


def active_caps(package: ReviewPackage) -> list[ActiveCap]:
    """Fatal caps from blocking decision issues, visual findings, and format checks."""
    caps: list[ActiveCap] = []
    seen: set[str] = set()

    def add(record_id: str, fatal_class: FatalClass, *, require_blocking: bool, blocking: bool | None) -> None:
        if record_id in seen:
            return
        if require_blocking and blocking is not True:
            return
        exemption = _exemption_for(package, record_id)
        default = CLASS_CAP[fatal_class]
        cap = default
        exempted = False
        if exemption is not None:
            raised = int(exemption.raises_cap_to)
            if raised not in ICLR_BINS:
                raise ValueError(f"exemption {exemption.id} raises_cap_to must be an ICLR bin")
            if raised <= default:
                raise ValueError(
                    f"exemption {exemption.id} raises_cap_to {raised} does not exceed the {default} cap"
                )
            cap = raised
            exempted = True
        seen.add(record_id)
        caps.append(ActiveCap(record_id, fatal_class, cap, exempted))

    for issue in package.issues:
        if issue.fatal_class is None or not issue.decision_relevant:
            continue
        add(
            issue.id,
            issue.fatal_class,
            require_blocking=True,
            blocking=issue_blocking(issue),
        )
    for finding in package.visual_findings:
        if finding.fatal_class is None or not finding.decision_relevant:
            continue
        add(finding.id, finding.fatal_class, require_blocking=False, blocking=None)
    for check in package.format_checks:
        if check.passed or check.fatal_class is None:
            continue
        add(check.id, check.fatal_class, require_blocking=False, blocking=None)
    return _drop_incomplete_related_work_caps(package, caps)


def _drop_incomplete_related_work_caps(package: ReviewPackage, caps: list[ActiveCap]) -> list[ActiveCap]:
    """A failed provider must not create or strengthen a related-work cap.

    ``complete`` keeps caps the search emitted. ``partial`` keeps only
    ``missing_closest_neighbor`` verified by a provider that succeeded.
    ``degraded`` and ``not_run`` drop every related-work cap.
    """
    if "related_work" not in package.metadata:
        return caps
    from pydantic import ValidationError

    from .related_work import RelatedWorkJudgment, related_work_cap_applies

    raw = package.metadata.get("related_work")
    try:
        judgment = RelatedWorkJudgment.model_validate(raw)
    except ValidationError:
        blocked = {
            issue.id
            for issue in package.issues
            if issue.metadata.get("source") == "related_work_search"
        }
        return [cap for cap in caps if cap.record_id not in blocked]
    blocked = {
        issue.id
        for issue in package.issues
        if issue.metadata.get("source") == "related_work_search" and not related_work_cap_applies(judgment, issue)
    }
    return [cap for cap in caps if cap.record_id not in blocked]


def _apply_ceilings(bands: dict[SarDimension, float], package: ReviewPackage, caps: list[ActiveCap]) -> None:
    for cap in caps:
        if cap.exempted:
            continue
        for dimension, ceiling in CLASS_CEILING[cap.fatal_class].items():
            bands[dimension] = min(bands[dimension], ceiling)
    for check in package.format_checks:
        if check.passed or not check.clause_id:
            continue
        dimension = SarDimension(clause_dimension(check.clause_id))
        bands[dimension] = min(bands[dimension], clause_score(check.clause_id))
    for finding in package.visual_findings:
        if not finding.clause_id or finding.fatal_class is not None:
            continue
        dimension = SarDimension(clause_dimension(finding.clause_id))
        bands[dimension] = min(bands[dimension], clause_score(finding.clause_id))


def effective_venue_cap(caps: list[ActiveCap]) -> int | None:
    if not caps:
        return None
    return min(item.cap for item in caps)


def _read_related_work_comparison(package: ReviewPackage) -> dict[str, Any] | None:
    """Read baselines against closest neighbours before originality is scored."""
    raw = package.metadata.get("related_work")
    if not raw:
        return None
    from .related_work import related_work_comparison

    comparison = related_work_comparison(raw)
    if comparison is None:
        return None
    baselines = comparison.get("baselines")
    neighbours = comparison.get("closest_neighbours")
    if not isinstance(baselines, dict) or not isinstance(neighbours, list):
        raise ValueError(
            "originality scoring requires the related-work comparison of baselines and closest neighbours"
        )
    return comparison


def rate_venue(package: ReviewPackage, *, confidence: int = 3) -> OverallScore:
    """Compute the discrete ICLR bin and store provenance on OverallScore."""
    if confidence not in {1, 2, 3, 4, 5}:
        raise ValueError("rating confidence must be an integer from 1 to 5")
    comparison = _read_related_work_comparison(package)
    bands = _bands(package)
    caps = active_caps(package)
    _apply_ceilings(bands, package, caps)
    # math.fsum, not sum(). CPython 3.11 adds these products left to right, so a
    # uniform 6 is 5.999999999999998 and snap_down returns 5 (a uniform 3
    # becomes 2.999999999999999 and returns 1). 3.12+ sum() uses Neumaier
    # summation and stays on the bin. fsum matches that result on 3.11/3.12/3.13.
    weighted = math.fsum(WEIGHTS[dimension] * bands[dimension] for dimension in WEIGHTS)
    gate = min(
        bands[SarDimension.claims_supported],
        bands[SarDimension.experimental_soundness],
    )
    raw = min(weighted, gate + 1.0)
    venue_cap = effective_venue_cap(caps)
    capped = raw if venue_cap is None else min(raw, float(venue_cap))
    venue = snap_down(capped)
    evidence_ids: list[str] = []
    for score in package.dimension_scores:
        evidence_ids.extend(score.evidence_ids)
    for issue in package.issues:
        if issue.id in {item.record_id for item in caps}:
            evidence_ids.extend(issue.evidence_ids)
    evidence_ids = list(dict.fromkeys(evidence_ids))
    visual_status = "unchecked"
    if package.visual_findings:
        visual_status = "checked"
    elif package.format_checks:
        visual_status = "format_only"
    exemption_ids = [item.id for item in package.cap_exemptions if item.issue_id in {cap.record_id for cap in caps}]
    metadata: dict[str, Any] = {
        "venue": "ICLR",
        "confidence": confidence,
        "weighted": round(weighted, 4),
        "gate": gate,
        "raw": round(raw, 4),
        "capped": round(capped, 4),
        "cap_ids": [item.record_id for item in caps],
        "caps": [
            {"id": item.record_id, "fatal_class": item.fatal_class.value, "cap": item.cap, "exempted": item.exempted}
            for item in caps
        ],
        "exemption_ids": exemption_ids,
        "bands_after_ceiling": {dimension.value: bands[dimension] for dimension in bands},
        "visual_status": visual_status,
        "related_work_comparison": (
            None
            if comparison is None
            else {
                "status": comparison["status"],
                "failed_providers": list(comparison.get("failed_providers") or []),
                "baseline_counts": {
                    name: len(values) for name, values in comparison["baselines"].items()
                },
                "closest_neighbour_ids": [
                    item["id"] for item in comparison["closest_neighbours"] if item.get("id")
                ],
            }
        ),
        "formula": (
            "raw = min(dot(weights, bands), min(claims, soundness) + 1); "
            "capped = min(raw, active caps); venue = snap_down onto 1/3/5/6/8/10"
        ),
    }
    return OverallScore(
        value=float(venue),
        scale_min=1.0,
        scale_max=10.0,
        method=OverallScoreMethod.declared_formula,
        coefficient_set=COEFFICIENT_SET,
        notes=(
            "apr-iclr-cap-v2 is a declared formula with fatal-flaw caps. "
            "The venue bin is the largest ICLR bin that does not exceed the capped raw score. "
            "It is not a fitted ICLR regression."
        ),
        evidence_ids=evidence_ids,
        metadata=metadata,
    )


def apply_rating(package: ReviewPackage, *, confidence: int = 3) -> ReviewPackage:
    scored = rate_venue(package, confidence=confidence)
    return package.model_copy(update={"overall_score": scored})


class TieNote:
    """Structured reason two projects may share a venue bin. Not free-form score editing."""

    def __init__(self, left_project_id: str, right_project_id: str, axis: str, same_bin_justified: str) -> None:
        self.left_project_id = left_project_id
        self.right_project_id = right_project_id
        self.axis = axis
        self.same_bin_justified = same_bin_justified


def calibrate_packages(packages: list[ReviewPackage], ties: list[TieNote] | None = None):
    """Order packages by the declared formula and require a reason for every tied neighbor."""
    from .models import BatchCalibration, PairRecord

    notes = list(ties or [])
    rows: list[tuple[ReviewPackage, OverallScore]] = []
    for package in packages:
        existing = package.overall_score
        if (
            existing is not None
            and existing.method == OverallScoreMethod.declared_formula
            and existing.coefficient_set == COEFFICIENT_SET
        ):
            scored = existing
            current = package
        else:
            scored = rate_venue(package)
            current = package.model_copy(update={"overall_score": scored})
        rows.append((current, scored))
    rows.sort(
        key=lambda item: (
            -float(item[1].value),
            -float(item[1].metadata.get("weighted") or 0.0),
            item[0].project_id,
        )
    )
    order = [item[0].project_id for item in rows]
    if len(order) != len(set(order)):
        raise ValueError("batch calibration requires unique project ids")

    def note_for(left_id: str, right_id: str) -> TieNote | None:
        wanted = {left_id, right_id}
        for note in notes:
            if {note.left_project_id, note.right_project_id} == wanted:
                return note
        return None

    pairs = []
    for (left_package, left_score), (right_package, right_score) in zip(rows, rows[1:]):
        left_cap = effective_venue_cap(active_caps(left_package))
        right_cap = effective_venue_cap(active_caps(right_package))
        gap = "cap_differs" if left_cap != right_cap else "same_cap"
        same = None
        axis = None
        if gap == "cap_differs" and int(left_score.value) == int(right_score.value):
            raise ValueError(
                "different active caps for "
                f"{left_package.project_id} and {right_package.project_id} "
                "cannot share a venue bin"
            )
        if int(left_score.value) == int(right_score.value):
            note = note_for(left_package.project_id, right_package.project_id)
            if note is None or not str(note.same_bin_justified).strip():
                raise ValueError(
                    "same venue bin for "
                    f"{left_package.project_id} and {right_package.project_id} "
                    "requires same_bin_justified"
                )
            same = str(note.same_bin_justified).strip()
            axis = note.axis
        pairs.append(
            PairRecord(
                left_project_id=left_package.project_id,
                right_project_id=right_package.project_id,
                left_value=int(left_score.value),
                right_value=int(right_score.value),
                decision_gap=gap,  # type: ignore[arg-type]
                same_bin_justified=same,
                axis=axis,
            )
        )
    return BatchCalibration(
        coefficient_set=COEFFICIENT_SET,
        order=order,
        pairs=pairs,
        metadata={"rule": COEFFICIENT_SET},
    )
