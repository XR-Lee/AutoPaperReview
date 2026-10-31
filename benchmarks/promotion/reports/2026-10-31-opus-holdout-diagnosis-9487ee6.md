# Why pre-release loses the Opus venue bin

Status: diagnosis only. **Do not promote.** No new model call. The accept-anchor paragraph was not edited, and no coefficient or scoring path was edited. Nothing in this file was fit on the holdout. This file has aggregates only. It does not name a paper, a title, an excerpt, a forum id, or a per-paper score.

The measurement is the Opus holdout report on the same commits. That comparison **FAIL**s: venue-bin Spearman, Kendall, MAE, and reviewer bin agreement are worse beyond their paired 95% intervals, and every rank interval contains zero. This file says why, using the stored Opus holdout judgments and the stored Grok judgments on the locked 192-paper dev set. There is no stored Opus run of that dev set.

## Mechanism

The rank and the venue bin are different numbers from the same forecast-k1 call.

`rank_score` is `forecast-k1`, the mean of the three reviewers' expected official ratings. The venue bin is `decision_bin` of the 1–6 decision. The cuts sit halfway between integer decisions: below 1.5 maps to 1, below 2.5 to 3, below 3.5 to 5, below 4.5 to 6, below 5.5 to 8, and otherwise to 10. On the integers these arms actually emit, 2 maps to 3, 3 maps to 5, 4 maps to 6, and 5 maps to 8. A one-point decision step therefore moves the bin by one or two points. `our_bin` matches that map on every stored paper in both Opus arms and both Grok arms.

The decision is not free of the chair's mean. The scale both trees share says a predicted mean at or below 5.4 takes a decision of 1, 2, or 3, and a predicted mean at or above 5.6 takes a decision of 4, 5, or 6. The same scale places 5.6 to 6.4 as borderline accept and a mean near 7 or 8 as an accept. Decision 6 is the rare strong accept. The stored judgments follow that rule with no exceptions, and they follow it more tightly than the text requires:

| Predicted mean | Opus holdout, both arms | Grok dev set |
| --- | --- | --- |
| 5.6 to 6.4 | decision 4 on all 4 pre-release papers and all 5 main papers | decision 4 on all 23 pre-release papers and all 31 main papers |
| above 6.4 and below 7 | decision 5 on all 2 and all 2 | decision 5 on 9 of 10 pre-release papers and all 8 main papers |
| 7 or above | decision 5 on the 1 and the 1 | decision 5 on all 29 and all 6 |

One Grok pre-release paper sits in the open band above 6.4 and below 7 with decision 4. Every other paper in these four arms with a predicted mean above 6.4 has decision 5. Decision 1 and decision 6 do not occur in any of the four arms, so bin 1 and bin 10 do not occur either. The anchor is not what keeps decision 6 off the page: main has no anchor, and main also never emits it.

The only forecast-path difference between `9487ee6` and `52aa237` is `forecast_task_text()`. Pre-release appends one paragraph. It says the review field `rating` is the official rating, that a paper reviewers would accept sits near 8 on `predicted_mean`, that the band 5.6 to 6.4 is only a borderline call, and not to score a paper you would accept as a 6. The first sentence names the review field. The last sentence does not name a field. On this form, 6 is an official rating, a predicted mean inside the borderline band, and the top of the 1–6 decision.

Because the bin is a step function of the predicted mean, the anchor changes the bin only by moving that mean across 5.6 or 6.4, or by stepping the decision inside a side of 5.4. It does not change the map. Rank metrics can hold while bin metrics move, because `rank_score` is a smooth expectation and does not pass through those cuts.

## Opus holdout

Point estimates and intervals are the ones in the holdout report. Pre-release minus main: rank Spearman −0.0384 with interval [−0.1515, 0.0316], Kendall +0.0074 [−0.0837, 0.0837], accept/reject AUC −0.0078 [−0.0444, 0.0000], rank MAE +0.0612 [−0.0326, 0.1517]. All four contain zero or touch zero. Bin Spearman −0.0719 [−0.1687, −0.0032], Kendall −0.0832 [−0.1788, −0.0057], MAE +0.2083 [0.0417, 0.4167], and reviewer bin agreement −0.0708 [−0.1333, −0.0208] lie entirely on the worse side.

