"""Calibrate anonymous comparative judgments against multiple development panels."""

from statistics import mean, pstdev

from .data import digest
from .prior_index import calibration_rows, historical_bounds


def excerpts(proposal):
    return {k: v[:750] for k, v in proposal.items() if isinstance(v, str) and v}


def panel(proposal, bank, round_number, exclude_key="", *, target_year=None, cutoff_ms=None, **exclusions):
    bounds = historical_bounds(target_year, cutoff_ms)
    pool = sorted(
        (r for r, _ in calibration_rows(bank, bounds, excerpt_field="excerpts", exclude_key=exclude_key, **exclusions)),
        key=lambda r: (r["target"], r["key"]),
    )
    if len(pool) < 15:
        raise ValueError(
            "comparison calibration needs at least 15 distinct development proposals"
        )
    items = []
    for q in range(5):
        band = pool[q * len(pool) // 5 : (q + 1) * len(pool) // 5]
        ranked = sorted(band, key=lambda r: digest(["comparison-panel", r["key"]]))
        r = ranked[round_number % len(ranked)]
        items.append((r["key"], excerpts(r["proposal"]), r["target"]))
    items.append(("@", excerpts(proposal), None))
    seed = digest(proposal)
    items.sort(key=lambda r: digest(["anonymous-order", seed, round_number, r[0]]))
    aliases = "ABCDEF"
    return {a: r[1] for a, r in zip(aliases, items, strict=True)}, {
        a: r[2] for a, r in zip(aliases, items, strict=True)
    }


def score_panel(verdict, reference):
    target = next(a for a, value in reference.items() if value is None)
    peers = [a for a in reference if a != target]
    result = {}
    for field in ("priority", "advance"):
        x = [verdict[a][field] for a in peers]
        y = [reference[a] for a in peers]
        v = verdict[target][field]
        scale = max(pstdev(x), 7.0)
        result["relative_" + field] = mean(y) + (v - mean(x)) / scale * max(
            pstdev(y), 0.25
        )
        result["wins_" + field] = 1 + 4 * mean(
            1 if v > p else 0.5 if v == p else 0 for p in x
        )
    return result
