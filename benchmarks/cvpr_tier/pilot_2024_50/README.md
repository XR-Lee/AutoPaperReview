# CVPR 2024 accepted-tier pilot (n=50)

Baseline measurement of locked `forecast-k1` (`holistic_mode` `forecast`, one panel) on a stratified CVPR 2024 sample. The reviewer is `grok-4.7-cursor` through the replay file-handoff backend. This directory does not change prompts or scoring.

PDFs, extracted text, answers, and per-paper tiers stay in the gitignored cache. `manifest.json` lists paper ids and seeds and does not attach a tier to an id. `metrics.json` holds the aggregate report.

## Sample

Pilot seed `20261029`. Quotas keep the 30/40/50 ratio at n=50: 13 oral, 17 highlight, 20 poster. All three quotas filled. Within each tier the draw sorts by paper id, then shuffles with `Random(f"{seed}:{tier}")`.

A disjoint confirmation set was drawn and not judged. Seed `20261030`, quotas 30 oral / 40 highlight / 50 poster (120 papers). Overlap with the pilot is 0. The report joined scores only to the pilot label file.

The live 2024 catalog is complete on the virtual JSON `decision` field: oral 90, highlight 324, poster 2302, unlabeled 0. This fetch found 2708 papers with a CVF Open Access PDF. Eight posters have no PDF, so the poster pool was 2294. Oral and highlight pools were complete. The docs record 2715 PDFs. This commit does not change that doc.

## Metrics

The score is `forecast_mean` (the mean of three reviewers' expected official ratings). It is not `predicted_mean`, and the named tier is not a cutoff of the score. Bootstrap: 4000 resamples, seed `20261029`. Intervals are the percentile 95% interval.

| model | backend | mode | n | spearman | oral vs poster | highlight vs poster | oral vs highlight | spread stdev | clustered |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| grok-4.7-cursor | replay | forecast | 50 | 0.276 [0.014, 0.505] | 0.713 [0.511, 0.883] | 0.566 [0.366, 0.754] | 0.627 [0.415, 0.827] | 0.665 | yes |

Rows of the confusion matrix are the program tier. Columns are the tier the model named. No numeric cutoff is applied.

| true \ predicted | reject | poster | highlight | oral |
| --- | --- | --- | --- | --- |
| oral | 0 | 5 | 6 | 2 |
| highlight | 0 | 8 | 8 | 1 |
| poster | 0 | 13 | 7 | 0 |

The named tier matched the program tier on 23 of 50 papers. The model named oral 3 times.

## Score by program tier

Standard deviation is the sample standard deviation. The same aggregates, including counts of scores rounded to 0.1, are in `metrics.json`.

| tier | n | mean | stdev | min | median | max |
| --- | --- | --- | --- | --- | --- | --- |
| oral | 13 | 6.8810 | 0.6680 | 5.9733 | 6.8967 | 7.9033 |
| highlight | 17 | 6.6355 | 0.6943 | 5.8333 | 6.7733 | 7.6867 |
| poster | 20 | 6.3927 | 0.5940 | 5.6433 | 6.0633 | 7.5967 |
| all | 50 | 6.6022 | 0.6652 | 5.6433 | 6.3967 | 7.9033 |

Seventeen distinct scores at 0.1 resolution, on a range of 5.6433 to 7.9033. The spread diagnostic is clustered because the sample standard deviation is under 1.

## Usage

| calls | tokens | parse failures | retries |
| --- | --- | --- | --- |
| 50 | unavailable | 0 | 0 |

The replay backend writes one prompt per paper and reads the answer file. It does not meter tokens. All 50 answers parsed on the run that produced this report. No answer was rewritten after a parse error.

## Limits

The sample is accepted papers, so the loop cannot measure accept versus reject. A tier is a program decision, not a review score. These numbers are not written back into a cutoff or a venue profile.
