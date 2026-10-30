# Promotion benchmark vs README holdout

Date: 2026-10-30. Investigation only. This branch does not change `main` or `pre-release`, and it does not merge.

The question is why promotion benchmark v1 (report `2026-10-30-pre-release-58c301fd.md`, commit `bf62d7f`) scored Rank Spearman 0.447 / AUC 0.715 on pre-release `58c301fd` and 0.481 / 0.750 on main `3865669`, while the README on both branches quotes APR Grok 4.7 `forecast-k1` at Spearman 0.761 [0.389, 0.939] / AUC 0.800 on holdout-15, with tune-30 draws 0.656 / 0.857 and 0.582 / 0.761.

## Conclusion

The gap is a different paper set, plus ordinary noise on a 15-paper correlation. It is not a label-join bug, not an inverted score, and not a regression in the `forecast-k1` formula between the README revision `efd0568`, main `3865669`, and pre-release `58c301fd`.

A new draw of the README holdout-15, through main's adapter and the locked flags, scored Spearman **0.576** [0.091, 0.833] and AUC **0.700** [0.400, 0.944]. The same answers scored through pre-release `58c301fd` matched on all 15 papers (rank and bin). The published 0.761 sits inside this interval, and so does the promotion main point 0.481. One 15-paper redraw cannot separate those two points. It also does not reproduce 0.761 as a point estimate.

The promotion ICLR block should not be read as a check of the README number. The promotion FAIL stays the CVPR A1 leak scan. Every ICLR paired interval in that report contains zero.

## Which 37 papers

The 37 are the forum ids already listed in `benchmarks/promotion/manifest.json`. Their id-list SHA-256, from `cvpr_sets.id_list_digest` (sorted ids, trailing newline), is `93672fd7601a38dbb136e57ffd7f372b696092b382cc1735152ae272d1cc5da4`. That digest matches the manifest. This report does not repeat the ids.

They were not a sample of holdout-15, and they were not a 40-paper draw that then dropped three papers at scoring time. Scoring finished 37/37.

Selection, as executed:

1. `scripts/select_public_calibration.py --cache <gitignored> --phase all --workers 6`.
2. Sample seed `20261021`, tune fraction 0.6, strata and quotas from `benchmark_select.py` (clear_reject 20, low 15, borderline 25, high 20, strong 20, at most 4 papers per area before the cap relaxes).
3. `--extend-rejected` was left at 0. The original 100-paper sample that holdout-15 was cut from used two extra rejected passes, 280 then 220. This re-materialize did not. The log line is `materialized 93 tune 56 holdout 37 failures 107`.
4. The promotion subset then keeps the holdout side only. Subset seed `20261029`. Quotas scaled from the 100-paper quotas by the holdout fraction: clear_reject 8, low 6, borderline 10, high 8, strong 8 (target 40). Within each stratum the ids are sorted, then shuffled with `Random(f"promotion-v1:20261029:{stratum}")`, and a short stratum keeps every holdout paper.
5. `clear_reject` holdout had 5 papers, short by 3. The other four strata met their quotas. Sizes 5 + 6 + 10 + 8 + 8 = 37. Accept/reject in the scored subset: 20 / 17.

So the 37 are the entire holdout of a newly materialized 93-paper public sample, after PDF skips, with one stratum short.

### Overlap with holdout-15 and tune-30

Holdout-15 is a different slice. It is 15 papers, 5 accept and 10 reject, drawn from the original 60/40 split (parent split SHA-256 `45b9464bee2c41789682afefb18994fbb28bdd786ed36abac64c53558858cf10`). Subset seed `20261022`. Quotas are on the nearest official-rating bin, not on the five promotion strata: bin 3, 5, and 6 take 4 papers each, and bin 8 takes 3. The README says this holdout was opened in rounds 2, 8, and 11, so it is not an untouched holdout. Its id-list digest, same trailing-newline function as the manifest, is `b748590b45095ff60ab671e62404c9dbada9dc6fc64ebfa8a3465a364159cc6a`.

Overlap with the promotion 37 is **5 papers**. Ten of the 15 were not in the promotion run.

Tune-30 was not recovered. The aggregates were pasted into the README revision without an id list, a draw seed, or a membership file. The transcripts that published 0.656 and 0.761 describe a private 30-paper tune split used as five folds of six. They do not say it is the first 30 of the original tune-60, and they do not give bin quotas. Overlap with the 37 cannot be computed.

A different 15-paper tune slice from the same seed-`20261022` file is not tune-30. Its digest is `b204c1da3337c270ff4b2e159fce3ac5b2d33a3299e3507ebe2a2ebba1a413e0`. Overlap with the promotion 37 is 0. Overlap with holdout-15 is 0.

## Configuration

The locked command is the same string on both sides:

