"""Declared rank signals. Not a fitted regression.

apr-fusion-v1 ranks by the holistic rating. A verified venue cap is the only
downward adjustment. Dimension bands stay on the package as diagnostics. They
do not gate this rank: a saturated soundness clause must not flatten every
paper onto one score.

apr-iclr-cap-v2 is unchanged and still uses its own weakest-link gate. It is
not a fusion candidate. A missing holistic rating is not filled in from the
dimension term.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rubrics import FUSION_COEFFICIENT_SET

__all__ = [
    "FUSION_COEFFICIENT_SET",
    "FUSION_VARIANT",
    "SHIPPABLE",
    "RankSignals",
    "candidate_scores",
    "DIAGNOSTIC",
    "select_variant",
    "selection_report",
]

# Pre-registered until a tune-slice comparison locks a different shippable name.
FUSION_VARIANT = "holistic-capped"

# Exact metric ties break by this priority, then by name. List order is not a vote.
# A smaller number is the simpler candidate.
TIE_PRIORITY = {
    "holistic": 0,
    "holistic-capped": 1,
}

# holistic-capped is listed first on purpose. An exact tie must still return
# holistic, which is the simpler signal, so selection is not list order.
SHIPPABLE = (
    "holistic-capped",
    "holistic",
)

# Printed by rank_eval and never selected. They show whether dimension bands still move.
DIAGNOSTIC = (
    "band-continuous",
    "band-bin",
    "mean-gated",
)


@dataclass(frozen=True)
class RankSignals:
    """Numbers already on a rated package. No manuscript text."""

    weighted: float
    gate: float
    claims: float
    soundness: float
    band_mean: float
    venue_cap: int | None
    holistic: float | None
    bands_available: bool = True


def band_term(signals: RankSignals) -> float:
    """Diagnostic weakest-link term. Not the fusion rank."""
    return min(signals.weighted, signals.gate + 1.0)


def _with_cap(value: float, venue_cap: int | None) -> float:
    if venue_cap is None:
        return value
    return min(value, float(venue_cap))


def _diagnostics(signals: RankSignals) -> dict[str, float]:
    from .rating import snap_down

    if not signals.bands_available:
        return {}
    continuous = _with_cap(band_term(signals), signals.venue_cap)
    mean_gated = _with_cap(min(signals.band_mean, signals.gate + 1.0), signals.venue_cap)
    return {
        "band-bin": float(snap_down(continuous)),
        "band-continuous": continuous,
        "mean-gated": mean_gated,
        "precap-weighted": signals.weighted,
        "band-mean": signals.band_mean,
    }


def candidate_scores(signals: RankSignals) -> dict[str, float]:
    """Shippable ranks plus diagnostic dimension terms.

    Shippable keys are omitted when the holistic rating is missing. Callers
    must fail that paper. They must not substitute a dimension score.
    """
    scores = _diagnostics(signals)
    if signals.holistic is None:
        return scores
    scores["holistic"] = signals.holistic
    scores["holistic-capped"] = _with_cap(signals.holistic, signals.venue_cap)
    return scores


def _metric_key(metrics: dict[str, dict[str, float | None]], name: str) -> tuple[float, float]:
    row = metrics.get(name) or {}
    spearman = row.get("spearman")
    auc = row.get("auc")
    return (
        float(spearman) if isinstance(spearman, (int, float)) else -1.0,
        float(auc) if isinstance(auc, (int, float)) else -1.0,
    )


def selection_report(metrics: dict[str, dict[str, float | None]]) -> dict[str, object]:
    """Tune-slice selection: highest Spearman, then highest AUC.

    Exact equality is a tie. The tie breaks by ``TIE_PRIORITY`` (simpler
    candidate first), then by name. The returned ``tied`` list is every
    shippable name with that same Spearman and AUC, so a tie is visible.
    """
    best = max(_metric_key(metrics, name) for name in SHIPPABLE)
    tied = [name for name in SHIPPABLE if _metric_key(metrics, name) == best]
    ordered = sorted(tied, key=lambda name: (TIE_PRIORITY.get(name, 99), name))
    return {"chosen": ordered[0], "tied": ordered}


def select_variant(metrics: dict[str, dict[str, float | None]]) -> str:
    chosen = selection_report(metrics)["chosen"]
    assert isinstance(chosen, str)
    return chosen
