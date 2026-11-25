#!/usr/bin/env python3
"""Write the dev192 reanalysis v2 report from recovered numeric forecasts.

The report contains aggregates only. A forum id or a title from the locked
manifest is a hard failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from autopaperreview.fixed_eval import dev_papers  # noqa: E402
from benchmarks.promotion.reanalysis_v2 import (  # noqa: E402
    PAPER_COPILOT,
    TIER_ORDER,
    arm_metrics,
    format_interval,
    ipw_weights,
    loro_metrics,
    mixture_tier_probabilities,
    pooled_statuses,
    thorndike_case_ii,
    weighted_mae,
    weighted_spearman,
    year_statuses,
)

REPORT = ROOT / "benchmarks/promotion/reports/2026-11-2x-reanalysis-v2.md"
SCORES = ROOT / "workdir/reanalysis/scores.json"


def _rows() -> tuple[list[dict], dict[str, dict]]:
    papers = dev_papers()
    stored = {}
    if SCORES.is_file():
        for record in json.loads(SCORES.read_text(encoding="utf-8")):
            stored[(record["arm"], int(record["index"]))] = record
    built: dict[str, list[dict]] = {"candidate": [], "main": []}
    for index, paper in enumerate(papers):
        base = {
            "tier": paper["tier"],
            "stratum": paper["tier"],
            "human_mean": float(paper["mean_rating"]),
            "decision": paper["decision"],
            "ratings": [float(item) for item in paper["ratings"]],
            "title_chars": len(str(paper["title"])),
            "year": int(paper["year"]),
        }
        for arm in ("candidate", "main"):
            row = dict(base)
            found = stored.get((arm, index))
            if found is not None:
                row["rank_score"] = float(found["rank_score"])
                row["our_bin"] = int(found["our_bin"])
            built[arm].append(row)
    return papers, built


def _population(include_withdraw: bool, years: tuple[int, ...] = (2024, 2025)) -> dict:
    statuses = pooled_statuses(years, include_withdraw=include_withdraw) if len(years) > 1 else year_statuses(years[0], include_withdraw=include_withdraw)
    mixture = mixture_tier_probabilities(statuses)
    return mixture


def _fmt_share(shares: dict[str, float]) -> str:
    return ", ".join(f"{name.replace('_', ' ')} {shares[name]:.3f}" for name in TIER_ORDER)


def main() -> int:
    papers, by_arm = _rows()
    years = {}
    for paper in papers:
        years[paper["year"]] = years.get(paper["year"], 0) + 1
    headline = _population(True)
    accept_reject = _population(False)
    only_2025 = mixture_tier_probabilities(year_statuses(2025, include_withdraw=True))
    only_2024 = mixture_tier_probabilities(year_statuses(2024, include_withdraw=True))
    candidate = arm_metrics(by_arm["candidate"], population=headline["tiers"])
    main_arm = arm_metrics(by_arm["main"], population=headline["tiers"])
    candidate_loro = loro_metrics(by_arm["candidate"])
    main_loro = loro_metrics(by_arm["main"])
    human_rows = [{key: value for key, value in row.items() if key not in {"rank_score", "our_bin"}} for row in by_arm["candidate"]]
    human_loro = loro_metrics(human_rows)

    def _sensitivity(rows: list[dict], population: dict) -> tuple[float | None, float | None]:
        scored = [row for row in rows if row.get("rank_score") is not None]
        weights = ipw_weights([row["tier"] for row in scored], population["tiers"])
        scores = [float(row["rank_score"]) for row in scored]
        means = [float(row["human_mean"]) for row in scored]
        return weighted_spearman(scores, means, weights), weighted_mae(scores, means, weights)

    candidate_point = candidate["point"]
    main_point = main_arm["point"]
    if candidate_point["spearman"] is not None and candidate_point["human_sd"] is not None:
        candidate_point["thorndike_spearman"] = thorndike_case_ii(
            candidate_point["spearman"], candidate_point["human_sd"], headline["std"]
        )
    if main_point["spearman"] is not None and main_point["human_sd"] is not None:
        main_point["thorndike_spearman"] = thorndike_case_ii(
            main_point["spearman"], main_point["human_sd"], headline["std"]
        )
    candidate_2025 = _sensitivity(by_arm["candidate"], only_2025)
    candidate_ar = _sensitivity(by_arm["candidate"], accept_reject)

    def line(label: str, interval: dict) -> str:
        return f"| {label} | {format_interval(interval)} |"

    def arm_table(result: dict) -> str:
        intervals = result["intervals"]
        point = result["point"]
        rows = [
            line("Spearman", intervals["spearman"]),
            line("Kendall tau-b", intervals["kendall"]),
            line("Accept/reject AUC", intervals["auc"]),
            line("MAE", intervals["mae"]),
            line("IPW Spearman", intervals["ipw_spearman"]),
            line("IPW Kendall tau-b", intervals["ipw_kendall"]),
            line("IPW AUC", intervals["ipw_auc"]),
            line("IPW MAE", intervals["ipw_mae"]),
            line("Cohen kappa, venue bin", intervals["kappa_venue"]),
            line("Quadratic weighted kappa, venue bin", intervals["qwk_venue"]),
            line("Cohen kappa, rank nearest bin", intervals["kappa_rank"]),
            line("Quadratic weighted kappa, rank nearest bin", intervals["qwk_rank"]),
            line("Nearest-bin agreement", intervals["bin_agreement"]),
            line("MAE improvement vs constant median", intervals["mae_improvement_vs_median"]),
        ]
        for name in (
            "clear_reject vs borderline_reject",
            "borderline_reject vs borderline_accept",
            "borderline_accept vs clear_accept",
        ):
            rows.append(line(f"Adjacent AUC, {name.replace('_', ' ')}", intervals[name]))
        corp = point["corp"] or {}
        reliability = point["reliability"]
        corp_lines = [
            f"| MCB (miscalibration) | {format_interval(intervals['corp_miscalibration'])} |",
            f"| DSC (discrimination) | {format_interval(intervals['corp_discrimination'])} |",
            f"| UNC (uncertainty) | {format_interval(intervals['corp_uncertainty'])} |",
            f"| MSE | {format_interval(intervals['corp_mse'])} |",
            f"| cross term | {format_interval(intervals['corp_cross'])} |",
            f"| identity gap | {corp.get('identity_gap', float('nan')):.6f} |",
        ]
        quintiles = [
            f"| {int(item['bin'])} | {int(item['n'])} | {item['mean_score']:.3f} | {item['mean_outcome']:.3f} |"
            for item in reliability
        ]
        return "\n".join(rows), "\n".join(corp_lines), "\n".join(quintiles)

    candidate_lines, candidate_corp, candidate_bins = arm_table(candidate)
    main_lines, main_corp, main_bins = arm_table(main_arm)
    weights = ipw_weights(list(TIER_ORDER), headline["tiers"])
    weight_text = ", ".join(
        f"{name.replace('_', ' ')} {weight:.3f}" for name, weight in zip(TIER_ORDER, weights, strict=True)
    )
    text = f"""# Dev192 reanalysis v2

