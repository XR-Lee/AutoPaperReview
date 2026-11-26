"""Regenerate the published proposal-rank headlines from committed artifacts.

The row files live in ``benchmarks/proposal/repro/``. Review quotes, proposal
plans, and OpenReview forum URLs are not in those files. The intervals match
the original resampling: final-score tiers with a numpy Generator, and the
84-proposal draw with ``random.Random``. This module does not download papers
and it does not call a model.
"""

from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path
from random import Random
from statistics import mean
from typing import Any

from ..public_calibration import roc_auc, spearman
from .data import read
from .fixed_benchmark import evaluate_fixed

PUBLISHED = "reproduced from committed artifacts"
TIER_DRAWS = 3000
TIER_SEEDS = {"fixed": 241125, "fresh": 241126, "replications": 241127}
NLP_SEED = 11752
NLP_DRAWS = 2000
NLP_THRESHOLD = 6.625


def default_root() -> Path:
    return Path(__file__).resolve().parents[3] / "benchmarks" / "proposal" / "repro"


def reproduce_headlines(root: Path | None = None) -> dict[str, Any]:
    """Score the committed cohorts and return the published point estimates and intervals."""
    folder = default_root() if root is None else Path(root)
    if not (folder / "fixed100" / "fixed-predictions.json").is_file():
        raise FileNotFoundError(
            f"committed proposal artifacts were not found under {folder}"
        )
    numpy = _numpy()
    fixed = _iclr_cohort(
        folder / "fixed100",
        "fixed-predictions.json",
        "fixed",
        numpy,
    )
    fresh = _iclr_cohort(
        folder / "confirm100",
        "fresh-predictions.json",
        "fresh",
        numpy,
    )
    replications = _replication_cohort(folder / "replications199", numpy)
    nlp = _nlp_cohort(folder / "nlp84")
    return {
        "published_headlines": PUBLISHED,
        "fixed100": fixed,
        "confirm100": fresh,
        "replications199": replications,
        "nlp84": nlp,
    }


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "reproducing the published intervals needs numpy "
            "(pip install 'numpy>=1.24,<3', or pip install -e '.[repro]')"
        ) from exc
    return np


def _iclr_cohort(folder: Path, predictions_name: str, seed_name: str, numpy) -> dict[str, Any]:
    lock = read(folder / "lock.json")
    predictions = read(folder / predictions_name)
    references = read(folder / "references.json")
    tiers = {
        row["forum"]: row["tier"]
        for row in lock["cases"]
        if row.get("split", "test") == "test"
    }
    scores = {row["forum"]: row["overall"] for row in predictions}
    rows = [
        _paper(
            row["forum"],
            tiers[row["forum"]],
            scores[row["forum"]],
            row["prospective"],
            row["final_rating"],
            row.get("accept"),
        )
        for row in references
    ]
    scored = evaluate_fixed(lock, predictions, references, bootstrap=0, stratified_draws=0)
    return _cohort_result(rows, TIER_SEEDS[seed_name], numpy, scored)


def _replication_cohort(folder: Path, numpy) -> dict[str, Any]:
    index = {row["key"]: row for row in read(folder / "blind-index.json")}
    rows = []
    enrolled = 0
    for row in read(folder / "joined.json"):
        if row["group"] not in ("replication_A", "replication_B"):
            continue
        enrolled += 1
        if row["base"] is None:
            continue
        meta = index[row["key"]]
        rows.append(
            _paper(
                meta["forum"],
                f"{row['group']}-{row['tier']}",
                row["base"],
                row["target"],
                row["rating"],
                row.get("accept"),
            )
        )
    from .data import digest

    manifest = {
        "ids_sha256": digest(sorted(row["forum"] for row in rows)),
        "cases": [{"forum": row["forum"], "split": "test"} for row in rows],
    }
    predictions = [{"forum": row["forum"], "overall": row["score"]} for row in rows]
    references = [
        {
            "forum": row["forum"],
            "prospective": row["idea"],
            "final_rating": row["final"],
            "accept": row["accept"],
            "year": 2024,
            "rating_scale": "iclr2024",
        }
        for row in rows
    ]
    scored = evaluate_fixed(manifest, predictions, references, bootstrap=0, stratified_draws=0)
    result = _cohort_result(rows, TIER_SEEDS["replications"], numpy, scored)
    result["enrolled"] = enrolled
    return result


def _paper(forum, stratum, score, idea, final, accept) -> dict[str, Any]:
    return {
        "forum": forum,
        "stratum": stratum,
        "score": score,
        "idea": idea,
        "final": final,
        "accept": accept,
    }


