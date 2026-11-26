# Anonymized rerun of Gate 1

Date: 2026-11-25. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score. The private holdout was not read. OpenReview was not called. Sealed sets S24-C1 and S24-C2 were not opened.

The rerun is locked forecast-k1: one grok-4.7 call per paper per arm, reasoning effort xhigh, through Cursor, with no xAI API key. The candidate arm uses the pre-release forecast task with grounded weaknesses. The main arm uses the forecast task from the Gate 1 comparison arm (`481c694`), which is the same task text still produced by `forecast_task_text` without grounded weaknesses. The manuscript section is the Gate 1 prompt's source text after `anonymize_iclr_submission`. Figure and contribution lines keep their place, with emails, named GitHub links, and camera-ready phrases redacted.

## What text could be recovered

Gate 1 worker transcripts still held the prompt text for 123 of 192 dev papers. The other 69 prompts were stored with an empty body. An exact-title arXiv search of those 69, rate-limited at about 3 seconds, returned no exact title match (0 downloads, 0 API errors). Their OpenReview PDF URLs are in the agent store at `release-assets/dev192-missing-pdf-urls.txt` and are not in this repository. No further fetch was attempted.

Recovered papers by tier: clear reject 15, borderline reject 32, borderline accept 35, clear accept 41. Missing papers by tier: clear reject 33, borderline reject 16, borderline accept 13, clear accept 7. The missing set is weighted toward clear rejects, so this rerun over-represents accepted, camera-ready papers relative to the locked dev set.

## Leak markers

Counts are papers in the recovered set with at least one marker. "First 40 lines, affiliation word" is a university, institute, laboratory, department, or company word in the first 40 lines. It is not the audit script's `affiliation_before_abstract` count: in this extract the word Abstract is often not on its own line, so that counter walks most of the source and mixes author blocks with later prose. Emails, named GitHub links, status lines, and acknowledgement headings are counted anywhere in the source.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear reject | 15 | 5 | 7 | 0 | 4 | 9 | 0 | 0 | 0 | 0 | 2 |
| borderline reject | 32 | 6 | 8 | 0 | 2 | 5 | 0 | 0 | 0 | 0 | 3 |
| borderline accept | 35 | 21 | 19 | 1 | 15 | 22 | 0 | 0 | 0 | 0 | 3 |
| clear accept | 41 | 26 | 25 | 0 | 26 | 38 | 0 | 0 | 0 | 0 | 2 |

Before anonymization, an email is present in 26 of 41 clear accepts and 5 of 15 clear rejects. An affiliation word in the first 40 lines is present in 38 clear accepts and 9 clear rejects. After anonymization, emails, named GitHub links, status lines, and acknowledgement headings are 0 in every tier. Affiliation words in the first 40 lines remain in 10 papers.

`scripts/audit_submission_leaks.py` counts marker hits, not papers. Its `affiliation_before_abstract` counter walks the whole extract when Abstract is not on its own line, so those hits mix the author block with later prose. Raw, anonymizer not applied:

| Tier | Papers | Papers with any marker | Email hits | GitHub hits | Status hits | Ack hits | Affiliation-before-abstract hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear reject | 15 | 14 | 9 | 10 | 0 | 4 | 42 |
| borderline reject | 32 | 26 | 13 | 15 | 0 | 2 | 95 |
| borderline accept | 35 | 34 | 47 | 56 | 6 | 15 | 180 |
| clear accept | 41 | 41 | 54 | 43 | 0 | 26 | 278 |

After `anonymize_iclr_submission`:

| Tier | Papers | Papers with any marker | Email hits | GitHub hits | Status hits | Ack hits | Affiliation-before-abstract hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear reject | 15 | 8 | 0 | 0 | 0 | 0 | 23 |
| borderline reject | 32 | 15 | 0 | 0 | 0 | 0 | 64 |
| borderline accept | 35 | 19 | 0 | 0 | 0 | 0 | 59 |
| clear accept | 41 | 17 | 0 | 0 | 0 | 0 | 88 |

The script reports missing files 69 and empty files 69 out of 192 manifest rows. Email, named-GitHub, status, and acknowledgement hits are 0 after anonymization. Residual markers are affiliation hits of the kind above.

## Raw versus anonymized