`calibrate-eval run --mode adapter --adapter autopaperreview.model_exec:OpenAICompatibleAdapter --model grok-4.7 --cheap --holistic-mode forecast --holistic-samples 1 --related-work off --coefficient-set apr-fusion-v1`

The rank is `rank_score`, the clamped mean expected rating of one three-seat forecast panel (`forecast-k1`). The 1–6 decision is mapped by `decision_bin` and is not the rank. Spearman and Kendall use the mean of the official ratings in `heldout/human.json`. AUC is accept versus reject, ties 0.5. `rank_metrics.agreement` is the point estimator. The README interval is the paper-level percentile bootstrap in `rank_metrics.py`: 4000 resamples, seed `20261021`.

What is byte-identical at `efd0568` (the README `forecast-k1` revision), main `3865669`, and pre-release `58c301fd`:

| File | Blob |
| --- | --- |
| `fusion.py` | `96feec9a76dd8af93c915dcd13194ce795d02cea` |
| `rating.py` | `095e22e2038a0641699827512e454df3bae8d4e5` |
| `rank_metrics.py` | `21a080faed50a721173751b8c2018f1b1d0ddff9` |
| `anchors.py` | `1cb031aa6ad6a2d62d3e6228da7fc22de83717b7` |

The forecast system prompt SHA-256 is `f20e596dacc9157cb695e29c63ab59e487a5c5f6dd4510c8ad285f5f9745edcd` at all three revisions. The forecast task text SHA-256 is `4374696de5c606470815b72c53ac5382afbeab79501f651c86608241fe1800b7` at all three. Those are the digests the promotion report printed for the two arms. `LOCKED_HOLISTIC_TEMPERATURE` is 0.7. The source-text cap is 240000 characters.

Every difference that remains:

1. **Paper set.** Promotion scored a re-drawn holdout of 37 (20 accept / 17 reject) with overlap 5/15 against holdout-15. The README number is one draw on that 15 (5 accept / 10 reject). Tune-30 is a third, unrecovered set. The two published tune draws already move from 0.656 to 0.582.
2. **How the public sample was filled.** The original 100 used `--extend-rejected 280` and then `220`. The promotion re-fetch did not, and `clear_reject` came up 3 short. Ten holdout-15 papers are absent from the 37, so the new holdout is not a superset of the old one.
3. **Which interval is printed.** The README interval is unstratified, seed `20261021`. The promotion arm contrast is a paired stratified bootstrap, seed `20261029`, difference pre-release minus main. The point estimator is the same function. The promotion Rank Spearman difference is −0.0339 [−0.1815, 0.1079].
4. **Effort and transport of the model call.** The README does not record APR effort for the 0.761 or 0.656 draws. It records OpenJudge effort as xhigh on the comparison rows only. The promotion run, and the redraw below, used Cursor `grok-4.7` at effort xhigh. An earlier harness that is not `forecast-k1` used the model id `grok-4.7-build-fast`. Whether round 11 was that id is not written on the 0.761 row. Cursor answers do not take the temperature knob. The adapter still sends temperature 0.7.
5. **Streaming.** From `efd0568` to main `3865669`, `model_exec.py` changes by five lines: the chat body sets `stream` true, and `_urlopen_transport` reads `stream_transport.urlopen_chat`. A non-streaming JSON body is still accepted. Pre-release `58c301fd` adds the longer timeout, exchange reuse, a tomllib fallback, and an awaiting-request check. Those do not change the forecast prompt functions or `ForecastJudgment`. `public_calibration.py` differs by the git-unavailable fail-closed check, not by the metric.
6. **Other prompt files.** `prompts/sar_grounded_review_v1.md` and `prompts/proposal_audit_v1.md` change between `efd0568` and main. Cheap forecast does not call them. The promotion run hashed the forecast system prompt and the task text, and all 37 paired user packets matched across the two arms.
7. **Truncation.** The promotion run cut 2 of 37 packets at 240000 characters. The holdout-15 redraw below cut 0 of 15. Two truncated packets cannot move a 37-paper Spearman from 0.76 to 0.45, and this redraw was not truncated at all.
8. **Class balance.** 20/17 versus 5/10. AUC and Spearman are not on the same mix of papers.

## Harness audit

No bug found that would assign the wrong label, invert the score, or reuse another paper's answer.

