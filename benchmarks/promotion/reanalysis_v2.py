"""Zero-cost reanalysis of a stratified ICLR dev set.

The functions take numbers the caller already has. They do not read a
manuscript, a forum id, or a private holdout. Intervals use the promotion
bootstrap: stratified by tier, 4000 resamples, seed 20261029, linear
percentiles 0.025 and 0.975.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

from autopaperreview.public_calibration import kendall_tau, nearest_bin, roc_auc, spearman
from autopaperreview.rubrics import ICLR_BINS

from .paired_bootstrap import RESAMPLES, SEED, linear_percentile, stratified_indexes

TIER_ORDER = ("clear_reject", "borderline_reject", "borderline_accept", "clear_accept")
# Continuous reviewer-mean cuts. The outer bins absorb the normal tails.
TIER_CUTS = (4.0, 5.5, 7.0)
SAMPLE_TIER_SHARE = 0.25

# Paper Copilot venue summary, read 2026-11-25 from
# https://papercopilot.com/statistics/iclr-statistics/iclr-2025-statistics/
# min/max/mean/std are of per-submission reviewer-average scores.
# Reject counts are opt-in public releases and can be smaller than all rejects.
# Accept + reject + withdraw + desk reject does not equal the stated total
# (2025: 11755 vs 11672; 2024: 7407 vs 7304). The mixture uses the three
# scored statuses and renormalizes them.
PAPER_COPILOT = {
    "source": "https://papercopilot.com/statistics/iclr-statistics/iclr-2025-statistics/",
    "retrieved": "2026-11-25",
    "years": {
        2025: {
            "stated_total": 11672,
            "stated_mean": 5.12,
            "stated_std": 1.25,
            "accept": {"count": 3703, "mean": 6.43, "std": 0.68},
            "reject": {"count": 4911, "mean": 4.77, "std": 0.92},
            "withdraw": {"count": 2993, "mean": 4.05, "std": 0.81},
            "desk_reject": 148,
        },
        2024: {
            "stated_total": 7304,
            "stated_mean": 5.11,
            "stated_std": 1.26,
            "accept": {"count": 2260, "mean": 6.44, "std": 0.70},
            "reject": {"count": 3438, "mean": 4.73, "std": 0.94},
            "withdraw": {"count": 1655, "mean": 4.02, "std": 0.80},
            "desk_reject": 54,
        },
    },
}


def normal_cdf(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 1.0 if value >= mean else 0.0
    return 0.5 * (1.0 + math.erf((value - mean) / (std * math.sqrt(2.0))))


def _components(statuses: Sequence[dict[str, float]]) -> list[tuple[float, float, float]]:
    total = sum(float(item["count"]) for item in statuses)
    if total <= 0:
        raise ValueError("a rating mixture needs a positive count")
    return [
        (float(item["count"]) / total, float(item["mean"]), float(item["std"]))
        for item in statuses
    ]


def mixture_tier_probabilities(statuses: Sequence[dict[str, float]]) -> dict[str, Any]:
    """Share of a normal mixture falling in each dev tier.

    The four tiers partition the real line, so the shares sum to 1. This is a
    normal approximation to reviewer-average scores, not a histogram.
    """
    components = _components(statuses)
    edges = ((-math.inf, TIER_CUTS[0]), (TIER_CUTS[0], TIER_CUTS[1]), (TIER_CUTS[1], TIER_CUTS[2]), (TIER_CUTS[2], math.inf))
    shares: dict[str, float] = {}
    for name, (low, high) in zip(TIER_ORDER, edges, strict=True):
        mass = 0.0
        for weight, mean, std in components:
            mass += weight * (normal_cdf(high, mean, std) - normal_cdf(low, mean, std))
        shares[name] = mass
    scale = sum(shares.values())
    shares = {name: value / scale for name, value in shares.items()}
    mean = sum(weight * center for weight, center, _std in components)
    variance = sum(weight * (std ** 2 + center ** 2) for weight, center, std in components) - mean ** 2
    return {
        "tiers": shares,
        "mean": mean,
        "std": math.sqrt(max(variance, 0.0)),
        "component_weights": [weight for weight, _mean, _std in components],
    }


def year_statuses(year: int, *, include_withdraw: bool) -> list[dict[str, float]]:
    block = PAPER_COPILOT["years"][year]
    names = ("accept", "reject", "withdraw") if include_withdraw else ("accept", "reject")
    return [dict(block[name]) for name in names]


def pooled_statuses(years: Sequence[int], *, include_withdraw: bool) -> list[dict[str, float]]:
    """Count-weighted pool. Same status names are summed; means and stds mix as normals."""
    names = ("accept", "reject", "withdraw") if include_withdraw else ("accept", "reject")
    pooled: list[dict[str, float]] = []
    for name in names:
        pieces = [PAPER_COPILOT["years"][year][name] for year in years]
        count = sum(int(item["count"]) for item in pieces)
        mean = sum(item["count"] * item["mean"] for item in pieces) / count
        # Law of total variance across the year-level normal components.
        second = sum(item["count"] * (item["std"] ** 2 + item["mean"] ** 2) for item in pieces) / count
        variance = max(second - mean ** 2, 0.0)
        pooled.append({"count": float(count), "mean": mean, "std": math.sqrt(variance)})
    return pooled


def ipw_weights(tiers: Sequence[str], population: dict[str, float]) -> list[float]:
    """Inverse probability weights from equal tier shares onto ``population``."""
    weights = []
    for tier in tiers:
        share = float(population[tier])
        if share < 0:
            raise ValueError("a tier share cannot be negative")
        weights.append(share / SAMPLE_TIER_SHARE)
    if abs(sum(population.values()) - 1.0) > 1e-6:
        raise ValueError("population tier shares must sum to 1")
    return weights


def effective_sample_size(weights: Sequence[float]) -> float:
    total = sum(weights)
    squares = sum(weight * weight for weight in weights)
    if squares <= 0:
        return 0.0
    return (total * total) / squares


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    return sum(weight * value for weight, value in zip(weights, values, strict=True)) / total


def weighted_pearson(left: Sequence[float], right: Sequence[float], weights: Sequence[float]) -> float | None:
    if len(left) < 2:
        return None
    mean_left = _weighted_mean(left, weights)
    mean_right = _weighted_mean(right, weights)
    covariance = sum(weight * (x - mean_left) * (y - mean_right) for weight, x, y in zip(weights, left, right, strict=True))
    left_scale = math.sqrt(sum(weight * (x - mean_left) ** 2 for weight, x in zip(weights, left, strict=True)))
    right_scale = math.sqrt(sum(weight * (y - mean_right) ** 2 for weight, y in zip(weights, right, strict=True)))
    if left_scale == 0 or right_scale == 0:
        return None
    return covariance / (left_scale * right_scale)


def weighted_ranks(values: Sequence[float], weights: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(values):
        end = cursor
        while end + 1 < len(values) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        below = sum(weights[order[index]] for index in range(cursor))
        tied = sum(weights[order[index]] for index in range(cursor, end + 1))
        rank = below + tied / 2.0
        for index in range(cursor, end + 1):
            ranks[order[index]] = rank
        cursor = end + 1
    return ranks


def weighted_spearman(left: Sequence[float], right: Sequence[float], weights: Sequence[float]) -> float | None:
    if len(left) < 2:
        return None
    return weighted_pearson(weighted_ranks(left, weights), weighted_ranks(right, weights), weights)


def weighted_kendall(left: Sequence[float], right: Sequence[float], weights: Sequence[float]) -> float | None:
    """Weighted Kendall tau-b. A pair contributes the product of its weights."""
    count = len(left)
    if count < 2:
        return None
    concordant = 0.0
    discordant = 0.0
    ties_left = 0.0
    ties_right = 0.0
    for index in range(count):
        for other in range(index + 1, count):
            pair = weights[index] * weights[other]
            left_gap = left[other] - left[index]
            right_gap = right[other] - right[index]
            if left_gap == 0 and right_gap == 0:
                continue
            if left_gap == 0:
                ties_left += pair
            elif right_gap == 0:
                ties_right += pair
            elif left_gap * right_gap > 0:
                concordant += pair
            else:
                discordant += pair
    denominator = (concordant + discordant + ties_left) * (concordant + discordant + ties_right)
    if denominator <= 0:
        return None
    return (concordant - discordant) / math.sqrt(denominator)


def weighted_mae(scores: Sequence[float], means: Sequence[float], weights: Sequence[float]) -> float | None:
    if not scores:
        return None
    return _weighted_mean([abs(score - mean) for score, mean in zip(scores, means, strict=True)], weights)


def weighted_auc(scores: Sequence[float], labels: Sequence[int], weights: Sequence[float]) -> float | None:
    positive = [(score, weight) for score, label, weight in zip(scores, labels, weights, strict=True) if label == 1]
    negative = [(score, weight) for score, label, weight in zip(scores, labels, weights, strict=True) if label == 0]
    if not positive or not negative:
        return None
    wins = 0.0
    total = 0.0
    for high, high_weight in positive:
        for low, low_weight in negative:
            pair = high_weight * low_weight
            total += pair
            if high > low:
                wins += pair
            elif high == low:
                wins += 0.5 * pair
    if total <= 0:
        return None
    return wins / total


def cohen_kappa(left: Sequence[int], right: Sequence[int], categories: Sequence[int] = ICLR_BINS) -> float | None:
    count = len(left)
    if count == 0 or count != len(right):
        return None
    observed = sum(item == other for item, other in zip(left, right, strict=True)) / count
    left_share = {category: 0.0 for category in categories}
    right_share = {category: 0.0 for category in categories}
    for item, other in zip(left, right, strict=True):
        if item not in left_share or other not in right_share:
            return None
        left_share[item] += 1
        right_share[other] += 1
    chance = sum((left_share[category] / count) * (right_share[category] / count) for category in categories)
    if chance == 1:
        return None
    return (observed - chance) / (1.0 - chance)


def quadratic_weighted_kappa(
    left: Sequence[int],
    right: Sequence[int],
    categories: Sequence[int] = ICLR_BINS,
) -> float | None:
    count = len(left)
    if count == 0:
        return None
    index = {category: position for position, category in enumerate(categories)}
    size = len(categories)
    observed = [[0.0 for _ in categories] for _ in categories]
    for item, other in zip(left, right, strict=True):
        if item not in index or other not in index:
            return None
        observed[index[item]][index[other]] += 1.0
    row = [sum(line) for line in observed]
    column = [sum(observed[row_index][column_index] for row_index in range(size)) for column_index in range(size)]
    numerator = 0.0
    denominator = 0.0
    span = (size - 1) ** 2
    for row_index in range(size):
        for column_index in range(size):
            weight = ((row_index - column_index) ** 2) / span
            numerator += weight * observed[row_index][column_index] / count
            denominator += weight * (row[row_index] / count) * (column[column_index] / count)
    if denominator == 0:
        return None
    return 1.0 - numerator / denominator


def adjacent_aucs(scores: Sequence[float], tiers: Sequence[str]) -> dict[str, float | None]:
    found: dict[str, float | None] = {}
    for low, high in zip(TIER_ORDER, TIER_ORDER[1:], strict=False):
        selected_scores = []
        selected_labels = []
        for score, tier in zip(scores, tiers, strict=True):
            if tier == high:
                selected_scores.append(score)
                selected_labels.append(1)
            elif tier == low:
                selected_scores.append(score)
                selected_labels.append(0)
        found[f"{low} vs {high}"] = roc_auc(selected_scores, selected_labels) if selected_scores else None
    return found


def constant_mae(means: Sequence[float], prediction: float) -> float:
    return sum(abs(prediction - mean) for mean in means) / len(means)


def loo_linear_mae(features: Sequence[float], targets: Sequence[float]) -> float | None:
    """Leave-one-out absolute error of a univariate linear fit."""
    count = len(features)
    if count < 3:
        return None
    sum_x = sum(features)
    sum_y = sum(targets)
    sum_xx = sum(value * value for value in features)
    sum_xy = sum(feature * target for feature, target in zip(features, targets, strict=True))
    errors = []
    for feature, target in zip(features, targets, strict=True):
        kept = count - 1
        kept_x = sum_x - feature
        kept_y = sum_y - target
        kept_xx = sum_xx - feature * feature
        kept_xy = sum_xy - feature * target
        variance = kept_xx - (kept_x * kept_x) / kept
        if variance == 0:
            prediction = kept_y / kept
        else:
            slope = (kept_xy - kept_x * kept_y / kept) / variance
            intercept = kept_y / kept - slope * kept_x / kept
            prediction = intercept + slope * feature
        errors.append(abs(prediction - target))
    return sum(errors) / len(errors)


def relative_improvement(baseline: float, model: float) -> float | None:
    if baseline == 0:
        return None
    return (baseline - model) / baseline


def thorndike_case_ii(correlation: float, sample_std: float, population_std: float) -> float | None:
    """Range-restriction formula applied to one correlation. An approximation, not IPW."""
    if sample_std <= 0 or population_std <= 0:
        return None
    ratio = population_std / sample_std
    denominator = math.sqrt(1.0 - correlation ** 2 + (correlation ** 2) * (ratio ** 2))
    if denominator == 0:
        return None
    return correlation * ratio / denominator


def isotonic_increasing(values: Sequence[float]) -> list[float]:
    """Pool-adjacent-violators fit. ``values`` are already in predictor order."""
    blocks: list[list[float]] = []
    for value in values:
        blocks.append([value, 1.0])
        while len(blocks) >= 2 and (blocks[-2][0] / blocks[-2][1]) > (blocks[-1][0] / blocks[-1][1]):
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    fitted: list[float] = []
    for total, count in blocks:
        fitted.extend([total / count] * int(count))
    return fitted


def corp_decomposition(scores: Sequence[float], outcomes: Sequence[float]) -> dict[str, float] | None:
    """CORP / Murphy decomposition of MSE after an increasing isotonic fit of outcomes on scores."""
    count = len(scores)
    if count < 2:
        return None
    order = sorted(range(count), key=lambda index: (scores[index], index))
    ordered_scores = [scores[index] for index in order]
    ordered_outcomes = [outcomes[index] for index in order]
    fitted = isotonic_increasing(ordered_outcomes)
    mean_outcome = sum(ordered_outcomes) / count
    mse = sum((score - outcome) ** 2 for score, outcome in zip(ordered_scores, ordered_outcomes, strict=True)) / count
    miscalibration = sum((score - fit) ** 2 for score, fit in zip(ordered_scores, fitted, strict=True)) / count
    discrimination = sum((fit - mean_outcome) ** 2 for fit in fitted) / count
    uncertainty = sum((outcome - mean_outcome) ** 2 for outcome in ordered_outcomes) / count
    # PAV pools distinct scores into one fitted value, so (score - fit) is not
    # constant on a block and the cross term does not vanish. Keeping it makes
    # MSE = MCB - DSC + UNC + cross.
    cross = sum(2.0 * (score - fit) * (fit - outcome) for score, fit, outcome in zip(ordered_scores, fitted, ordered_outcomes, strict=True)) / count
    return {
        "mse": mse,
        "miscalibration": miscalibration,
        "discrimination": discrimination,
        "uncertainty": uncertainty,
        "cross": cross,
        "identity_gap": mse - (miscalibration - discrimination + uncertainty + cross),
    }


def reliability_table(scores: Sequence[float], outcomes: Sequence[float], *, bins: int = 5) -> list[dict[str, float]]:
    order = sorted(range(len(scores)), key=lambda index: (scores[index], index))
    rows = []
    for bin_index in range(bins):
        start = bin_index * len(order) // bins
        end = (bin_index + 1) * len(order) // bins
        chosen = order[start:end]
        if not chosen:
            continue
        rows.append(
            {
                "bin": float(bin_index + 1),
                "n": float(len(chosen)),
                "mean_score": sum(scores[index] for index in chosen) / len(chosen),
                "mean_outcome": sum(outcomes[index] for index in chosen) / len(chosen),
            }
        )
    return rows


def loro_pairs(ratings: Sequence[float], score: float | None) -> list[dict[str, float]]:
    """One row per held-out reviewer. The target is the mean of the others."""
    count = len(ratings)
    if count < 3:
        return []
    total = sum(ratings)
    pairs = []
    for rating in ratings:
        target = (total - rating) / (count - 1)
        row = {"human": float(rating), "target": target}
        if score is not None:
            row["apr"] = float(score)
        pairs.append(row)
    return pairs


def _mae(left: Sequence[float], right: Sequence[float]) -> float | None:
    if not left:
        return None
    return sum(abs(item - other) for item, other in zip(left, right, strict=True)) / len(left)


def _interval(values: list[float], point: float | None, *, resamples: int) -> dict[str, Any]:
    if point is None or not values:
        return {"point": point, "low": None, "high": None, "defined": len(values), "resamples": resamples}
    ordered = sorted(values)
    return {
        "point": point,
        "low": linear_percentile(ordered, 0.025),
        "high": linear_percentile(ordered, 0.975),
        "defined": len(values),
        "resamples": resamples,
    }


def _take(rows: Sequence[dict[str, Any]], indexes: Sequence[int]) -> list[dict[str, Any]]:
    return [rows[index] for index in indexes]


def arm_metrics(
    rows: Sequence[dict[str, Any]],
    *,
    population: dict[str, float],
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Agreement, IPW, chance correction, LORO, calibration, and baselines.

    Each row needs ``tier``, ``human_mean``, ``decision`` (accept/reject),
    ``ratings``, and ``title_chars``. ``rank_score`` and ``our_bin`` may be
    missing. Missing forecasts are left out of model metrics and kept in the
    human-only metrics.
    """
    scored = [row for row in rows if row.get("rank_score") is not None and row.get("our_bin") is not None]
    weights = ipw_weights([str(row["tier"]) for row in scored], population) if scored else []
    scores = [float(row["rank_score"]) for row in scored]
    means = [float(row["human_mean"]) for row in scored]
    labels = [1 if row["decision"] == "accept" else 0 for row in scored]
    tiers = [str(row["tier"]) for row in scored]
    human_bins = [nearest_bin(mean) for mean in means]
    venue_bins = [int(row["our_bin"]) for row in scored]
    rank_bins = [nearest_bin(score) for score in scores]
    all_means = [float(row["human_mean"]) for row in rows]
    median_prediction = statistics.median(all_means) if all_means else None
    mean_prediction = statistics.fmean(all_means) if all_means else None
    scored_median = statistics.median(means) if means else None
    title_rows = scored or list(rows)
    title_features = [float(row["title_chars"]) for row in title_rows]
    title_targets = [float(row["human_mean"]) for row in title_rows]
    point = {
        "n_rows": len(rows),
        "n_scored": len(scored),
        "spearman": spearman(scores, means) if scored else None,
        "kendall": kendall_tau(scores, means) if scored else None,
        "auc": roc_auc(scores, labels) if scored else None,
        "mae": _mae(scores, means),
        "bias": (sum(score - mean for score, mean in zip(scores, means, strict=True)) / len(scores)) if scores else None,
        "ipw_spearman": weighted_spearman(scores, means, weights) if scored else None,
        "ipw_kendall": weighted_kendall(scores, means, weights) if scored else None,
        "ipw_auc": weighted_auc(scores, labels, weights) if scored else None,
        "ipw_mae": weighted_mae(scores, means, weights) if scored else None,
        "n_eff": effective_sample_size(weights) if weights else None,
        "kappa_venue": cohen_kappa(human_bins, venue_bins) if scored else None,
        "qwk_venue": quadratic_weighted_kappa(human_bins, venue_bins) if scored else None,
        "kappa_rank": cohen_kappa(human_bins, rank_bins) if scored else None,
        "qwk_rank": quadratic_weighted_kappa(human_bins, rank_bins) if scored else None,
        "bin_agreement": (sum(human == venue for human, venue in zip(human_bins, venue_bins, strict=True)) / len(scored)) if scored else None,
        "adjacent_auc": adjacent_aucs(scores, tiers) if scored else {},
        "corp": corp_decomposition(scores, means) if scored else None,
        "reliability": reliability_table(scores, means) if scored else [],
        "constant_median": median_prediction,
        "constant_median_mae": constant_mae(means, scored_median) if means and scored_median is not None else None,
        "constant_mean_mae": constant_mae(means, statistics.fmean(means)) if means else None,
        "human_constant_median_mae": constant_mae(all_means, median_prediction) if all_means and median_prediction is not None else None,
        "human_constant_mean_mae": constant_mae(all_means, mean_prediction) if all_means and mean_prediction is not None else None,
        "title_length_loo_mae": loo_linear_mae(title_features, title_targets) if title_rows else None,
        "human_sd": statistics.pstdev(all_means) if len(all_means) > 1 else None,
    }
    if point["mae"] is not None and point["constant_median_mae"] is not None:
        point["mae_improvement_vs_median"] = relative_improvement(point["constant_median_mae"], point["mae"])
    else:
        point["mae_improvement_vs_median"] = None
    if point["mae"] is not None and point["title_length_loo_mae"] is not None:
        point["mae_improvement_vs_title"] = relative_improvement(point["title_length_loo_mae"], point["mae"])
    else:
        point["mae_improvement_vs_title"] = None
    if point["spearman"] is not None and point["human_sd"] is not None:
        point["thorndike_spearman"] = None  # filled by the caller once the population SD is known

    draws = stratified_indexes([str(row["stratum"]) for row in rows], resamples=resamples, seed=seed) if rows else []
    buckets: dict[str, list[float]] = {name: [] for name in (
        "spearman", "kendall", "auc", "mae", "ipw_spearman", "ipw_kendall", "ipw_auc", "ipw_mae",
        "kappa_venue", "qwk_venue", "kappa_rank", "qwk_rank", "bin_agreement", "mae_improvement_vs_median",
        "corp_miscalibration", "corp_discrimination", "corp_uncertainty", "corp_mse", "corp_cross",
    )}
    adjacent_names = [f"{low} vs {high}" for low, high in zip(TIER_ORDER, TIER_ORDER[1:], strict=False)]
    for name in adjacent_names:
        buckets[name] = []
    for indexes in draws:
        sample = _take(rows, indexes)
        sample_scored = [row for row in sample if row.get("rank_score") is not None and row.get("our_bin") is not None]
        if len(sample_scored) < 2:
            continue
        sample_weights = ipw_weights([str(row["tier"]) for row in sample_scored], population)
        sample_scores = [float(row["rank_score"]) for row in sample_scored]
        sample_means = [float(row["human_mean"]) for row in sample_scored]
        sample_labels = [1 if row["decision"] == "accept" else 0 for row in sample_scored]
        sample_tiers = [str(row["tier"]) for row in sample_scored]
        sample_human_bins = [nearest_bin(mean) for mean in sample_means]
        sample_venue_bins = [int(row["our_bin"]) for row in sample_scored]
        sample_rank_bins = [nearest_bin(score) for score in sample_scores]
        corp = corp_decomposition(sample_scores, sample_means) or {}
        values = {
            "spearman": spearman(sample_scores, sample_means),
            "kendall": kendall_tau(sample_scores, sample_means),
            "auc": roc_auc(sample_scores, sample_labels),
            "mae": _mae(sample_scores, sample_means),
            "ipw_spearman": weighted_spearman(sample_scores, sample_means, sample_weights),
            "ipw_kendall": weighted_kendall(sample_scores, sample_means, sample_weights),
            "ipw_auc": weighted_auc(sample_scores, sample_labels, sample_weights),
            "ipw_mae": weighted_mae(sample_scores, sample_means, sample_weights),
            "kappa_venue": cohen_kappa(sample_human_bins, sample_venue_bins),
            "qwk_venue": quadratic_weighted_kappa(sample_human_bins, sample_venue_bins),
            "kappa_rank": cohen_kappa(sample_human_bins, sample_rank_bins),
            "qwk_rank": quadratic_weighted_kappa(sample_human_bins, sample_rank_bins),
            "bin_agreement": sum(human == venue for human, venue in zip(sample_human_bins, sample_venue_bins, strict=True)) / len(sample_scored),
            "corp_miscalibration": corp.get("miscalibration"),
            "corp_discrimination": corp.get("discrimination"),
            "corp_uncertainty": corp.get("uncertainty"),
            "corp_mse": corp.get("mse"),
            "corp_cross": corp.get("cross"),
        }
        sample_all_means = [float(row["human_mean"]) for row in sample_scored]
        baseline = constant_mae(sample_all_means, statistics.median(sample_all_means))
        model_mae = values["mae"]
        values["mae_improvement_vs_median"] = relative_improvement(baseline, model_mae) if model_mae is not None else None
        adjacent = adjacent_aucs(sample_scores, sample_tiers)
        for name, value in values.items():
            if value is not None:
                buckets[name].append(float(value))
        for name in adjacent_names:
            if adjacent[name] is not None:
                buckets[name].append(float(adjacent[name]))
    intervals = {name: _interval(values, point.get(name) if name in point else (point["adjacent_auc"].get(name) if name in adjacent_names else None), resamples=resamples) for name, values in buckets.items()}
    for name in adjacent_names:
        intervals[name]["point"] = (point.get("adjacent_auc") or {}).get(name)
        if buckets[name]:
            ordered = sorted(buckets[name])
            intervals[name]["low"] = linear_percentile(ordered, 0.025)
            intervals[name]["high"] = linear_percentile(ordered, 0.975)
    corp = point.get("corp") or {}
    for name, key in (
        ("corp_miscalibration", "miscalibration"),
        ("corp_discrimination", "discrimination"),
        ("corp_uncertainty", "uncertainty"),
        ("corp_mse", "mse"),
        ("corp_cross", "cross"),
    ):
        intervals[name]["point"] = corp.get(key)
        if buckets[name] and corp.get(key) is not None:
            ordered = sorted(buckets[name])
            intervals[name]["low"] = linear_percentile(ordered, 0.025)
            intervals[name]["high"] = linear_percentile(ordered, 0.975)
    return {"point": point, "intervals": intervals}


