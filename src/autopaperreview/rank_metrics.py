"""Paper-level agreement metrics for a score vector.

Spearman and Kendall are against the mean official rating. AUC is accept
versus reject with ties counted as 0.5. Intervals are a 95% paper-level
percentile bootstrap. Undefined resamples are skipped. This module does not
read a manuscript and does not fit a coefficient.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Sequence

from .public_calibration import kendall_tau, roc_auc, spearman

BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20261021


def _linear_percentile(sorted_values: list[float], probability: float) -> float:
    """NumPy's linear percentile, without a NumPy dependency."""
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def agreement(scores: Sequence[float], means: Sequence[float], labels: Sequence[int]) -> dict[str, float | None]:
    """Point estimates. ``labels`` is 1 for accept and 0 for reject."""
    score_list = [float(item) for item in scores]
    mean_list = [float(item) for item in means]
    label_list = [int(item) for item in labels]
    errors = [score - mean for score, mean in zip(score_list, mean_list, strict=True)]
    absolute = [abs(item) for item in errors]
    return {
        "n": float(len(score_list)),
        "spearman": spearman(score_list, mean_list),
        "kendall": kendall_tau(score_list, mean_list),
        "auc": roc_auc(score_list, label_list),
        "mae": (sum(absolute) / len(absolute)) if absolute else None,
        "bias": (sum(errors) / len(errors)) if errors else None,
    }


def bootstrap_interval(
    scores: Sequence[float],
    means: Sequence[float],
    labels: Sequence[int],
    statistic: str,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Percentile interval for one statistic. Constant-score resamples are omitted."""
    score_list = [float(item) for item in scores]
    mean_list = [float(item) for item in means]
    label_list = [int(item) for item in labels]
    count = len(score_list)
    if not (count == len(mean_list) == len(label_list)):
        raise ValueError("scores, means, and labels must have the same length")
    point = agreement(score_list, mean_list, label_list).get(statistic)
    rng = random.Random(seed)
    sample: list[float] = []
    for _ in range(resamples):
        indexes = [rng.randrange(count) for _ in range(count)]
        drawn_scores = [score_list[index] for index in indexes]
        drawn_means = [mean_list[index] for index in indexes]
        drawn_labels = [label_list[index] for index in indexes]
        value = agreement(drawn_scores, drawn_means, drawn_labels).get(statistic)
        if value is None:
            continue
        sample.append(float(value))
    sample.sort()
    if not sample:
        low = high = None
    else:
        low = _linear_percentile(sample, 0.025)
        high = _linear_percentile(sample, 0.975)
    return {
        "statistic": statistic,
        "point": point,
        "low": low,
        "high": high,
        "defined_resamples": len(sample),
        "resamples": resamples,
        "seed": seed,
    }


def metric_row(
    scores: Sequence[float],
    means: Sequence[float],
    labels: Sequence[int],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    point = agreement(scores, means, labels)
    intervals = {
        name: bootstrap_interval(scores, means, labels, name, resamples=resamples, seed=seed)
        for name in ("spearman", "kendall", "auc", "mae", "bias")
    }
    return {"point": point, "intervals": intervals}


def format_interval(interval: dict[str, Any]) -> str:
    point = interval.get("point")
    low = interval.get("low")
    high = interval.get("high")
    if not isinstance(point, (int, float)):
        return "n/a"
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return f"{point:.3f}"
    return f"{point:.3f} [{low:.3f}, {high:.3f}]"


def candidate_table(
    records: Sequence[dict[str, Any]],
    score_of: Callable[[dict[str, Any], str], float | None],
    names: Sequence[str],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, Any]]:
    """Metrics per candidate. A record needs ``human_mean``, ``accept``, and the score source."""
    table: dict[str, dict[str, Any]] = {}
    means = [float(item["human_mean"]) for item in records]
    labels = [1 if item.get("accept") else 0 for item in records]
    for name in names:
        scores: list[float] = []
        kept_means: list[float] = []
        kept_labels: list[int] = []
        for record, mean, label in zip(records, means, labels, strict=True):
            value = score_of(record, name)
            if value is None:
                continue
            scores.append(float(value))
            kept_means.append(mean)
            kept_labels.append(label)
        table[name] = metric_row(scores, kept_means, kept_labels, resamples=resamples, seed=seed)
        table[name]["n"] = len(scores)
    return table
