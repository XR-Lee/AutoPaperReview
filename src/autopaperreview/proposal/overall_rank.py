"""Fit and evaluate a proposal-only rank without using test paper outcomes."""

from __future__ import annotations

from collections import defaultdict
from math import floor, sqrt
from random import Random
from statistics import mean

from ..public_calibration import roc_auc, spearman
from .merit import AXES

# Same generator seed as the unstratified proposal bootstrap. 3000 draws match
# the reported procedure. The published intervals are not reproduced from the
# artifacts in this repository.
STRATIFIED_DRAWS = 3000
STRATIFIED_SEED = 61124


def expectation(probabilities):
    return sum(i * p for i, p in enumerate(probabilities, 1)) / sum(probabilities)


def features(verdict):
    seats = [expectation(p) for p in verdict["panel"]]
    return {
        "holistic": mean(seats),
        **{d: verdict["dimensions"][d]["score"] for d in AXES},
        "specialist": seats[0],
        "neighbor": seats[1],
        "chair": seats[2],
    }


def solve(a, b):
    """Pivoted elimination for the small positive-definite ridge system."""
    rows = [list(r) + [v] for r, v in zip(a, b, strict=True)]
    for i in range(len(rows)):
        pivot = max(range(i, len(rows)), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        scale = rows[i][i]
        if abs(scale) < 1e-12:
            raise ValueError("singular calibration matrix")
        rows[i] = [v / scale for v in rows[i]]
        for j, r in enumerate(rows):
            if j != i:
                w = r[i]
                rows[j] = [u - w * v for u, v in zip(r, rows[i], strict=True)]
    return [r[-1] for r in rows]


def fit_ridge(rows, names, shrink=16.0):
    if not rows or shrink <= 0:
        raise ValueError("need development rows and positive ridge penalty")
    centers = []
    scales = []
    for key in names:
        available = [
            r["features"][key] for r in rows if r["features"].get(key) is not None
        ]
        center = mean(available) if available else 3.0
        centers.append(center)
        scales.append(
            max(0.15, sqrt(sum((v - center) ** 2 for v in available) / len(rows)))
        )
    x = [
        [
            ((r["features"][k] if r["features"].get(k) is not None else c) - c) / s
            for k, c, s in zip(names, centers, scales, strict=True)
        ]
        for r in rows
    ]
    ybar = mean(r["target"] for r in rows)
    n = len(names)
    a = [
        [
            sum(row[i] * row[j] for row in x) + (shrink if i == j else 0.0)
            for j in range(n)
        ]
        for i in range(n)
    ]
    b = [
        sum((r["target"] - ybar) * row[i] for r, row in zip(rows, x, strict=True))
        for i in range(n)
    ]
    return {
        "method": "ridge",
        "names": list(names),
        "center": centers,
        "scale": scales,
        "weights": solve(a, b),
        "intercept": ybar,
        "shrink": shrink,
    }


def predict(values, spec):
    if spec["method"] == "conditional":

        def average(keys):
            available = [values[k] for k in keys if values.get(k) is not None]
            return mean(available) if available else None

        advance = average(spec["advance"])
        value = average(spec["value"])
        credibility = average(spec["credibility"])
        available = [(v, w) for v, w in [(advance, 0.8), (value, 0.2)] if v is not None]
        priority = (
            sum(v * w for v, w in available) / sum(w for _, w in available)
            if available
            else 3.0
        )
        # A concrete weakness in an essential mechanism can reduce prospective utility.
        return priority - (
            0.4 * max(0.0, 3.0 - credibility) if credibility is not None else 0.0
        )
    if spec["method"] == "percentile":
        return 1 + 4 * sum(
            w * percentile(values.get(k), spec["distributions"][k])
            for k, w in zip(spec["names"], spec["weights"], strict=True)
        )
    if spec["method"] == "weighted":
        present = [
            (values[k], w)
            for k, w in zip(spec["names"], spec["weights"], strict=True)
            if values.get(k) is not None
        ]
        return (
            sum(v * w for v, w in present) / sum(w for _, w in present)
            if present
            else 3.0
        )
    if spec["method"] == "mean":
        present = [values[n] for n in spec["names"] if values.get(n) is not None]
        return mean(present) if present else 3.0
    return spec["intercept"] + sum(
        w * ((values[n] if values.get(n) is not None else c) - c) / s
        for n, w, c, s in zip(
            spec["names"], spec["weights"], spec["center"], spec["scale"], strict=True
        )
    )


def _accept_pairs(rows, scores, indexes):
    kept_scores = []
    kept_labels = []
    for index in indexes:
        label = rows[index].get("accept")
        if label in (0, 1):
            kept_scores.append(scores[index])
            kept_labels.append(int(label))
    return kept_scores, kept_labels


def _metrics(rows, scores, indexes):
    picked_scores = [scores[i] for i in indexes]
    targets = [rows[i]["target"] for i in indexes]
    labels = [int(target >= 3.5) for target in targets]
    accept_scores, accept_labels = _accept_pairs(rows, scores, indexes)
    return {
        "rho": spearman(picked_scores, targets),
        "auc": roc_auc(picked_scores, labels),
        "apr_rho": spearman(picked_scores, [rows[i]["rating"] for i in indexes]),
        "apr_auc": roc_auc(accept_scores, accept_labels) if accept_labels else None,
    }


def linear_percentile(sorted_values, probability):
    """Linear percentile, the same construction as the promotion bootstrap."""
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * probability
    lower = floor(position)
    upper = lower + 1 if lower < len(sorted_values) - 1 else lower
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower]) * (1.0 - weight) + float(sorted_values[upper]) * weight


