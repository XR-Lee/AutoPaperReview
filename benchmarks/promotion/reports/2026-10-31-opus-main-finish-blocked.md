# Opus main arm, finish attempt

Status: **not scored.** The 18 missing main-arm calls did not start. The platform refused `claude-opus-5-5-xhigh` before any of them ran, with a usage-limit error. No further call was made. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score.

## What was already done

The cold-start slice is the first 18 papers per tier of `opus_validation` (seed `20261102`): 72 papers, candidate arm at the field-named accept anchor, main arm at `52aa2379abf7ae18c8b2f215d301b7e564b19e41`. Model `claude-opus-5-5`, effort xhigh, forecast-k1.

| Arm | Valid answers | Missing |
| --- | ---: | ---: |
| candidate | 72 | 0 |
| main | 54 | 18 |

The 18 missing main papers are 5 borderline accepts and 13 clear accepts. The two lower tiers are complete on both arms. Schema failures among the answers on disk: 0. Papers that already had a valid answer were not rerun.

## This attempt

Pre-release is `6d088b7eea3258d00627dc9306ddb2b9ab703a51`. The cap for the round was raised to $107 so those 18 papers could finish. Eighteen calls at $0.66 would have brought the accounted spend from $95.04 to $106.92.

All 18 were refused before start. The error was the same usage limit as the earlier stop. No answer file was written. The candidate arm was not rerun. The holdout, the sealed CVPR sets, and `main` were not touched.

A retry was not launched. Another call would not have stayed inside a hard stop once the platform is refusing the model, and one extra call past these 18 would have crossed $107 if the refused calls had been billed.

## Metrics

The paired v1 bootstrap on all 72 papers was not computed. A comparison on the 54 finished main papers would drop most of the clear-accept tier, which is not the registered slice. There is no PASS or FAIL under the v1 rule.

The main-arm Opus baseline was not cached. A 54-paper arm is not the standing baseline for later candidate-only runs.

## Cost

| Item | Calls | Accounted cost |
| --- | ---: | ---: |
| Earlier launches, both arms | 144 | $95.04 |
| Finish attempt, refused before start | 18 | $0.00 |
| Cumulative Opus | 144 | $95.04 |

The Grok screen on the 192-paper dev set remains the pass recorded in `2026-10-31-field-named-anchor-3bc831c.md`. This Opus screen is still incomplete.
