"""Stratified public-benchmark selection. Synthetic ids and areas only."""

from __future__ import annotations

import unittest

from autopaperreview.benchmark_select import (
    SEED,
    STRATA,
    Candidate,
    assign_split,
    criteria_public,
    select_benchmark,
    stratum_for,
)


def _candidate(
    forum_id: str,
    *,
    year: int = 2024,
    area: str = "area-alpha",
    ratings: tuple[int, ...] = (3, 3),
    decision: str = "reject",
    listing: str = "ICLR 2024 rejected",
) -> Candidate:
    return Candidate(
        forum_id=forum_id,
        year=year,
        listing=listing,
        decision=decision,
        primary_area=area,
        ratings=ratings,
    )


def _pool() -> list[Candidate]:
    """Enough synthetic papers to fill every quota, spread across areas and years."""
    areas = ("area-alpha", "area-beta", "area-gamma", "area-delta", "area-epsilon", "area-zeta")
    plan = (
        ("clear_reject", (1, 3), "reject", "ICLR {year} rejected"),
        ("low", (3, 5), "reject", "ICLR {year} rejected"),
        ("borderline", (5, 6), "reject", "ICLR {year} rejected"),
        ("high", (6, 8), "accept", "ICLR {year} poster"),
        ("strong", (8, 8), "accept", "ICLR {year} oral"),
    )
    pool: list[Candidate] = []
    serial = 0
    for stratum, ratings, decision, listing in plan:
        quota = next(item[3] for item in STRATA if item[0] == stratum)
        for index in range(quota + 6):
            serial += 1
            year = 2024 if index % 2 == 0 else 2025
            pool.append(
                _candidate(
                    f"SYN{serial:04d}",
                    year=year,
                    area=areas[index % len(areas)],
                    ratings=ratings,
                    decision=decision,
                    listing=listing.format(year=year),
                )
            )
    return pool


class StratumTests(unittest.TestCase):
    def test_means_cover_rejects_borderline_and_strong_accepts(self) -> None:
        self.assertEqual(stratum_for(1.0), "clear_reject")
        self.assertEqual(stratum_for(3.9), "clear_reject")
        self.assertEqual(stratum_for(4.0), "low")
        self.assertEqual(stratum_for(5.0), "borderline")
        self.assertEqual(stratum_for(6.0), "borderline")
        self.assertEqual(stratum_for(6.5), "high")
        self.assertEqual(stratum_for(8.0), "strong")
        self.assertIsNone(stratum_for(11.0))

    def test_public_criteria_have_no_forum_ids(self) -> None:
        record = criteria_public()
        self.assertEqual(record["seed"], SEED)
        self.assertEqual(sum(item[3] for item in STRATA), 100)
        self.assertNotIn("forum_id", record)


class SelectionTests(unittest.TestCase):
    def test_selection_is_stable_and_fills_every_stratum(self) -> None:
        pool = _pool()
        forward = select_benchmark(pool, seed=SEED)
        backward = select_benchmark(list(reversed(pool)), seed=SEED)
        self.assertEqual([item.forum_id for item in forward.selected], [item.forum_id for item in backward.selected])
        self.assertEqual(forward.counts, {name: quota for name, _low, _high, quota in STRATA})
        self.assertEqual(sum(forward.shortfalls.values()), 0)
        self.assertEqual(len(forward.selected), 100)
        areas = {}
        for item in forward.by_stratum["strong"]:
            areas[item.primary_area] = areas.get(item.primary_area, 0) + 1
        self.assertTrue(all(count <= 4 for count in areas.values()))
        self.assertGreater(len(areas), 1)
        years = {item.year for item in forward.by_stratum["borderline"]}
        self.assertEqual(years, {2024, 2025})

    def test_a_single_rating_is_excluded(self) -> None:
        pool = [_candidate("SYN-ONE", ratings=(8,))]
        selection = select_benchmark(pool, seed=SEED, quotas={"strong": 1})
        self.assertEqual(selection.selected, ())
        self.assertEqual(selection.shortfalls["strong"], 1)

    def test_area_cap_relaxes_when_the_stratum_would_otherwise_be_short(self) -> None:
        pool = [
            _candidate(f"SYN{index:04d}", area="area-only", ratings=(8, 10), decision="accept")
            for index in range(5)
        ]
        selection = select_benchmark(pool, seed=SEED, quotas={"strong": 5}, max_per_area=2)
        self.assertEqual(len(selection.by_stratum["strong"]), 5)

    def test_split_is_sixty_forty_inside_each_stratum_and_ignores_input_order(self) -> None:
        selection = select_benchmark(_pool(), seed=SEED)
        tune, holdout = assign_split(list(selection.selected), seed=SEED)
        flipped, holdout_flipped = assign_split(list(reversed(selection.selected)), seed=SEED)
        self.assertEqual([item.forum_id for item in tune], [item.forum_id for item in flipped])
        self.assertEqual([item.forum_id for item in holdout], [item.forum_id for item in holdout_flipped])
        self.assertEqual(len(tune), 60)
        self.assertEqual(len(holdout), 40)
        overlap = {item.forum_id for item in tune} & {item.forum_id for item in holdout}
        self.assertEqual(overlap, set())
        tune_ids = {item.forum_id for item in tune}
        for name, _low, _high, quota in STRATA:
            members = [item for item in selection.by_stratum[name] if item.forum_id in tune_ids]
            self.assertEqual(len(members), int(round(quota * 0.6)))


if __name__ == "__main__":
    unittest.main()