def stratum_of(row):
    if row.get("stratum") is not None:
        return str(row["stratum"])
    return f"{float(row['rating']):.6f}"


def stratified_ci(rows, spec, *, draws=STRATIFIED_DRAWS, seed=STRATIFIED_SEED):
    """Percentile interval from resampling inside final-score strata.

    Each draw keeps every stratum's size. Stratum names are sorted, and the
    rows inside a stratum are sorted by key, so the seed does not depend on
    input order. A paper with no ``stratum`` uses its official final rating.
    Draws with an undefined statistic are omitted, and the interval is
    withheld when fewer than 90% of draws are defined.
    """
    if draws < 1:
        raise ValueError("stratified draws must be positive")
    if len({r["key"] for r in rows}) != len(rows):
        raise ValueError("duplicate evaluation row")
    scores = [predict(r["features"], spec) for r in rows]
    pools = defaultdict(list)
    for index, row in enumerate(rows):
        pools[stratum_of(row)].append(index)
    for name in pools:
        pools[name].sort(key=lambda index: (str(rows[index]["key"]), index))
    names = sorted(pools)
    rng = Random(seed)
    trials = {"rho": [], "auc": [], "apr_rho": [], "apr_auc": []}
    for _ in range(draws):
        indexes = []
        for name in names:
            pool = pools[name]
            indexes.extend(pool[rng.randrange(len(pool))] for _ in range(len(pool)))
        for key, value in _metrics(rows, scores, indexes).items():
            if value is not None:
                trials[key].append(value)

    def interval(values):
        if len(values) < 0.9 * draws:
            return None
        ordered = sorted(values)
        return [linear_percentile(ordered, 0.025), linear_percentile(ordered, 0.975)]

    return {key: interval(values) for key, values in trials.items()}


def top_fraction(scores, ratings, *, fraction=0.2, threshold=7.0):
    """How many of the top slice have an official mean at or above ``threshold``."""
    count = len(scores)
    if count == 0 or len(ratings) != count:
        raise ValueError("scores and ratings must be the same nonempty cohort")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    width = max(1, int(round(count * fraction)))
    order = sorted(range(count), key=lambda i: (scores[i], -i), reverse=True)
    chosen = order[:width]
    population = sum(1 for rating in ratings if rating >= threshold)
    return {
        "k": width,
        "held": sum(1 for index in chosen if ratings[index] >= threshold),
        "expected_random": width * population / count,
        "threshold": threshold,
        "fraction": fraction,
    }


def evaluate(rows, spec, bootstrap=0):
    if len({r["key"] for r in rows}) != len(rows):
        raise ValueError("duplicate evaluation row")
    scores = [predict(r["features"], spec) for r in rows]
    indexes = range(len(rows))
    labels = [int(r["target"] >= 3.5) for r in rows]
    stats = _metrics(rows, scores, indexes)
    out = {
        "n": len(rows),
        "positive": sum(labels),
        **stats,
    }
    if bootstrap:
        rng = Random(61124)
        trials = {"rho": [], "auc": [], "apr_rho": [], "apr_auc": []}
        for _ in range(bootstrap):
            ids = rng.choices(range(len(rows)), k=len(rows))
            for key, value in _metrics(rows, scores, ids).items():
                if value is not None:
                    trials[key].append(value)

        def interval(values):
            values.sort()
            return (
                [values[int((len(values) - 1) * q)] for q in (0.025, 0.975)]
                if len(values) >= 0.9 * bootstrap
                else None
            )

        out["ci95"] = {key: interval(value) for key, value in trials.items()}
    return out


def percentile(value, reference):
    """Mid-distribution for a tied empirical reference; unassessable inputs stay central."""
    from bisect import bisect_left, bisect_right

    if value is None or not reference:
        return 0.5
    return (bisect_left(reference, value) + bisect_right(reference, value)) / (
        2 * len(reference)
    )


def fit_percentile(rows, names):
    distributions = {
        k: sorted(r["features"][k] for r in rows if r["features"].get(k) is not None)
        for k in names
    }
    target_values = sorted(r["target"] for r in rows)
    ranked = [
        {
            "features": {
                k: percentile(r["features"].get(k), distributions[k]) for k in names
            },
            "target": percentile(r["target"], target_values),
        }
        for r in rows
    ]
    fit = fit_ridge(ranked, names, 18.0)
    # Monotonicity preserves score direction. Shrinking toward uniform limits instability in small samples.
    positive = [
        max(0.0, w / s) for w, s in zip(fit["weights"], fit["scale"], strict=True)
    ]
    total = sum(positive)
    weights = [
        (0.80 * w / total + 0.2 / len(names)) if total else 1 / len(names)
        for w in positive
    ]
    return {
        "method": "percentile",
        "names": list(names),
        "weights": weights,
        "distributions": distributions,
    }
