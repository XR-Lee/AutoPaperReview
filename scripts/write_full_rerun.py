#!/usr/bin/env python3
"""Write the full-dev anonymized-rerun report. Aggregates only."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from autopaperreview.fixed_eval import dev_papers  # noqa: E402
from autopaperreview.iclr_anonymize import (  # noqa: E402
    _ACK_HEAD,
    _AFFILIATION,
    _EMAIL,
    _GITHUB,
    _STATUS_INLINE,
    _STATUS_LINE,
    anonymize_iclr_submission,
)
from benchmarks.promotion.anonymized_delta import paired_deltas  # noqa: E402
from benchmarks.promotion.forecast_recovery import numeric_forecast  # noqa: E402
from benchmarks.promotion.paired_bootstrap import RESAMPLES, SEED, linear_percentile, stratified_indexes  # noqa: E402
from benchmarks.promotion.pdftotext_norm import collapse_smallcaps  # noqa: E402
from benchmarks.promotion.reanalysis_v2 import (  # noqa: E402
    PAPER_COPILOT,
    TIER_ORDER,
    format_interval,
    mixture_tier_probabilities,
    pooled_statuses,
)

REPORT = ROOT / "benchmarks/promotion/reports/2026-11-26-full-dev192-rerun.md"
OLD_SOURCES = ROOT / "workdir/leak-audit/papers"
NEW_ROOT = ROOT / "workdir/missing-text"
NEW_ANSWERS = ROOT / "workdir/full-rerun/answers"
OLD_ANSWERS = ROOT / "workdir/anon-rerun/answers"
SCORES = ROOT / "workdir/reanalysis/scores.json"
ASSIGNMENT = ROOT / "workdir/full-rerun/assignment.json"
COVERAGE = ROOT / "workdir/full-rerun/coverage.json"
OLD_MANIFEST = ROOT / "workdir/anon-rerun/manifest.json"


def _fmt(interval: dict | None) -> str:
    return format_interval(interval)


def _num(value: float | None) -> str:
    if value is None:
        return "undefined"
    return f"{float(value):.4f}"


def _flips(rows: list[dict]) -> str:
    changed = sum(int(row["bin_raw"]) != int(row["bin_anon"]) for row in rows)
    return f"{changed}/{len(rows)}"


def _flip_diff(published_rows: list[dict], anonymous_rows: list[dict]) -> dict:
    """Published flip rate minus anonymous flip rate. The slices are separate papers."""

    def flags(rows: list[dict]) -> list[float]:
        return [1.0 if int(row["bin_raw"]) != int(row["bin_anon"]) else 0.0 for row in rows]

    left = flags(published_rows)
    right = flags(anonymous_rows)
    if not left or not right:
        return {"point": None, "low": None, "high": None}
    point = sum(left) / len(left) - sum(right) / len(right)
    draws_left = stratified_indexes([str(row["stratum"]) for row in published_rows], resamples=RESAMPLES, seed=SEED)
    draws_right = stratified_indexes([str(row["stratum"]) for row in anonymous_rows], resamples=RESAMPLES, seed=SEED + 1)
    samples = []
    for left_index, right_index in zip(draws_left, draws_right, strict=True):
        left_mean = sum(left[index] for index in left_index) / len(left_index)
        right_mean = sum(right[index] for index in right_index) / len(right_index)
        samples.append(left_mean - right_mean)
    ordered = sorted(samples)
    return {
        "point": point,
        "low": linear_percentile(ordered, 0.025),
        "high": linear_percentile(ordered, 0.975),
    }


def _removal_summary(assignment: list[dict]) -> str:
    """Aggregate characters the anonymizer drops, by PDF banner. No text is kept."""
    removed: dict[str, list[int]] = {}
    for record in assignment:
        raw = collapse_smallcaps((NEW_ROOT / "txt" / f"{record['sha']}.txt").read_text(encoding="utf-8", errors="replace"))
        anon, _counts = anonymize_iclr_submission(raw)
        removed.setdefault(str(record["kind"]), []).append(len(raw) - len(anon))
    parts = []
    for kind in ("published", "anonymous", "neither"):
        values = sorted(removed.get(kind, []))
        if not values:
            continue
        middle = values[len(values) // 2]
        mean = sum(values) / len(values)
        parts.append(f"{kind} median {middle} characters (mean {mean:.0f}, n={len(values)})")
    return "Characters removed by the anonymizer, after the small-caps join: " + "; ".join(parts) + "."


def _level(interval: dict) -> str:
    """Direction of anonymized minus raw. Positive means the anonymized text scored higher."""
    side = _side(interval, higher_better=True)
    if side == "includes zero":
        return "includes zero"
    if side == "favors the second score":
        return "is positive, so the anonymized text scored higher"
    if side == "favors the first score":
        return "is negative, so the anonymized text scored lower"
    return "is undefined"


def _side(interval: dict, *, higher_better: bool) -> str:
    low = interval.get("low")
    high = interval.get("high")
    if low is None or high is None:
        return "undefined"
    if low <= 0 <= high:
        return "includes zero"
    favors_second = (low > 0) if higher_better else (high < 0)
    if favors_second:
        return "favors the second score"
    return "favors the first score"


def _reading(label: str, interval: dict, *, higher_better: bool, first: str, second: str) -> str:
    side = _side(interval, higher_better=higher_better)
    if side == "includes zero":
        where = "includes zero"
    elif side == "favors the second score":
        where = f"lies where {second} scores better"
    elif side == "favors the first score":
        where = f"lies where {first} scores better"
    else:
        where = "is undefined"
    return f"{label} is {_fmt(interval)}; the interval {where}."


def _paper_flags(text: str) -> dict[str, int]:
    lines = text.splitlines()
    head = "\n".join(lines[:40])
    return {
        "email": int(_EMAIL.search(text) is not None),
        "github": int(_GITHUB.search(text) is not None),
        "status": int(bool(_STATUS_LINE.search(text) or _STATUS_INLINE.search(text))),
        "ack": int(any(_ACK_HEAD.match(line.strip()) for line in lines)),
        "aff40": int(_AFFILIATION.search(head) is not None),
    }


def _leak_rows(papers: list[dict]) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Paper-level marker counts for the recovered Gate 1 sources and the new texts."""
    by_forum = {str(paper["forum_id"]): (index, paper) for index, paper in enumerate(papers)}
    old = {tier: Counter() for tier in TIER_ORDER}
    new = {tier: Counter() for tier in TIER_ORDER}
    for path in OLD_SOURCES.glob("*/source.txt"):
        found = by_forum.get(path.parent.name)
        if found is None:
            continue
        _index, paper = found
        raw = path.read_text(encoding="utf-8", errors="replace")
        anon, _removed = anonymize_iclr_submission(raw)
        bucket = old[str(paper["tier"])]
        bucket["papers"] += 1
        for key, value in _paper_flags(raw).items():
            bucket[key] += value
        for key, value in _paper_flags(anon).items():
            bucket[key + "_after"] += value
    assignment = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
    for record in assignment:
        raw = collapse_smallcaps((NEW_ROOT / "txt" / f"{record['sha']}.txt").read_text(encoding="utf-8", errors="replace"))
        anon, _removed = anonymize_iclr_submission(raw)
        bucket = new[str(record["tier"])]
        bucket["papers"] += 1
        bucket[record["kind"]] += 1
        for key, value in _paper_flags(raw).items():
            bucket[key] += value
        for key, value in _paper_flags(anon).items():
            bucket[key + "_after"] += value
    return old, new


