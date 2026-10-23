"""Declared rank signals. Not a fitted regression.

The dimension formula stays ``min(weighted, min(claims, soundness) + 1)`` with
verified fatal caps. A holistic rating is a second signal. Blends let that
rating move the rank. The weakest-link ceiling still binds the blend when both
critical bands are low, or whenever a verified venue cap is active.

Weights and the candidate list are fixed in this module. A tune-slice comparison
may choose one candidate. It does not estimate a coefficient.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rubrics import FUSION_COEFFICIENT_SET

__all__ = ["FUSION_COEFFICIENT_SET"]
# Locked after the tune-slice comparison. The selector may replace this name.
FUSION_VARIANT = "blend-60-gated"

HOLISTIC_WEIGHTS = (0.5, 0.6, 0.7)


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


def band_term(signals: RankSignals) -> float:
    """Existing weakest-link term, before the venue cap."""
    return min(signals.weighted, signals.gate + 1.0)


def _with_cap(value: float, venue_cap: int | None) -> float:
    if venue_cap is None:
        return value
    return min(value, float(venue_cap))


def corroborated_low(signals: RankSignals) -> bool:
    """Both critical bands are in the bottom two clauses.

    One stuck soundness clause does not by itself flatten a second signal.
    Both critical bands at 2 or 4, or a verified venue cap, does.
    """
    return signals.claims <= 4.0 and signals.soundness <= 4.0


def _finish(value: float, signals: RankSignals, *, hard_gate: bool) -> float:
    rank = value
    if hard_gate or corroborated_low(signals) or signals.venue_cap is not None:
        rank = min(rank, signals.gate + 1.0)
    return _with_cap(rank, signals.venue_cap)


def band_continuous(signals: RankSignals) -> float:
    return _with_cap(band_term(signals), signals.venue_cap)


def mean_gated(signals: RankSignals) -> float:
    return _with_cap(min(signals.band_mean, signals.gate + 1.0), signals.venue_cap)


def _blend(signals: RankSignals, holistic_weight: float, *, hard_gate: bool) -> float:
    band = band_term(signals)
    if signals.holistic is None:
        mixed = band
    else:
        mixed = holistic_weight * signals.holistic + (1.0 - holistic_weight) * band
    return _finish(mixed, signals, hard_gate=hard_gate)


def candidate_scores(signals: RankSignals) -> dict[str, float]:
    """Every declared candidate, including diagnostics that the selector will not ship."""
    from .rating import snap_down

    scores = {
        "band-bin": float(snap_down(band_continuous(signals))),
        "band-continuous": band_continuous(signals),
        "mean-gated": mean_gated(signals),
        "holistic-gated": _blend(signals, 1.0, hard_gate=True),
        "precap-weighted": signals.weighted,
        "band-mean": signals.band_mean,
        "holistic-only": signals.holistic if signals.holistic is not None else band_continuous(signals),
    }
    for weight in HOLISTIC_WEIGHTS:
        label = f"blend-{int(weight * 100)}-gated"
        scores[label] = _blend(signals, weight, hard_gate=False)
    scores["blend-60-hardgate"] = _blend(signals, 0.6, hard_gate=True)
    return scores


# The gate or a verified cap is part of each of these. Diagnostics are not.
SHIPPABLE = (
    "band-bin",
    "band-continuous",
    "mean-gated",
    "holistic-gated",
    "blend-50-gated",
    "blend-60-gated",
    "blend-70-gated",
    "blend-60-hardgate",
)


def select_variant(metrics: dict[str, dict[str, float | None]]) -> str:
    """Pick a shippable candidate from tune-slice metrics.

    Highest Spearman, then highest AUC. ``band-continuous`` wins a tie inside
    0.02 on both, so a noisy blend does not replace the dimension formula.
    """

    def sort_key(name: str) -> tuple[float, float]:
        row = metrics.get(name) or {}
        spearman = row.get("spearman")
        auc = row.get("auc")
        return (
            float(spearman) if isinstance(spearman, (int, float)) else -1.0,
            float(auc) if isinstance(auc, (int, float)) else -1.0,
        )

    best = max(SHIPPABLE, key=sort_key)
    base = sort_key("band-continuous")
    top = sort_key(best)
    if best != "band-continuous" and top[0] - base[0] < 0.02 and top[1] - base[1] < 0.02:
        return "band-continuous"
    return best