- **Join.** `scripts/rank_eval.py` reads `papers/*/project/our_score.json` and joins to `papers/*/heldout/human.json` one directory above the score file. The rank target is the mean of `ratings`. AUC uses `decision == "accept"`. Stratum labels for the promotion interval came from the draw file, not from `human.json`.
- **Target and direction.** The promotion rank block is `rank_score` against that mean. The bin block, which uses `our_bin`, is lower (Spearman 0.4364 / 0.4558) and is also far from 0.761, so reporting the bin by mistake does not explain the README number. Rank bias is about −0.49 on both arms (score minus human mean). The correlation is positive, so the score is not flipped.
- **Loopback.** The promotion server bound `127.0.0.1`, read no API key, and indexed SHA-256 of `prompt.txt` to the text of `answer.json` once at process start. The key is the user message only. A duplicate key in one arm aborts at startup. A missing answer is skipped and the index is not reloaded. The servers were restarted after all 37 answer files existed and before the batch. The batch then scored 37/37 per arm, 0 parse failures, 74 calls, first attempt only. A request can receive another paper's answer only if that answer was stored beside a byte-identical user message. The two arms were separate processes. No paper had the same rank on both arms. Per-paper rows were not kept in the transcript or on this machine, so the published 0.4469 / 0.4807 were not recomputed from the raw vectors here. They are the `agreement()` figures the run printed.
- **Cache.** The index is fixed at startup. A smoke call before the restart could not have filled the batch index. This investigation's own loopback, below, had zero unmatched user messages.
- **Truncation.** See the configuration list. The cap is recorded in `main_text`. It is not silent.

## Reproduction

Holdout-15 was fetched into a gitignored cache and scored with the locked flags. No xAI, OpenAI, or `APR_MODEL_API_KEY` was set. Main `3865669` ran `OpenAICompatibleAdapter` against a loopback on `127.0.0.1`. Each answer was one Cursor `grok-4.7` call at effort xhigh, written to `answer.json` and checked with `ForecastJudgment` before the server started. The server was started only after all 15 answers existed. All 15 requests returned HTTP 200. The user-message digest matched the prompt that `full_text_packet` plus `forecast_task_text()` builds from the M0 packet. Parse failures: 0. Papers at the 240000 cap: 0.

Five of the 15 no longer answer on the direct OpenReview notes route (browser check). They were recovered from the public listing search, and the PDF came from an arXiv file whose title matched. Text extraction was `pdftotext -layout`. The scored set is still the holdout-15 id list (digest above), with 5 accepts and 10 rejects.

`scripts/rank_eval.py --mode locked --resamples 4000` on that cache, which is `rank_metrics.py` seed `20261021`:

| | n | Spearman | AUC | MAE | Bias |
| --- | ---: | --- | --- | --- | --- |
| README r11, as published | 15 | 0.761 [0.389, 0.939] | 0.800 | 1.330 | −0.848 |
| This redraw, main `3865669` | 15 | 0.576 [0.091, 0.833] | 0.700 [0.400, 0.944] | 1.228 [0.852, 1.615] | −0.604 [−1.267, 0.051] |
| Same answers, pre-release `58c301fd` | 15 | 0.576 [0.091, 0.833] | 0.700 [0.400, 0.944] | same rows | same rows |
| Promotion pre-release / main | 37 | 0.447 / 0.481 | 0.715 / 0.750 | 1.393 / 1.316 | −0.487 / −0.492 |

Kendall on the redraw is 0.402 [0.043, 0.674]. Rank scores on the redraw span 3.82 to 6.68 (mean 4.87, population standard deviation 0.79). The published Grok holdout scores, shuffled and without ids, span 3.89 to 6.38. The redraw is not collapsed and not flipped. `rank_score` equals the `forecast-k1` candidate on the scored papers. Human means span 2.75 to 8.00.

Of 4000 bootstrap resamples of this redraw, 3586 (89.7%) fall below the published 0.761, and 1332 (33.3%) fall below the promotion main point 0.481. Both points are inside the 95% interval. The upper end of the interval is 0.833, so 0.761 is a high draw relative to this sample and still allowed by it.

The pre-release column is not a second model sample. It is the same 15 answer files executed by `58c301fd`. User-message digests matched again (15/15 HTTP 200, zero mismatches). Rank scores and venue bins matched 15/15, maximum absolute rank difference 0. The two revisions do not disagree on this configuration.

What the redraw does not settle: the October 25 answers are not in the transcripts that were searched, and the README does not record APR effort. A Cursor xhigh call is the promotion transport, not a proven replay of round 11. Temperature 0.7 was sent by the adapter and was not applied by the answer generator.

## Recommended fix

Do not treat 0.447 / 0.481 as a failed reproduction of 0.761. Say, in the promotion report and in the README scope note, that the ICLR arm is the re-drawn 37-paper holdout (digest `93672fd7…`) and that 0.761 is one `forecast-k1` draw on holdout-15 (digest `b748590b…`), which was opened during tuning.

If a later gate is supposed to protect the README claim, score that holdout-15 with the locked flags, publish the `rank_metrics.py` interval (4000, seed `20261021`), and record the model id and the effort next to the number. Keep the id list gitignored. Keep the per-paper score rows in the gitignored cache so the next check can recompute the correlation. The promotion run did not leave those rows.

Do not change `forecast-k1`, `rank_metrics.py`, or the promotion verdict for this gap. The formula files match from the README revision through both scored refs, and the paired ICLR intervals already contain zero. The promotion FAIL remains the one-sided CVPR A1 leak scan.
