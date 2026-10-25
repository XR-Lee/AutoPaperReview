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


def _bounded_sample_list(
    raw: dict[str, Any],
    key: str,
    *,
    low: float,
    high: float,
    lone: str | None,
) -> tuple[float, ...] | None:
    """Verified samples on one scale. A lone field counts as one sample."""
    if raw.get("verified") is not True:
        return None
    raw_samples = raw.get(key)
    if isinstance(raw_samples, list) and raw_samples:
        source: list[Any] = list(raw_samples)
    elif lone is not None and raw.get(lone) is not None:
        source = [raw.get(lone)]
    else:
        return None
    values: list[float] = []
    for item in source:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        value = float(item)
        if value < low or value > high:
            return None
        values.append(value)
    return tuple(values)


def _decision_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–6.0 decision samples. A lone rating counts as one sample."""
    return _bounded_sample_list(raw, "samples", low=1.0, high=6.0, lone="rating")


def _mean_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 venue-mean samples. A lone predicted_mean counts as one."""
    return _bounded_sample_list(raw, "mean_samples", low=1.0, high=10.0, lone="predicted_mean")


def _place_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 ladder placements. A lone place value counts as one."""
    return _bounded_sample_list(raw, "place_samples", low=1.0, high=10.0, lone="place")


def _count_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 vote counts. A lone count value counts as one."""
    return _bounded_sample_list(raw, "count_samples", low=1.0, high=10.0, lone="count")


def _isolate_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 isolated-pair scores. A lone isolate value counts as one."""
    return _bounded_sample_list(raw, "isolate_samples", low=1.0, high=10.0, lone="isolate")


def _audited_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 audited venue means. A lone audited_mean counts as one."""
    return _bounded_sample_list(raw, "audited_samples", low=1.0, high=10.0, lone="audited_mean")


def _checks_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 audit pass counts. Diagnostic. A lone checks_score counts as one."""
    return _bounded_sample_list(raw, "checks_samples", low=1.0, high=10.0, lone="checks_score")


def _expert_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 expert-read venue means. A lone expert_mean counts as one."""
    return _bounded_sample_list(raw, "expert_samples", low=1.0, high=10.0, lone="expert_mean")


def _form_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 review-form triads. Diagnostic. A lone form_score counts as one."""
    return _bounded_sample_list(raw, "form_samples", low=1.0, high=10.0, lone="form_score")


def _forecast_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 mean expected ratings of a simulated forecast panel. A lone forecast_mean counts as one."""
    return _bounded_sample_list(raw, "forecast_samples", low=1.0, high=10.0, lone="forecast_mean")


def _vote_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 means of a forecast panel's integer ratings. Diagnostic. A lone vote_mean counts as one."""
    return _bounded_sample_list(raw, "vote_samples", low=1.0, high=10.0, lone="vote_mean")


def _rebuttal_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 final-rating means after a simulated discussion. A lone rebuttal_mean counts as one."""
    return _bounded_sample_list(raw, "rebuttal_samples", low=1.0, high=10.0, lone="rebuttal_mean")


def _initial_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 first-rating means before the discussion. Diagnostic. A lone initial_mean counts as one."""
    return _bounded_sample_list(raw, "initial_samples", low=1.0, high=10.0, lone="initial_mean")


def _matched_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 matched-panel means. A lone matched_mean counts as one."""
    return _bounded_sample_list(raw, "matched_samples", low=1.0, high=10.0, lone="matched_mean")


def _panel_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 panel means. A lone panel_mean counts as one."""
    return _bounded_sample_list(raw, "panel_samples", low=1.0, high=10.0, lone="panel_mean")


def _chair_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 chair estimates after a panel. Diagnostic. A lone chair_mean counts as one."""
    return _bounded_sample_list(raw, "chair_samples", low=1.0, high=10.0, lone="chair_mean")


def _high_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 highest simulated ratings per panel. Diagnostic."""
    return _bounded_sample_list(raw, "high_samples", low=1.0, high=10.0, lone=None)


def _low_sample_list(raw: dict[str, Any]) -> tuple[float, ...] | None:
    """Verified 1.0–10.0 lowest simulated ratings per panel. Diagnostic."""
    return _bounded_sample_list(raw, "low_samples", low=1.0, high=10.0, lone=None)