Date: 2026-11-25. Sample: the locked ICLR dev set of 192 papers, 48 in each tier. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score. The private holdout was not read. No new model call was made.

Forecast numbers were read back from the Gate 1 worker transcripts (decision, six probabilities, chair mean). `rank_score` is the clamped mean of the three expected ratings, the same `forecast-k1` definition as Gate 1. One candidate transcript and two main transcripts did not yield a numeric forecast, so model metrics below use 191 and 190 papers. Human-only figures use all 192. The recovered rank Spearman, Kendall, AUC, and MAE sit within about 0.01 of the published 192-paper Gate 1 point estimates. That is a sanity check on the recovery, not a second experiment.

Bootstrap: stratified by the four tiers, 4000 resamples, seed 20261029, linear percentiles 0.025 and 0.975. An interval is omitted when the resample is undefined. IPW weights are fixed by the tier, not re-estimated inside a resample.

## Target distribution

Paper Copilot's ICLR summary, retrieved 2026-11-25 from {PAPER_COPILOT['source']}. The same page lists 2024 and 2025. Mean and std are of per-submission reviewer-average scores. Reject counts are opt-in public releases and can be smaller than every reject. Accept + reject + withdraw + desk reject does not equal the stated total (2025: 11755 against 11672; 2024: 7407 against 7304). Desk rejects are left out of the mixture because the table does not give them a reviewer-average mean. The three scored statuses are renormalized.

| Year | Stated n | Stated mean (std) | Accept n, mean, std | Reject n, mean, std | Withdraw n, mean, std |
| --- | ---: | --- | --- | --- | --- |
| 2025 | 11672 | 5.12 (1.25) | 3703, 6.43, 0.68 | 4911, 4.77, 0.92 | 2993, 4.05, 0.81 |
| 2024 | 7304 | 5.11 (1.26) | 2260, 6.44, 0.70 | 3438, 4.73, 0.94 | 1655, 4.02, 0.80 |

The dev set mixes years: {years.get(2024, 0)} papers from 2024 and {years.get(2025, 0)} from 2025. The headline population is the count-pooled 2024+2025 accept/reject/withdraw mixture. Tier shares are the normal mixture integrated over (-inf, 4), [4, 5.5), [5.5, 7), [7, inf). That is an approximation, not a public histogram.

