"""Aggregate rank metrics for a local calibration cache.

The cache, the id file, and ``--output`` stay outside the git checkout.
Stdout is aggregate metrics only. Per-paper rows are written to ``--output``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autopaperreview.fusion import DIAGNOSTIC, SHIPPABLE, selection_report
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
                "samples": score.get("samples") or [],
                "mean_samples": score.get("mean_samples") or [],
                "place_samples": score.get("place_samples") or [],
                "count_samples": score.get("count_samples") or [],
                "placements": score.get("placements") or [],
                "caps": score.get("caps") or [],
                "drop_counts": score.get("drop_counts") or {},
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

    if args.mode == "locked":
        names: tuple[str, ...] = ("shipped",)
    else:
        present = [
            name
            for name in DIAGNOSTIC
            if any((record.get("candidates") or {}).get(name) is not None for record in records)
        ]
        names = (*SHIPPABLE, *present)
    table = candidate_table(records, score_of, names, resamples=args.resamples)
    summary = {
        name: {
            "spearman": row["point"]["spearman"],
            "auc": row["point"]["auc"],
        }
        for name, row in table.items()
    }
    report = None if args.mode == "locked" else selection_report(summary)
    chosen = None if report is None else report["chosen"]
    tied = [] if report is None else list(report["tied"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "chosen": chosen,
                "tied": tied,
                "n": len(records),
                "metrics": table,
                "rows": records,
            },
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
        if report and report.get("rule"):
            print(f"rule={report['rule']}")
        if report and report.get("cap_adopted"):
            print("cap_adopted=yes")
        if len(tied) > 1:
            print("tied=" + ",".join(str(name) for name in tied))
    drops: dict[str, int] = {}
    for record in records:
        for key, count in (record.get("drop_counts") or {}).items():
            drops[str(key)] = drops.get(str(key), 0) + int(count)
    if drops:
        print("drops " + " ".join(f"{key}={drops[key]}" for key in sorted(drops)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