def _clamp_span(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def rate_venue(
    package: ReviewPackage,
    *,
    confidence: int = 3,
    coefficient_set: str | None = None,
) -> OverallScore:
    """Compute the discrete ICLR bin and store provenance on OverallScore."""
    if confidence not in {1, 2, 3, 4, 5}:
        raise ValueError("rating confidence must be an integer from 1 to 5")
    from .fusion import (
        FUSION_COEFFICIENT_SET,
        FUSION_VARIANT,
        RankSignals,
        candidate_scores,
        decision_bin,
    )

    chosen = coefficient_set or COEFFICIENT_SET
    if chosen not in {COEFFICIENT_SET, FUSION_COEFFICIENT_SET}:
        raise ValueError(f"unknown coefficient set {chosen}")
    comparison = _read_related_work_comparison(package)
    caps = active_caps(package)
    venue_cap = effective_venue_cap(caps)
    bands = None
    if not (chosen == FUSION_COEFFICIENT_SET and not package.dimension_scores):
        bands = _bands(package)
        _apply_ceilings(bands, package, caps)
    # math.fsum, not sum(). CPython 3.11 adds these products left to right, so a
    # uniform 6 is 5.999999999999998 and snap_down returns 5 (a uniform 3
    # becomes 2.999999999999999 and returns 1). 3.12+ sum() uses Neumaier
    # summation and stays on the bin. fsum matches that result on 3.11/3.12/3.13.
    if bands is None:
        weighted = 0.0
        gate = 0.0
        raw = 0.0
        capped = 0.0
        band_mean = 0.0
        claims = 0.0
        soundness = 0.0
    else:
        weighted = math.fsum(WEIGHTS[dimension] * bands[dimension] for dimension in WEIGHTS)
        gate = min(
            bands[SarDimension.claims_supported],
            bands[SarDimension.experimental_soundness],
        )
        raw = min(weighted, gate + 1.0)
        capped = raw if venue_cap is None else min(raw, float(venue_cap))
        band_mean = sum(bands.values()) / len(bands)
        claims = bands[SarDimension.claims_supported]
        soundness = bands[SarDimension.experimental_soundness]
    venue = snap_down(capped) if bands is not None else 1
    raw_decision = package.metadata.get("holistic_rating")
    decision_samples = (
        _decision_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    )
    mean_samples = _mean_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    place_samples = _place_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    count_samples = _count_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    isolate_samples = _isolate_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    audited_samples = _audited_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    checks_samples = _checks_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    expert_samples = _expert_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    form_samples = _form_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    forecast_samples = _forecast_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    vote_samples = _vote_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    rebuttal_samples = _rebuttal_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    initial_samples = _initial_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    matched_samples = _matched_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    panel_samples = _panel_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    chair_samples = _chair_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    high_samples = _high_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    low_samples = _low_sample_list(raw_decision) if isinstance(raw_decision, dict) else None
    raw_audits = raw_decision.get("audits") if isinstance(raw_decision, dict) else None
    audits = list(raw_audits) if isinstance(raw_audits, list) else []
    raw_forms = raw_decision.get("forms") if isinstance(raw_decision, dict) else None
    forms = list(raw_forms) if isinstance(raw_forms, list) else []
    raw_panels = raw_decision.get("panels") if isinstance(raw_decision, dict) else None
    panels = list(raw_panels) if isinstance(raw_panels, list) else []
    raw_matched = raw_decision.get("matched_panels") if isinstance(raw_decision, dict) else None
    matched_panels = list(raw_matched) if isinstance(raw_matched, list) else []
    raw_rebuttal = raw_decision.get("rebuttal_panels") if isinstance(raw_decision, dict) else None
    rebuttal_panels = list(raw_rebuttal) if isinstance(raw_rebuttal, list) else []
    raw_forecast = raw_decision.get("forecast_panels") if isinstance(raw_decision, dict) else None
    forecast_panels = list(raw_forecast) if isinstance(raw_forecast, list) else []
    raw_main_text = raw_decision.get("main_text") if isinstance(raw_decision, dict) else None
    main_text = dict(raw_main_text) if isinstance(raw_main_text, dict) else {}
    holistic_mode = raw_decision.get("mode") if isinstance(raw_decision, dict) else None
    raw_placements = raw_decision.get("placements") if isinstance(raw_decision, dict) else None
    placements = list(raw_placements) if isinstance(raw_placements, list) else []
    raw_poles = raw_decision.get("poles") if isinstance(raw_decision, dict) else None
    poles = list(raw_poles) if isinstance(raw_poles, list) else []
    raw_ladder = raw_decision.get("ladder") if isinstance(raw_decision, dict) else None
    ladder = list(raw_ladder) if isinstance(raw_ladder, list) else []
    signals = RankSignals(
        weighted=weighted,
        gate=gate,
        claims=claims,
        soundness=soundness,
        band_mean=band_mean,
        venue_cap=venue_cap,
        holistic=None if not decision_samples else sum(decision_samples) / len(decision_samples),
        samples=decision_samples or (),
        mean_samples=mean_samples or (),
        place_samples=place_samples or (),
        count_samples=count_samples or (),
        isolate_samples=isolate_samples or (),
        audited_samples=audited_samples or (),
        checks_samples=checks_samples or (),
        expert_samples=expert_samples or (),
        form_samples=form_samples or (),
        forecast_samples=forecast_samples or (),
        vote_samples=vote_samples or (),
        rebuttal_samples=rebuttal_samples or (),
        initial_samples=initial_samples or (),
        matched_samples=matched_samples or (),
        panel_samples=panel_samples or (),
        chair_samples=chair_samples or (),
        high_samples=high_samples or (),
        low_samples=low_samples or (),
        bands_available=bands is not None,
    )
    candidates = {name: round(value, 4) for name, value in candidate_scores(signals).items()}
    scale_max = 10.0
    variant: str | None = None
    if chosen == FUSION_COEFFICIENT_SET:
        if FUSION_VARIANT in candidates:
            variant = FUSION_VARIANT
        elif "rebuttal-k2" in candidates:
            variant = "rebuttal-k2"
        elif "rebuttal" in candidates:
            variant = "rebuttal"
        elif "matched-k2" in candidates:
            variant = "matched-k2"
        elif "matched" in candidates:
            variant = "matched"
        elif "panel-k2" in candidates:
            variant = "panel-k2"
        elif "panel" in candidates:
            variant = "panel"
        elif "expert-k2" in candidates:
            variant = "expert-k2"
        elif "expert" in candidates:
            variant = "expert"
        elif "audited-k1" in candidates:
            variant = "audited-k1"
        elif "mean-k3" in candidates:
            variant = "mean-k3"
        elif "isolate-k1" in candidates:
            variant = "isolate-k1"
        elif "place" in candidates:
            variant = "place"
        elif "mean" in candidates:
            variant = "mean"
        else:
            variant = "decision"
        if variant not in candidates or "decision" not in candidates:
            raise ValueError(
                "apr-fusion-v1 requires a decision score; refusing to fall back to dimension bands"
            )
        venue = decision_bin(candidates["decision"])
        if variant == "forecast-k1":
            rank_score = round(_clamp_span(candidates["forecast-k1"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by forecast-k1, one call's mean expected rating on 1.0-10.0. "
                "The call saw the whole manuscript (the source text capped only for the longest "
                "manuscripts), simulated three of the reviewers the venue would assign, in declared "
                "seats, and had each write a review, the official rating they would most likely "
                "submit, and a probability over the six official ratings; each reviewer's expected "
                "rating is taken under that probability, and the sample is the mean of the three "
                "expected ratings. The 1-6 decision from the same call sets the ICLR bin and is not "
                "the rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The mean of the "
                "three integer ratings is recorded as vote-k1 and is not the rank; the model's own "
                "estimate of the mean is chair-k1 and is not the rank; the highest and lowest "
                "expected rating are high-k1 and low-k1 and are not the rank. A verified venue cap "
                "is not applied to this rank. Dimension bands are not a gate. A missing decision "
                "score fails the paper. A second call is not this candidate. It is not a fitted "
                "regression."
            )
            formula = (
                "rank = forecast-k1; "
                "rank = clamp(mean expected rating of the first forecast panel on 1.0-10.0, 1, 10); "
                "expected rating = sum(probability of each official rating 1, 3, 5, 6, 8, 10 times "
                "that rating), probabilities normalised to one; "
                "mean expected rating = mean(three simulated reviewers' expected ratings from one "
                "call on the full manuscript); "
                "vote-k1 = mean(three most-likely integer ratings) (diagnostic); "
                "chair-k1 = chair estimate (diagnostic); "
                "high-k1 and low-k1 = highest and lowest expected rating (diagnostic); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "rebuttal-k2":
            rank_score = round(_clamp_span(candidates["rebuttal-k2"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by rebuttal-k2, the mean of two final-rating means on "
                "1.0-10.0 from two calls that ran together and did not see each other. Each call "
                "saw the whole manuscript (the source text capped only for the longest "
                "manuscripts), simulated three of the reviewers the venue would assign, in "
                "declared seats, had each write a first review and submit an official rating, "
                "wrote the authors' response, and had each reviewer give the official rating "
                "they hold at the end of the discussion; the per-call sample is the mean of "
                "those three final ratings. The 1-6 decision from the same calls sets the ICLR "
                "bin and is not the rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. "
                "The mean of the first ratings is recorded as initial-k2 and is not the rank; "
                "the model's own estimate of the final mean is chair-k2 and is not the rank; the "
                "per-call highest and lowest final rating are high-k2 and low-k2 and are not the "
                "rank. A verified venue cap is not applied to this rank. Dimension bands are not "
                "a gate. A missing decision score fails the paper. A third call is not this "
                "candidate. It is not a fitted regression."
            )
            formula = (
                "rank = rebuttal-k2; "
                "rank = clamp(mean(first two final-rating means on 1.0-10.0), 1, 10); "
                "final-rating mean = mean(three simulated official ratings held at the end of the "
                "discussion from one call on the full manuscript); "
                "initial-k2 = mean(first two first-rating means) (diagnostic); "
                "chair-k2 = mean(first two chair estimates) (diagnostic); "
                "high-k2 and low-k2 = mean(per-call highest and lowest final rating) (diagnostic); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "rebuttal":
            rank_score = round(_clamp_span(candidates["rebuttal"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by the mean of the stored final-rating means on 1.0-10.0; "
                "this package does not hold the two calls of the locked rebuttal-k2. Each "
                "final-rating mean is the mean of the three official ratings a simulated panel "
                "holds at the end of the discussion period in one call on the full manuscript. "
                "The 1-6 decision from the same calls sets the ICLR bin and is not the rank. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The first-rating mean, "
                "the chair estimate and the panel extremes are recorded and are not the rank. A "
                "verified venue cap is not applied to this rank. Dimension bands are not a gate. "
                "A missing decision score fails the paper. It is not a fitted regression."
            )
            formula = (
                "rank = rebuttal; "
                "rank = clamp(mean(final-rating means on 1.0-10.0), 1, 10); "
                "final-rating mean = mean(three simulated official ratings held at the end of the "
                "discussion from one call on the full manuscript); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "matched-k2":
            rank_score = round(_clamp_span(candidates["matched-k2"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by matched-k2, the mean of two panel means on 1.0-10.0 from "
                "two calls that ran together and did not see each other. Each call saw the "
                "manuscript's main text (the reference list and the appendix cut from the packet, "
                "the text capped), simulated the four topic-matched reviewers of mixed seniority "
                "the venue would assign, in declared seats, with the venue's rating norms stated, "
                "wrote their reviews, and had each submit an official rating; the panel mean is "
                "the mean of those four ratings. The 1-6 decision from the same calls sets the "
                "ICLR bin and is not the rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, "
                "and 6. The model's own estimate of the mean after writing its panel is recorded "
                "as chair-k2 and is not the rank; the per-call highest and lowest rating are "
                "high-k2 and low-k2 and are not the rank. A verified venue cap is not applied to "
                "this rank. Dimension bands are not a gate. A missing decision score fails the "
                "paper. A third panel is not this candidate. It is not a fitted regression."
            )
            formula = (
                "rank = matched-k2; "
                "rank = clamp(mean(first two matched-panel means on 1.0-10.0), 1, 10); "
                "panel mean = mean(four simulated official ratings from one call on the main text); "
                "chair-k2 = mean(first two chair estimates) (diagnostic); "
                "high-k2 and low-k2 = mean(per-call highest and lowest rating) (diagnostic); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "matched":
            rank_score = round(_clamp_span(candidates["matched"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by the mean of the stored matched-panel means on 1.0-10.0; "
                "this package does not hold the two panels of the locked matched-k2. Each panel "
                "mean is the mean of the four official ratings a simulated topic-matched panel "
                "submitted in one call after reading the manuscript's main text. The 1-6 decision "
                "from the same calls sets the ICLR bin and is not the rank. Levels 1, 2, 3, and 4 "
                "map to ICLR bins 1, 3, 5, and 6. The chair estimate and the panel extremes are "
                "recorded and are not the rank. A verified venue cap is not applied to this rank. "
                "Dimension bands are not a gate. A missing decision score fails the paper. It is "
                "not a fitted regression."
            )
            formula = (
                "rank = matched; "
                "rank = clamp(mean(matched-panel means on 1.0-10.0), 1, 10); "
                "panel mean = mean(four simulated official ratings from one call on the main text); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "panel-k2":
            rank_score = round(_clamp_span(candidates["panel-k2"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by panel-k2, the mean of two panel means on 1.0-10.0 from "
                "two calls that ran together and did not see each other. Each call simulated the "
                "four reviewers the venue would assign, in declared roles, wrote their reviews, and "
                "had each submit an official rating; the panel mean is the mean of those four "
                "ratings. The 1-6 decision from the same calls sets the ICLR bin and is not the "
                "rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The model's own "
                "estimate of the mean after writing its panel is recorded as chair-k2 and is not "
                "the rank; the per-call highest and lowest rating are high-k2 and low-k2 and are "
                "not the rank. A verified venue cap is not applied to this rank. Dimension bands "
                "are not a gate. A missing decision score fails the paper. A third panel is not "
                "this candidate. It is not a fitted regression."
            )
            formula = (
                "rank = panel-k2; "
                "rank = clamp(mean(first two panel means on 1.0-10.0), 1, 10); "
                "panel mean = mean(four simulated official ratings from one call); "
                "chair-k2 = mean(first two chair estimates) (diagnostic); "
                "high-k2 and low-k2 = mean(per-call highest and lowest rating) (diagnostic); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "panel":
            rank_score = round(_clamp_span(candidates["panel"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by the mean of the stored panel means on 1.0-10.0; this "
                "package does not hold the two panels of the locked panel-k2. Each panel mean is "
                "the mean of the four official ratings a simulated review panel submitted in one "
                "call. The 1-6 decision from the same calls sets the ICLR bin and is not the rank. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The chair estimate and the "
                "panel extremes are recorded and are not the rank. A verified venue cap is not "
                "applied to this rank. Dimension bands are not a gate. A missing decision score "
                "fails the paper. It is not a fitted regression."
            )
            formula = (
                "rank = panel; "
                "rank = clamp(mean(panel means on 1.0-10.0), 1, 10); "
                "panel mean = mean(four simulated official ratings from one call); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "expert-k2":
            rank_score = round(_clamp_span(candidates["expert-k2"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by expert-k2, the mean of two venue-scale estimates on "
                "1.0-10.0 from two expert reads that ran together and did not see each other. "
                "Each read named the contribution type, the closest prior work and the field "
                "context it recalled, and filled the review form before estimating the mean "
                "official rating. The 1-6 decision from the same calls sets the ICLR bin and is "
                "not the rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The "
                "review-form triad is recorded as form-k2 and is not the rank. A verified venue "
                "cap is not applied to this rank. Dimension bands are not a gate. A missing "
                "decision score fails the paper. A third read is not this candidate. It is not a "
                "fitted regression."
            )
            formula = (
                "rank = expert-k2; "
                "rank = clamp(mean(first two expert-read venue-scale estimates on 1.0-10.0), 1, 10); "
                "form-k2 = mean(1 + soundness + presentation + contribution - 3) (diagnostic); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "expert":
            rank_score = round(_clamp_span(candidates["expert"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by the mean of the stored expert-read venue-scale estimates "
                "on 1.0-10.0; this package does not hold the two reads of the locked expert-k2. "
                "The 1-6 decision from the same calls sets the ICLR bin and is not the rank. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The review-form triad is "
                "recorded as form and is not the rank. A verified venue cap is not applied to "
                "this rank. Dimension bands are not a gate. A missing decision score fails the "
                "paper. It is not a fitted regression."
            )
            formula = (
                "rank = expert; "
                "rank = clamp(mean(expert-read venue-scale estimates on 1.0-10.0), 1, 10); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "audited-k1":
            rank_score = round(_clamp_span(candidates["audited-k1"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by audited-k1, one venue-scale estimate on 1.0-10.0 from "
                "a scoring call that saw a verified audit of the manuscript beside the "
                "manuscript. The audit answered twelve fixed questions and listed flaws; each "
                "pass, fail, and flaw kept only when its excerpt resolved in the manuscript. "
                "The 1-6 decision from the same scoring call sets the ICLR bin and is not the "
                "rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. The verified "
                "pass count is recorded as checks-k1 and is not the rank. A verified venue cap "
                "is not applied to this rank. Dimension bands are not a gate. A missing "
                "decision score fails the paper. A second pair is not this candidate. It is "
                "not a fitted regression."
            )
            formula = (
                "rank = audited-k1; "
                "rank = clamp(audited venue-scale estimate from the first audit-then-score pair, 1, 10); "
                "checks-k1 = 1 + 9 * verified_passes / 12 (diagnostic); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "mean-k3":
            rank_score = round(_clamp_span(candidates["mean-k3"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by mean-k3, the mean of three independent venue-scale "
                "samples on 1.0-10.0. Each sample estimates the mean official rating. "
                "The 1-6 decision sets the ICLR bin and is not the rank. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. "
                "A verified venue cap is not applied to this rank. Dimension bands are not a "
                "gate. A missing decision score fails the paper. A fourth sample is not this "
                "candidate. It is not a fitted regression."
            )
            formula = (
                "rank = mean-k3; "
                "rank = mean(first three venue-scale samples on 1.0-10.0); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "isolate-k1":
            rank_score = round(_clamp_span(candidates["isolate-k1"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by isolate-k1, the mean of two implied scores from one "
                "pair of isolated comparisons on 1.0-10.0. weak-reject is at 2.5 and "
                "strong-accept is at 8.5. Better adds the margin, worse subtracts it, and a "
                "tie stays on that pole. The margin stops at 6, the declared gap between the "
                "poles, and the result is clamped to 1.0-10.0. The 1-6 decision sets the ICLR "
                "bin and is not the rank. Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. "
                "A verified venue cap is not applied to this rank. Dimension bands are not a "
                "gate. A missing decision score fails the paper. Further pairs are not this "
                "candidate. It is not a fitted regression."
            )
            formula = (
                "rank = isolate-k1; "
                "rank = mean(implied(weak-reject), implied(strong-accept)) from the first pair; "
                "implied = clamp(position + margin if better, position - margin if worse, position if tie, 1, 10); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "place":
            rank_score = round(_clamp_span(candidates["place"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by placement against a fixed ladder of synthetic references "
                "on 1.0-10.0. The 1-6 decision sets the ICLR bin and is not the rank. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. "
                "The venue-scale mean is recorded and is not the rank. "
                "A verified venue cap is not applied to this rank. "
                "Dimension bands are not a gate. A missing decision score fails the paper. "
                "It is not a fitted regression."
            )
            formula = (
                "rank = place; "
                "rank = mean(ladder placements on 1.0-10.0); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        elif variant == "mean":
            rank_score = round(_clamp_span(candidates["mean"], 1.0, 10.0), 4)
            scale_max = 10.0
            notes = (
                "apr-fusion-v1 ranks by the mean of independent venue-scale samples on 1.0-10.0. "
                "The 1-6 decision sets the ICLR bin and is not the rank. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. "
                "A verified venue cap is not applied to this rank. "
                "Dimension bands are not a gate. A missing decision score fails the paper. "
                "It is not a fitted regression."
            )
            formula = (
                "rank = mean; "
                "rank = mean(venue-scale samples on 1.0-10.0); "
                "venue_bin maps decision levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        else:
            rank_score = round(_clamp_span(candidates["decision"], 1.0, 6.0), 4)
            scale_max = 6.0
            notes = (
                "apr-fusion-v1 ranks by the mean of independent decision samples on a 1.0-6.0 scale. "
                "Levels 1, 2, 3, and 4 map to ICLR bins 1, 3, 5, and 6. "
                "A verified venue cap is not applied to this rank. "
                "Dimension bands are not a gate. A missing decision score fails the paper. "
                "It is not a fitted regression."
            )
            formula = (
                "rank = decision; "
                "rank = mean(decision samples on 1.0-6.0); "
                "venue_bin maps levels 1/2/3/4/5/6 onto 1/3/5/6/8/10; "
                "a verified venue cap does not change this rank"
            )
        reported = rank_score
    else:
        rank_score = round(capped, 4)
        reported = float(venue)
        notes = (
            "apr-iclr-cap-v2 is a declared formula with fatal-flaw caps. "
            "The venue bin is the largest ICLR bin that does not exceed the capped raw score. "
            "It is not a fitted ICLR regression."
        )
        formula = (
            "raw = min(dot(weights, bands), min(claims, soundness) + 1); "
            "capped = min(raw, active caps); venue = snap_down onto 1/3/5/6/8/10"
        )
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
        "venue_bin": int(venue),
        "rank_score": rank_score,
        "candidates": candidates,
        "holistic": None if signals.holistic is None else round(signals.holistic, 4),
        "samples": [round(item, 4) for item in signals.samples],
        "mean_samples": [round(item, 4) for item in signals.mean_samples],
        "place_samples": [round(item, 4) for item in signals.place_samples],
        "count_samples": [round(item, 4) for item in signals.count_samples],
        "isolate_samples": [round(item, 4) for item in signals.isolate_samples],
        "audited_samples": [round(item, 4) for item in signals.audited_samples],
        "checks_samples": [round(item, 4) for item in signals.checks_samples],
        "expert_samples": [round(item, 4) for item in signals.expert_samples],
        "form_samples": [round(item, 4) for item in signals.form_samples],
        "forecast_samples": [round(item, 4) for item in signals.forecast_samples],
        "vote_samples": [round(item, 4) for item in signals.vote_samples],
        "rebuttal_samples": [round(item, 4) for item in signals.rebuttal_samples],
        "initial_samples": [round(item, 4) for item in signals.initial_samples],
        "matched_samples": [round(item, 4) for item in signals.matched_samples],
        "panel_samples": [round(item, 4) for item in signals.panel_samples],
        "chair_samples": [round(item, 4) for item in signals.chair_samples],
        "high_samples": [round(item, 4) for item in signals.high_samples],
        "low_samples": [round(item, 4) for item in signals.low_samples],
        "audits": audits,
        "forms": forms,
        "panels": panels,
        "matched_panels": matched_panels,
        "rebuttal_panels": rebuttal_panels,
        "forecast_panels": forecast_panels,
        "main_text": main_text,
        "holistic_mode": holistic_mode,
        "placements": placements,
        "poles": poles,
        "ladder": ladder,
        "cap_ids": [item.record_id for item in caps],
        "caps": [
            {"id": item.record_id, "fatal_class": item.fatal_class.value, "cap": item.cap, "exempted": item.exempted}
            for item in caps
        ],
        "exemption_ids": exemption_ids,
        "bands_after_ceiling": (
            {} if bands is None else {dimension.value: bands[dimension] for dimension in bands}
        ),
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
        "formula": formula,
        "fusion_variant": variant,
    }
    return OverallScore(
        value=reported,
        scale_min=1.0,
        scale_max=scale_max,
        method=OverallScoreMethod.declared_formula,
        coefficient_set=chosen,
        notes=notes,
        evidence_ids=evidence_ids,
        metadata=metadata,
    )


def apply_rating(
    package: ReviewPackage,
    *,
    confidence: int = 3,
    coefficient_set: str | None = None,
) -> ReviewPackage:
    scored = rate_venue(package, confidence=confidence, coefficient_set=coefficient_set)
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
