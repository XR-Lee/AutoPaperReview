"""Stratified metrics for the CVPR accepted-tier report.

The primary number is macro-AUC, the unweighted mean of the three
accepted-tier contrasts. Nothing here fits a cutoff or writes a weight
back into ``rate``. Population counts are the public program totals used
as Horvitz–Thompson weights, not as a fitted model.
"""

from __future__ import annotations

import csv
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from .public_calibration import kendall_tau, roc_auc, spearman

TIER_ORDER = ("reject", "poster", "highlight", "oral")
PAIRWISE = (
    ("oral_vs_poster", "oral", "poster"),
    ("highlight_vs_poster", "highlight", "poster"),
    ("oral_vs_highlight", "oral", "highlight"),
)
# CVPR 2024 virtual JSON, collapsed on uid. CVPR 2025 virtual JSON labels
# oral only; the other rows stay non-oral.
POPULATION_2024 = {"oral": 90, "highlight": 324, "poster": 2302}
POPULATION_2025 = {"oral": 95, "non_oral": 2776}
POPULATION = {
    (2024, "oral"): 90,
    (2024, "highlight"): 324,
    (2024, "poster"): 2302,
    (2025, "oral"): 95,
    (2025, "non_oral"): 2776,
}
# Stated 2024 mixture weights. They sum to 1.001 as published.
CPOP_WEIGHTS = {
    "oral_vs_poster": 0.211,
    "highlight_vs_poster": 0.760,
    "oral_vs_highlight": 0.030,
}
O_REST_2024_DENOMINATOR = POPULATION_2024["highlight"] + POPULATION_2024["poster"]
_Z_95 = 1.959963984540054


def pairwise_aucs(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    found: dict[str, float | None] = {}
    for name, high, low in PAIRWISE:
        found[name] = _auc(rows, high, low)
    return found


def macro_auc(aucs: dict[str, float | None]) -> float | None:
    """(AUC oral-poster + AUC highlight-poster + AUC oral-highlight) / 3."""
    values = [aucs.get(name) for name, _high, _low in PAIRWISE]
    if any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None) / 3.0


def cpop(aucs: dict[str, float | None]) -> float | None:
    """2024 population mixture of the three pairwise AUCs."""
    total = 0.0
    for name, _high, _low in PAIRWISE:
        value = aucs.get(name)
        if value is None:
            return None
        total += CPOP_WEIGHTS[name] * float(value)
    return total


def auc_oral_versus_rest(rows: list[dict[str, Any]], *, year: int | None = None) -> float | None:
    """Oral against every other accepted tier. ``year`` limits the rows."""
    scores: list[float] = []
    labels: list[int] = []
    for row in rows:
        if year is not None and row.get("year") != year:
            continue
        tier = row.get("true_tier")
        if tier == "oral":
            scores.append(float(row["score"]))
            labels.append(1)
        elif tier in {"highlight", "poster", "non_oral"}:
            scores.append(float(row["score"]))
            labels.append(0)
    return roc_auc(scores, labels)


def auc_o_rest_weighted_2024(aucs: dict[str, float | None]) -> float | None:
    """(N_highlight * AUC_OH + N_poster * AUC_OP) / (N_highlight + N_poster)."""
    oral_highlight = aucs.get("oral_vs_highlight")
    oral_poster = aucs.get("oral_vs_poster")
    if oral_highlight is None or oral_poster is None:
        return None
    weighted = (
        POPULATION_2024["highlight"] * float(oral_highlight)
        + POPULATION_2024["poster"] * float(oral_poster)
    )
    return weighted / O_REST_2024_DENOMINATOR