| Population | Tier shares | Mixture mean | Mixture std |
| --- | --- | ---: | ---: |
| 2024+2025, with withdraw | {_fmt_share(headline['tiers'])} | {headline['mean']:.3f} | {headline['std']:.3f} |
| 2024+2025, accept+reject only | {_fmt_share(accept_reject['tiers'])} | {accept_reject['mean']:.3f} | {accept_reject['std']:.3f} |
| 2025 only, with withdraw | {_fmt_share(only_2025['tiers'])} | {only_2025['mean']:.3f} | {only_2025['std']:.3f} |
| 2024 only, with withdraw | {_fmt_share(only_2024['tiers'])} | {only_2024['mean']:.3f} | {only_2024['std']:.3f} |

Equal-tier sampling uses share 0.25. Headline weight = population share / 0.25: {weight_text}. Kish effective n is {candidate_point['n_eff']:.1f} on the candidate recovered set and {main_point['n_eff']:.1f} on main. The dev human-mean SD is {candidate_point['human_sd']:.3f}, against a headline mixture SD of {headline['std']:.3f}.

## Rank metrics and IPW

Published Gate 1 on all 192, for reference: candidate Spearman 0.6467, AUC 0.8446, MAE 1.1247; main Spearman 0.6002, AUC 0.8494, MAE 1.1987.

Candidate, n={candidate_point['n_scored']}.

| Metric | point [95% interval] |
| --- | --- |
{candidate_lines}

Main, n={main_point['n_scored']}.

| Metric | point [95% interval] |
| --- | --- |
{main_lines}

Unweighted candidate Spearman {candidate_point['spearman']:.4f} moves to IPW Spearman {candidate_point['ipw_spearman']:.4f}. Main moves from {main_point['spearman']:.4f} to {main_point['ipw_spearman']:.4f}. The unweighted and IPW intervals overlap, so the drop is a shift of the point estimate, not a clean separation. It has the same sign under both sensitivities below. Applying the Case II range-restriction formula to the unweighted Spearman, with the dev human SD and the headline mixture SD, gives {candidate_point['thorndike_spearman']:.4f} (candidate) and {main_point['thorndike_spearman']:.4f} (main). That formula is a one-number approximation. It is not the IPW estimate.

Sensitivity, point IPW Spearman and IPW MAE only. Candidate under the 2025-only mixture: {candidate_2025[0]:.4f}, {candidate_2025[1]:.4f}. Candidate under accept+reject without withdraw: {candidate_ar[0]:.4f}, {candidate_ar[1]:.4f}.

## Trivial baselines

On the scored rows, predicting the sample median of the human means has MAE {candidate_point['constant_median_mae']:.4f} (candidate comparison set) and {main_point['constant_median_mae']:.4f} (main). Predicting the mean has MAE {candidate_point['constant_mean_mae']:.4f} and {main_point['constant_mean_mae']:.4f}. Title-character length, leave-one-out linear fit, has MAE {candidate_point['title_length_loo_mae']:.4f} and {main_point['title_length_loo_mae']:.4f}. Relative MAE improvement of APR versus the constant median is {candidate_point['mae_improvement_vs_median']:.4f} (candidate) and {main_point['mae_improvement_vs_median']:.4f} (main). Versus title length it is {candidate_point['mae_improvement_vs_title']:.4f} and {main_point['mae_improvement_vs_title']:.4f}.

Page count is not in the locked manifest. Title-plus-abstract text is not cached on this machine. Both baselines are TODO.

## Chance-corrected agreement

Nearest-bin agreement counts a hit when the venue bin equals the nearest official bin of the human mean. Cohen kappa and quadratic weighted kappa use the ordered bins 1, 3, 5, 6, 8, 10. Venue-bin kappa compares the model's decision bin with the human nearest bin. Rank-bin kappa compares the nearest bin of `rank_score` with the human nearest bin.

Adjacent AUC is the Mann-Whitney AUC of `rank_score` between two neighboring tiers, higher tier positive. It is not the accept/reject AUC. The published point estimates were 0.696 / 0.733 / 0.650 (candidate) and 0.634 / 0.715 / 0.658 (main).

## Leave-one-reviewer-out

For each paper with at least three official ratings, each reviewer is held out in turn. The target is the mean of the remaining reviewers. APR's `rank_score` and the held-out rating are both compared to that same target. Papers are the bootstrap units, so a drawn paper contributes every held-out reviewer. This is an Alt-test-style comparison, not a preregistered non-inferiority test. Every dev paper has at least three ratings. Human-only figures use all 192 papers. The model comparison uses the recovered forecasts.

Human only, n={human_loro['point']['n_papers_human']}: Spearman {format_interval(human_loro['intervals']['human_spearman'])}, MAE {format_interval(human_loro['intervals']['human_mae'])}.