The shift that produces those intervals is small on the rank and large once it is quantized. Means on all 24 papers:

| Quantity | pre-release | main | shift | SD of the paired shift |
| --- | ---: | ---: | ---: | ---: |
| `rank_score` | 4.5929 | 4.6681 | −0.0751 | 0.2392 |
| chair predicted mean | 4.5667 | 4.7083 | −0.1417 | 0.2518 |
| vote mean of the three ratings | 4.5417 | 4.5833 | −0.0417 | 0.4538 |
| 1–6 decision | 2.8333 | 2.9167 | −0.0833 | 0.4082 |
| venue bin | 4.3750 | 4.5000 | −0.1250 | 0.7409 |

Probability mass on 8 moves by +0.004 (0.114 against 0.109). Mass on 6 falls by 0.028 and mass on 3 rises by 0.028. On clear accept the same pattern is sharper: mass on 6 falls by 0.050, mass on 3 rises by 0.036, and mass on 8 rises by 0.003. The anchor does not move Opus probability onto 8. The drop in mass on 6 is matched by the rise in mass on 3.

Decisions are integers 2, 3, 4, and 5 only. Pre-release has 14, 3, 4, and 3 papers on those four values. Main has 13, 3, 5, and 3. Spearman of the decision with the human mean equals Spearman of the bin with the human mean on both arms (0.6216 and 0.6935), because the map is strictly monotone on these integers. Rank Spearman is 0.6116 and 0.6499.

Twenty papers have the same bin. The MAE gap is the other four. The sum of absolute bin errors is 32.78 on pre-release and 27.78 on main. The difference is +5.00, and +5.00 / 24 is the published MAE gap of +0.2083. The twenty unchanged papers contribute 0.

Those four bin changes, as counts:

| Tier | what the predicted mean did | decision | bin | papers | change in absolute bin error |
| --- | --- | --- | ---: | ---: | ---: |
| clear accept | crossed below 6.4 | 5 to 4 | 8 to 6 | 1 | +1 |
| clear accept | crossed below 5.6 | 4 to 3 | 6 to 5 | 1 | +1 |
| borderline accept | stayed at or below 5.4 and stepped down | 3 to 2 | 5 to 3 | 1 | +2 |
| borderline accept | crossed above 6.4 | 4 to 5 | 6 to 8 | 1 | +1 |

Each decision change is exactly one point. The three drops have a mean rank shift of −0.477. The one rise has a positive rank shift. Wherever the rank and the bin both move, they move in the same direction: 4 papers, 0 sign disagreements. This is a quantization of a small downward move, not a rank/bin inversion.

Per tier, bin MAE and reviewer bin agreement. Reviewer agreement is the locked metric, the mean of the per-paper fractions. The bottom two tiers are identical on the two arms.

| Tier | bin MAE pre-release | bin MAE main | MAE shift | reviewer agreement shift |
| --- | ---: | ---: | ---: | ---: |
| clear reject | 0.417 | 0.417 | 0 | 0 |
| borderline reject | 1.347 | 1.347 | 0 | 0 |
| borderline accept | 1.742 | 1.242 | +0.500 | −0.1167 |
| clear accept | 1.958 | 1.625 | +0.333 | −0.1667 |

Borderline accept is the tier where the mean bin does not move. One paper goes down and one goes up, so the tier means of the decision and of the bin stay put. Both moves land farther from the human ratings, so MAE on that tier rises by 0.500 and reviewer agreement falls by 0.1167. Clear accept loses one bin-8 and gains one bin-5. Its predicted-mean bands make the same exchange: pre-release has two papers at or below 5.4 and one in (6.4, 7); main has one at or below 5.4 and two in (6.4, 7). Each arm has one paper at or above 7 and two in 5.6 to 6.4.

Both arms compress clear accept. Human mean 7.708. Pre-release rank 5.658, chair 5.767, decision 3.833, bin 6.000. Main rank 5.761, chair 6.033, decision 4.167, bin 6.500. The tier's mean chair sits inside the band the scale already calls borderline. The anchor moves that mean from 6.033 to 5.767, away from the instructed "near 8."

## The same paragraph on the Grok dev set

The stored Grok arms are the 192-paper baseline caches for these commits, grok-4.7, effort xhigh, `forecast-k1`. The anchor moves Grok the other way. Means, pre-release minus main: rank +0.352, chair +0.356, decision +0.312, bin +0.536, mass on 8 +0.080 (0.182 against 0.103).

