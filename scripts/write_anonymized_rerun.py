#!/usr/bin/env python3
"""Write the anonymized-rerun report. Aggregates only."""

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
from benchmarks.promotion.reanalysis_v2 import (  # noqa: E402
    PAPER_COPILOT,
    TIER_ORDER,
    format_interval,
    mixture_tier_probabilities,
    pooled_statuses,
)

REPORT = ROOT / "benchmarks/promotion/reports/2026-11-25-anonymized-rerun.md"
ANSWERS = ROOT / "workdir/anon-rerun/answers"
SCORES = ROOT / "workdir/reanalysis/scores.json"
SOURCES = ROOT / "workdir/leak-audit/papers"


def _fmt(interval: dict) -> str:
    return format_interval(interval)


def _leak_table(papers: list[dict]) -> dict[str, dict[str, int]]:
    by_forum = {str(paper["forum_id"]): paper for paper in papers}
    tiers: dict[str, dict[str, int]] = {
        tier: {
            "papers": 0,
            "email": 0,
            "github": 0,
            "status": 0,
            "ack": 0,
            "aff40": 0,
            "email_after": 0,
            "github_after": 0,
            "status_after": 0,
            "ack_after": 0,
            "aff40_after": 0,
        }
        for tier in TIER_ORDER
    }
    for path in SOURCES.glob("*/source.txt"):
        paper = by_forum.get(path.parent.name)
        if paper is None:
            continue
        raw = path.read_text(encoding="utf-8")
        anon, _removed = anonymize_iclr_submission(raw)
        tier = str(paper["tier"])
        bucket = tiers[tier]
        bucket["papers"] += 1

        def _flags(text: str, suffix: str) -> None:
            lines = text.splitlines()
            head = "\n".join(lines[:40])
            bucket["email" + suffix] += int(_EMAIL.search(text) is not None)
            bucket["github" + suffix] += int(_GITHUB.search(text) is not None)
            bucket["status" + suffix] += int(bool(_STATUS_LINE.search(text) or _STATUS_INLINE.search(text)))
            bucket["ack" + suffix] += int(any(_ACK_HEAD.match(line.strip()) for line in lines))
            bucket["aff40" + suffix] += int(_AFFILIATION.search(head) is not None)

        _flags(raw, "")
        _flags(anon, "_after")
    return tiers


def _rows_for_arm(papers: list[dict], arm: str, raw_by_index: dict[int, dict]) -> list[dict]:
    rows = []
    for index, paper in enumerate(papers):
        raw = raw_by_index.get(index)
        path = ANSWERS / arm / f"{index:03d}.json"
        if raw is None or not path.is_file():
            continue
        anon = numeric_forecast(json.loads(path.read_text(encoding="utf-8")))
        if not anon:
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
            }
        )
    return rows


def _contrast_rows(candidate: list[dict], main: list[dict], *, condition: str) -> list[dict]:
    """Candidate minus main on one condition. ``condition`` is ``raw`` or ``anon``.

    Several papers share a rating tuple, so the join is the dev index. The
    index is not written into the report.
    """
    main_by_index = {int(row["index"]): row for row in main}
    rows = []
    score_key = "rank_" + condition
    bin_key = "bin_" + condition
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