def _forecast(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return numeric_forecast(json.loads(path.read_text(encoding="utf-8")))


def _rows(papers: list[dict], arm: str, raw_by_index: dict[int, dict], new_indexes: set[int], kinds: dict[int, str]) -> tuple[list[dict], list[str]]:
    old_indexes = set(json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))["indexes"])
    rows = []
    missing = []
    for index, paper in enumerate(papers):
        if index in new_indexes:
            raw = _forecast(NEW_ANSWERS / "raw" / arm / f"{index:03d}.json")
            anon = _forecast(NEW_ANSWERS / "anon" / arm / f"{index:03d}.json")
            origin = "fresh"
        elif index in old_indexes:
            stored = raw_by_index.get(index)
            anon = _forecast(OLD_ANSWERS / arm / f"{index:03d}.json")
            raw = None if stored is None else {"rank_score": stored["rank_score"], "our_bin": stored["our_bin"]}
            origin = "gate1"
        else:
            missing.append(f"{arm}:{index}:unassigned")
            continue
        if not raw or not anon:
            missing.append(f"{arm}:{index}:{origin}")
            continue
        rows.append(
            {
                "tier": paper["tier"],
                "stratum": paper["tier"],
                "human_mean": float(paper["mean_rating"]),
                "decision": paper["decision"],
                "ratings": [float(item) for item in paper["ratings"]],
                "rank_raw": float(raw["rank_score"]),
                "rank_anon": float(anon["rank_score"]),
                "bin_raw": int(raw["our_bin"]),
                "bin_anon": int(anon["our_bin"]),
                "index": index,
                "origin": origin,
                "kind": kinds.get(index, "recovered"),
            }
        )
    return rows, missing