Paired on the papers that have both a recovered Gate 1 score and an anonymized forecast. Delta is anonymized minus raw. One paper has no original main forecast, so the main arm has one fewer row than the candidate arm. Bootstrap: stratified by tier, 4000 resamples, seed 20261029, linear percentiles 0.025 and 0.975. IPW uses the same count-pooled ICLR 2024+2025 accept/reject/withdraw mixture as the 2026-11-25 reanalysis (https://papercopilot.com/statistics/iclr-statistics/iclr-2025-statistics/, retrieved 2026-11-25). Weights are fixed by tier. The LORO gap is APR's Spearman with the other reviewers' mean, minus the held-out reviewer's Spearman with that same mean. A negative gap means APR agrees less than a held-out human.

The anonymized numbers are a new model draw. There is no second draw of the original text, so each interval bounds anonymization and rerun noise together.

Candidate, n=123.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.4875 | 0.5505 | 0.0630 [-0.0046, 0.1314] |
| IPW Spearman | 0.3834 | 0.4290 | 0.0456 [-0.0578, 0.1366] |
| Accept/reject AUC | 0.7782 | 0.8207 | 0.0424 [-0.0032, 0.0867] |
| MAE | 1.2522 | 1.2101 | -0.0421 [-0.1214, 0.0366] |
| Cohen kappa, venue bin | 0.1164 | 0.0637 | -0.0527 [-0.1451, 0.0380] |
| Quadratic weighted kappa, venue bin | 0.3953 | 0.4221 | 0.0268 [-0.0455, 0.0952] |
| LORO Spearman gap | -0.1528 | -0.0925 | 0.0603 [-0.0054, 0.1255] |
| Adjacent AUC, clear reject vs borderline reject | 0.6094 | 0.5604 | -0.0490 [-0.1500, 0.0448] |
| Adjacent AUC, borderline reject vs borderline accept | 0.6741 | 0.7040 | 0.0299 [-0.0549, 0.1112] |
| Adjacent AUC, borderline accept vs clear accept | 0.6544 | 0.6739 | 0.0195 [-0.0578, 0.0997] |

On the candidate arm, the Spearman, IPW Spearman, AUC, MAE, kappa, QWK, LORO-gap, and adjacent-AUC intervals all include zero. The Spearman interval is 0.0630 [-0.0046, 0.1314]. Its lower end is -0.0046, so an original-text rank benefit larger than 0.0046 is outside the interval.

Main, n=122.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.4510 | 0.5151 | 0.0640 [-0.0087, 0.1377] |
| IPW Spearman | 0.3399 | 0.4030 | 0.0631 [-0.0181, 0.1533] |
| Accept/reject AUC | 0.7792 | 0.8093 | 0.0301 [-0.0129, 0.0744] |
| MAE | 1.3362 | 1.2376 | -0.0985 [-0.1633, -0.0327] |
| Cohen kappa, venue bin | 0.0680 | 0.1588 | 0.0909 [0.0130, 0.1702] |
| Quadratic weighted kappa, venue bin | 0.3321 | 0.4169 | 0.0848 [0.0136, 0.1619] |
| LORO Spearman gap | -0.1781 | -0.1284 | 0.0497 [-0.0199, 0.1199] |
| Adjacent AUC, clear reject vs borderline reject | 0.5094 | 0.5823 | 0.0729 [-0.0146, 0.1625] |
| Adjacent AUC, borderline reject vs borderline accept | 0.6746 | 0.6888 | 0.0143 [-0.0437, 0.0790] |
| Adjacent AUC, borderline accept vs clear accept | 0.6543 | 0.6521 | -0.0021 [-0.0639, 0.0596] |

On the main arm, Spearman, IPW Spearman, AUC, the LORO gap, and the three adjacent AUCs include zero. MAE -0.0985 [-0.1633, -0.0327], kappa 0.0909 [0.0130, 0.1702], and QWK 0.0848 [0.0136, 0.1619] lie entirely where the anonymized text scores better.

## Does candidate still beat main?

The difference is candidate minus main, on the 122 papers where both arms have an original score and an anonymized score. On the full 192, Gate 1 reported a Spearman difference of +0.0466 whose interval included zero, and an MAE difference of −0.0740 whose interval lay below zero. Raw Spearman on this recovered slice is lower than those published figures because the missing papers are mostly clear rejects.

Raw inputs, n=122.

| Metric | Main | Candidate | Candidate − main |
| --- | ---: | ---: | ---: |
| Spearman | 0.4510 | 0.4872 | 0.0361 [-0.0528, 0.1256] |
| IPW Spearman | 0.3399 | 0.3820 | 0.0422 [-0.0373, 0.1257] |
| Accept/reject AUC | 0.7792 | 0.7773 | -0.0019 [-0.0521, 0.0462] |
| MAE | 1.3362 | 1.2487 | -0.0875 [-0.1587, -0.0122] |
| Cohen kappa, venue bin | 0.0680 | 0.1195 | 0.0515 [-0.0449, 0.1524] |
| Quadratic weighted kappa, venue bin | 0.3321 | 0.3918 | 0.0597 [-0.0329, 0.1485] |
| LORO Spearman gap | -0.1781 | -0.1569 | 0.0212 [-0.0652, 0.1042] |
| Adjacent AUC, clear reject vs borderline reject | 0.5094 | 0.6094 | 0.1000 [-0.0042, 0.2125] |
| Adjacent AUC, borderline reject vs borderline accept | 0.6746 | 0.6741 | -0.0004 [-0.0598, 0.0598] |
| Adjacent AUC, borderline accept vs clear accept | 0.6543 | 0.6543 | 0.0000 [-0.0897, 0.0850] |

Anonymized inputs, n=122.

| Metric | Main | Candidate | Candidate − main |
| --- | ---: | ---: | ---: |
| Spearman | 0.5151 | 0.5476 | 0.0325 [-0.0437, 0.1095] |
| IPW Spearman | 0.4030 | 0.4263 | 0.0233 [-0.0812, 0.1192] |
| Accept/reject AUC | 0.8093 | 0.8192 | 0.0099 [-0.0371, 0.0542] |
| MAE | 1.2376 | 1.2111 | -0.0265 [-0.0982, 0.0495] |
| Cohen kappa, venue bin | 0.1588 | 0.0665 | -0.0923 [-0.1788, 0.0027] |
| Quadratic weighted kappa, venue bin | 0.4169 | 0.4191 | 0.0022 [-0.0855, 0.0901] |
| LORO Spearman gap | -0.1284 | -0.0983 | 0.0301 [-0.0465, 0.1086] |
| Adjacent AUC, clear reject vs borderline reject | 0.5823 | 0.5604 | -0.0219 [-0.1063, 0.0615] |
| Adjacent AUC, borderline reject vs borderline accept | 0.6888 | 0.7040 | 0.0152 [-0.0656, 0.0920] |
| Adjacent AUC, borderline accept vs clear accept | 0.6521 | 0.6700 | 0.0179 [-0.0604, 0.1000] |

On the original scores, the Spearman contrast is 0.0361 [-0.0528, 0.1256] and the MAE contrast is -0.0875 [-0.1587, -0.0122]. On the anonymized scores, the Spearman contrast is 0.0325 [-0.0437, 0.1095] and the MAE contrast is -0.0265 [-0.0982, 0.0495]. A negative MAE difference means the candidate is closer to the human mean.

## Conclusion

The recovered source carries a decision-correlated leak. Emails and first-page affiliation words are much more common on clear accepts than on clear rejects. Anonymization removes every email, named GitHub link, status line, and acknowledgement heading in this set. 10 papers still have an affiliation word in the first 40 lines.

That leak did not carry a detectable share of the rank signal. No Spearman, IPW Spearman, accept/reject AUC, adjacent-AUC, or LORO-gap interval lies where the original text scores better. The candidate Spearman lower bound leaves room for at most 0.0046 of original-text rank benefit. The point estimates move the other way: anonymized Spearman and AUC are higher, and anonymized MAE is lower. On the main arm, MAE, kappa, and QWK improve after anonymization and those three intervals exclude zero. The anonymized scores are a fresh draw, with no second draw of the original prompt, so each interval bounds the shortcut and rerun noise together.

The candidate-over-main MAE result is still present on the original scores of these 122 papers: -0.0875 [-0.1587, -0.0122], the same direction as the published 192-paper difference of −0.0740. After anonymization it is -0.0265 [-0.0982, 0.0495]. That interval includes zero, so the MAE advantage is not detected once the source is anonymized. The anonymized point estimate stays negative, at about −0.03. Spearman stays compatible with no arm difference, on the original scores and the anonymized scores, which matches the published Gate 1 Spearman interval. This slice is not the full dev set. Its raw Spearman is about 0.45 for main and 0.49 for the candidate, against the published 0.6002 and 0.6467, because 33 of the 69 missing papers are clear rejects. A rerun of those 69 could move both the shortcut delta and the arm contrast. Their PDF URLs are only in the agent store.
