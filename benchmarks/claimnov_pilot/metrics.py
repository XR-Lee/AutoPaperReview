"""ClaimNov-style rates for APR novelty outputs.

Rows are dicts. This module does not read manuscripts.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict

THRESHOLDS = (2, 4, 6, 8, 10)
BOOTSTRAP_DRAWS = 4000
BOOTSTRAP_SEED = 20261028


def rejects(row: dict, threshold: float) -> bool:
    """Non-novel verdict: low originality, or a closest-neighbor fatal flag."""
    if row.get("leakage"):
        return False
    if row.get("missing_closest_neighbor"):
        return True
    score = row.get("originality")
    if score is None:
        return False
    return float(score) <= threshold


def _rate(rows: list[dict], threshold: float) -> float | None:
    usable = [row for row in rows if not row.get("leakage")]
    if not usable:
        return None
    return sum(1 for row in usable if rejects(row, threshold)) / len(usable)


def informedness(rows: list[dict], threshold: float) -> float | None:
    """Catch on non-novel rows minus false alarms on novel rows."""
    catch = _rate([row for row in rows if row.get("label") == "non_novel"], threshold)
    false_alarm = _rate([row for row in rows if row.get("label") == "novel"], threshold)
    if catch is None or false_alarm is None:
        return None
    return catch - false_alarm


def choose_threshold(train_rows: list[dict]) -> int:
    """Pick the originality cutoff on the train half.

    Ties prefer the higher informedness, then the lower false-alarm rate,
    then the smaller cutoff.
    """
    best_threshold = THRESHOLDS[0]
    best_key: tuple[float, float, int] | None = None
    for threshold in THRESHOLDS:
        score = informedness(train_rows, threshold)
        if score is None:
            continue
        false_alarm = _rate(
            [row for row in train_rows if row.get("label") == "novel"], threshold
        )
        key = (score, -(false_alarm or 0.0), -threshold)
        if best_key is None or key > best_key:
            best_key = key
            best_threshold = threshold
    return best_threshold


def split_rows(rows: list[dict], seed: int = BOOTSTRAP_SEED) -> tuple[list[dict], list[dict]]:
    """Stratified half split on dataset and label. Train is the first half."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row.get("dataset"), row.get("label"))].append(row)
    rng = random.Random(seed)
    train: list[dict] = []
    test: list[dict] = []
    for name in sorted(groups, key=lambda item: (str(item[0]), str(item[1]))):
        ordered = sorted(
            groups[name],
            key=lambda row: str(row.get("key") or row.get("item_id") or row.get("item_sha256") or ""),
        )
        rng.shuffle(ordered)
        cut = len(ordered) // 2
        train.extend(ordered[:cut])
        test.extend(ordered[cut:])
    return train, test


def sweep(rows: list[dict]) -> list[dict]:
    table = []
    for threshold in THRESHOLDS:
        table.append(
            {
                "threshold": threshold,
                "catch": _rate([row for row in rows if row.get("label") == "non_novel"], threshold),
                "false_alarm": _rate([row for row in rows if row.get("label") == "novel"], threshold),
                "src_out_rejection": _rate(
                    [row for row in rows if row.get("label") == "src_out"], threshold
                ),
                "hybrid_keep": _keep(rows, threshold),
                "informedness": informedness(rows, threshold),
            }
        )
    return table


def _keep(rows: list[dict], threshold: float) -> float | None:
    hybrids = [row for row in rows if row.get("dataset") == "hybrids" and not row.get("leakage")]
    if not hybrids:
        return None
    rejected = sum(1 for row in hybrids if rejects(row, threshold))
    return 1.0 - rejected / len(hybrids)