def _metric_table(result: dict) -> str:
    order = [
        ("Spearman", "spearman", True),
        ("IPW Spearman", "ipw_spearman", True),
        ("Accept/reject AUC", "auc", True),
        ("MAE", "mae", False),
        ("Cohen kappa, venue bin", "kappa_venue", True),
        ("Quadratic weighted kappa, venue bin", "qwk_venue", True),
        ("LORO Spearman gap", "loro_spearman_gap", True),
    ]
    lines = [
        "| Metric | Raw | Anonymized | Anonymized − raw |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, key, _higher in order:
        raw = result["raw"][key]
        anon = result["anonymized"][key]
        lines.append(
            f"| {label} | {raw:.4f} | {anon:.4f} | {_fmt(result['delta'][key])} |"
        )
    for low, high in zip(TIER_ORDER, TIER_ORDER[1:], strict=False):
        key = f"adjacent:{low} vs {high}"
        label = f"Adjacent AUC, {low.replace('_', ' ')} vs {high.replace('_', ' ')}"
        raw = result["raw"].get(key)
        anon = result["anonymized"].get(key)
        raw_text = "undefined" if raw is None else f"{raw:.4f}"
        anon_text = "undefined" if anon is None else f"{anon:.4f}"
        lines.append(f"| {label} | {raw_text} | {anon_text} | {_fmt(result['delta'].get(key))} |")
    return "\n".join(lines)


def _contrast_table(result: dict) -> str:
    """``raw`` holds the main arm and ``anonymized`` holds the candidate arm."""
    lines = [
        "| Metric | Main | Candidate | Candidate − main |",
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
        lines.append(
            f"| {label} | {result['raw'][key]:.4f} | {result['anonymized'][key]:.4f} | {_fmt(result['delta'][key])} |"
        )
    for low, high in zip(TIER_ORDER, TIER_ORDER[1:], strict=False):
        key = f"adjacent:{low} vs {high}"
        label = f"Adjacent AUC, {low.replace('_', ' ')} vs {high.replace('_', ' ')}"
        raw = result["raw"].get(key)
        anon = result["anonymized"].get(key)
        raw_text = "undefined" if raw is None else f"{raw:.4f}"
        anon_text = "undefined" if anon is None else f"{anon:.4f}"
        lines.append(f"| {label} | {raw_text} | {anon_text} | {_fmt(result['delta'].get(key))} |")
    return "\n".join(lines)


def _audit_table(payload: dict) -> str:
    lines = [
        "| Tier | Papers | Papers with any marker | Email hits | GitHub hits | Status hits | Ack hits | Affiliation-before-abstract hits |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for tier in TIER_ORDER:
        bucket = payload["tiers"][tier]
        lines.append(
            f"| {tier.replace('_', ' ')} | {bucket['papers']} | {bucket['papers_with_any_marker']} | "
            f"{bucket['email']} | {bucket['github_named']} | {bucket['camera_ready_or_status']} | "
            f"{bucket['acknowledgement_heading']} | {bucket['affiliation_before_abstract']} |"
        )
    return "\n".join(lines)


def _includes_zero(interval: dict) -> bool:
    low = interval.get("low")
    high = interval.get("high")
    return low is not None and high is not None and low <= 0 <= high


def _favors_second(interval: dict, *, higher_better: bool) -> bool:
    """True when the whole interval favors anonymized, or the candidate."""
    low = interval.get("low")
    high = interval.get("high")
    if low is None or high is None:
        return False
    if higher_better:
        return low > 0
    return high < 0


def _favors_first(interval: dict, *, higher_better: bool) -> bool:
    """True when the whole interval favors the original text, or the main arm."""
    low = interval.get("low")
    high = interval.get("high")
    if low is None or high is None:
        return False
    if higher_better:
        return high < 0
    return low > 0


def main() -> int:
    papers = dev_papers()
    stored = json.loads(SCORES.read_text(encoding="utf-8"))
    raw_by_arm: dict[str, dict[int, dict]] = {"candidate": {}, "main": {}}
    for record in stored:
        raw_by_arm[record["arm"]][int(record["index"])] = record
    population = mixture_tier_probabilities(pooled_statuses((2024, 2025), include_withdraw=True))["tiers"]
    arms = {
        arm: _rows_for_arm(papers, arm, raw_by_arm[arm])
        for arm in ("candidate", "main")
    }
    results = {
        arm: paired_deltas(rows, population=population)
        for arm, rows in arms.items()
    }
    contrasts = {
        "raw": paired_deltas(_contrast_rows(arms["candidate"], arms["main"], condition="raw"), population=population),
        "anonymized": paired_deltas(
            _contrast_rows(arms["candidate"], arms["main"], condition="anon"),
            population=population,
        ),
    }
    leaks = _leak_table(papers)
    recovered = sum(bucket["papers"] for bucket in leaks.values())
    missing_tiers = Counter(paper["tier"] for index, paper in enumerate(papers) if index not in set(json.loads((ROOT / "workdir/anon-rerun/manifest.json").read_text())["indexes"]))
    present_tiers = {tier: leaks[tier]["papers"] for tier in TIER_ORDER}

    def leak_line(tier: str) -> str:
        bucket = leaks[tier]
        name = tier.replace("_", " ")
        return (
            f"| {name} | {bucket['papers']} | {bucket['email']} | {bucket['github']} | "
            f"{bucket['status']} | {bucket['ack']} | {bucket['aff40']} | "
            f"{bucket['email_after']} | {bucket['github_after']} | {bucket['status_after']} | "
            f"{bucket['ack_after']} | {bucket['aff40_after']} |"
        )

    shared = len(set(row["index"] for row in arms["candidate"]) & set(row["index"] for row in arms["main"]))
    if contrasts["raw"]["n"] != shared or contrasts["anonymized"]["n"] != shared:
        raise SystemExit(f"contrast n {contrasts['raw']['n']}/{contrasts['anonymized']['n']} != shared {shared}")
    candidate = results["candidate"]
    main_arm = results["main"]
    raw_gap = contrasts["raw"]
    anon_gap = contrasts["anonymized"]
    audit_raw = json.loads((ROOT / "workdir/leak-audit/raw.json").read_text(encoding="utf-8"))
    audit_anon = json.loads((ROOT / "workdir/leak-audit/anon.json").read_text(encoding="utf-8"))
    spearman_floor = -float(candidate["delta"]["spearman"]["low"])
    rank_keys = ("spearman", "ipw_spearman", "auc", "loro_spearman_gap")
    for arm_name, result in results.items():
        keys = [key for key in result["delta"] if key in rank_keys or key.startswith("adjacent:")]
        for key in keys:
            if not _includes_zero(result["delta"][key]):
                raise SystemExit(f"{arm_name} {key} does not include zero; rewrite the conclusion")
            if _favors_first(result["delta"][key], higher_better=True):
                raise SystemExit(f"{arm_name} {key} favors the original text; rewrite the conclusion")
    for key in ("mae", "kappa_venue", "qwk_venue"):
        if not _favors_second(main_arm["delta"][key], higher_better=(key != "mae")):
            raise SystemExit(f"main {key} no longer favors anonymized text; rewrite the conclusion")
        if not _includes_zero(candidate["delta"][key]):
            raise SystemExit(f"candidate {key} excludes zero; rewrite the conclusion")
    if not _favors_second(raw_gap["delta"]["mae"], higher_better=False):
        raise SystemExit("raw MAE contrast no longer favors the candidate; rewrite the conclusion")
    if not _includes_zero(anon_gap["delta"]["mae"]) or not _includes_zero(raw_gap["delta"]["spearman"]):
        raise SystemExit("arm-contrast intervals changed; rewrite the conclusion")
    if not _includes_zero(anon_gap["delta"]["spearman"]):
        raise SystemExit("anonymized Spearman contrast excludes zero; rewrite the conclusion")
    text = f"""# Anonymized rerun of Gate 1

Date: 2026-11-25. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score. The private holdout was not read. OpenReview was not called. Sealed sets S24-C1 and S24-C2 were not opened.

The rerun is locked forecast-k1: one grok-4.7 call per paper per arm, reasoning effort xhigh, through Cursor, with no xAI API key. The candidate arm uses the pre-release forecast task with grounded weaknesses. The main arm uses the forecast task from the Gate 1 comparison arm (`481c694`), which is the same task text still produced by `forecast_task_text` without grounded weaknesses. The manuscript section is the Gate 1 prompt's source text after `anonymize_iclr_submission`. Figure and contribution lines keep their place, with emails, named GitHub links, and camera-ready phrases redacted.

## What text could be recovered

Gate 1 worker transcripts still held the prompt text for {recovered} of 192 dev papers. The other {192 - recovered} prompts were stored with an empty body. An exact-title arXiv search of those {192 - recovered}, rate-limited at about 3 seconds, returned no exact title match (0 downloads, 0 API errors). Their OpenReview PDF URLs are in the agent store at `release-assets/dev192-missing-pdf-urls.txt` and are not in this repository. No further fetch was attempted.

Recovered papers by tier: clear reject {present_tiers['clear_reject']}, borderline reject {present_tiers['borderline_reject']}, borderline accept {present_tiers['borderline_accept']}, clear accept {present_tiers['clear_accept']}. Missing papers by tier: clear reject {missing_tiers['clear_reject']}, borderline reject {missing_tiers['borderline_reject']}, borderline accept {missing_tiers['borderline_accept']}, clear accept {missing_tiers['clear_accept']}. The missing set is weighted toward clear rejects, so this rerun over-represents accepted, camera-ready papers relative to the locked dev set.

## Leak markers

Counts are papers in the recovered set with at least one marker. "First 40 lines, affiliation word" is a university, institute, laboratory, department, or company word in the first 40 lines. It is not the audit script's `affiliation_before_abstract` count: in this extract the word Abstract is often not on its own line, so that counter walks most of the source and mixes author blocks with later prose. Emails, named GitHub links, status lines, and acknowledgement headings are counted anywhere in the source.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{leak_line('clear_reject')}
{leak_line('borderline_reject')}
{leak_line('borderline_accept')}
{leak_line('clear_accept')}

Before anonymization, an email is present in {leaks['clear_accept']['email']} of {leaks['clear_accept']['papers']} clear accepts and {leaks['clear_reject']['email']} of {leaks['clear_reject']['papers']} clear rejects. An affiliation word in the first 40 lines is present in {leaks['clear_accept']['aff40']} clear accepts and {leaks['clear_reject']['aff40']} clear rejects. After anonymization, emails, named GitHub links, status lines, and acknowledgement headings are 0 in every tier. Affiliation words in the first 40 lines remain in {sum(leaks[tier]['aff40_after'] for tier in TIER_ORDER)} papers.

`scripts/audit_submission_leaks.py` counts marker hits, not papers. Its `affiliation_before_abstract` counter walks the whole extract when Abstract is not on its own line, so those hits mix the author block with later prose. Raw, anonymizer not applied:

{_audit_table(audit_raw)}

After `anonymize_iclr_submission`:

{_audit_table(audit_anon)}

The script reports missing files {audit_raw['missing_files']} and empty files {audit_raw['empty']} out of {audit_raw['rows']} manifest rows. Email, named-GitHub, status, and acknowledgement hits are 0 after anonymization. Residual markers are affiliation hits of the kind above.

## Raw versus anonymized

Paired on the papers that have both a recovered Gate 1 score and an anonymized forecast. Delta is anonymized minus raw. One paper has no original main forecast, so the main arm has one fewer row than the candidate arm. Bootstrap: stratified by tier, 4000 resamples, seed 20261029, linear percentiles 0.025 and 0.975. IPW uses the same count-pooled ICLR 2024+2025 accept/reject/withdraw mixture as the 2026-11-25 reanalysis ({PAPER_COPILOT['source']}, retrieved {PAPER_COPILOT['retrieved']}). Weights are fixed by tier. The LORO gap is APR's Spearman with the other reviewers' mean, minus the held-out reviewer's Spearman with that same mean. A negative gap means APR agrees less than a held-out human.

The anonymized numbers are a new model draw. There is no second draw of the original text, so each interval bounds anonymization and rerun noise together.

Candidate, n={candidate['n']}.

{_metric_table(candidate)}

On the candidate arm, the Spearman, IPW Spearman, AUC, MAE, kappa, QWK, LORO-gap, and adjacent-AUC intervals all include zero. The Spearman interval is {_fmt(candidate['delta']['spearman'])}. Its lower end is {candidate['delta']['spearman']['low']:.4f}, so an original-text rank benefit larger than {spearman_floor:.4f} is outside the interval.

Main, n={main_arm['n']}.

{_metric_table(main_arm)}

On the main arm, Spearman, IPW Spearman, AUC, the LORO gap, and the three adjacent AUCs include zero. MAE {_fmt(main_arm['delta']['mae'])}, kappa {_fmt(main_arm['delta']['kappa_venue'])}, and QWK {_fmt(main_arm['delta']['qwk_venue'])} lie entirely where the anonymized text scores better.

## Does candidate still beat main?

The difference is candidate minus main, on the {shared} papers where both arms have an original score and an anonymized score. On the full 192, Gate 1 reported a Spearman difference of +0.0466 whose interval included zero, and an MAE difference of −0.0740 whose interval lay below zero. Raw Spearman on this recovered slice is lower than those published figures because the missing papers are mostly clear rejects.

Raw inputs, n={raw_gap['n']}.

{_contrast_table(raw_gap)}

Anonymized inputs, n={anon_gap['n']}.

{_contrast_table(anon_gap)}

On the original scores, the Spearman contrast is {_fmt(raw_gap['delta']['spearman'])} and the MAE contrast is {_fmt(raw_gap['delta']['mae'])}. On the anonymized scores, the Spearman contrast is {_fmt(anon_gap['delta']['spearman'])} and the MAE contrast is {_fmt(anon_gap['delta']['mae'])}. A negative MAE difference means the candidate is closer to the human mean.

## Conclusion

The recovered source carries a decision-correlated leak. Emails and first-page affiliation words are much more common on clear accepts than on clear rejects. Anonymization removes every email, named GitHub link, status line, and acknowledgement heading in this set. {sum(leaks[tier]['aff40_after'] for tier in TIER_ORDER)} papers still have an affiliation word in the first 40 lines.

That leak did not carry a detectable share of the rank signal. No Spearman, IPW Spearman, accept/reject AUC, adjacent-AUC, or LORO-gap interval lies where the original text scores better. The candidate Spearman lower bound leaves room for at most {spearman_floor:.4f} of original-text rank benefit. The point estimates move the other way: anonymized Spearman and AUC are higher, and anonymized MAE is lower. On the main arm, MAE, kappa, and QWK improve after anonymization and those three intervals exclude zero. The anonymized scores are a fresh draw, with no second draw of the original prompt, so each interval bounds the shortcut and rerun noise together.

The candidate-over-main MAE result is still present on the original scores of these {shared} papers: {_fmt(raw_gap['delta']['mae'])}, the same direction as the published 192-paper difference of −0.0740. After anonymization it is {_fmt(anon_gap['delta']['mae'])}. That interval includes zero, so the MAE advantage is not detected once the source is anonymized. The anonymized point estimate stays negative, at about −0.03. Spearman stays compatible with no arm difference, on the original scores and the anonymized scores, which matches the published Gate 1 Spearman interval. This slice is not the full dev set. Its raw Spearman is about {raw_gap['raw']['spearman']:.2f} for main and {raw_gap['anonymized']['spearman']:.2f} for the candidate, against the published 0.6002 and 0.6467, because {missing_tiers['clear_reject']} of the {192 - recovered} missing papers are clear rejects. A rerun of those {192 - recovered} could move both the shortcut delta and the arm contrast. Their PDF URLs are only in the agent store.
"""
    forbidden = []
    for paper in papers:
        title = str(paper["title"])
        forum = str(paper["forum_id"])
        if title and title in text:
            forbidden.append("title")
        if forum and forum in text:
            forbidden.append("forum")
    if forbidden:
        raise SystemExit(f"report would contain locked-manifest identifiers: {sorted(set(forbidden))}")
    REPORT.write_text(text, encoding="utf-8")
    print(f"wrote {REPORT}")
    for arm, result in results.items():
        print(arm, "n", result["n"], "spearman", round(result["raw"]["spearman"], 4), "->", round(result["anonymized"]["spearman"], 4), "delta", _fmt(result["delta"]["spearman"]))
        print("   mae", round(result["raw"]["mae"], 4), "->", round(result["anonymized"]["mae"], 4), "delta", _fmt(result["delta"]["mae"]))
        print("   auc", round(result["raw"]["auc"], 4), "->", round(result["anonymized"]["auc"], 4), "delta", _fmt(result["delta"]["auc"]))
        print("   ipw", round(result["raw"]["ipw_spearman"], 4), "->", round(result["anonymized"]["ipw_spearman"], 4), "delta", _fmt(result["delta"]["ipw_spearman"]))
    print("contrast raw spearman", _fmt(raw_gap["delta"]["spearman"]), "mae", _fmt(raw_gap["delta"]["mae"]))
    print("contrast anon spearman", _fmt(anon_gap["delta"]["spearman"]), "mae", _fmt(anon_gap["delta"]["mae"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
