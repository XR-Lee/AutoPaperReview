"""Paired raw-versus-anonymized forecast deltas.

Rows are numbers the caller already has. This module does not read a
manuscript, a forum id, or a private holdout.
"""

from __future__ import annotations

from typing import Any, Sequence

from autopaperreview.public_calibration import kendall_tau, nearest_bin, roc_auc, spearman

from .paired_bootstrap import RESAMPLES, SEED, linear_percentile, stratified_indexes
from .reanalysis_v2 import (
    TIER_ORDER,
    adjacent_aucs,
    cohen_kappa,
    ipw_weights,
    loro_pairs,
    quadratic_weighted_kappa,
    weighted_spearman,
)

METRIC_NAMES = (
    "spearman",
    "ipw_spearman",
    "auc",
    "mae",
    "kappa_venue",
    "qwk_venue",
    "loro_spearman_gap",
)


def _mae(left: Sequence[float], right: Sequence[float]) -> float | None:
    if not left:
        return None
    return sum(abs(a - b) for a, b in zip(left, right, strict=True)) / len(left)


def _loro_gap(rows: Sequence[dict[str, Any]], score_key: str) -> float | None:
    human: list[float] = []
    target: list[float] = []
    model: list[float] = []
    for row in rows:
        ratings = row.get("ratings") or []
        if len(ratings) < 3 or row.get(score_key) is None:
            continue
        for pair in loro_pairs(ratings, float(row[score_key])):
            human.append(pair["human"])
            target.append(pair["target"])
            model.append(pair["apr"])
    human_rho = spearman(human, target)
    model_rho = spearman(model, target)
    if human_rho is None or model_rho is None:
        return None
    return model_rho - human_rho


def block_metrics(
    rows: Sequence[dict[str, Any]],
    *,
    score_key: str,
    bin_key: str,
    population: dict[str, float],
) -> dict[str, float | None]:
    """Rank metrics for one input condition. Missing scores are dropped."""
    usable = [row for row in rows if row.get(score_key) is not None and row.get(bin_key) is not None]
    scores = [float(row[score_key]) for row in usable]
    means = [float(row["human_mean"]) for row in usable]
    labels = [1 if row["decision"] == "accept" else 0 for row in usable]
    tiers = [str(row["tier"]) for row in usable]
    weights = ipw_weights(tiers, population) if usable else []
    human_bins = [nearest_bin(mean) for mean in means]
    venue_bins = [int(row[bin_key]) for row in usable]
    adjacent = adjacent_aucs(scores, tiers) if usable else {}
    point: dict[str, float | None] = {
        "n": float(len(usable)),
        "spearman": spearman(scores, means) if usable else None,
        "kendall": kendall_tau(scores, means) if usable else None,
        "ipw_spearman": weighted_spearman(scores, means, weights) if usable else None,
        "auc": roc_auc(scores, labels) if usable else None,
        "mae": _mae(scores, means),
        "kappa_venue": cohen_kappa(human_bins, venue_bins) if usable else None,
        "qwk_venue": quadratic_weighted_kappa(human_bins, venue_bins) if usable else None,
        "loro_spearman_gap": _loro_gap(usable, score_key),
    }
    for name, value in adjacent.items():
        point[f"adjacent:{name}"] = value
    return point


def _delta(anon: float | None, raw: float | None) -> float | None:
    if anon is None or raw is None:
        return None
    return anon - raw


def paired_deltas(
    rows: Sequence[dict[str, Any]],
    *,
    population: dict[str, float],
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Anonymized minus raw, with a stratified paired bootstrap.

    Each row needs ``tier``, ``stratum``, ``human_mean``, ``decision``,
    ``ratings``, ``rank_raw``, ``rank_anon``, ``bin_raw``, and ``bin_anon``.
    A resample draws papers once and scores both conditions on that draw.
    """
    raw = block_metrics(rows, score_key="rank_raw", bin_key="bin_raw", population=population)
    anon = block_metrics(rows, score_key="rank_anon", bin_key="bin_anon", population=population)
    names = [name for name in raw if name != "n"]
    point = {name: _delta(anon.get(name), raw.get(name)) for name in names}
    draws = stratified_indexes([str(row["stratum"]) for row in rows], resamples=resamples, seed=seed) if rows else []
    buckets: dict[str, list[float]] = {name: [] for name in names}
    for indexes in draws:
        sample = [rows[index] for index in indexes]
        sample_raw = block_metrics(sample, score_key="rank_raw", bin_key="bin_raw", population=population)
        sample_anon = block_metrics(sample, score_key="rank_anon", bin_key="bin_anon", population=population)
        for name in names:
            value = _delta(sample_anon.get(name), sample_raw.get(name))
            if value is not None:
                buckets[name].append(value)
    intervals = {}
    for name in names:
        values = buckets[name]
        low = high = None
        if values and point[name] is not None:
            ordered = sorted(values)
            low = linear_percentile(ordered, 0.025)
            high = linear_percentile(ordered, 0.975)
        intervals[name] = {
            "point": point[name],
            "low": low,
            "high": high,
            "defined": len(values),
            "resamples": resamples,
        }
    return {
        "n": int(raw["n"]),
        "raw": raw,
        "anonymized": anon,
        "delta": intervals,
    }
