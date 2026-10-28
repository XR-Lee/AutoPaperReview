"""Aggregate published ClaimNov verdicts on the pilot sample.

Reads boolean ``novel`` fields and claim-disputed ranks only. Proposal text,
titles, and justifications are not written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.claimnov_pilot.metrics import bootstrap_rate, split_rows
from benchmarks.claimnov_pilot.select import digest

_DISGUISE_FILES = (
    "coupled_veto_smuggle_profile.jsonl",
    "smuggle_v2.jsonl",
    "smuggle_v2_api.jsonl",
    "smuggle_full.jsonl",
)
_LATER_FILES = (
    "coupled_veto_fa_profile.jsonl",
    "coupled_veto_ac_profile.jsonl",
    "positive_full.jsonl",
)
_HYBRID_FILES = ("hybrids_eval.jsonl",)
_CLAIM_FILES = (
    "coupled_veto_neg_profile.jsonl",
    "negative_full.jsonl",
)


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def join_key(item: dict) -> str:
    return str(item.get("item_id") or item.get("item_sha256") or "")


def keyed(dataset: str, raw: dict) -> dict:
    """Index a raw-id map by dataset plus the id and its sha256.

    The same ClaimNov id can be a later-realized idea and a hybrid. Those
    verdicts stay apart.
    """
    indexed = {}
    for item_id, value in raw.items():
        indexed[(dataset, item_id)] = value
        indexed[(dataset, digest(item_id))] = value
    return indexed


def _remember(
    store: dict[str, bool],
    dropped: set[str],
    item_id: str,
    novel: bool,
    conflicts: list[str],
) -> None:
    """Keep one boolean. A disagreement drops that id from the rate."""
    if item_id in dropped:
        return
    if item_id in store and store[item_id] != novel:
        conflicts.append(digest(item_id))
        store.pop(item_id, None)
        dropped.add(item_id)
        return
    store.setdefault(item_id, novel)


def load_gate_maps(paths: list[Path], *, default_gate: str | None = None) -> tuple[dict[str, dict[str, bool]], int]:
    """Map each gate to item-id to ``novel``. Returns how many ids were dropped."""
    gates: dict[str, dict[str, bool]] = {}
    dropped: dict[str, set[str]] = {}
    conflicts: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        for row in _jsonl(path):
            gate = row.get("gate") or default_gate
            if not gate or "item" not in row or "novel" not in row:
                continue
            name = str(gate)
            _remember(
                gates.setdefault(name, {}),
                dropped.setdefault(name, set()),
                str(row["item"]),
                bool(row["novel"]),
                conflicts,
            )
    return gates, len(conflicts)


def load_ranks(path: Path) -> dict[str, int | None]:
    ranks: dict[str, int | None] = {}
    if not path.is_file():
        return ranks
    for row in _jsonl(path):
        rank = row.get("B_rank")
        ranks[str(row["item"])] = rank if isinstance(rank, int) else None
    return ranks


def _rate(flags: list[bool]) -> dict:
    boot = bootstrap_rate(flags)
    if boot is None:
        return {"n": 0, "rate": None, "low": None, "high": None}
    return boot


def _rejected(items: list[dict], verdicts: dict[str, bool], predicate) -> tuple[list[bool], int]:
    flags: list[bool] = []
    missing = 0
    for item in items:
        if not predicate(item):
            continue
        key = (item.get("dataset"), join_key(item))
        if key not in verdicts:
            missing += 1
            continue
        flags.append(not verdicts[key])
    return flags, missing


def _kept(items: list[dict], verdicts: dict[str, bool]) -> tuple[list[bool], int]:
    flags: list[bool] = []
    missing = 0
    for item in items:
        if item.get("dataset") != "hybrids":
            continue
        key = (item.get("dataset"), join_key(item))
        if key not in verdicts:
            missing += 1
            continue
        flags.append(bool(verdicts[key]))
    return flags, missing


def _top1(items: list[dict], ranks: dict[tuple[str, str], int | None] | None) -> tuple[list[bool], int]:
    flags: list[bool] = []
    missing = 0
    for item in items:
        if item.get("dataset") != "claim_disputed":
            continue
        key = (item.get("dataset"), join_key(item))
        if not ranks or key not in ranks:
            missing += 1
            continue
        flags.append(ranks[key] == 1)
    return flags, missing


def system_report(
    items: list[dict],
    verdicts: dict[tuple[str, str], bool],
    ranks: dict[tuple[str, str], int | None] | None = None,
) -> dict:
    """Rejection is ``novel == false``. Hybrid keep is ``novel == true``."""
    catch, catch_missing = _rejected(items, verdicts, lambda item: item.get("label") == "non_novel")
    false_alarm, false_alarm_missing = _rejected(items, verdicts, lambda item: item.get("label") == "novel")
    src_out, src_out_missing = _rejected(items, verdicts, lambda item: item.get("label") == "src_out")
    keep, keep_missing = _kept(items, verdicts)
    accept, accept_missing = _rejected(
        items,
        verdicts,
        lambda item: item.get("dataset") == "claim_disputed" and item.get("decision") == "accept",
    )
    reject, reject_missing = _rejected(
        items,
        verdicts,
        lambda item: item.get("dataset") == "claim_disputed" and item.get("decision") == "reject",
    )
    report = {
        "catch": _rate(catch),
        "catch_missing": catch_missing,
        "false_alarm": _rate(false_alarm),
        "false_alarm_missing": false_alarm_missing,
        "src_out_rejection": _rate(src_out),
        "src_out_missing": src_out_missing,
        "hybrid_keep": _rate(keep),
        "hybrid_keep_missing": keep_missing,
        "claim_disputed_rejection_on_accept": _rate(accept),
        "claim_disputed_accept_missing": accept_missing,
        "claim_disputed_rejection_on_reject": _rate(reject),
        "claim_disputed_reject_missing": reject_missing,
    }
    if ranks is not None:
        top1, top1_missing = _top1(items, ranks)
        report["claim_disputed_top1"] = _rate(top1)
        report["claim_disputed_top1_missing"] = top1_missing
    return report


def _with_keys(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        row = dict(item)
        row["key"] = join_key(item)
        rows.append(row)
    return rows


def summarize(
    items: list[dict],
    series: dict[str, dict[tuple[str, str], bool]],
    ranks: dict[tuple[str, str], int | None] | None = None,
) -> dict:
    """Full-sample rates, plus the same stratified test half the APR cutoff uses."""
    prepared = _with_keys(items)
    _train, test = split_rows(prepared)
    claimnov = series.get("claimnov", {})
    baselines = {
        gate: {
            "full": system_report(prepared, verdicts, ranks if gate == "claimnov" else None),
            "test_half": system_report(test, verdicts, ranks if gate == "claimnov" else None),
        }
        for gate, verdicts in sorted(series.items())
        if gate != "claimnov"
    }
    return {
        "system": "claimnov_published",
        "apr_ran": False,
        "note": (
            "novel=false is a rejection. Rates use sampled items that have a published row. "
            "test_half is the stratified half with seed 20261028. No originality cutoff is fit."
        ),
        "n": len(items),
        "test_half_n": len(test),
        "claimnov": {
            "full": system_report(prepared, claimnov, ranks),
            "test_half": system_report(test, claimnov, ranks),
        },
        "baselines": baselines,
    }


def _existing(root: Path, names: tuple[str, ...]) -> list[Path]:
    directory = root / "eval" / "results"
    return [directory / name for name in names]


def build_report(root: Path, items: list[dict]) -> dict:
    dropped = 0
    series: dict[str, dict[tuple[str, str], bool]] = {}
    groups = (
        ("disguised", _existing(root, _DISGUISE_FILES), "claimnov"),
        ("later_realized", _existing(root, _LATER_FILES), "claimnov"),
        ("hybrids", _existing(root, _HYBRID_FILES), None),
        ("claim_disputed", _existing(root, _CLAIM_FILES), "claimnov"),
    )
    for dataset, paths, default_gate in groups:
        found, found_dropped = load_gate_maps(paths, default_gate=default_gate)
        dropped += found_dropped
        for gate, verdicts in found.items():
            bucket = series.setdefault(gate, {})
            for key, novel in keyed(dataset, verdicts).items():
                if key in bucket and bucket[key] != novel:
                    dropped += 1
                    bucket.pop(key, None)
                    continue
                bucket.setdefault(key, novel)
    ranks = keyed(
        "claim_disputed",
        load_ranks(root / "eval" / "results" / "coupled_veto_neg_profile.jsonl"),
    )
    report = summarize(items, series, ranks)
    report["verdicts_dropped_for_disagreement"] = dropped
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claimnov", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    document = json.loads(args.sample.read_text(encoding="utf-8"))
    report = build_report(args.claimnov, document["items"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    claimnov = report["claimnov"]["full"]
    print(
        json.dumps(
            {
                "n": report["n"],
                "catch_n": claimnov["catch"]["n"],
                "false_alarm_n": claimnov["false_alarm"]["n"],
                "hybrid_keep_n": claimnov["hybrid_keep"]["n"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
