"""Aggregate rank metrics for a local calibration cache.

The cache, the id file, and ``--output`` stay outside the git checkout.
Stdout is aggregate metrics only. Per-paper rows are written to ``--output``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autopaperreview.fusion import SHIPPABLE, select_variant
from autopaperreview.public_calibration import assert_local_only
from autopaperreview.rank_metrics import candidate_table, format_interval


def _records(cache: Path, ids: set[str] | None) -> list[dict]:
    rows = []
    for score_path in sorted(cache.glob("papers/*/project/our_score.json")):
        score = json.loads(score_path.read_text(encoding="utf-8"))
        forum_id = str(score.get("forum_id") or "")
        if ids is not None and forum_id not in ids:
            continue
        human_path = score_path.parents[1] / "heldout" / "human.json"
        if not human_path.is_file():
            continue
        human = json.loads(human_path.read_text(encoding="utf-8"))
        decision = human.get("decision")
        if decision not in {"accept", "reject"}:
            continue
        ratings = [float(item) for item in human.get("ratings") or []]
        if len(ratings) < 2:
            continue
        rows.append(
            {
                "forum_id": forum_id,
                "decision": decision,
                "accept": decision == "accept",
                "human_mean": sum(ratings) / len(ratings),
                "rank_score": score.get("rank_score"),
                "our_bin": score.get("our_bin"),
                "candidates": score.get("candidates") or {},
                "usage_totals": score.get("usage_totals") or {},
                "holistic": score.get("holistic"),
                "caps": score.get("caps") or [],
            }
        )
    return rows


def _load_ids(path: Path | None, split: str | None) -> set[str] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if not split or split not in payload:
            raise SystemExit("an id file that is an object needs --split")
        return {str(item) for item in payload[split]}
    if isinstance(payload, list):
        return {str(item) for item in payload}
    raise SystemExit("id file must be a list or an object of lists")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="rank metrics for a local calibration cache")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--ids")
    parser.add_argument("--split")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("candidates", "locked"), default="candidates")
    parser.add_argument("--resamples", type=int, default=4000)
    args = parser.parse_args(argv)
    cache = assert_local_only(Path(args.cache))
    output = assert_local_only(Path(args.output))
    records = _records(cache, _load_ids(Path(args.ids) if args.ids else None, args.split))
    if len(records) < 2:
        print("not enough scored papers", file=sys.stderr)
        return 2

    def score_of(record: dict, name: str) -> float | None:
        if name == "shipped":
            value = record.get("rank_score")
            return None if value is None else float(value)
        value = (record.get("candidates") or {}).get(name)
        return None if value is None else float(value)

    names: tuple[str, ...] = ("shipped",) if args.mode == "locked" else SHIPPABLE
    table = candidate_table(records, score_of, names, resamples=args.resamples)
    summary = {
        name: {
            "spearman": row["point"]["spearman"],
            "auc": row["point"]["auc"],
        }
        for name, row in table.items()
    }
    chosen = None if args.mode == "locked" else select_variant(summary)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"chosen": chosen, "n": len(records), "metrics": table, "rows": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"n={len(records)} mode={args.mode}")
    for name in names:
        intervals = table[name]["intervals"]
        print(
            f"{name}\tSpearman {format_interval(intervals['spearman'])}\t"
            f"AUC {format_interval(intervals['auc'])}"
        )
    if chosen:
        print(f"selected={chosen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
