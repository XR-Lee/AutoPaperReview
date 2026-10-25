"""Declared rank signals. Not a fitted regression.

apr-fusion-v1 keeps a 1–6 decision and the locked rank ``forecast-k1``. That
rank is one call's mean expected rating on 1.0–10.0: three simulated
reviewers on the full manuscript each give a probability over the six
official ratings, each reviewer's expected rating is taken under it, and the
three are averaged. The decision from the same call sets the ICLR bin and is
not the rank. ``vote-k*``, the mean of the same reviewers' integer ratings,
``initial-k*``, the mean of a discussed panel's first ratings, ``chair-k*``,
the model's own estimate of the mean after writing its panel, ``high-k*`` and
``low-k*``, the per-call highest and lowest simulated rating, ``form-k*``, the
review-form triad mapped onto 1–10, and ``checks-k*``, the count of verified
audit passes, are diagnostic. A package that has only two simulated
discussions still ranks by ``rebuttal-k2``, one with only two topic-matched
panels by ``matched-k2``, one with only two mixed-role panels by ``panel-k2``,
one with only two expert reads by ``expert-k2``, one with only an audited mean
by ``audited-k1``, and one with only three plain venue means by ``mean-k3``.
One that has only an older isolated pair, a ladder placement, a shorter venue
mean, or a decision still ranks by that older signal.

The measurement uses this one candidate. ``selection_report`` is not a
license to replace it after seeing tune or held-out metrics.

apr-iclr-cap-v2 is unchanged and still uses its own weakest-link gate. It is
not a fusion candidate. A missing decision score is not filled in from the
dimension term.
"""

from __future__ import annotations

from dataclasses import dataclass

from .anchors import LOCKED_CANDIDATE
from .rubrics import FUSION_COEFFICIENT_SET

__all__ = [
    "FUSION_COEFFICIENT_SET",
    "FUSION_VARIANT",
    "LOCKED_CANDIDATE",
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

# Shipped rank for a package that stored a forecast panel. Older packages fall
# through to two simulated discussions, then any discussion, then two matched
# panels, then any matched panel, then two mixed-role panels, then any panel,
# then two expert reads, then any expert reads, then the audited mean, then
# three venue means, then the isolated pair, then placement, then a shorter
# venue mean, then the decision.
FUSION_VARIANT = LOCKED_CANDIDATE

# Eligible core ranks when a diagnostic table is built. The locked measurement
# does not pick from this list. Order at the same K is preference for that
# table only: the forecast panel, then the discussed panel, then the matched
# panel, then the panel, then the expert read, then the audited mean, then the
# venue mean, then the isolated pair, then placement, then the decision. A
# shorter prefix stays ahead of a longer one. A within-0.02 tie keeps the
# earlier name. vote-*, initial-*, chair-*, high-*, low-*, form-*, checks-*,
# and count-* are diagnostic.
SHIPPABLE = (
    "forecast-k1",
    "rebuttal-k1",
    "matched-k1",
    "panel-k1",
    "expert-k1",
    "audited-k1",
    "mean-k1",
    "isolate-k1",
    "place-k1",
    "decision-k1",
    "forecast-k2",
    "rebuttal-k2",
    "matched-k2",
    "panel-k2",
    "expert-k2",
    "audited-k2",
    "mean-k2",
    "isolate-k2",
    "place-k2",
    "decision-k2",
    "forecast-k3",
    "rebuttal-k3",
    "matched-k3",
    "panel-k3",
    "expert-k3",
    "audited-k3",
    "mean-k3",
    "isolate-k3",
    "place-k3",
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
    "checks-k1",
    "checks-k2",
    "checks-k3",
    "form-k1",
    "form-k2",
    "form-k3",
    "vote-k1",
    "vote-k2",
    "vote-k3",
    "initial-k1",
    "initial-k2",
    "initial-k3",
    "chair-k1",
    "chair-k2",
    "chair-k3",
    "high-k1",
    "high-k2",
    "high-k3",
    "low-k1",
    "low-k2",
    "low-k3",
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
    isolate_samples: tuple[float, ...] = ()
    audited_samples: tuple[float, ...] = ()
    checks_samples: tuple[float, ...] = ()
    expert_samples: tuple[float, ...] = ()
    form_samples: tuple[float, ...] = ()
    forecast_samples: tuple[float, ...] = ()
    vote_samples: tuple[float, ...] = ()
    rebuttal_samples: tuple[float, ...] = ()
    initial_samples: tuple[float, ...] = ()
    matched_samples: tuple[float, ...] = ()
    panel_samples: tuple[float, ...] = ()
    chair_samples: tuple[float, ...] = ()
    high_samples: tuple[float, ...] = ()
    low_samples: tuple[float, ...] = ()
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
    """Forecast, discussed, matched, panel, expert, audited, and venue-mean ranks, decision ranks, bins, and diagnostics.

    Shippable keys are omitted when that family has no sample. Callers must
    fail a paper that has no decision. They must not substitute a dimension
    score. ``vote-k*`` is the mean of a forecast panel's integer ratings,
    ``initial-k*`` is the mean of the first ratings before the discussion,
    ``chair-k*`` is the model's own mean after its panel, ``high-k*`` and
    ``low-k*`` are the panel's extremes, ``form-k*`` is the review-form triad
    and ``checks-k*`` is the verified audit pass count; all are diagnostic.
    """
    scores = _diagnostics(signals)
    scores.update(_prefix_means(signals.vote_samples, "vote"))
    scores.update(_prefix_means(signals.initial_samples, "initial"))
    scores.update(_prefix_means(signals.chair_samples, "chair"))
    scores.update(_prefix_means(signals.high_samples, "high"))
    scores.update(_prefix_means(signals.low_samples, "low"))
    scores.update(_prefix_means(signals.form_samples, "form"))
    scores.update(_prefix_means(signals.checks_samples, "checks"))
    scores.update(_prefix_means(signals.forecast_samples, "forecast"))
    scores.update(_prefix_means(signals.rebuttal_samples, "rebuttal"))
    scores.update(_prefix_means(signals.matched_samples, "matched"))
    scores.update(_prefix_means(signals.panel_samples, "panel"))
    scores.update(_prefix_means(signals.expert_samples, "expert"))
    scores.update(_prefix_means(signals.audited_samples, "audited"))
    scores.update(_prefix_means(signals.isolate_samples, "isolate"))
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
    """Diagnostic table among stored rank families.

    The locked measurement does not call this. It reports ``LOCKED_CANDIDATE``.

    A candidate is eligible when its Spearman is within ``NEAR_TOLERANCE`` of
    the best Spearman and its AUC is within ``NEAR_TOLERANCE`` of the best
    AUC. The bests may come from different candidates. The cheapest eligible
    candidate wins. At the same K the forecast panel is preferred to the
    discussed panel, the discussed panel to the matched panel, the matched
    panel to the panel, the panel to the expert read,
    the expert read to the audited mean, the audited mean to the venue mean,
    the venue mean to the isolated pair, the isolated pair to the placement,
    and the placement to the decision. A shorter prefix is preferred to a
    longer one. If none are
    eligible, the higher Spearman wins, then the higher AUC, then the cheaper
    candidate.

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
        "preregistered": LOCKED_CANDIDATE,
        "tied": list(eligible),
        "rule": rule,
        "cap_adopted": cap_adopted,
    }


def select_variant(metrics: dict[str, dict[str, float | None]]) -> str:
    chosen = selection_report(metrics)["chosen"]
    assert isinstance(chosen, str)
    return chosen
