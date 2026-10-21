"""Reproducible stratified sample of public ICLR calibration papers.

The selector sees forum ids, years, decisions, area labels, and official
rating bins. It does not see manuscript text. Callers write the manifest
under a gitignored directory. Nothing in this module is a fitted weight.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Fixed sample design. Changing the seed or a quota is a different benchmark.
SEED = 20261021
TARGET_N = 100
MAX_PER_AREA = 4
TUNE_FRACTION = 0.6

# name, inclusive lower mean, exclusive upper mean, quota
STRATA: tuple[tuple[str, float, float, int], ...] = (
    ("clear_reject", 0.0, 4.0, 20),
    ("low", 4.0, 5.0, 15),
    ("borderline", 5.0, 6.5, 25),
    ("high", 6.5, 8.0, 20),
    ("strong", 8.0, 11.0, 20),
)

STRATUM_NAMES = tuple(item[0] for item in STRATA)


@dataclass(frozen=True)
class Candidate:
    """One public submission that already has official rating bins."""

    forum_id: str
    year: int
    listing: str
    decision: str
    primary_area: str
    ratings: tuple[int, ...]

    @property
    def mean(self) -> float:
        return sum(self.ratings) / len(self.ratings)


@dataclass(frozen=True)
class Selection:
    """Ordered sample plus the leftovers, in the same walk order."""

    seed: int
    selected: tuple[Candidate, ...]
    alternates: tuple[Candidate, ...]
    by_stratum: dict[str, tuple[Candidate, ...]]
    alternate_by_stratum: dict[str, tuple[Candidate, ...]]
    shortfalls: dict[str, int]

    @property
    def counts(self) -> dict[str, int]:
        return {name: len(self.by_stratum.get(name, ())) for name in STRATUM_NAMES}


def criteria_public() -> dict[str, object]:
    """Selection rules safe to commit. No forum ids and no scores."""
    return {
        "seed": SEED,
        "target": TARGET_N,
        "max_per_area": MAX_PER_AREA,
        "tune_fraction": TUNE_FRACTION,
        "min_ratings": 2,
        "years": [2024, 2025],
        "strata": [
            {"name": name, "mean_low_inclusive": low, "mean_high_exclusive": high, "quota": quota}
            for name, low, high, quota in STRATA
        ],
        "area_rule": (
            "Within a stratum, walk areas in sorted order and alternate 2024 then 2025. "
            "Take at most max_per_area from one area, then relax that cap if the quota is still short."
        ),
        "order_rule": (
            "Each area-year group is sorted by forum id and shuffled with "
            "Random(seed:stratum:area:year). Input order does not matter."
        ),
    }


def stratum_for(mean: float) -> str | None:
    for name, low, high, _quota in STRATA:
        if low <= mean < high:
            return name
    return None


def _quota(name: str, quotas: dict[str, int] | None) -> int:
    if quotas is not None and name in quotas:
        return int(quotas[name])
    for stratum_name, _low, _high, quota in STRATA:
        if stratum_name == name:
            return quota
    raise KeyError(name)


def _group_order(items: list[Candidate], *, seed: int, stratum: str) -> dict[tuple[str, int], list[Candidate]]:
    grouped: dict[tuple[str, int], list[Candidate]] = {}
    for item in items:
        grouped.setdefault((item.primary_area, item.year), []).append(item)
    ordered: dict[tuple[str, int], list[Candidate]] = {}
    for area, year in sorted(grouped):
        group = sorted(grouped[(area, year)], key=lambda candidate: candidate.forum_id)
        rng = random.Random(f"{seed}:{stratum}:{area}:{year}")
        rng.shuffle(group)
        ordered[(area, year)] = group
    return ordered


def _walk(ordered: dict[tuple[str, int], list[Candidate]], *, cap: int | None) -> list[Candidate]:
    """Round-robin areas, alternating years. ``cap`` limits how many one area can contribute."""
    areas = sorted({area for area, _year in ordered})
    years = tuple(sorted({year for _area, year in ordered}))
    indices = {key: 0 for key in ordered}
    area_counts: dict[str, int] = {}
    picked: list[Candidate] = []
    while True:
        progress = False
        for area in areas:
            for year in years:
                key = (area, year)
                group = ordered.get(key)
                if not group:
                    continue
                index = indices[key]
                if index >= len(group):
                    continue
                if cap is not None and area_counts.get(area, 0) >= cap:
                    continue
                picked.append(group[index])
                indices[key] = index + 1
                area_counts[area] = area_counts.get(area, 0) + 1
                progress = True
        if not progress:
            return picked


def order_stratum(
    items: list[Candidate],
    *,
    seed: int,
    stratum: str,
    max_per_area: int,
) -> list[Candidate]:
    """Full walk for one stratum: area-capped prefix, then the rest."""
    eligible = [item for item in items if len(item.ratings) >= 2 and item.decision in {"accept", "reject"}]
    ordered = _group_order(eligible, seed=seed, stratum=stratum)
    capped = _walk(ordered, cap=max_per_area)
    seen = {item.forum_id for item in capped}
    rest = [item for item in _walk(ordered, cap=None) if item.forum_id not in seen]
    return capped + rest


def select_benchmark(
    candidates: list[Candidate],
    *,
    seed: int = SEED,
    quotas: dict[str, int] | None = None,
    max_per_area: int = MAX_PER_AREA,
) -> Selection:
    """Stratified sample. The same candidates in any order yield the same selection."""
    buckets: dict[str, list[Candidate]] = {name: [] for name in STRATUM_NAMES}
    for candidate in candidates:
        if len(candidate.ratings) < 2 or candidate.decision not in {"accept", "reject"}:
            continue
        name = stratum_for(candidate.mean)
        if name is None:
            continue
        buckets[name].append(candidate)

    selected: list[Candidate] = []
    alternates: list[Candidate] = []
    by_stratum: dict[str, tuple[Candidate, ...]] = {}
    alternate_by_stratum: dict[str, tuple[Candidate, ...]] = {}
    shortfalls: dict[str, int] = {}
    for name in STRATUM_NAMES:
        quota = _quota(name, quotas)
        walked = order_stratum(buckets[name], seed=seed, stratum=name, max_per_area=max_per_area)
        chosen = walked[:quota]
        leftover = walked[quota:]
        by_stratum[name] = tuple(chosen)
        alternate_by_stratum[name] = tuple(leftover)
        shortfalls[name] = max(0, quota - len(chosen))
        selected.extend(chosen)
        alternates.extend(leftover)
    return Selection(
        seed=seed,
        selected=tuple(selected),
        alternates=tuple(alternates),
        by_stratum=by_stratum,
        alternate_by_stratum=alternate_by_stratum,
        shortfalls=shortfalls,
    )


def assign_split(
    selected: list[Candidate],
    *,
    seed: int = SEED,
    tune_fraction: float = TUNE_FRACTION,
) -> tuple[list[Candidate], list[Candidate]]:
    """Hold out ``1 - tune_fraction`` inside each stratum.

    The shuffle key is the seed and the stratum, not the model score. Papers
    are sorted by forum id before the shuffle, so input order does not matter.
    """
    if not 0 < tune_fraction < 1:
        raise ValueError("tune_fraction must be between 0 and 1")
    buckets: dict[str, list[Candidate]] = {name: [] for name in STRATUM_NAMES}
    for candidate in selected:
        name = stratum_for(candidate.mean)
        if name is None:
            raise ValueError(f"candidate {candidate.forum_id} does not fall in a stratum")
        buckets[name].append(candidate)
    tune: list[Candidate] = []
    holdout: list[Candidate] = []
    for name in STRATUM_NAMES:
        group = sorted(buckets[name], key=lambda candidate: candidate.forum_id)
        rng = random.Random(f"{seed}:split:{name}")
        rng.shuffle(group)
        tune_count = int(round(len(group) * tune_fraction))
        tune.extend(group[:tune_count])
        holdout.extend(group[tune_count:])
    return tune, holdout
