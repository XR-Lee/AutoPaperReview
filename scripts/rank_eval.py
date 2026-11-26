"""Aggregate rank metrics for a local calibration cache.

The cache, the id file, and ``--output`` stay outside the git checkout.
Stdout is aggregate metrics only. Per-paper rows are written to ``--output``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from autopaperreview.fixed_eval import (
    FORECAST_K1,
    RANK_BOOTSTRAP,
    FixedEvalError,
    assert_holdout_file,
    assert_manifest,
    assert_model_recorded,
    dev_ids,
    load_baseline_cache,
    manifest_path,
)
from autopaperreview.fusion import DIAGNOSTIC, LOCKED_CANDIDATE, SHIPPABLE, selection_report
from autopaperreview.pipeline import _atomic_write
from autopaperreview.proposal.data import require_distinct_output
from autopaperreview.public_calibration import assert_local_only
from autopaperreview.rank_metrics import candidate_table, format_interval


def _records(
    cache: Path,
    ids: set[str] | None,
    baseline_scores: dict[str, dict] | None = None,
) -> list[dict]:
    rows = []
    seen: set[str] = set()
    indexed: list[tuple[str, dict, Path]] = []
    for score_path in sorted(cache.glob("papers/*/project/our_score.json")):
        score = json.loads(score_path.read_text(encoding="utf-8"))
        forum_id = str(score.get("forum_id") or score_path.parents[1].name)
        indexed.append((forum_id, score, score_path.parents[1] / "heldout" / "human.json"))
    if baseline_scores:
        for forum_id, score in baseline_scores.items():
            if any(item[0] == forum_id for item in indexed):
                continue
            indexed.append((forum_id, score, cache / "papers" / forum_id / "heldout" / "human.json"))
    for forum_id, score, human_path in indexed:
        if ids is not None and forum_id not in ids:
            continue
        if forum_id in seen:
            continue
        if not human_path.is_file():
            continue
        human = json.loads(human_path.read_text(encoding="utf-8"))
        decision = human.get("decision")
        if decision not in {"accept", "reject"}:
            continue
        ratings = [float(item) for item in human.get("ratings") or []]
        if len(ratings) < 2:
            continue
        seen.add(forum_id)
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
                "isolate_samples": score.get("isolate_samples") or [],
                "audited_samples": score.get("audited_samples") or [],
                "checks_samples": score.get("checks_samples") or [],
                "expert_samples": score.get("expert_samples") or [],
                "form_samples": score.get("form_samples") or [],
                "forecast_samples": score.get("forecast_samples") or [],
                "vote_samples": score.get("vote_samples") or [],
                "rebuttal_samples": score.get("rebuttal_samples") or [],
                "initial_samples": score.get("initial_samples") or [],
                "matched_samples": score.get("matched_samples") or [],
                "panel_samples": score.get("panel_samples") or [],
                "chair_samples": score.get("chair_samples") or [],
                "high_samples": score.get("high_samples") or [],
                "low_samples": score.get("low_samples") or [],
                "audit": score.get("audit") or [],
                "expert": score.get("expert") or [],
                "panel": score.get("panel") or [],
                "matched": score.get("matched") or [],
                "rebuttal": score.get("rebuttal") or [],
                "forecast": score.get("forecast") or [],
                "main_text": score.get("main_text") or {},
                "holistic_mode": score.get("holistic_mode"),
                **({"grounded_weaknesses": score["grounded_weaknesses"]} if "grounded_weaknesses" in score else {}),
                "placements": score.get("placements") or [],
                "poles": score.get("poles") or [],
                "caps": score.get("caps") or [],
                "drop_counts": score.get("drop_counts") or {},
            }
        )
    return rows


def _dev_stamp(cache: Path) -> dict | None:
    try:
        stamp = json.loads((cache / "fixed-eval-run.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise FixedEvalError("fixed-run stamp cannot be read") from exc
    if not isinstance(stamp, dict):
        raise FixedEvalError("invalid fixed-run stamp")
    if stamp.get("run_state", "complete") != "complete":
        raise FixedEvalError("fixed-run stamp records an incomplete run")
    if "run_state" in stamp or "run_id" in stamp:
        if stamp.get("run_state") != "complete" or not re.fullmatch(r"[0-9a-f]{32}", str(stamp.get("run_id"))):
            raise FixedEvalError("invalid fixed-run identity")
    return stamp


def _check_fixed_prompt(records: list[dict], meta: dict, stamp: dict | None) -> None:
    flags = [row.get("grounded_weaknesses", False) for row in records]
    if any(type(flag) is not bool for flag in flags):
        raise FixedEvalError("invalid fixed-set forecast prompt flag")
    if len(set(flags)) > 1:
        raise FixedEvalError("fixed set mixes different forecast prompts")
    if meta["fixed_set"] != "iclr-dev" or stamp is None:
        return
    if any(stamp.get(k) != meta[k] for k in ("fixed_set", "content_sha256", "model_id", "effort", "status")):
        raise FixedEvalError("fixed-run stamp does not match this confirmation")
    for field in ("cheap", "coefficient_set", "holistic_mode", "holistic_samples", "related_work"):
        value = stamp.get(field)
        if type(value) is not type(FORECAST_K1[field]) or value != FORECAST_K1[field]:
            raise FixedEvalError("invalid forecast settings in fixed-run stamp")
    grounded = stamp.get("grounded_weaknesses", False)
    if type(grounded) is not bool or grounded != any(flags):
        raise FixedEvalError("fixed-run stamp does not match the scored forecast prompt")


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
    parser.add_argument(
        "--mode",
        choices=("candidates", "locked"),
        default="locked",
        help="locked reports the shipped rank (the preregistered candidate); candidates is diagnostic",
    )
    parser.add_argument("--resamples", type=int, default=4000)
    parser.add_argument(
        "--fixed-set",
        choices=("iclr-dev", "none"),
        default="iclr-dev",
        help="iclr-dev scores the manifest dev set (192 papers, 48 per tier); none scores the cache as given",
    )
    parser.add_argument("--model-id", default="", help="required when --fixed-set iclr-dev")
    parser.add_argument("--effort", default="", help="required when --fixed-set iclr-dev")
    parser.add_argument(
        "--holdout-file",
        default="",
        help="gitignored private-holdout operator file (24 papers, 6 per tier); scores that slice instead of the dev set",
    )
    parser.add_argument(
        "--baseline-cache",
        default="",
        help="stored forecast-k1 scores (metadata.json plus papers/*/our_score.json) used when the local cache has no score",
    )
    parser.add_argument("--baseline-commit", default="", help="commit the baseline cache must name")
    parser.add_argument(
        "--baseline-digest",
        default="",
        help="manifest digest the baseline cache must name when --fixed-set is none",
    )
    args = parser.parse_args(argv)
    cache = assert_local_only(Path(args.cache))
    output = assert_local_only(Path(args.output))
    inputs = [Path(value) for value in (args.ids, args.holdout_file) if value]
    if args.fixed_set == "iclr-dev":
        inputs.append(manifest_path())
    inputs.append(cache / "fixed-eval-run.json")
    inputs.extend(cache.glob("papers/*/project/our_score.json"))
    inputs.extend(cache.glob("papers/*/heldout/human.json"))
    if args.baseline_cache:
        baseline = Path(args.baseline_cache).expanduser()
        inputs.append(baseline / "metadata.json")
        inputs.extend(baseline.glob("papers/*/our_score.json"))
    try:
        require_distinct_output(output, *inputs)
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    fixed_meta: dict | None = None
    try:
        if args.fixed_set == "iclr-dev":
            manifest = assert_manifest()
            model_id, effort = assert_model_recorded(args.model_id, args.effort)
            if args.resamples != RANK_BOOTSTRAP["resamples"]:
                raise FixedEvalError(
                    f"fixed-set rank bootstrap resamples must be {RANK_BOOTSTRAP['resamples']}"
                )
            if args.holdout_file:
                if args.ids:
                    raise FixedEvalError("do not pass --ids with --holdout-file")
                holdout_papers = assert_holdout_file(assert_local_only(Path(args.holdout_file)), manifest)
                wanted = {str(paper["forum_id"]) for paper in holdout_papers}
                strata = {str(paper["forum_id"]): str(paper["tier"]) for paper in holdout_papers}
                slice_name = "iclr-holdout"
            else:
                if args.ids:
                    given = _load_ids(Path(args.ids), args.split)
                    if given != set(dev_ids(manifest)):
                        raise FixedEvalError("ICLR run ids do not match the fixed dev set")
                wanted = set(dev_ids(manifest))
                strata = {str(paper["forum_id"]): str(paper["tier"]) for paper in manifest["iclr"]["papers"]}
                slice_name = "iclr-dev"
            fixed_meta = {
                "bootstrap": dict(RANK_BOOTSTRAP),
                "content_sha256": manifest["content_sha256"],
                "effort": effort,
                "fixed_set": slice_name,
                "model_id": model_id,
                "status": manifest["status"],
            }
        else:
            if args.holdout_file:
                raise FixedEvalError("--holdout-file requires --fixed-set iclr-dev")
            wanted = _load_ids(Path(args.ids) if args.ids else None, args.split)
            strata = {}
    except FixedEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    baseline_scores = None
    if args.baseline_cache:
        if not args.baseline_commit.strip():
            print("baseline cache requires --baseline-commit", file=sys.stderr)
            return 2
        if not args.model_id.strip() or not args.effort.strip():
            print("baseline cache requires --model-id and --effort", file=sys.stderr)
            return 2
        digest = fixed_meta["content_sha256"] if fixed_meta is not None else args.baseline_digest.strip()
        if not digest:
            print("baseline cache requires --baseline-digest when --fixed-set is none", file=sys.stderr)
            return 2
        try:
            loaded = load_baseline_cache(
                assert_local_only(Path(args.baseline_cache)),
                commit=args.baseline_commit.strip(),
                model_id=args.model_id.strip(),
                effort=args.effort.strip(),
                content_sha256=digest,
            )
        except FixedEvalError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        baseline_scores = loaded["scores"]
    dev_confirmation = fixed_meta is not None and fixed_meta["fixed_set"] == "iclr-dev"
    try:
        stamp = _dev_stamp(cache) if dev_confirmation else None
        records = _records(cache, wanted, baseline_scores)
        if dev_confirmation and stamp != _dev_stamp(cache):
            raise FixedEvalError("fixed-run stamp changed while reading scores")
        if fixed_meta is not None:
            _check_fixed_prompt(records, fixed_meta, stamp)
    except FixedEvalError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if fixed_meta is not None:
        scored = {str(record["forum_id"]) for record in records}
        if scored != wanted:
            print(
                f"fixed set incomplete: scored {len(scored)} of {len(wanted or ())}",
                file=sys.stderr,
            )
            return 2
        for record in records:
            record["stratum"] = strata.get(str(record["forum_id"]))
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
    body = {
        "preregistered": LOCKED_CANDIDATE,
        "chosen": chosen,
        "tied": tied,
        "n": len(records),
        "metrics": table,
        "rows": records,
    }
    if fixed_meta is not None:
        body.update(fixed_meta)
    grounded_count = sum(bool(row.get("grounded_weaknesses")) for row in records)
    if dev_confirmation and stamp and "run_id" in stamp:
        body["fixed_run_id"] = stamp["run_id"]
    if grounded_count:
        body["grounded_weaknesses_records"] = grounded_count
        body["forecast_prompt"] = "grounded-weaknesses" if grounded_count == len(records) else "mixed"
    try:
        require_distinct_output(output, *inputs)
        _atomic_write(output, json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"preregistered={LOCKED_CANDIDATE}")
    if fixed_meta is not None:
        print(
            f"fixed_set={fixed_meta['fixed_set']} status={fixed_meta['status']} "
            f"digest={fixed_meta['content_sha256']} model={fixed_meta['model_id']} "
            f"effort={fixed_meta['effort']}"
        )
    print(f"n={len(records)} mode={args.mode}")
    if grounded_count:
        print(f"grounded_weaknesses_records={grounded_count}/{len(records)}")
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