def bootstrap_rate(
    flags: list[bool],
    *,
    seed: int = BOOTSTRAP_SEED,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict | None:
    """Item bootstrap. Returns the rate and a percentile 95% interval."""
    if not flags:
        return None
    rng = random.Random(seed)
    size = len(flags)
    stats = []
    for _ in range(draws):
        total = 0
        for _index in range(size):
            if flags[rng.randrange(size)]:
                total += 1
        stats.append(total / size)
    stats.sort()
    low_index = int(0.025 * (draws - 1))
    high_index = int(0.975 * (draws - 1))
    return {
        "n": size,
        "rate": sum(flags) / size,
        "low": stats[low_index],
        "high": stats[high_index],
    }


def bootstrap_difference(
    left: list[bool],
    right: list[bool],
    *,
    seed: int = BOOTSTRAP_SEED,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict | None:
    """Paired item bootstrap of left minus right. Lengths must match."""
    if len(left) != len(right) or not left:
        return None
    rng = random.Random(seed)
    size = len(left)
    stats = []
    for _ in range(draws):
        total = 0.0
        for _index in range(size):
            pick = rng.randrange(size)
            total += int(left[pick]) - int(right[pick])
        stats.append(total / size)
    stats.sort()
    low_index = int(0.025 * (draws - 1))
    high_index = int(0.975 * (draws - 1))
    point = sum(int(a) - int(b) for a, b in zip(left, right, strict=True)) / size
    return {"n": size, "difference": point, "low": stats[low_index], "high": stats[high_index]}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def claim_agreement(
    apr_claims: list[dict],
    gold_claims: list[dict],
    *,
    minimum_jaccard: float = 0.2,
) -> dict:
    """Align APR contribution strings to gold claims and compare coverage.

    Gold ``established`` true agrees with APR tag ``overlap``. Anything else
    agrees with a non-overlap tag. No usable APR claims is an unusable profile.
    """
    if not apr_claims or not gold_claims:
        return {"unusable": True, "agreement": None, "aligned": 0, "gold": len(gold_claims)}
    aligned = 0
    agreed = 0
    for gold in gold_claims:
        gold_tokens = _tokens(str(gold.get("text") or ""))
        best = None
        best_score = 0.0
        for claim in apr_claims:
            score = 0.0
            claim_tokens = _tokens(str(claim.get("text") or ""))
            union = gold_tokens | claim_tokens
            if union:
                score = len(gold_tokens & claim_tokens) / len(union)
            if score > best_score:
                best = claim
                best_score = score
        if best is None or best_score < minimum_jaccard:
            continue
        aligned += 1
        established = bool(gold.get("established"))
        overlap = str(best.get("tag") or "") == "overlap"
        if established == overlap:
            agreed += 1
    if aligned == 0:
        return {"unusable": True, "agreement": None, "aligned": 0, "gold": len(gold_claims)}
    return {
        "unusable": False,
        "agreement": agreed / aligned,
        "aligned": aligned,
        "gold": len(gold_claims),
    }


def headline(train_rows: list[dict], test_rows: list[dict], all_rows: list[dict]) -> dict:
    """Threshold from the train half. Catch and false alarms on the test half."""
    threshold = choose_threshold(train_rows)
    non_novel = [row for row in test_rows if row.get("label") == "non_novel" and not row.get("leakage")]
    novel = [row for row in test_rows if row.get("label") == "novel" and not row.get("leakage")]
    return {
        "threshold": threshold,
        "catch": bootstrap_rate([rejects(row, threshold) for row in non_novel]),
        "false_alarm": bootstrap_rate([rejects(row, threshold) for row in novel]),
        "src_out_rejection": bootstrap_rate(
            [
                rejects(row, threshold)
                for row in all_rows
                if row.get("label") == "src_out" and not row.get("leakage")
            ]
        ),
        "hybrid_keep": bootstrap_rate(
            [
                not rejects(row, threshold)
                for row in all_rows
                if row.get("dataset") == "hybrids" and not row.get("leakage")
            ]
        ),
        "test_sweep": sweep(test_rows),
        "leakage_excluded": sum(1 for row in all_rows if row.get("leakage")),
    }