def _contrast(candidate: list[dict], main: list[dict], *, condition: str) -> list[dict]:
    main_by_index = {int(row["index"]): row for row in main}
    score_key = "rank_" + condition
    bin_key = "bin_" + condition
    rows = []
    for row in candidate:
        other = main_by_index.get(int(row["index"]))
        if other is None:
            continue
        rows.append(
            {
                "tier": row["tier"],
                "stratum": row["tier"],
                "human_mean": row["human_mean"],
                "decision": row["decision"],
                "ratings": row["ratings"],
                "rank_raw": other[score_key],
                "rank_anon": row[score_key],
                "bin_raw": other[bin_key],
                "bin_anon": row[bin_key],
            }
        )
    return rows


def _metric_table(result: dict, *, left: str, right: str, delta: str) -> str:
    lines = [
        f"| Metric | {left} | {right} | {delta} |",
        "| --- | ---: | ---: | ---: |",
    ]
    order = [
        ("Spearman", "spearman"),
        ("IPW Spearman", "ipw_spearman"),
        ("Accept/reject AUC", "auc"),
        ("MAE", "mae"),
        ("Cohen kappa, venue bin", "kappa_venue"),
        ("Quadratic weighted kappa, venue bin", "qwk_venue"),
        ("LORO Spearman gap", "loro_spearman_gap"),
    ]
    for label, key in order:
        lines.append(f"| {label} | {_num(result['raw'].get(key))} | {_num(result['anonymized'].get(key))} | {_fmt(result['delta'].get(key))} |")
    for low, high in zip(TIER_ORDER, TIER_ORDER[1:], strict=False):
        key = f"adjacent:{low} vs {high}"
        label = f"Adjacent AUC, {low.replace('_', ' ')} vs {high.replace('_', ' ')}"
        raw = result["raw"].get(key)
        anon = result["anonymized"].get(key)
        raw_text = "undefined" if raw is None else f"{raw:.4f}"
        anon_text = "undefined" if anon is None else f"{anon:.4f}"
        lines.append(f"| {label} | {raw_text} | {anon_text} | {_fmt(result['delta'].get(key))} |")
    return "\n".join(lines)


def _score_shift(rows: list[dict]) -> dict:
    point_rows = [row for row in rows if row.get("rank_raw") is not None and row.get("rank_anon") is not None]
    if not point_rows:
        return {"point": None, "low": None, "high": None}
    shifts = [float(row["rank_anon"]) - float(row["rank_raw"]) for row in point_rows]
    point = sum(shifts) / len(shifts)
    draws = stratified_indexes([str(row["stratum"]) for row in point_rows], resamples=RESAMPLES, seed=SEED)
    samples = []
    for indexes in draws:
        chosen = [shifts[index] for index in indexes]
        samples.append(sum(chosen) / len(chosen))
    ordered = sorted(samples)
    return {
        "point": point,
        "low": linear_percentile(ordered, 0.025),
        "high": linear_percentile(ordered, 0.975),
        "n": len(point_rows),
    }