def ht_weights(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """w_i = N_t / n_t for a known (year, tier). Other rows are unweighted."""
    counts: Counter[tuple[Any, Any]] = Counter(
        (row.get("year"), row.get("true_tier")) for row in rows
    )
    weights: dict[str, float | None] = {}
    for row in rows:
        key = (row.get("year"), row.get("true_tier"))
        population = POPULATION.get(key)
        sample_n = counts[key]
        paper_id = str(row["paper_id"])
        if population and sample_n:
            weights[paper_id] = population / sample_n
        else:
            weights[paper_id] = None
    return weights


def _weighted_rows(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    weights = ht_weights(rows)
    paired: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        weight = weights.get(str(row["paper_id"]))
        if weight is None:
            continue
        paired.append((row, float(weight)))
    return paired


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(values):
        end = cursor
        while end + 1 < len(values) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + end) / 2 + 1
        for index in range(cursor, end + 1):
            ranks[order[index]] = average
        cursor = end + 1
    return ranks


def _weighted_pearson(left: list[float], right: list[float], weights: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right) or len(left) != len(weights):
        return None
    total = math.fsum(weights)
    if total <= 0:
        return None
    mean_left = math.fsum(weight * value for weight, value in zip(weights, left, strict=True)) / total
    mean_right = math.fsum(weight * value for weight, value in zip(weights, right, strict=True)) / total
    covariance = math.fsum(
        weight * (x - mean_left) * (y - mean_right)
        for weight, x, y in zip(weights, left, right, strict=True)
    )
    variance_left = math.fsum(weight * (x - mean_left) ** 2 for weight, x in zip(weights, left, strict=True))
    variance_right = math.fsum(weight * (y - mean_right) ** 2 for weight, y in zip(weights, right, strict=True))
    if variance_left <= 0 or variance_right <= 0:
        return None
    return covariance / math.sqrt(variance_left * variance_right)


def ht_spearman(rows: list[dict[str, Any]]) -> float | None:
    paired = _weighted_rows(rows)
    if len(paired) < 2:
        return None
    scores = [float(row["score"]) for row, _weight in paired]
    truth = [_tier_rank(row["true_tier"]) for row, _weight in paired]
    weights = [weight for _row, weight in paired]
    return _weighted_pearson(_average_ranks(scores), _average_ranks(truth), weights)


def ht_kendall(rows: list[dict[str, Any]]) -> float | None:
    paired = _weighted_rows(rows)
    if len(paired) < 2:
        return None
    scores = [float(row["score"]) for row, _weight in paired]
    truth = [_tier_rank(row["true_tier"]) for row, _weight in paired]
    weights = [weight for _row, weight in paired]
    concordant = 0.0
    discordant = 0.0
    ties_left = 0.0
    ties_right = 0.0
    for index in range(len(paired)):
        for other in range(index + 1, len(paired)):
            weight = weights[index] * weights[other]
            if weight == 0:
                continue
            left_gap = scores[other] - scores[index]
            right_gap = truth[other] - truth[index]
            if left_gap == 0 and right_gap == 0:
                continue
            if left_gap == 0:
                ties_left += weight
            elif right_gap == 0:
                ties_right += weight
            elif left_gap * right_gap > 0:
                concordant += weight
            else:
                discordant += weight
    denominator = (concordant + discordant + ties_left) * (concordant + discordant + ties_right)
    if denominator <= 0:
        return None
    return (concordant - discordant) / math.sqrt(denominator)


def confusion(rows: list[dict[str, Any]], *, weighted: bool = False) -> dict[str, dict[str, float]]:
    labels = ("oral", "highlight", "poster")
    matrix = {truth: {predicted: 0.0 for predicted in TIER_ORDER} for truth in labels}
    weights = ht_weights(rows) if weighted else {}
    for row in rows:
        truth = str(row.get("true_tier"))
        predicted = str(row.get("predicted_tier"))
        if truth not in matrix or predicted not in matrix[truth]:
            continue
        if weighted:
            weight = weights.get(str(row["paper_id"]))
            if weight is None:
                continue
            matrix[truth][predicted] += float(weight)
        else:
            matrix[truth][predicted] += 1.0
    return matrix


def per_tier_precision_recall(rows: list[dict[str, Any]], *, weighted: bool = False) -> dict[str, dict[str, float | None]]:
    weights = ht_weights(rows) if weighted else {}
    table: dict[str, dict[str, float | None]] = {}
    for tier in TIER_ORDER:
        true_positive = 0.0
        predicted = 0.0
        actual = 0.0
        for row in rows:
            weight = float(weights.get(str(row["paper_id"])) or 0.0) if weighted else 1.0
            if weighted and weights.get(str(row["paper_id"])) is None:
                continue
            if row.get("predicted_tier") == tier:
                predicted += weight
                if row.get("true_tier") == tier:
                    true_positive += weight
            if row.get("true_tier") == tier:
                actual += weight
        table[tier] = {
            "precision": None if predicted == 0 else true_positive / predicted,
            "recall": None if actual == 0 else true_positive / actual,
            "support": actual,
        }
    return table


def labelled_share(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of parsed rows that name a tier, plus stratum coverage."""
    if not rows:
        return {"predictions": None, "ht_weighted_predictions": None, "strata": {}}
    named = [row for row in rows if row.get("predicted_tier") in TIER_ORDER]
    weights = ht_weights(rows)
    weighted_rows = [(row, weight) for row in rows if (weight := weights.get(str(row["paper_id"]))) is not None]
    ht_share = None
    if weighted_rows:
        total = math.fsum(weight for _row, weight in weighted_rows)
        named_weight = math.fsum(
            weight for row, weight in weighted_rows if row.get("predicted_tier") in TIER_ORDER
        )
        ht_share = named_weight / total if total else None
    counts: Counter[tuple[Any, Any]] = Counter((row.get("year"), row.get("true_tier")) for row in rows)
    strata = {}
    for key, population in POPULATION.items():
        sample_n = counts.get(key, 0)
        if not sample_n:
            continue
        strata[f"{key[0]}:{key[1]}"] = {
            "n": sample_n,
            "population": population,
            "fraction": sample_n / population,
        }
    return {
        "predictions": len(named) / len(rows),
        "ht_weighted_predictions": ht_share,
        "strata": strata,
    }


def quadratic_weighted_kappa(rows: list[dict[str, Any]]) -> float | None:
    """Quadratic weights on reject < poster < highlight < oral."""
    order = {name: index for index, name in enumerate(TIER_ORDER)}
    width = len(TIER_ORDER)
    usable = [
        row
        for row in rows
        if row.get("predicted_tier") in order and row.get("true_tier") in order
    ]
    count = len(usable)
    if count == 0:
        return None
    observed = [[0.0 for _ in range(width)] for _ in range(width)]
    row_totals = [0.0 for _ in range(width)]
    column_totals = [0.0 for _ in range(width)]
    for row in usable:
        truth = order[str(row["true_tier"])]
        predicted = order[str(row["predicted_tier"])]
        observed[truth][predicted] += 1.0
        row_totals[truth] += 1.0
        column_totals[predicted] += 1.0
    span = float((width - 1) ** 2)
    numerator = 0.0
    denominator = 0.0
    for truth in range(width):
        for predicted in range(width):
            weight = ((truth - predicted) ** 2) / span
            expected = row_totals[truth] * column_totals[predicted] / count
            numerator += weight * observed[truth][predicted]
            denominator += weight * expected
    if denominator == 0:
        return None
    return 1.0 - numerator / denominator


def _psi(left: float, right: float) -> float:
    if left > right:
        return 1.0
    if left < right:
        return 0.0
    return 0.5


def _components(scores: list[float], positive: list[int], negative: list[int]) -> tuple[list[float], list[float]]:
    v10: list[float] = []
    for index in positive:
        total = math.fsum(_psi(scores[index], scores[other]) for other in negative)
        v10.append(total / len(negative))
    v01: list[float] = []
    for index in negative:
        total = math.fsum(_psi(scores[other], scores[index]) for other in positive)
        v01.append(total / len(positive))
    return v10, v01


def _sample_covariance(left: list[float], right: list[float]) -> float:
    count = len(left)
    if count < 2 or count != len(right):
        return 0.0
    mean_left = math.fsum(left) / count
    mean_right = math.fsum(right) / count
    return math.fsum((x - mean_left) * (y - mean_right) for x, y in zip(left, right, strict=True)) / (count - 1)


def _two_sided_normal(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def delong_auc(scores: list[float], labels: list[int], *, null: float = 0.5) -> dict[str, Any]:
    """DeLong variance for one ROC against a null AUC. Ties count as 0.5."""
    positive = [index for index, label in enumerate(labels) if label == 1]
    negative = [index for index, label in enumerate(labels) if label == 0]
    auc = roc_auc(scores, labels)
    result: dict[str, Any] = {
        "auc": auc,
        "se": None,
        "z": None,
        "p": None,
        "low": None,
        "high": None,
        "n_pos": len(positive),
        "n_neg": len(negative),
        "null": null,
    }
    if auc is None or len(positive) < 2 or len(negative) < 2:
        return result
    v10, v01 = _components(scores, positive, negative)
    variance = _sample_covariance(v10, v10) / len(positive) + _sample_covariance(v01, v01) / len(negative)
    if variance < 0:
        variance = 0.0
    se = math.sqrt(variance)
    result["se"] = se
    if se == 0:
        result["z"] = None if auc == null else math.inf
        result["p"] = 1.0 if auc == null else 0.0
        result["low"] = auc
        result["high"] = auc
        return result
    z = (auc - null) / se
    result["z"] = z
    result["p"] = _two_sided_normal(z)
    result["low"] = auc - _Z_95 * se
    result["high"] = auc + _Z_95 * se
    return result


def delong_paired(
    scores_left: list[float],
    scores_right: list[float],
    labels: list[int],
) -> dict[str, Any]:
    """Paired DeLong test of two scores on the same papers."""
    if len(scores_left) != len(scores_right) or len(scores_left) != len(labels):
        raise ValueError("paired DeLong inputs must have the same length")
    positive = [index for index, label in enumerate(labels) if label == 1]
    negative = [index for index, label in enumerate(labels) if label == 0]
    left = delong_auc(scores_left, labels)
    right = delong_auc(scores_right, labels)
    result: dict[str, Any] = {
        "left": left,
        "right": right,
        "diff": None if left["auc"] is None or right["auc"] is None else float(left["auc"]) - float(right["auc"]),
        "se": None,
        "z": None,
        "p": None,
    }
    if left["auc"] is None or right["auc"] is None or len(positive) < 2 or len(negative) < 2:
        return result
    left_10, left_01 = _components(scores_left, positive, negative)
    right_10, right_01 = _components(scores_right, positive, negative)
    variance = (
        _sample_covariance(left_10, left_10)
        + _sample_covariance(right_10, right_10)
        - 2 * _sample_covariance(left_10, right_10)
    ) / len(positive) + (
        _sample_covariance(left_01, left_01)
        + _sample_covariance(right_01, right_01)
        - 2 * _sample_covariance(left_01, right_01)
    ) / len(negative)
    if variance < 0:
        variance = 0.0
    se = math.sqrt(variance)
    result["se"] = se
    if se == 0:
        result["p"] = 1.0 if result["diff"] == 0 else 0.0
        result["z"] = 0.0 if result["diff"] == 0 else math.inf
        return result
    z = float(result["diff"]) / se
    result["z"] = z
    result["p"] = _two_sided_normal(z)
    return result


def adjust_holm(p_values: list[float | None]) -> list[float | None]:
    """Holm step-down. Missing p-values stay missing and are outside the family."""
    return _adjust(p_values, step_down=True)


def adjust_bh(p_values: list[float | None]) -> list[float | None]:
    """Benjamini–Hochberg step-up. Missing p-values stay missing."""
    return _adjust(p_values, step_down=False)


def _adjust(p_values: list[float | None], *, step_down: bool) -> list[float | None]:
    indexed = [(index, float(value)) for index, value in enumerate(p_values) if value is not None]
    indexed.sort(key=lambda item: item[1])
    family = len(indexed)
    adjusted: list[float | None] = [None] * len(p_values)
    if family == 0:
        return adjusted
    if step_down:
        running = 0.0
        for rank, (index, value) in enumerate(indexed):
            running = max(running, min(1.0, (family - rank) * value))
            adjusted[index] = running
        return adjusted
    running = 1.0
    for rank, (index, value) in reversed(list(enumerate(indexed))):
        running = min(running, min(1.0, (family / (rank + 1)) * value))
        adjusted[index] = running
    return adjusted


def _interval(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    ordered = sorted(values)

    def percentile(probability: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = probability * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return percentile(0.025), percentile(0.975)


def _bootstrap_result(
    point: float | None,
    drawn: list[float],
    *,
    resamples: int,
    seed: int,
    method: str,
) -> dict[str, Any]:
    low, high = _interval(drawn)
    return {
        "point": point,
        "low": None if low is None else round(low, 4),
        "high": None if high is None else round(high, 4),
        "defined_resamples": len(drawn),
        "resamples": resamples,
        "seed": seed,
        "method": method,
    }


def stratified_bootstrap(
    rows: list[dict[str, Any]],
    statistic: Callable[[list[dict[str, Any]]], float | None],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Resample with replacement inside each (year, true tier) stratum."""
    point = statistic(rows)
    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row.get("year"), row.get("true_tier"))].append(row)
    keys = list(groups)
    rng = random.Random(seed)
    drawn: list[float] = []
    if rows:
        for _ in range(resamples):
            sample: list[dict[str, Any]] = []
            for key in keys:
                bucket = groups[key]
                sample.extend(bucket[rng.randrange(len(bucket))] for _ in range(len(bucket)))
            value = statistic(sample)
            if value is None:
                continue
            drawn.append(float(value))
    return _bootstrap_result(point, drawn, resamples=resamples, seed=seed, method="year_tier")


