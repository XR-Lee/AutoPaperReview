"""Seeded, stratified draw for the ClaimNov novelty pilot.

The draw stores opaque ids. Title-bearing ClaimNov ids are sha256 digests.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from collections.abc import Callable, Sequence

SEED = 20261028
PER_DATASET = 50
DATASETS = ("disguised", "claim_disputed", "later_realized", "hybrids")

# Nested stratum fields, in order. Empty means a uniform draw.
STRATA = {
    "disguised": ("condition", "rung"),
    "claim_disputed": ("decision",),
    "later_realized": ("bucket",),
    "hybrids": (),
}

PUBLIC_FIELDS = {
    "disguised": ("item_id", "label", "condition", "rung"),
    "claim_disputed": ("item_id", "label", "decision"),
    "later_realized": ("item_sha256", "label", "bucket"),
    "hybrids": ("item_sha256", "label"),
}


def digest(item_id: str) -> str:
    return hashlib.sha256(item_id.encode("utf-8")).hexdigest()


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder quotas. Names sort the remainder ties."""
    total = sum(counts.values())
    if n < 0 or total < 0:
        raise ValueError("counts and n must be non-negative")
    if n > total:
        raise ValueError(f"cannot draw {n} from {total}")
    if n == 0 or total == 0:
        return {name: 0 for name in counts}
    raw = {name: n * count / total for name, count in counts.items()}
    quotas = {name: min(int(raw[name]), counts[name]) for name in counts}
    leftover = n - sum(quotas.values())
    order = sorted(counts, key=lambda name: (-(raw[name] - quotas[name]), name))
    for name in order:
        if leftover == 0:
            break
        if quotas[name] < counts[name]:
            quotas[name] += 1
            leftover -= 1
    if leftover:
        raise RuntimeError("quota leftover")
    return quotas


def hierarchical_sample(
    items: Sequence[dict],
    key_fns: Sequence[Callable[[dict], str]],
    n: int,
    rng: random.Random,
) -> list[dict]:
    def rec(pool: Sequence[dict], fns: Sequence[Callable[[dict], str]], take: int) -> list[dict]:
        if take == 0 or not pool:
            return []
        if not fns:
            ordered = sorted(pool, key=lambda item: str(item["key"]))
            rng.shuffle(ordered)
            return ordered[:take]
        groups: dict[str, list[dict]] = defaultdict(list)
        for item in pool:
            groups[str(fns[0](item))].append(item)
        quotas = allocate({name: len(group) for name, group in groups.items()}, take)
        chosen: list[dict] = []
        for name in sorted(quotas):
            chosen.extend(rec(groups[name], fns[1:], quotas[name]))
        return chosen

    return rec(items, key_fns, n)


def draw_sample(
    catalogs: dict[str, list[dict]],
    *,
    seed: int = SEED,
    per_dataset: int = PER_DATASET,
) -> list[dict]:
    """Return public records for each dataset. Extra catalog fields are dropped."""
    missing = [name for name in DATASETS if name not in catalogs]
    if missing:
        raise KeyError(f"missing catalogs: {missing}")
    rng = random.Random(seed)
    chosen: list[dict] = []
    for dataset in DATASETS:
        fields = STRATA[dataset]
        key_fns = [lambda item, field=field: str(item[field]) for field in fields]
        picked = hierarchical_sample(catalogs[dataset], key_fns, per_dataset, rng)
        if len(picked) != per_dataset:
            raise RuntimeError(f"{dataset} produced {len(picked)} items")
        public_fields = PUBLIC_FIELDS[dataset]
        for item in picked:
            record = {"dataset": dataset}
            for field in public_fields:
                record[field] = item[field]
            chosen.append(record)
    return chosen