Candidate, n={candidate_loro['point']['n_papers_model']}: held-out human Spearman {format_interval(candidate_loro['intervals']['human_spearman'])}, MAE {format_interval(candidate_loro['intervals']['human_mae'])}. APR Spearman {format_interval(candidate_loro['intervals']['apr_spearman'])}, MAE {format_interval(candidate_loro['intervals']['apr_mae'])}. APR minus human Spearman {format_interval(candidate_loro['intervals']['spearman_gap'])}. Human MAE minus APR MAE {format_interval(candidate_loro['intervals']['mae_gap'])}. A negative Spearman gap means APR agrees less with the other reviewers than a held-out human does.

Main, n={main_loro['point']['n_papers_model']}: held-out human Spearman {format_interval(main_loro['intervals']['human_spearman'])}, MAE {format_interval(main_loro['intervals']['human_mae'])}. APR Spearman {format_interval(main_loro['intervals']['apr_spearman'])}, MAE {format_interval(main_loro['intervals']['apr_mae'])}. APR minus human Spearman {format_interval(main_loro['intervals']['spearman_gap'])}. Human MAE minus APR MAE {format_interval(main_loro['intervals']['mae_gap'])}.

The candidate Spearman gap includes zero. The main Spearman gap does not: its interval lies below zero, so on this sample main agrees less with the other reviewers than a held-out human does. Both MAE gaps include zero. This sample does not show that APR is closer in absolute error than a held-out reviewer.

## Calibration

CORP decomposes MSE of `rank_score` against the human mean after an increasing isotonic fit. MCB is the mean squared gap between the score and the fitted value. DSC is the variance of the fitted value. UNC is the variance of the human mean. Pooling distinct scores into one fitted value leaves a cross term, so MSE = MCB - DSC + UNC + cross. The identity gap is the leftover after that term.

Candidate.

| Term | point [95% interval] |
| --- | ---: |
{candidate_corp}

| Quintile | n | mean score | mean human |
| --- | ---: | ---: | ---: |
{candidate_bins}

Main.

| Term | point [95% interval] |
| --- | ---: |
{main_corp}

| Quintile | n | mean score | mean human |
| --- | ---: | ---: | ---: |
{main_bins}

## Leakage

The first OpenReview notes request for this audit returned HTTP 403. No further OpenReview call was made, and no dev manuscript was fetched. Residual leak-marker counts on the dev set are TODO until a client can read the original submission PDFs. The `scripts/audit_submission_leaks.py` counter is in the tree and was checked on synthetic text.

How much of the Gate 1 correlation disappears on anonymized inputs is TODO. It needs a new forecast-k1 run on the anonymized text. That is a paid model call, and this reanalysis does not make one.

## What the current headline numbers overstate

Accept/reject AUC (about 0.84) is mostly the gap between clear rejects and clear accepts. Adjacent-tier AUC is about 0.63 to 0.73. A reader who treats 0.84 as fine-grained ranking is misreading the metric.

Nearest-bin agreement near 0.41 is close to what the base rates allow. Cohen kappa and QWK are the chance-corrected readings. Quote resolution (490/576 on the candidate in the Gate 1 report) says a quote occurs in the source. It does not say the criticism is right.

The middle quintiles sit below the human mean (candidate gaps about 0.35, 0.43, and 0.59). The end quintiles are close. That is the same direction as the Gate 1 negative bias, and it is a monotone under-prediction in the middle of the score range rather than a reversal.

The dev set gives each tier a quarter of the sample. The natural mixture puts more mass in the middle, and the dev human-mean SD ({candidate_point['human_sd']:.3f}) is wider than the headline mixture SD ({headline['std']:.3f}). Unweighted Spearman is the correlation inside that widened sample. IPW Spearman is the reweighted estimate. The Thorndike number is only a cross-check.

MAE looks small next to a 1-10 scale and large next to a constant median. The relative improvement versus that median is the comparison that matches the question "did the model beat a trivial guess?"

The Gate 1 forecasts were not run on anonymized submission text. Accepted camera-ready PDFs and rejected anonymous PDFs can leak the decision. Until the anonymized rerun exists, the published Spearman and AUC are not known to be free of that shortcut.

## TODO

- Residual leak-marker audit of the 192 dev manuscripts, after an OpenReview client that is not answered with 403.
- Forecast-k1 rerun on anonymized text, then the same metrics, to measure how much of the signal the leak carried.
- Page-count and title-plus-abstract baselines. Neither input is cached here.
- The one candidate forecast and two main forecasts that were not recoverable. They are omitted, not imputed.
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
    print("candidate", candidate_point["n_scored"], "spearman", round(candidate_point["spearman"], 4), "ipw", round(candidate_point["ipw_spearman"], 4))
    print("main", main_point["n_scored"], "spearman", round(main_point["spearman"], 4), "ipw", round(main_point["ipw_spearman"], 4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