def _cohort_result(rows: list[dict], seed: int, numpy, scored: dict) -> dict[str, Any]:
    scores = numpy.array([row["score"] for row in rows], dtype=float)
    finals = numpy.array([row["final"] for row in rows], dtype=float)
    decisions = numpy.array([_decision(row["accept"]) for row in rows], dtype=float)
    tiers = numpy.array([row["stratum"] for row in rows])
    rho = spearman(scores.tolist(), finals.tolist())
    auc = _auc(scores, decisions, numpy)
    top = _top_fifth(scores, finals >= 7, numpy)
    decided = int(numpy.isfinite(decisions).sum())
    return {
        "n": len(rows),
        "decision_n": decided,
        "rho": rho,
        "rho_ci95": _tier_interval(scores, finals, tiers, seed, numpy),
        "auc": auc,
        "top20": top,
        "draws": TIER_DRAWS,
        "seed": seed,
        "evaluate_rank": {
            "n": scored["n"],
            "decision_n": scored["decision_n"],
            "apr_rho": scored["apr_rho"],
            "apr_auc": scored["apr_auc"],
        },
    }


def _decision(value: Any) -> float:
    if value is None:
        return float("nan")
    return float(value)


def _tier_interval(scores, finals, tiers, seed: int, numpy) -> list[float]:
    rng = numpy.random.default_rng(seed)
    pieces = []
    for name in numpy.unique(tiers):
        idx = numpy.flatnonzero(tiers == name)
        pieces.append(rng.choice(idx, (TIER_DRAWS, len(idx))))
    chosen = numpy.column_stack(pieces)
    left = _average_ranks(scores[chosen], numpy)
    right = _average_ranks(finals[chosen], numpy)
    left = left - left.mean(axis=1, keepdims=True)
    right = right - right.mean(axis=1, keepdims=True)
    estimates = (left * right).sum(axis=1) / numpy.sqrt(
        (left * left).sum(axis=1) * (right * right).sum(axis=1)
    )
    return numpy.quantile(estimates, [0.025, 0.975], method="linear").tolist()


def _average_ranks(matrix, numpy):
    ranks = numpy.empty(matrix.shape, dtype=float)
    for row_index, values in enumerate(matrix):
        order = numpy.argsort(values, kind="mergesort")
        ordered = values[order]
        cursor = 0
        size = len(values)
        while cursor < size:
            end = cursor
            while end + 1 < size and ordered[end + 1] == ordered[cursor]:
                end += 1
            ranks[row_index, order[cursor : end + 1]] = (cursor + end) / 2 + 1
            cursor = end + 1
    return ranks


def _auc(scores, decisions, numpy) -> float:
    known = numpy.isfinite(decisions)
    labels = decisions[known] == 1
    positive = int(labels.sum())
    ranks = _average_ranks(scores[known][None, :], numpy)[0]
    return float(
        (ranks[labels].sum() - positive * (positive + 1) / 2)
        / (positive * (len(labels) - positive))
    )


def _top_fifth(scores, positive, numpy) -> dict[str, Any]:
    count = len(scores)
    k = math.ceil(count / 5)
    cutoff = float(numpy.sort(scores)[-k])
    strict = scores > cutoff
    tied = scores == cutoff
    strict_hits = int(positive[strict].sum())
    tied_hits = int(positive[tied].sum())
    available = int(tied.sum())
    picked = k - int(strict.sum())
    low = strict_hits + max(0, picked - (available - tied_hits))
    high = strict_hits + min(picked, tied_hits)
    return {
        "k": k,
        "cut": cutoff,
        "positive": int(positive.sum()),
        "included_positive_range": [low, high],
        "random_expected": float(k * positive.mean()),
    }


def _nlp_cohort(folder: Path) -> dict[str, Any]:
    rows = read(folder / "deployed-external-rows.json")
    overall = [row["overall"] for row in rows]
    human = [row["human"] for row in rows]
    labels = [int(value >= NLP_THRESHOLD) for value in human]
    pairs = []
    for left, right in combinations(rows, 2):
        if left["topic"] != right["topic"] or left["human"] == right["human"]:
            continue
        gap = (left["human"] - right["human"]) * (left["overall"] - right["overall"])
        pairs.append(float(gap > 0) if gap else 0.5)
    rho_draws = []
    auc_draws = []
    rng = Random(NLP_SEED)
    for _ in range(NLP_DRAWS):
        sample = rng.choices(rows, k=len(rows))
        sample_score = [row["overall"] for row in sample]
        sample_human = [row["human"] for row in sample]
        rho = spearman(sample_score, sample_human)
        auc = roc_auc(sample_score, [int(value >= NLP_THRESHOLD) for value in sample_human])
        if rho is not None:
            rho_draws.append(rho)
        if auc is not None:
            auc_draws.append(auc)
    return {
        "n": len(rows),
        "positive": sum(labels),
        "rho": spearman(overall, human),
        "auc": roc_auc(overall, labels),
        "rho_ci95": _index_interval(rho_draws),
        "auc_ci95": _index_interval(auc_draws),
        "same_topic_comparable_pairs": len(pairs),
        "same_topic_concordance": mean(pairs) if pairs else None,
        "draws": NLP_DRAWS,
        "seed": NLP_SEED,
        "threshold": NLP_THRESHOLD,
    }


def _index_interval(values: list[float]) -> list[float]:
    ordered = sorted(values)
    return [ordered[int(quantile * (len(ordered) - 1))] for quantile in (0.025, 0.975)]
