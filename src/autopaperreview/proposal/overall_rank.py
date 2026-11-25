"""Fit and evaluate a proposal-only rank without using test paper outcomes."""

from __future__ import annotations

from math import sqrt
from random import Random
from statistics import mean

from ..public_calibration import roc_auc, spearman
from .merit import AXES


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


def evaluate(rows, spec, bootstrap=0):
    if len({r["key"] for r in rows}) != len(rows):
        raise ValueError("duplicate evaluation row")
    scores = [predict(r["features"], spec) for r in rows]
    targets = [r["target"] for r in rows]
    labels = [int(t >= 3.5) for t in targets]
    out = {
        "n": len(rows),
        "positive": sum(labels),
        "rho": spearman(scores, targets),
        "auc": roc_auc(scores, labels),
        "apr_rho": spearman(scores, [r["rating"] for r in rows]),
        "apr_auc": roc_auc(scores, [r["accept"] for r in rows]),
    }
    if bootstrap:
        rng = Random(61124)
        trials = {"rho": [], "auc": [], "apr_rho": [], "apr_auc": []}
        for _ in range(bootstrap):
            ids = rng.choices(range(len(rows)), k=len(rows))
            p = [scores[i] for i in ids]
            y = [targets[i] for i in ids]
            for k, v in [
                ("rho", spearman(p, y)),
                ("auc", roc_auc(p, [labels[i] for i in ids])),
                ("apr_rho", spearman(p, [rows[i]["rating"] for i in ids])),
                ("apr_auc", roc_auc(p, [rows[i]["accept"] for i in ids])),
            ]:
                if v is not None:
                    trials[k].append(v)

        def interval(x):
            x.sort()
            return (
                [x[int((len(x) - 1) * q)] for q in (0.025, 0.975)]
                if len(x) >= 0.9 * bootstrap
                else None
            )

        out["ci95"] = {k: interval(v) for k, v in trials.items()}
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