def _leak_line(bucket: Counter, tier: str) -> str:
    papers = int(bucket.get("papers", 0))
    return (
        f"| {tier.replace('_', ' ')} | {papers} | {int(bucket.get('email', 0))} | {int(bucket.get('github', 0))} | "
        f"{int(bucket.get('status', 0))} | {int(bucket.get('ack', 0))} | {int(bucket.get('aff40', 0))} | "
        f"{int(bucket.get('email_after', 0))} | {int(bucket.get('github_after', 0))} | {int(bucket.get('status_after', 0))} | "
        f"{int(bucket.get('ack_after', 0))} | {int(bucket.get('aff40_after', 0))} |"
    )


def _pooled_line(old: Counter, new: Counter, tier: str) -> str:
    papers = int(old.get("papers", 0)) + int(new.get("papers", 0))
    keys = ("email", "github", "status", "ack", "aff40", "email_after", "github_after", "status_after", "ack_after", "aff40_after")
    values = [int(old.get(key, 0)) + int(new.get(key, 0)) for key in keys]
    cells = " | ".join(str(value) for value in values)
    return f"| {tier.replace('_', ' ')} | {papers} | {cells} |"


def main() -> int:
    papers = dev_papers()
    stored = json.loads(SCORES.read_text(encoding="utf-8"))
    raw_by_arm: dict[str, dict[int, dict]] = {"candidate": {}, "main": {}}
    for record in stored:
        raw_by_arm[record["arm"]][int(record["index"])] = record
    assignment = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
    new_indexes = {int(record["index"]) for record in assignment}
    kinds = {int(record["index"]): str(record["kind"]) for record in assignment}
    population = mixture_tier_probabilities(pooled_statuses((2024, 2025), include_withdraw=True))["tiers"]
    arms = {}
    missing = []
    for arm in ("candidate", "main"):
        rows, absent = _rows(papers, arm, raw_by_arm[arm], new_indexes, kinds)
        arms[arm] = rows
        missing.extend(absent)
    fresh_missing = [item for item in missing if item.endswith(":fresh") or ":unassigned" in item]
    if fresh_missing:
        raise SystemExit("missing fresh forecasts: " + ",".join(fresh_missing[:20]) + f" ({len(fresh_missing)})")
    gate_missing = [item for item in missing if item.endswith(":gate1")]
    results = {arm: paired_deltas(rows, population=population) for arm, rows in arms.items()}
    fresh = {arm: [row for row in rows if row["origin"] == "fresh"] for arm, rows in arms.items()}
    fresh_results = {arm: paired_deltas(rows, population=population) for arm, rows in fresh.items()}
    published = {arm: [row for row in rows if row["kind"] == "published"] for arm, rows in fresh.items()}
    anonymous = {arm: [row for row in rows if row["kind"] == "anonymous"] for arm, rows in fresh.items()}
    published_results = {arm: paired_deltas(rows, population=population) for arm, rows in published.items()}
    anonymous_results = {arm: paired_deltas(rows, population=population) for arm, rows in anonymous.items()}
    shared = set(row["index"] for row in arms["candidate"]) & set(row["index"] for row in arms["main"])
    contrasts = {
        "raw": paired_deltas(_contrast(arms["candidate"], arms["main"], condition="raw"), population=population),
        "anonymized": paired_deltas(_contrast(arms["candidate"], arms["main"], condition="anon"), population=population),
    }
    if contrasts["raw"]["n"] != len(shared):
        raise SystemExit(f"contrast n {contrasts['raw']['n']} != shared {len(shared)}")
    shifts = {arm: _score_shift(rows) for arm, rows in arms.items()}
    fresh_shifts = {arm: _score_shift(rows) for arm, rows in fresh.items()}
    published_shifts = {arm: _score_shift(rows) for arm, rows in published.items()}
    anonymous_shifts = {arm: _score_shift(rows) for arm, rows in anonymous.items()}
    flip_gaps = {arm: _flip_diff(published[arm], anonymous[arm]) for arm in ("candidate", "main")}
    decision_kind: Counter = Counter()
    for index, paper in enumerate(papers):
        kind = kinds.get(index)
        if kind is None:
            continue
        decision_kind[(kind, str(paper["decision"]))] += 1
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    removal_sentence = _removal_summary(assignment)
    old_leak, new_leak = _leak_rows(papers)
    kind_counts = Counter(record["kind"] for record in assignment)
    kind_tier = Counter((record["tier"], record["kind"]) for record in assignment)
    fresh_n = len(fresh["candidate"])
    gate_n = sum(1 for row in arms["candidate"] if row["origin"] == "gate1")
    gap_note = ""
    if len(gate_missing) == 1:
        gap_note = " One original Gate 1 forecast was never recovered, so that arm-paper pair is absent."
    elif gate_missing:
        gap_note = f" {len(gate_missing)} original Gate 1 forecasts were never recovered, so those arm-paper pairs are absent."
    neither = int(kind_counts["neither"])
    neither_clause = "1 says neither" if neither == 1 else f"{neither} say neither"
    neither_paper = "One paper has neither banner" if neither == 1 else f"{neither} papers have neither banner"
    anon_clear = int(kind_tier[("clear_accept", "anonymous")])
    one_paper = ""
    if anon_clear == 1:
        one_paper = " The anonymous clear-accept tier in this batch is one paper, so the adjacent AUC against that tier is a one-paper comparison."
    published_accepts = int(decision_kind[("published", "accept")])
    published_rejects = int(decision_kind[("published", "reject")])
    anonymous_accepts = int(decision_kind[("anonymous", "accept")])
    anonymous_rejects = int(decision_kind[("anonymous", "reject")])
    if published_rejects == 0 and anonymous_accepts == 0 and published_accepts and anonymous_rejects:
        split_sentence = (
            f"In this batch the banner and the venue decision are the same split: "
            f"all {published_accepts} published PDFs are accepts, and all {anonymous_rejects} anonymous PDFs are rejects."
        )
    else:
        split_sentence = (
            f"Published PDFs are {published_accepts} accepts and {published_rejects} rejects. "
            f"Anonymous PDFs are {anonymous_accepts} accepts and {anonymous_rejects} rejects."
        )

    def block(title: str, result: dict) -> str:
        return f"{title}, n={result['n']}.\n\n{_metric_table(result, left='Raw', right='Anonymized', delta='Anonymized − raw')}\n"

    candidate = results["candidate"]
    main_arm = results["main"]
    text = f"""# Full dev set after the missing texts

Date: 2026-11-26. Aggregates only. The private holdout was not read. Sealed sets S24-C1 and S24-C2 were not opened. OpenReview was not called from this machine. The new texts were supplied as pdftotext from a browser download.

## Mapping

Mapped {int(coverage['mapped'])}, unmatched {int(coverage['extra_unmatched_files'])}, missing {int(coverage['missing_targets'])}. Filename prefixes match the mapped dev papers, one each. The unmatched file matches none of the 192 titles and is left out. The mapped papers are the ones whose Gate 1 prompt text was empty: clear reject {kind_tier[('clear_reject','anonymous')] + kind_tier[('clear_reject','published')] + kind_tier[('clear_reject','neither')]}, borderline reject {kind_tier[('borderline_reject','anonymous')] + kind_tier[('borderline_reject','published')] + kind_tier[('borderline_reject','neither')]}, borderline accept {kind_tier[('borderline_accept','anonymous')] + kind_tier[('borderline_accept','published')] + kind_tier[('borderline_accept','neither')]}, clear accept {kind_tier[('clear_accept','anonymous')] + kind_tier[('clear_accept','published')] + kind_tier[('clear_accept','neither')]}.

pdftotext split small-caps after the first letter (`A BSTRACT`). Those letters are joined before the audit and before the forecast. A normal sentence is not joined. After that join, every new file has an Abstract heading the anonymizer can see.

Of the mapped papers, {kind_counts['anonymous']} still say anonymous authors, {kind_counts['published']} say published as a conference paper, and {neither_clause}. {split_sentence}

| PDF banner | Accept | Reject |
| --- | ---: | ---: |
| anonymous | {int(decision_kind[('anonymous', 'accept')])} | {int(decision_kind[('anonymous', 'reject')])} |
| published | {int(decision_kind[('published', 'accept')])} | {int(decision_kind[('published', 'reject')])} |
| neither | {int(decision_kind[('neither', 'accept')])} | {int(decision_kind[('neither', 'reject')])} |

{removal_sentence} Published copies by rating tier: {kind_tier[('clear_accept','published')]} in the clear-accept tier and {kind_tier[('borderline_accept','published')]} in the borderline-accept tier. The clear-reject texts are anonymous submissions.

## Leak markers

Counts are papers with at least one marker. The first block is the 123 Gate 1 source extracts. The second is the 69 new texts after the small-caps join. The third adds them. Status on the new texts is mostly the venue banner (`Under review` or `Published as`), which the Gate 1 extracts usually did not keep, so the pooled status column is not a camera-ready count. Emails, named GitHub links, acknowledgement headings, and affiliation words in the first 40 lines are the comparable columns.

Recovered Gate 1 sources.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{_leak_line(old_leak['clear_reject'], 'clear_reject')}
{_leak_line(old_leak['borderline_reject'], 'borderline_reject')}
{_leak_line(old_leak['borderline_accept'], 'borderline_accept')}
{_leak_line(old_leak['clear_accept'], 'clear_accept')}

New pdftotext, after the small-caps join.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{_leak_line(new_leak['clear_reject'], 'clear_reject')}
{_leak_line(new_leak['borderline_reject'], 'borderline_reject')}
{_leak_line(new_leak['borderline_accept'], 'borderline_accept')}
{_leak_line(new_leak['clear_accept'], 'clear_accept')}

Full set.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{_pooled_line(old_leak['clear_reject'], new_leak['clear_reject'], 'clear_reject')}
{_pooled_line(old_leak['borderline_reject'], new_leak['borderline_reject'], 'borderline_reject')}
{_pooled_line(old_leak['borderline_accept'], new_leak['borderline_accept'], 'borderline_accept')}
{_pooled_line(old_leak['clear_accept'], new_leak['clear_accept'], 'clear_accept')}

## Scores

Locked forecast-k1, grok-4.7, effort xhigh, through Cursor. The candidate task is pre-release with grounded weaknesses. The main task is the Gate 1 comparison task, which `forecast_task_text` still emits without grounded weaknesses. For the original 123 papers, raw is the Gate 1 forecast and anonymized is the rerun from the previous report. For the 69 new papers, both raw and anonymized are a fresh draw on the supplied text, before and after `anonymize_iclr_submission`. Delta is anonymized minus raw. Bootstrap: stratified by tier, 4000 resamples, seed 20261029, linear percentiles 0.025 and 0.975. IPW uses the count-pooled ICLR 2024+2025 accept/reject/withdraw mixture ({PAPER_COPILOT['source']}, retrieved {PAPER_COPILOT['retrieved']}).

The pooled raw column mixes a Gate 1 draw on {gate_n} papers with a fresh draw on {fresh_n} papers. The fresh-only tables below are the paired comparison that holds the session fixed.{gap_note}

### Pooled

{block('Candidate', candidate)}
{_reading('Spearman', candidate['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', candidate['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} {_reading('AUC', candidate['delta']['auc'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('IPW Spearman', candidate['delta']['ipw_spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Kappa', candidate['delta']['kappa_venue'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('QWK', candidate['delta']['qwk_venue'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('LORO gap', candidate['delta']['loro_spearman_gap'], higher_better=True, first='the raw text', second='the anonymized text')}

{block('Main', main_arm)}
{_reading('Spearman', main_arm['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', main_arm['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} {_reading('AUC', main_arm['delta']['auc'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('IPW Spearman', main_arm['delta']['ipw_spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Kappa', main_arm['delta']['kappa_venue'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('QWK', main_arm['delta']['qwk_venue'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('LORO gap', main_arm['delta']['loro_spearman_gap'], higher_better=True, first='the raw text', second='the anonymized text')}

Mean score shift, anonymized minus raw, candidate {_fmt(shifts['candidate'])}, main {_fmt(shifts['main'])}.

### Fresh draw on the 69 new papers

Both conditions are a new call. This is the shortcut comparison that does not mix in the original Gate 1 sample.

{block('Candidate, fresh texts', fresh_results['candidate'])}
{_reading('Spearman', fresh_results['candidate']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', fresh_results['candidate']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')}

{block('Main, fresh texts', fresh_results['main'])}
{_reading('Spearman', fresh_results['main']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', fresh_results['main']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')}

Mean score shift on the fresh texts, candidate {_fmt(fresh_shifts['candidate'])}, main {_fmt(fresh_shifts['main'])}.

### Published copies versus anonymous submissions

Same fresh draws, split by the banner on the new PDF. Published n={published_results['candidate']['n']} candidate and {published_results['main']['n']} main. Anonymous n={anonymous_results['candidate']['n']} candidate and {anonymous_results['main']['n']} main. {neither_paper} and stays in the fresh total only. {split_sentence} A slice with one decision has no accept/reject AUC. Rank metrics on the published slice compare forecasts with the human mean inside the accepts. Rank metrics on the anonymous slice compare them inside the rejects. A shortcut that is the camera-ready author block would move the published slice and leave the anonymous slice where it is.

{block('Candidate, published PDFs', published_results['candidate'])}
{_reading('Spearman', published_results['candidate']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', published_results['candidate']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')}

{block('Main, published PDFs', published_results['main'])}
{_reading('Spearman', published_results['main']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', published_results['main']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')}

Mean score shift on the published copies, candidate {_fmt(published_shifts['candidate'])} ({_level(published_shifts['candidate'])}), main {_fmt(published_shifts['main'])} ({_level(published_shifts['main'])}).

{block('Candidate, anonymous PDFs', anonymous_results['candidate'])}
{_reading('Spearman', anonymous_results['candidate']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', anonymous_results['candidate']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')}

{block('Main, anonymous PDFs', anonymous_results['main'])}
{_reading('Spearman', anonymous_results['main']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('MAE', anonymous_results['main']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')}

Mean score shift on the anonymous submissions, candidate {_fmt(anonymous_shifts['candidate'])} ({_level(anonymous_shifts['candidate'])}), main {_fmt(anonymous_shifts['main'])} ({_level(anonymous_shifts['main'])}).{one_paper}

Venue-bin changes, candidate: {_flips(arms['candidate'])} pooled, {_flips(fresh['candidate'])} fresh, {_flips(published['candidate'])} published, {_flips(anonymous['candidate'])} anonymous. Main: {_flips(arms['main'])} pooled, {_flips(fresh['main'])} fresh, {_flips(published['main'])} published, {_flips(anonymous['main'])} anonymous. Candidate flip rate, published minus anonymous, is {_fmt(flip_gaps['candidate'])}. Main flip rate, published minus anonymous, is {_fmt(flip_gaps['main'])}.

## Candidate versus main

Difference is candidate minus main, on the {len(shared)} papers scored by both arms.

Raw inputs, n={contrasts['raw']['n']}.

{_metric_table(contrasts['raw'], left='Main', right='Candidate', delta='Candidate − main')}

Anonymized inputs, n={contrasts['anonymized']['n']}.

{_metric_table(contrasts['anonymized'], left='Main', right='Candidate', delta='Candidate − main')}

On the raw scores, {_reading('Spearman', contrasts['raw']['delta']['spearman'], higher_better=True, first='main', second='the candidate')} {_reading('MAE', contrasts['raw']['delta']['mae'], higher_better=False, first='main', second='the candidate')} On the anonymized scores, {_reading('Spearman', contrasts['anonymized']['delta']['spearman'], higher_better=True, first='main', second='the candidate')} {_reading('MAE', contrasts['anonymized']['delta']['mae'], higher_better=False, first='main', second='the candidate')}

## Conclusion

Mapped {int(coverage['mapped'])}, unmatched {int(coverage['extra_unmatched_files'])}, missing {int(coverage['missing_targets'])}. Emails, named GitHub links, acknowledgement headings, and the venue banner are cleared. A few papers still have an affiliation word in the first 40 lines. {removal_sentence}

{split_sentence} The fresh raw scores are the ones that still contain that banner. After it is removed, {_reading('candidate AUC', fresh_results['candidate']['delta']['auc'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Main AUC', fresh_results['main']['delta']['auc'], higher_better=True, first='the raw text', second='the anonymized text')}

The camera-ready author block is the published slice. On those copies the candidate level shift {_level(published_shifts['candidate'])}. The main level shift {_level(published_shifts['main'])}. Candidate bins change on {_flips(published['candidate'])} of them, against {_flips(anonymous['candidate'])} of the anonymous submissions; the flip-rate gap is {_fmt(flip_gaps['candidate'])}. The main flip-rate gap is {_fmt(flip_gaps['main'])}. {_reading('Candidate Spearman on the published slice', published_results['candidate']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Candidate MAE on the published slice', published_results['candidate']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} The published slice has {published_results['candidate']['n']} papers, so those rank intervals are wide.

The anonymous submissions are the rejects in this batch. Ranking inside that band can move while the mean score stays put. The candidate level shift {_level(anonymous_shifts['candidate'])}. The main level shift {_level(anonymous_shifts['main'])}. {_reading('Candidate Spearman on the anonymous slice', anonymous_results['candidate']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Candidate MAE on the anonymous slice', anonymous_results['candidate']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} {_reading('Main Spearman on the anonymous slice', anonymous_results['main']['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Main MAE on the anonymous slice', anonymous_results['main']['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} The Spearman and MAE comparison is inside the reject band. It is not the camera-ready author block.{one_paper} The two calls are independent draws, and the anonymizer also removes the shared banner plus any named GitHub link or acknowledgement heading.

On the pooled set the raw score for {gate_n} papers is the original Gate 1 draw, so that anonymized-minus-raw interval also contains a change of session. The fresh tables are the shortcut estimate. {_reading('Pooled candidate Spearman', candidate['delta']['spearman'], higher_better=True, first='the raw text', second='the anonymized text')} {_reading('Pooled candidate MAE', candidate['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} {_reading('Pooled main MAE', main_arm['delta']['mae'], higher_better=False, first='the raw text', second='the anonymized text')} Candidate minus main on the pooled raw scores: {_reading('Spearman', contrasts['raw']['delta']['spearman'], higher_better=True, first='main', second='the candidate')} {_reading('MAE', contrasts['raw']['delta']['mae'], higher_better=False, first='main', second='the candidate')} On the anonymized scores: {_reading('Spearman', contrasts['anonymized']['delta']['spearman'], higher_better=True, first='main', second='the candidate')} {_reading('MAE', contrasts['anonymized']['delta']['mae'], higher_better=False, first='main', second='the candidate')}
"""
    for paper in papers:
        title = str(paper["title"])
        forum = str(paper["forum_id"])
        if title and title in text:
            raise SystemExit("report contains a title")
        if forum and forum in text:
            raise SystemExit("report contains a forum id")
    REPORT.write_text(text, encoding="utf-8")
    print(f"wrote {REPORT}")
    print("candidate", candidate["n"], "spearman", _fmt(candidate["delta"]["spearman"]), "mae", _fmt(candidate["delta"]["mae"]))
    print("main", main_arm["n"], "spearman", _fmt(main_arm["delta"]["spearman"]), "mae", _fmt(main_arm["delta"]["mae"]))
    print("fresh candidate", fresh_results["candidate"]["n"], "spearman", _fmt(fresh_results["candidate"]["delta"]["spearman"]), "mae", _fmt(fresh_results["candidate"]["delta"]["mae"]))
    print("fresh main", fresh_results["main"]["n"], "spearman", _fmt(fresh_results["main"]["delta"]["spearman"]), "mae", _fmt(fresh_results["main"]["delta"]["mae"]))
    print("contrast raw", _fmt(contrasts["raw"]["delta"]["spearman"]), _fmt(contrasts["raw"]["delta"]["mae"]))
    print("contrast anon", _fmt(contrasts["anonymized"]["delta"]["spearman"]), _fmt(contrasts["anonymized"]["delta"]["mae"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