Clear accept, 48 papers, human mean 7.722:

| Quantity | pre-release | main | shift |
| --- | ---: | ---: | ---: |
| `rank_score` | 6.150 | 5.656 | +0.494 |
| chair predicted mean | 6.215 | 5.692 | +0.523 |
| 1–6 decision | 3.979 | 3.479 | +0.500 |
| venue bin | 6.312 | 5.438 | +0.875 |
| mass on 8 | 0.332 | 0.204 | +0.129 |
| papers with predicted mean at or above 7 | 17 | 5 | +12 |
| papers on bin 8 | 20 | 9 | +11 |

Grok's mass on 8 rises, and its mass on 3 and on 6 falls. Opus's mass on 6 falls and its mass on 3 rises. Opus with the anchor lands on the clear-accept rank that Grok has without the anchor (5.66 to 5.76). Grok with the anchor reaches 6.15, still short of the human 7.72. Twelve more clear accepts then have a predicted mean at or above 7, and the step turns each of those into decision 5. Bin 8 on that tier rises by 11, from 9 to 20.

Grok bin MAE moves with that lift. Pre-release minus main: clear reject +0.194, borderline reject +0.201, borderline accept −0.122, clear accept −0.785. The sum of absolute bin errors falls by 24.53, which is the published overall MAE difference of −0.1278. The published paired interval for that MAE contains zero. The published intervals for bin accept/reject AUC and reviewer bin agreement lie entirely above zero. No Grok primary interval lies entirely on the worse side. That pass is a Grok result. The same paragraph does not do this to Opus.

## No stored Opus dev run

The agent store and the local workdir have Grok scores for the 192 and Opus scores for the holdout. They do not have an Opus score for the dev set. How Opus behaves on the 192 is not something these files can answer.

## Proposed fix

Do not promote. The Grok pass does not transfer.

The change to test later is a wording change in the accept-anchor paragraph, not a new cutoff and not a new bin map. The cuts 5.4, 5.6, and 6.4, and the phrase "near 7 or 8," are already in the shared scale. The four holdout papers are not a place to choose a threshold.

The paragraph should name the field in every sentence:

- On the review `rating` and its probabilities, an accept is an 8, not an official rating of 6.
- On `predicted_mean`, an accept sits near 7 or 8. The band 5.6 to 6.4 stays the borderline-accept band the scale already defines.
- The 1–6 decision is chosen from `predicted_mean` by the existing rule, after that mean is set. A predicted mean near 7 or 8 is decision 5. Decision 6 stays the rare strong accept, a predicted mean near 9. The official-rating instruction does not lower the decision.

That wording is not applied on this branch.

Scoring the venue bin from `rank_score` would change the locked metric. It would also leave the compression where it is. Both Opus clear-accept ranks are about 5.7, and the nearest official bin to 5.7 is 6. Pre-release's mean bin on that tier is already 6.0. The human mean is 7.7, whose nearest bin is 8. The missing point is in the forecast, upstream of the map.

## Dev-set validation

Not run. The registered Opus check is in `benchmarks/fixed_eval/OPUS_VALIDATION.md`. With no Opus baseline cache, a validation runs both arms on the cold-start prefix: 18 papers per tier, 72 papers, 144 calls, $95.04 at $0.66. This promotion has already spent about $37.62 on Opus (48 first holdout calls and 9 schema retries). About $62 remains. $95.04 does not fit. One arm of the standing 36-per-tier slice is also $95.04, and there is no stored Opus baseline to compare it with. A smaller slice was not drawn after reading the holdout.

The diagnosis itself spent $0. The Opus total for this promotion stays about $37.62. The Grok total stays about $34.32 and is separate from the Opus cap.

When a later run has the budget, apply the field-named paragraph on a fresh candidate and score it only on the pre-registered dev slice. The v1 rule is unchanged: no primary metric worse beyond the paired interval. The check that matches this mechanism is whether accept-tier predicted means and mass on 8 move up together, and whether the decision follows them across 6.4 into decision 5. Re-check Grok on the dev set before replacing the paragraph that produced Grok's bin gain. Do not reopen the holdout to tune or to confirm the wording.