def loro_metrics(
    rows: Sequence[dict[str, Any]],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Held-out reviewer versus the other reviewers' mean, and APR versus that same mean.

    Papers are the bootstrap units. Every held-out reviewer of a drawn paper is kept.
    """
    usable = [row for row in rows if len(row.get("ratings") or []) >= 3]
    scored = [row for row in usable if row.get("rank_score") is not None]

    def _collect(sample: Sequence[dict[str, Any]], *, with_model: bool) -> dict[str, float | None]:
        human: list[float] = []
        target: list[float] = []
        model: list[float] = []
        for row in sample:
            pairs = loro_pairs(row["ratings"], float(row["rank_score"]) if with_model else None)
            for pair in pairs:
                human.append(pair["human"])
                target.append(pair["target"])
                if with_model:
                    model.append(pair["apr"])
        result = {
            "human_spearman": spearman(human, target),
            "human_mae": _mae(human, target),
        }
        if with_model and model:
            result["apr_spearman"] = spearman(model, target)
            result["apr_mae"] = _mae(model, target)
            if result["human_spearman"] is not None and result["apr_spearman"] is not None:
                result["spearman_gap"] = result["apr_spearman"] - result["human_spearman"]
            if result["human_mae"] is not None and result["apr_mae"] is not None:
                result["mae_gap"] = result["human_mae"] - result["apr_mae"]
        return result

    point = _collect(scored if scored else usable, with_model=bool(scored))
    point["n_papers_human"] = len(usable)
    point["n_papers_model"] = len(scored)
    draws = stratified_indexes([str(row["stratum"]) for row in (scored or usable)], resamples=resamples, seed=seed)
    names = [name for name in ("human_spearman", "human_mae", "apr_spearman", "apr_mae", "spearman_gap", "mae_gap") if name in point]
    buckets = {name: [] for name in names}
    source = scored or usable
    for indexes in draws:
        sample = _take(source, indexes)
        values = _collect(sample, with_model=bool(scored))
        for name in names:
            if values.get(name) is not None:
                buckets[name].append(float(values[name]))
    intervals = {name: _interval(buckets[name], point.get(name), resamples=resamples) for name in names}
    return {"point": point, "intervals": intervals}


def format_interval(interval: dict[str, Any] | None) -> str:
    if not interval or interval.get("point") is None:
        return "undefined"
    point = f"{float(interval['point']):.4f}"
    if interval.get("low") is None:
        return point
    return f"{point} [{float(interval['low']):.4f}, {float(interval['high']):.4f}]"
