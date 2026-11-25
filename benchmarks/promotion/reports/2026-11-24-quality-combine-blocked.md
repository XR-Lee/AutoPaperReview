# Grounded-feedback candidate, gates not scored

Status: **not promoted.** The locked 192-paper Grok gate and the Opus holdout were not scored. `main` stays at `481c694`. This file records aggregates only.

## Refs

| Role | Commit |
| --- | --- |
| main | `481c694021fcd6386d7157b7d58f1ab0a03b41e7` |
| pre-release after #63 | `4a5c13555f98d3043923f58b42da1b66fdb513b0` |
| candidate | `e2879da` plus this report, branch `cursor/apr-grok-quality-combine-d373` (PR #64) |

#63 squash-merged into `pre-release` at `4a5c135`. Head matched `7726c8ae595c1272c572342afe2452a70d3849aa`. `delete_branch_on_merge` stayed false. `codex/apr-grok-extra-data-fix` is still on the remote.

## What the candidate combines

From `codex/apr-grok-quality-20261124` (`499e89f`): grounded forecast feedback, quote checks that reject a changed number or negation, the packet-prep truncation notice, the opt-in reproducibility pointer, contribution actions, author-report evidence retention, and the synthetic feedback harness with a reading-budget blind packet.

The grounded prompt keeps each original diagnosis and adds one short quoted fix on the last weakness only. A weakness with no quote is kept. A quote is kept only when it is one continuous passage in the shown manuscript and unique in the source.

Paused with no gain in the Codex notes, and not included: similarity compression, forced citation filtering, blanket severity removal, per-claim deep check.

With the flag off, `forecast_task_text()` is the same 7302-character string on `481c694`, `406ef6c`, `4a5c135`, and the candidate. SHA-256 `3ff44530081b79911122a6618b5d5bf18fced30f0b192da0376dffee8eddcccd`. `full_text_packet` matches on those refs. `PromptPacketStage` matches on `481c694`, `406ef6c`, and `4a5c135`. The candidate packet stage adds the truncation notice. `--grounded-weaknesses` adds 938 characters to the forecast task.

## Why #62's score gap is not a forecast-prompt edit

PR #62 compared `pre-release` `406ef6c` with `main` `481c694` and was reported as n=191, Spearman 0.638 [0.544, 0.719] versus 0.667 [0.575, 0.742], AUC 0.862 versus 0.877. The locked forecast task text and the prompt-packet stage source are identical on those two commits. `#60` main-PDF-only and the local CLI are off the locked `calibrate-eval` forecast path. `#63` recovers a restarted JSON object only when the command transport reports `finish_reason=stop`. A missing paper fails the no-new-failure rule. This environment did not re-score that pair.

## Manuscripts

OpenReview notes and PDF routes returned HTTP 403. The fetch stopped. No xAI API key was used. arXiv exact-title fallback, using titles already in the locked manifest, recovered manuscripts for 119 of 192 dev papers.

| Tier | Manuscripts |
| --- | ---: |
| clear reject | 25 / 48 |
| borderline reject | 18 / 48 |
| borderline accept | 36 / 48 |
| clear accept | 40 / 48 |

The cold-start first 18 per tier had text for 8, 7, 15, and 16 papers. 26 of 72 were missing. Gate 1 was not scored.

## Synthetic feedback, 80-word budget

24 purpose-written scenarios, 48 vignettes per arm, grok-4.7 at effort xhigh through Cursor subagents. No xAI key. The reading budget was fixed at 80 words before grading. Blind adjudication used the harness rubric. 96 grades, 0 missing. Truncated reviews: baseline 20 / 48, candidate 25 / 48. Intervals are the harness Clopper–Pearson and Bonferroni paired bounds.

| Rate | Baseline | Candidate | Paired difference |
| --- | ---: | ---: | --- |
| Defect recall | 24/24 = 1.000 | 24/24 = 1.000 | 0.000 [-0.167, 0.167] |
| Localized recall | 24/24 = 1.000 | 24/24 = 1.000 | 0.000 [-0.167, 0.167] |
| Actionable recall | 3/24 = 0.125 [0.027, 0.324] | 23/24 = 0.958 [0.789, 0.999] | +0.833 [0.430, 0.962] |
| Specificity on the repaired control | 22/24 = 0.917 | 22/24 = 0.917 | 0.000 [-0.237, 0.237] |
| Primary discrimination | 3/24 = 0.125 [0.027, 0.324] | 21/24 = 0.875 [0.676, 0.973] | +0.750 [0.337, 0.916] |

The baseline arm is the plain forecast weakness instruction. The candidate arm is the diagnosis-then-one-fix prompt. This is not a remeasurement of the earlier prompt that appended a fix to every weakness, and it is not human-concern coverage on OpenReview reviews.

One arXiv manuscript was also answered once, as a transport check. The answer validated as `ForecastJudgment`. Three quoted weaknesses resolved in the manuscript and three diagnosis-only weaknesses were kept. That call is not a rank score.

## Holdout

`workdir/fixed-eval/private-holdout.json` is not on this machine. Gate 2 was not started. The holdout was not opened.

## Tests

`python3 -m unittest discover -s tests`: 639 tests, 1 skipped, pass. `python3 scripts/check_confidentiality.py`: pass.
