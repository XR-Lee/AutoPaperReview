"""Paired stratified bootstrap for the promotion benchmark.

The procedure is fixed in ``benchmark-v1.md``. A resample draws papers with
replacement inside each stratum, keeps each stratum's size, and applies the
same draw to both arms. The recorded difference is pre-release minus main.
The interval is the linear 2.5 and 97.5 percentiles of the defined resamples.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Callable, Sequence

RESAMPLES = 4000
SEED = 20261029


def linear_percentile(sorted_values: Sequence[float], probability: float) -> float:
    """Same linear percentile as ``rank_metrics._linear_percentile``."""
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower]) * (1.0 - weight) + float(sorted_values[upper]) * weight


def stratified_indexes(strata: Sequence[str], *, resamples: int, seed: int) -> list[list[int]]:
    """Index draws. Stratum order is sorted by name so the seed does not depend on row order."""
    pools: dict[str, list[int]] = defaultdict(list)
    for index, stratum in enumerate(strata):
        pools[str(stratum)].append(index)
    rng = random.Random(seed)
    draws: list[list[int]] = []
    names = sorted(pools)
    for _ in range(resamples):
        chosen: list[int] = []
        for name in names:
            pool = pools[name]
            chosen.extend(pool[rng.randrange(len(pool))] for _ in range(len(pool)))
        draws.append(chosen)
    return draws


def paired_difference(
    rows: Sequence[dict[str, Any]],
    metric: Callable[[Sequence[dict[str, Any]]], float | None],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Point difference and paired stratified 95% interval.

    ``metric`` receives a list of row dicts and returns the arm-specific
    contrast's statistic on that list, or ``None`` when the statistic is
    undefined. Callers pass a closure that reads both arms from each row and
    returns pre-release minus main.
    """
    point = metric(rows)
    draws = stratified_indexes(
        [str(row["stratum"]) for row in rows],
        resamples=resamples,
        seed=seed,
    )
    defined: list[float] = []
    for indexes in draws:
        sample = [rows[index] for index in indexes]
        value = metric(sample)
        if value is None:
            continue
        defined.append(float(value))
    if defined:
        ordered = sorted(defined)
        low: float | None = linear_percentile(ordered, 0.025)
        high: float | None = linear_percentile(ordered, 0.975)
    else:
        low = high = None
    return {
        "point": None if point is None else float(point),
        "low": low,
        "high": high,
        "defined_resamples": len(defined),
        "resamples": resamples,
        "seed": seed,
        "n": len(rows),
    }


def worse_beyond_interval(interval: dict[str, Any], *, higher_is_better: bool) -> bool:
    """True when the 95% interval lies entirely on the worse side of zero.

    An undefined interval is worse: the comparison was not shown.
    """
    low = interval.get("low")
    high = interval.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return True
    if higher_is_better:
        return float(high) < 0.0
    return float(low) > 0.0