def paired_stratified_bootstrap(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    statistic: Callable[[list[tuple[dict[str, Any], dict[str, Any]]]], float | None],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Resample paper ids inside the true tier's year stratum, keeping both sides."""
    point = statistic(pairs)
    groups: dict[tuple[Any, Any], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for left, right in pairs:
        groups[(left.get("year"), left.get("true_tier"))].append((left, right))
    keys = list(groups)
    rng = random.Random(seed)
    drawn: list[float] = []
    if pairs:
        for _ in range(resamples):
            sample: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for key in keys:
                bucket = groups[key]
                sample.extend(bucket[rng.randrange(len(bucket))] for _ in range(len(bucket)))
            value = statistic(sample)
            if value is None:
                continue
            drawn.append(float(value))
    return _bootstrap_result(point, drawn, resamples=resamples, seed=seed, method="paired_year_tier")


def icc_matrix(matrix: list[list[float]]) -> dict[str, Any]:
    """Shrout–Fleiss ICC(2,1) absolute agreement and ICC(3,1) consistency.

    Rows are papers. Columns are repeats. Single-score, two-way models.
    """
    papers = len(matrix)
    repeats = len(matrix[0]) if matrix else 0
    result: dict[str, Any] = {
        "icc_2_1": None,
        "icc_3_1": None,
        "n": papers,
        "k": repeats,
    }
    if papers < 2 or repeats < 2:
        return result
    if any(len(row) != repeats for row in matrix):
        raise ValueError("ICC rows must have the same number of repeats")
    grand = math.fsum(value for row in matrix for value in row) / (papers * repeats)
    row_means = [math.fsum(row) / repeats for row in matrix]
    column_means = [
        math.fsum(matrix[paper][repeat] for paper in range(papers)) / papers for repeat in range(repeats)
    ]
    between_papers = repeats * math.fsum((mean - grand) ** 2 for mean in row_means) / (papers - 1)
    between_repeats = papers * math.fsum((mean - grand) ** 2 for mean in column_means) / (repeats - 1)
    residual_sum = 0.0
    for paper in range(papers):
        for repeat in range(repeats):
            residual_sum += (matrix[paper][repeat] - row_means[paper] - column_means[repeat] + grand) ** 2
    residual = residual_sum / ((papers - 1) * (repeats - 1))
    result["icc_3_1"] = _icc_ratio(between_papers - residual, between_papers + (repeats - 1) * residual)
    absolute_denominator = between_papers + (repeats - 1) * residual + repeats * (between_repeats - residual) / papers
    result["icc_2_1"] = _icc_ratio(between_papers - residual, absolute_denominator)
    return result


def _icc_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def repeat_matrix(rows: list[dict[str, Any]]) -> list[list[float]]:
    """Papers whose repeat vector is complete and at least length 2."""
    widths = [len(row["repeat_scores"]) for row in rows if isinstance(row.get("repeat_scores"), list)]
    if not widths:
        return []
    width = max(widths)
    if width < 2:
        return []
    matrix: list[list[float]] = []
    for row in rows:
        scores = row.get("repeat_scores")
        if not isinstance(scores, list) or len(scores) != width:
            continue
        matrix.append([float(score) for score in scores])
    return matrix


def _tier_rank(tier: object) -> float:
    order = {"reject": 0.0, "poster": 1.0, "highlight": 2.0, "oral": 3.0, "non_oral": 1.0}
    if tier not in order:
        raise ValueError(f"unknown tier {tier}")
    return order[str(tier)]


def _auc(rows: list[dict[str, Any]], high: str, low: str) -> float | None:
    scores: list[float] = []
    labels: list[int] = []
    for row in rows:
        if row.get("true_tier") == high:
            scores.append(float(row["score"]))
            labels.append(1)
        elif row.get("true_tier") == low:
            scores.append(float(row["score"]))
            labels.append(0)
    return roc_auc(scores, labels)


def _labels_for(rows: list[dict[str, Any]], high: str, low: str) -> tuple[list[float], list[int], list[str]]:
    scores: list[float] = []
    labels: list[int] = []
    ids: list[str] = []
    for row in rows:
        tier = row.get("true_tier")
        if tier == high:
            label = 1
        elif tier == low:
            label = 0
        else:
            continue
        scores.append(float(row["score"]))
        labels.append(label)
        ids.append(str(row["paper_id"]))
    return scores, labels, ids


def contrast_delong(rows: list[dict[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    p_values: list[float | None] = []
    names: list[str] = []
    for name, high, low in PAIRWISE:
        scores, labels, _ids = _labels_for(rows, high, low)
        estimate = delong_auc(scores, labels)
        found[name] = estimate
        names.append(name)
        p_values.append(estimate.get("p"))
    rest_scores: list[float] = []
    rest_labels: list[int] = []
    for row in rows:
        tier = row.get("true_tier")
        if tier == "oral":
            rest_scores.append(float(row["score"]))
            rest_labels.append(1)
        elif tier in {"highlight", "poster", "non_oral"}:
            rest_scores.append(float(row["score"]))
            rest_labels.append(0)
    found["oral_vs_rest"] = delong_auc(rest_scores, rest_labels)
    holm = adjust_holm(p_values)
    bh = adjust_bh(p_values)
    found["holm"] = {name: holm[index] for index, name in enumerate(names)}
    found["bh"] = {name: bh[index] for index, name in enumerate(names)}
    found["family"] = "three pairwise AUCs against 0.5"
    return found


def extended_metrics(
    rows: list[dict[str, Any]],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Primary macro-AUC and the stratified companions. No coefficient is fit."""
    aucs = pairwise_aucs(rows)
    primary = macro_auc(aucs)
    unweighted_spearman = None
    if len(rows) >= 2:
        try:
            unweighted_spearman = spearman(
                [float(row["score"]) for row in rows],
                [_tier_rank(row["true_tier"]) for row in rows],
            )
        except ValueError:
            unweighted_spearman = None
    unweighted_kendall = None
    if len(rows) >= 2:
        try:
            unweighted_kendall = kendall_tau(
                [float(row["score"]) for row in rows],
                [_tier_rank(row["true_tier"]) for row in rows],
            )
        except ValueError:
            unweighted_kendall = None
    return {
        "primary": "macro_auc",
        "macro_auc": stratified_bootstrap(rows, _macro_of, resamples=resamples, seed=seed),
        "cpop": cpop(aucs),
        "auc_o_rest": auc_oral_versus_rest(rows),
        "auc_o_rest_2024": auc_oral_versus_rest(rows, year=2024),
        "auc_o_rest_weighted_2024": auc_o_rest_weighted_2024(aucs),
        "auc_oral_vs_rest_2025": auc_oral_versus_rest(rows, year=2025),
        "population_2024": dict(POPULATION_2024),
        "population_2025": dict(POPULATION_2025),
        "pairwise_auc_point": aucs,
        "spearman_ht": ht_spearman(rows),
        "kendall_ht": ht_kendall(rows),
        "spearman": unweighted_spearman,
        "kendall": unweighted_kendall,
        "confusion_ht": confusion(rows, weighted=True),
        "precision_recall": per_tier_precision_recall(rows, weighted=False),
        "precision_recall_ht": per_tier_precision_recall(rows, weighted=True),
        "labelled_share": labelled_share(rows),
        "quadratic_kappa": stratified_bootstrap(rows, quadratic_weighted_kappa, resamples=resamples, seed=seed),
        "delong": contrast_delong(rows),
        "icc": icc_matrix(repeat_matrix(rows)),
        "spearman_stratified": stratified_bootstrap(
            rows,
            lambda sample: _safe_spearman(sample),
            resamples=resamples,
            seed=seed,
        ),
    }


def _macro_of(rows: list[dict[str, Any]]) -> float | None:
    return macro_auc(pairwise_aucs(rows))


def _safe_spearman(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    try:
        return spearman([float(row["score"]) for row in rows], [_tier_rank(row["true_tier"]) for row in rows])
    except ValueError:
        return None


def split_by_recognition(rows: list[dict[str, Any]], *, resamples: int, seed: int) -> dict[str, Any]:
    """Macro-AUC on papers the probe marked recognised or not."""
    recognised = [row for row in rows if row.get("recognised") is True]
    unrecognised = [row for row in rows if row.get("recognised") is False]
    return {
        "recognised": {
            "n": len(recognised),
            "macro_auc": None if not recognised else macro_auc(pairwise_aucs(recognised)),
            "spearman": _safe_spearman(recognised),
        },
        "unrecognised": {
            "n": len(unrecognised),
            "macro_auc": None if not unrecognised else macro_auc(pairwise_aucs(unrecognised)),
            "spearman": _safe_spearman(unrecognised),
        },
        "unprobed": sum(1 for row in rows if row.get("recognised") is None),
        "bootstrap_seed": seed,
        "bootstrap_resamples": resamples,
    }


def paired_contrast(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    """Paired DeLong and a stratified bootstrap of the macro-AUC difference."""
    by_right = {str(row["paper_id"]): row for row in right}
    pairs = [(row, by_right[str(row["paper_id"])]) for row in left if str(row["paper_id"]) in by_right]
    contrasts: dict[str, Any] = {}
    for name, high, low in list(PAIRWISE) + [("oral_vs_rest", "oral", "rest")]:
        scores_left: list[float] = []
        scores_right: list[float] = []
        labels: list[int] = []
        for left_row, right_row in pairs:
            tier = left_row.get("true_tier")
            if name == "oral_vs_rest":
                if tier == "oral":
                    label = 1
                elif tier in {"highlight", "poster", "non_oral"}:
                    label = 0
                else:
                    continue
            elif tier == high:
                label = 1
            elif tier == low:
                label = 0
            else:
                continue
            scores_left.append(float(left_row["score"]))
            scores_right.append(float(right_row["score"]))
            labels.append(label)
        contrasts[name] = delong_paired(scores_left, scores_right, labels)

    def difference(sample: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
        left_auc = _macro_of([item[0] for item in sample])
        right_auc = _macro_of([item[1] for item in sample])
        if left_auc is None or right_auc is None:
            return None
        return left_auc - right_auc

    p_values = [contrasts[name]["p"] for name, _high, _low in PAIRWISE]
    return {
        "n": len(pairs),
        "delong": contrasts,
        "holm": {name: value for name, value in zip((item[0] for item in PAIRWISE), adjust_holm(p_values), strict=True)},
        "bh": {name: value for name, value in zip((item[0] for item in PAIRWISE), adjust_bh(p_values), strict=True)},
        "macro_auc_diff": paired_stratified_bootstrap(pairs, difference, resamples=resamples, seed=seed),
    }


def analysis_rows(model_id: str, rows: list[dict[str, Any]], *, variant: str | None, round_id: str | None) -> list[dict[str, Any]]:
    weights = ht_weights(rows)
    exported: list[dict[str, Any]] = []
    for row in rows:
        repeats = row.get("repeat_scores")
        repeat_text = ""
        if isinstance(repeats, list):
            repeat_text = ";".join(str(score) for score in repeats)
        exported.append(
            {
                "model_id": model_id,
                "variant": "" if variant is None else variant,
                "round": "" if round_id is None else round_id,
                "paper_id": row["paper_id"],
                "year": "" if row.get("year") is None else row["year"],
                "true_tier": row["true_tier"],
                "predicted_tier": row["predicted_tier"],
                "score": row["score"],
                "weight": "" if weights.get(str(row["paper_id"])) is None else weights[str(row["paper_id"])],
                "recognised": "" if row.get("recognised") is None else int(bool(row["recognised"])),
                "repeat_scores": repeat_text,
            }
        )
    return exported


CSV_COLUMNS = (
    "model_id",
    "variant",
    "round",
    "paper_id",
    "year",
    "true_tier",
    "predicted_tier",
    "score",
    "weight",
    "recognised",
    "repeat_scores",
)


def write_analysis_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
