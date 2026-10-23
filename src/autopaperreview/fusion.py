"""Declared rank signals. Not a fitted regression.

apr-fusion-v1 keeps a 1–6 decision and a ladder placement from one judgment.
The decision sets the ICLR bin. The placement is where the paper sits against
a fixed ladder of synthetic references, on 1.0–10.0, and it is the shipped
rank. A 1.0–10.0 venue mean from the same call stays eligible. A verified
venue cap and the seven dimension bands stay off this rank unless a tune-slice
comparison shows the cap is strictly better on both Spearman and AUC. They
are recorded so a report can explain the score.

apr-iclr-cap-v2 is unchanged and still uses its own weakest-link gate. It is
not a fusion candidate. A missing decision score is not filled in from the
dimension term.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rubrics import FUSION_COEFFICIENT_SET

__all__ = [
    "FUSION_COEFFICIENT_SET",
    "FUSION_VARIANT",
    "SHIPPABLE",
    "NEAR_TOLERANCE",
    "RankSignals",
    "candidate_scores",
    "cap_decision",
    "decision_bin",
    "DIAGNOSTIC",
    "select_variant",
    "selection_report",
]

# Shipped rank when a ladder placement was stored. A package with only a venue
# mean, or only a decision, still ranks by that older signal.
FUSION_VARIANT = "place"

# Eligible core ranks. One call produces every family at that K, so order is
# preference: placement, then venue mean, then decision. A within-0.02 tie
# keeps the earlier name. count-* is diagnostic and is not listed here.
SHIPPABLE = (
    "place-k1",
    "mean-k1",
    "decision-k1",
    "place-k2",
    "mean-k2",
    "decision-k2",
    "place-k3",
    "mean-k3",
    "decision-k3",
)

_CHEAPNESS = {name: index for index, name in enumerate(SHIPPABLE)}

# A candidate stays eligible when it is within this gap of the best Spearman
# and the best AUC. Those two bests may come from different candidates.
NEAR_TOLERANCE = 0.02

# Printed by rank_eval and never selected on their own. A capped name is
# adopted only by selection_report, and only when it beats the same-K core
# on both metrics. Dimension bands never are.
DIAGNOSTIC = (
    "band-continuous",
    "band-bin",
    "mean-gated",
    "decision-k1-bin",
    "decision-k2-bin",
    "decision-k3-bin",
    "decision-k1-capped",
    "decision-k2-capped",
    "decision-k3-capped",
    "count-k1",
    "count-k2",
    "count-k3",
)

# Decision levels 1, 2, 3, 4 map to four ICLR bins. snap_down of a 1–10 score
# in [3, 5) collapses that whole band onto bin 3.
_BIN_CUTS = (
    (1.5, 1),
    (2.5, 3),
    (3.5, 5),
    (4.5, 6),
    (5.5, 8),
)


def decision_bin(value: float) -> int:
    """ICLR bin for a 1.0–6.0 decision score.

    Cuts sit halfway between integer decisions, and the higher decision owns
    the boundary. 1.0–1.5 is strong reject, then reject, borderline reject,
    borderline accept, accept, and strong accept.
    """
    for cut, bin_value in _BIN_CUTS:
        if value < cut:
            return bin_value
    return 10


def cap_decision(score: float, venue_cap: int | None) -> float:
    """Lower a decision score so its bin does not exceed ``venue_cap``.

    The cap is an ICLR bin, not a point on the 1–6 scale. Subtracting the bin
    number from the decision score would mix the two scales.
    """
    if venue_cap is None or venue_cap >= 10:
        return score
    ceiling = 6.0
    for cut, bin_value in _BIN_CUTS:
        if bin_value <= venue_cap:
            ceiling = cut - 1e-4
    return min(score, ceiling)


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
    samples: tuple[float, ...] = ()
    mean_samples: tuple[float, ...] = ()
    place_samples: tuple[float, ...] = ()
    count_samples: tuple[float, ...] = ()
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


def _decision_samples(signals: RankSignals) -> tuple[float, ...]:
    if signals.samples:
        return signals.samples
    if signals.holistic is None:
        return ()
    return (float(signals.holistic),)


def _prefix_means(samples: tuple[float, ...], stem: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    if not samples:
        return scores
    for width in range(1, min(3, len(samples)) + 1):
        scores[f"{stem}-k{width}"] = sum(samples[:width]) / width
    scores[stem] = sum(samples) / len(samples)
    return scores


def candidate_scores(signals: RankSignals) -> dict[str, float]:
    """Venue-mean ranks, decision ranks, their bins, and diagnostic bands.

    Shippable keys are omitted when that family has no sample. Callers must
    fail a paper that has neither family. They must not substitute a dimension
    score.
    """
    scores = _diagnostics(signals)
    scores.update(_prefix_means(signals.place_samples, "place"))
    scores.update(_prefix_means(signals.count_samples, "count"))
    scores.update(_prefix_means(signals.mean_samples, "mean"))
    samples = _decision_samples(signals)
    if not samples:
        return scores
    scores.update(_prefix_means(samples, "decision"))
    for width in range(1, min(3, len(samples)) + 1):
        name = f"decision-k{width}"
        scores[f"{name}-capped"] = cap_decision(scores[name], signals.venue_cap)
        scores[f"{name}-bin"] = float(decision_bin(scores[name]))
    scores["decision-capped"] = cap_decision(scores["decision"], signals.venue_cap)
    return scores


def _pair(metrics: dict[str, dict[str, float | None]], name: str) -> tuple[float, float] | None:
    row = metrics.get(name) or {}
    spearman = row.get("spearman")
    auc = row.get("auc")
    if not isinstance(spearman, (int, float)) or not isinstance(auc, (int, float)):
        return None
    return float(spearman), float(auc)


def selection_report(metrics: dict[str, dict[str, float | None]]) -> dict[str, object]:
    """Tune-slice selection among the venue-mean and decision prefixes.

    A candidate is eligible when its Spearman is within ``NEAR_TOLERANCE`` of
    the best Spearman and its AUC is within ``NEAR_TOLERANCE`` of the best
    AUC. The bests may come from different candidates. The cheapest eligible
    candidate wins. At the same K the placement is preferred to the venue
    mean, and the venue mean is preferred to the decision, because they share
    a call. If none are eligible, the higher Spearman wins, then the higher
    AUC, then the cheaper candidate.

    ``<name>-capped`` replaces that choice only when both metrics are strictly
    higher. An equal cap is explanatory and stays off the rank.
    """
    present = [(name, pair) for name in SHIPPABLE if (pair := _pair(metrics, name)) is not None]
    if not present:
        raise ValueError("selection requires a decision candidate")
    best_spearman = max(pair[0] for _, pair in present)
    best_auc = max(pair[1] for _, pair in present)
    eligible = [
        name
        for name, pair in present
        if pair[0] >= best_spearman - NEAR_TOLERANCE and pair[1] >= best_auc - NEAR_TOLERANCE
    ]
    if eligible:
        chosen = sorted(eligible, key=lambda name: (_CHEAPNESS[name], name))[0]
        rule = "within-0.02-then-cheapest"
    else:
        chosen = sorted(
            present,
            key=lambda item: (-item[1][0], -item[1][1], _CHEAPNESS[item[0]], item[0]),
        )[0][0]
        rule = "spearman-then-auc"
    base = _pair(metrics, chosen)
    cap_name = f"{chosen}-capped"
    cap = _pair(metrics, cap_name)
    cap_adopted = False
    if (
        base is not None
        and cap is not None
        and cap[0] > base[0]
        and cap[1] > base[1]
    ):
        chosen = cap_name
        cap_adopted = True
    return {
        "chosen": chosen,
        "tied": list(eligible),
        "rule": rule,
        "cap_adopted": cap_adopted,
    }


def select_variant(metrics: dict[str, dict[str, float | None]]) -> str:
    chosen = selection_report(metrics)["chosen"]
    assert isinstance(chosen, str)
    return chosen
