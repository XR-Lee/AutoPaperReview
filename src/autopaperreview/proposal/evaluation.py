"""Fit offsets on development papers only. All reported errors use paired holdout papers."""

from __future__ import annotations

import itertools
import random
from statistics import mean

from ..public_calibration import spearman
from .data import digest, read
from .runner import stage_result
from .stages import RUBRIC, calibrated, check, proposal_text, scored


def signature(record):
    return {k: v for k, v in record["request"].items() if k != "input_sha256"}


def collect(root, split, reviewer="review"):
    rows = []
    identities = set()
    for case in read(root / "manifest.json")["cases"]:
        if case["split"] != split:
            continue
        bundle = root / "inputs" / case["bundle"]
        provenance = read(bundle / "provenance.json")
        model = stage_result(root, case, reviewer)
        labels = stage_result(root, case, "label")
        audit = stage_result(root, case, "audit")
        compressor = stage_result(root, case, "compress")
        config = {
            k: signature(v)
            for k, v in [
                ("compress", compressor),
                ("review", model),
                ("label", labels),
                ("audit", audit),
            ]
        }
        identities.add(digest(config))
        coded = {d: [] for d in RUBRIC}
        for ref, items in labels["output"].items():
            for item in items:
                quotes = audit["output"][f"{ref}.{item['dimension']}"]
                if not quotes:
                    continue
                coded[item["dimension"]].append(
                    {
                        "review_id": provenance["review_ids"][ref],
                        "score": item["score"],
                        "quotes": quotes,
                    }
                )
        rows.append(
            {
                "key": case["key"],
                "forum": provenance["forum"],
                "split": split,
                "config": config,
                "proposal": proposal_text(compressor["output"]),
                "review": scored(model),
                "labels": coded,
                "reviewers": len(labels["output"]),
                "pre_review": provenance["pre_review"],
            }
        )
    check(rows, f"no {split} papers")
    check(len(identities) == 1, f"inconsistent model or prompt on {split} papers")
    return rows


def gold(row, dim):
    scores = [x["score"] for x in row["labels"][dim]]
    return mean(scores) if scores else None


def fit(root, reviewer="review"):
    rows = collect(root, "dev", reviewer)
    dims = {}
    for dim in RUBRIC:
        paired, targets = [], []
        for row in rows:
            target = gold(row, dim)
            estimate = row["review"][dim]["score"]
            if target is not None:
                targets.append(target)
                if estimate is not None:
                    paired.append(target - estimate)
        # Four zero-offset pseudo-observations stabilize small development sets.
        dims[dim] = {
            "offset": sum(paired) / (len(paired) + 4),
            "dev_pairs": len(paired),
            "baseline": mean(targets) if targets else 3.0,
            "dev_labels": len(targets),
        }
    return {
        "version": 1,
        "config": rows[0]["config"],
        "dev_keys": sorted(r["key"] for r in rows),
        "dimensions": dims,
    }


def bootstrap_rho(x, y):
    if len(x) < 5 or spearman(x, y) is None:
        return None
    rng = random.Random(914)
    stats = []
    for _ in range(1000):
        ids = rng.choices(range(len(x)), k=len(x))
        r = spearman([x[i] for i in ids], [y[i] for i in ids])
        if r is not None:
            stats.append(r)
    if len(stats) < 900:
        return None
    stats.sort()
    return [stats[int((len(stats) - 1) * q)] for q in (0.025, 0.975)]


def mae(actual, predicted):
    return (
        mean(abs(a - b) for a, b in zip(actual, predicted, strict=True))
        if actual
        else None
    )


def evaluate(root, calibration):
    rows = collect(root, "holdout", calibration["config"]["review"]["stage"])
    check(
        set(calibration["dev_keys"]).isdisjoint(r["key"] for r in rows),
        "training/holdout overlap",
    )
    check(
        calibration["config"] == rows[0]["config"],
        "calibration and holdout runs differ",
    )
    dims = calibration["dimensions"]
    for row in rows:
        row["calibrated"] = calibrated(row["review"], dims)
    result = {}
    for dim in RUBRIC:
        predicted, actual, adjusted = [], [], []
        disagreements = []
        labeled = scored = 0
        for row in rows:
            target = gold(row, dim)
            score = row["review"][dim]["score"]
            labeled += target is not None
            scored += score is not None
            human_scores = [i["score"] for i in row["labels"][dim]]
            if len(human_scores) >= 2:
                disagreements.append(
                    mean(abs(a - b) for a, b in itertools.combinations(human_scores, 2))
                )
            if target is not None and score is not None:
                predicted.append(score)
                actual.append(target)
                adjusted.append(row["calibrated"][dim])

        result[dim] = {
            "papers": len(rows),
            "labeled": labeled,
            "scored": scored,
            "paired": len(actual),
            "raw_mae": mae(actual, predicted),
            "calibrated_mae": mae(actual, adjusted),
            "constant_mae": mae(actual, [dims[dim]["baseline"]] * len(actual)),
            "raw_spearman": spearman(predicted, actual),
            "raw_rho_ci95": bootstrap_rho(predicted, actual),
            "calibrated_spearman": spearman(adjusted, actual),
            "multi_reviewer_papers": len(disagreements),
            "reviewer_pair_mae": mean(disagreements) if disagreements else None,
        }
    return {
        "n": len(rows),
        "reviewers": sum(r["reviewers"] for r in rows),
        "dimensions": result,
    }, rows
