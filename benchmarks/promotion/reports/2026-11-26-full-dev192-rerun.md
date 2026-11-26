# Full dev set after the missing texts

Date: 2026-11-26. Aggregates only. The private holdout was not read. Sealed sets S24-C1 and S24-C2 were not opened. OpenReview was not called from this machine. The new texts were supplied as pdftotext from a browser download.

## Mapping

Mapped 69, unmatched 1, missing 0. Filename prefixes match the mapped dev papers, one each. The unmatched file matches none of the 192 titles and is left out. The mapped papers are the ones whose Gate 1 prompt text was empty: clear reject 33, borderline reject 16, borderline accept 13, clear accept 7.

pdftotext split small-caps after the first letter (`A BSTRACT`). Those letters are joined before the audit and before the forecast. A normal sentence is not joined. After that join, every new file has an Abstract heading the anonymizer can see.

Of the mapped papers, 58 still say anonymous authors, 10 say published as a conference paper, and 1 says neither. In this batch the banner and the venue decision are the same split: all 10 published PDFs are accepts, and all 58 anonymous PDFs are rejects.

| PDF banner | Accept | Reject |
| --- | ---: | ---: |
| anonymous | 0 | 58 |
| published | 10 | 0 |
| neither | 0 | 1 |

Characters removed by the anonymizer, after the small-caps join: published median 1450 characters (mean 1545, n=10); anonymous median 925 characters (mean 956, n=58); neither median 564 characters (mean 564, n=1). Published copies by rating tier: 6 in the clear-accept tier and 4 in the borderline-accept tier. The clear-reject texts are anonymous submissions.

## Leak markers

Counts are papers with at least one marker. The first block is the 123 Gate 1 source extracts. The second is the 69 new texts after the small-caps join. The third adds them. Status on the new texts is mostly the venue banner (`Under review` or `Published as`), which the Gate 1 extracts usually did not keep, so the pooled status column is not a camera-ready count. Emails, named GitHub links, acknowledgement headings, and affiliation words in the first 40 lines are the comparable columns.

Recovered Gate 1 sources.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear reject | 15 | 5 | 7 | 0 | 4 | 9 | 0 | 0 | 0 | 0 | 2 |
| borderline reject | 32 | 6 | 8 | 0 | 2 | 5 | 0 | 0 | 0 | 0 | 3 |
| borderline accept | 35 | 21 | 19 | 1 | 15 | 22 | 0 | 0 | 0 | 0 | 3 |
| clear accept | 41 | 26 | 25 | 0 | 26 | 38 | 0 | 0 | 0 | 0 | 2 |

New pdftotext, after the small-caps join.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear reject | 33 | 0 | 9 | 33 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| borderline reject | 16 | 0 | 4 | 16 | 0 | 2 | 0 | 0 | 0 | 0 | 2 |
| borderline accept | 13 | 1 | 6 | 13 | 3 | 2 | 0 | 0 | 0 | 0 | 1 |
| clear accept | 7 | 3 | 2 | 7 | 2 | 5 | 0 | 0 | 0 | 0 | 1 |

Full set.

| Tier | Papers | Email | GitHub | Status | Ack | Affil. in first 40 | Email after | GitHub after | Status after | Ack after | Affil. in first 40 after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear reject | 48 | 5 | 16 | 33 | 5 | 9 | 0 | 0 | 0 | 0 | 2 |
| borderline reject | 48 | 6 | 12 | 16 | 2 | 7 | 0 | 0 | 0 | 0 | 5 |
| borderline accept | 48 | 22 | 25 | 14 | 18 | 24 | 0 | 0 | 0 | 0 | 4 |
| clear accept | 48 | 29 | 27 | 7 | 28 | 43 | 0 | 0 | 0 | 0 | 3 |

## Scores

Locked forecast-k1, grok-4.7, effort xhigh, through Cursor. The candidate task is pre-release with grounded weaknesses. The main task is the Gate 1 comparison task, which `forecast_task_text` still emits without grounded weaknesses. For the original 123 papers, raw is the Gate 1 forecast and anonymized is the rerun from the previous report. For the 69 new papers, both raw and anonymized are a fresh draw on the supplied text, before and after `anonymize_iclr_submission`. Delta is anonymized minus raw. Bootstrap: stratified by tier, 4000 resamples, seed 20261029, linear percentiles 0.025 and 0.975. IPW uses the count-pooled ICLR 2024+2025 accept/reject/withdraw mixture (https://papercopilot.com/statistics/iclr-statistics/iclr-2025-statistics/, retrieved 2026-11-25).

The pooled raw column mixes a Gate 1 draw on 123 papers with a fresh draw on 69 papers. The fresh-only tables below are the paired comparison that holds the session fixed. One original Gate 1 forecast was never recovered, so that arm-paper pair is absent.

### Pooled

Candidate, n=192.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.6548 | 0.6447 | -0.0101 [-0.0635, 0.0392] |
| IPW Spearman | 0.5655 | 0.5362 | -0.0292 [-0.0948, 0.0309] |
| Accept/reject AUC | 0.8481 | 0.8712 | 0.0231 [-0.0101, 0.0543] |
| MAE | 1.1151 | 1.1247 | 0.0096 [-0.0514, 0.0696] |
| Cohen kappa, venue bin | 0.2030 | 0.1677 | -0.0353 [-0.1096, 0.0399] |
| Quadratic weighted kappa, venue bin | 0.5269 | 0.5243 | -0.0026 [-0.0578, 0.0499] |
| LORO Spearman gap | -0.0429 | -0.0504 | -0.0074 [-0.0593, 0.0406] |
| Adjacent AUC, clear reject vs borderline reject | 0.6910 | 0.6341 | -0.0569 [-0.1233, 0.0072] |
| Adjacent AUC, borderline reject vs borderline accept | 0.7363 | 0.7391 | 0.0028 [-0.0606, 0.0634] |
| Adjacent AUC, borderline accept vs clear accept | 0.6513 | 0.6706 | 0.0193 [-0.0451, 0.0831] |

Spearman is -0.0101 [-0.0635, 0.0392]; the interval includes zero. MAE is 0.0096 [-0.0514, 0.0696]; the interval includes zero. AUC is 0.0231 [-0.0101, 0.0543]; the interval includes zero. IPW Spearman is -0.0292 [-0.0948, 0.0309]; the interval includes zero. Kappa is -0.0353 [-0.1096, 0.0399]; the interval includes zero. QWK is -0.0026 [-0.0578, 0.0499]; the interval includes zero. LORO gap is -0.0074 [-0.0593, 0.0406]; the interval includes zero.

Main, n=191.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.6157 | 0.6518 | 0.0362 [-0.0101, 0.0834] |
| IPW Spearman | 0.5201 | 0.5506 | 0.0305 [-0.0216, 0.0859] |
| Accept/reject AUC | 0.8507 | 0.8679 | 0.0172 [-0.0137, 0.0490] |
| MAE | 1.1789 | 1.1172 | -0.0617 [-0.1116, -0.0122] |
| Cohen kappa, venue bin | 0.1765 | 0.2328 | 0.0563 [-0.0110, 0.1256] |
| Quadratic weighted kappa, venue bin | 0.4728 | 0.5258 | 0.0531 [-0.0067, 0.1139] |
| LORO Spearman gap | -0.0732 | -0.0440 | 0.0293 [-0.0153, 0.0739] |
| Adjacent AUC, clear reject vs borderline reject | 0.6671 | 0.6816 | 0.0145 [-0.0401, 0.0701] |
| Adjacent AUC, borderline reject vs borderline accept | 0.7235 | 0.7478 | 0.0243 [-0.0239, 0.0792] |
| Adjacent AUC, borderline accept vs clear accept | 0.6325 | 0.6543 | 0.0217 [-0.0357, 0.0805] |

Spearman is 0.0362 [-0.0101, 0.0834]; the interval includes zero. MAE is -0.0617 [-0.1116, -0.0122]; the interval lies where the anonymized text scores better. AUC is 0.0172 [-0.0137, 0.0490]; the interval includes zero. IPW Spearman is 0.0305 [-0.0216, 0.0859]; the interval includes zero. Kappa is 0.0563 [-0.0110, 0.1256]; the interval includes zero. QWK is 0.0531 [-0.0067, 0.1139]; the interval includes zero. LORO gap is 0.0293 [-0.0153, 0.0739]; the interval includes zero.

Mean score shift, anonymized minus raw, candidate -0.0138 [-0.0771, 0.0481], main 0.0437 [-0.0127, 0.0970].

### Fresh draw on the 69 new papers

Both conditions are a new call. This is the shortcut comparison that does not mix in the original Gate 1 sample.

Candidate, fresh texts, n=69.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.7798 | 0.6431 | -0.1366 [-0.2549, -0.0444] |
| IPW Spearman | 0.7272 | 0.5921 | -0.1351 [-0.2497, -0.0469] |
| Accept/reject AUC | 0.9508 | 0.9136 | -0.0373 [-0.1363, 0.0164] |
| MAE | 0.8707 | 0.9725 | 0.1018 [0.0151, 0.1898] |
| Cohen kappa, venue bin | 0.2837 | 0.2879 | 0.0042 [-0.1253, 0.1283] |
| Quadratic weighted kappa, venue bin | 0.6866 | 0.6144 | -0.0722 [-0.1450, -0.0127] |
| LORO Spearman gap | 0.1437 | 0.0227 | -0.1210 [-0.2379, -0.0341] |
| Adjacent AUC, clear reject vs borderline reject | 0.6771 | 0.5975 | -0.0795 [-0.1781, 0.0208] |
| Adjacent AUC, borderline reject vs borderline accept | 0.8846 | 0.8125 | -0.0721 [-0.1924, 0.0192] |
| Adjacent AUC, borderline accept vs clear accept | 0.7253 | 0.6154 | -0.1099 [-0.2967, 0.0330] |

Spearman is -0.1366 [-0.2549, -0.0444]; the interval lies where the raw text scores better. MAE is 0.1018 [0.0151, 0.1898]; the interval lies where the raw text scores better.

Main, fresh texts, n=69.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.7400 | 0.7118 | -0.0282 [-0.0902, 0.0312] |
| IPW Spearman | 0.6874 | 0.6475 | -0.0400 [-0.1001, 0.0185] |
| Accept/reject AUC | 0.9492 | 0.9288 | -0.0203 [-0.0599, 0.0094] |
| MAE | 0.9009 | 0.9042 | 0.0033 [-0.0688, 0.0771] |
| Cohen kappa, venue bin | 0.3157 | 0.2835 | -0.0322 [-0.1742, 0.1072] |
| Quadratic weighted kappa, venue bin | 0.6533 | 0.6243 | -0.0289 [-0.1366, 0.0863] |
| LORO Spearman gap | 0.1098 | 0.0821 | -0.0278 [-0.0872, 0.0297] |
| Adjacent AUC, clear reject vs borderline reject | 0.6913 | 0.6392 | -0.0521 [-0.1326, 0.0303] |
| Adjacent AUC, borderline reject vs borderline accept | 0.8413 | 0.8654 | 0.0240 [-0.0673, 0.1346] |
| Adjacent AUC, borderline accept vs clear accept | 0.5604 | 0.6868 | 0.1264 [-0.0495, 0.3132] |

Spearman is -0.0282 [-0.0902, 0.0312]; the interval includes zero. MAE is 0.0033 [-0.0688, 0.0771]; the interval includes zero.

Mean score shift on the fresh texts, candidate -0.0481 [-0.1343, 0.0409], main -0.0281 [-0.1205, 0.0638].

### Published copies versus anonymous submissions

Same fresh draws, split by the banner on the new PDF. Published n=10 candidate and 10 main. Anonymous n=58 candidate and 58 main. One paper has neither banner and stays in the fresh total only. In this batch the banner and the venue decision are the same split: all 10 published PDFs are accepts, and all 58 anonymous PDFs are rejects. A slice with one decision has no accept/reject AUC. Rank metrics on the published slice compare forecasts with the human mean inside the accepts. Rank metrics on the anonymous slice compare them inside the rejects. A shortcut that is the camera-ready author block would move the published slice and leave the anonymous slice where it is.

Candidate, published PDFs, n=10.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.3816 | -0.3323 | -0.7139 [-1.5426, 0.2821] |
| IPW Spearman | 0.4744 | -0.3374 | -0.8118 [-1.5445, 0.5294] |
| Accept/reject AUC | undefined | undefined | undefined |
| MAE | 1.2173 | 1.3990 | 0.1817 [-0.0767, 0.4473] |
| Cohen kappa, venue bin | 0.0909 | 0.2727 | 0.1818 [-0.3727, 0.6897] |
| Quadratic weighted kappa, venue bin | -0.1818 | -0.0909 | 0.0909 [-0.3104, 0.6000] |
| LORO Spearman gap | -0.2034 | -0.8893 | -0.6859 [-1.4811, 0.2394] |
| Adjacent AUC, clear reject vs borderline reject | undefined | undefined | undefined |
| Adjacent AUC, borderline reject vs borderline accept | undefined | undefined | undefined |
| Adjacent AUC, borderline accept vs clear accept | 0.5833 | 0.2917 | -0.2917 [-0.7083, 0.1667] |

Spearman is -0.7139 [-1.5426, 0.2821]; the interval includes zero. MAE is 0.1817 [-0.0767, 0.4473]; the interval includes zero.

Main, published PDFs, n=10.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | -0.1108 | -0.1354 | -0.0246 [-0.8409, 0.9165] |
| IPW Spearman | -0.3094 | -0.2167 | 0.0926 [-0.9834, 1.1491] |
| Accept/reject AUC | undefined | undefined | undefined |
| MAE | 1.4050 | 1.3833 | -0.0217 [-0.2407, 0.2197] |
| Cohen kappa, venue bin | -0.0909 | -0.0909 | -0.0000 [-0.5820, 0.6694] |
| Quadratic weighted kappa, venue bin | -0.2727 | -0.2727 | 0.0000 [-0.5026, 0.4377] |
| LORO Spearman gap | -0.6803 | -0.7455 | -0.0652 [-0.8081, 0.7994] |
| Adjacent AUC, clear reject vs borderline reject | undefined | undefined | undefined |
| Adjacent AUC, borderline reject vs borderline accept | undefined | undefined | undefined |
| Adjacent AUC, borderline accept vs clear accept | 0.2500 | 0.4167 | 0.1667 [-0.3333, 0.6667] |

Spearman is -0.0246 [-0.8409, 0.9165]; the interval includes zero. MAE is -0.0217 [-0.2407, 0.2197]; the interval includes zero.

Mean score shift on the published copies, candidate -0.4024 [-0.6410, -0.1423] (is negative, so the anonymized text scored lower), main -0.2290 [-0.4633, -0.0137] (is negative, so the anonymized text scored lower).

Candidate, anonymous PDFs, n=58.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.6855 | 0.5312 | -0.1543 [-0.2879, -0.0402] |
| IPW Spearman | 0.6322 | 0.4611 | -0.1711 [-0.3179, -0.0493] |
| Accept/reject AUC | undefined | undefined | undefined |
| MAE | 0.8103 | 0.9154 | 0.1051 [0.0194, 0.1963] |
| Cohen kappa, venue bin | 0.1375 | 0.0927 | -0.0449 [-0.1586, 0.0827] |
| Quadratic weighted kappa, venue bin | 0.3932 | 0.3025 | -0.0906 [-0.2303, 0.0219] |
| LORO Spearman gap | 0.2241 | 0.0934 | -0.1307 [-0.2557, -0.0270] |
| Adjacent AUC, clear reject vs borderline reject | 0.6556 | 0.5747 | -0.0808 [-0.1859, 0.0242] |
| Adjacent AUC, borderline reject vs borderline accept | 0.8889 | 0.7704 | -0.1185 [-0.2963, 0.0148] |
| Adjacent AUC, borderline accept vs clear accept | 0.8889 | 0.4444 | -0.4444 [-0.7778, -0.1111] |

Spearman is -0.1543 [-0.2879, -0.0402]; the interval lies where the raw text scores better. MAE is 0.1051 [0.0194, 0.1963]; the interval lies where the raw text scores better.

Main, anonymous PDFs, n=58.

| Metric | Raw | Anonymized | Anonymized − raw |
| --- | ---: | ---: | ---: |
| Spearman | 0.6317 | 0.6127 | -0.0190 [-0.0922, 0.0452] |
| IPW Spearman | 0.5801 | 0.5469 | -0.0333 [-0.1121, 0.0375] |
| Accept/reject AUC | undefined | undefined | undefined |
| MAE | 0.8197 | 0.8209 | 0.0012 [-0.0664, 0.0758] |
| Cohen kappa, venue bin | 0.2262 | 0.1915 | -0.0347 [-0.2255, 0.1586] |
| Quadratic weighted kappa, venue bin | 0.3376 | 0.3502 | 0.0127 [-0.2361, 0.2505] |
| LORO Spearman gap | 0.1767 | 0.1608 | -0.0160 [-0.0872, 0.0463] |
| Adjacent AUC, clear reject vs borderline reject | 0.6707 | 0.6253 | -0.0455 [-0.1273, 0.0323] |
| Adjacent AUC, borderline reject vs borderline accept | 0.8148 | 0.8296 | 0.0148 [-0.0593, 0.1037] |
| Adjacent AUC, borderline accept vs clear accept | 0.1111 | 0.7222 | 0.6111 [0.2778, 0.8889] |

Spearman is -0.0190 [-0.0922, 0.0452]; the interval includes zero. MAE is 0.0012 [-0.0664, 0.0758]; the interval includes zero.

Mean score shift on the anonymous submissions, candidate 0.0282 [-0.0577, 0.1205] (includes zero), main 0.0321 [-0.0487, 0.1154] (includes zero). The anonymous clear-accept tier in this batch is one paper, so the adjacent AUC against that tier is a one-paper comparison.

Venue-bin changes, candidate: 43/192 pooled, 11/69 fresh, 5/10 published, 5/58 anonymous. Main: 43/191 pooled, 14/69 fresh, 4/10 published, 9/58 anonymous. Candidate flip rate, published minus anonymous, is 0.4138 [0.1138, 0.7138]. Main flip rate, published minus anonymous, is 0.2448 [-0.0552, 0.5793].

## Candidate versus main

Difference is candidate minus main, on the 191 papers scored by both arms.

Raw inputs, n=191.

| Metric | Main | Candidate | Candidate − main |
| --- | ---: | ---: | ---: |
| Spearman | 0.6157 | 0.6547 | 0.0390 [-0.0140, 0.0938] |
| IPW Spearman | 0.5201 | 0.5648 | 0.0447 [-0.0093, 0.1020] |
| Accept/reject AUC | 0.8507 | 0.8476 | -0.0031 [-0.0350, 0.0291] |
| MAE | 1.1789 | 1.1121 | -0.0668 [-0.1216, -0.0109] |
| Cohen kappa, venue bin | 0.1765 | 0.2050 | 0.0286 [-0.0533, 0.1096] |
| Quadratic weighted kappa, venue bin | 0.4728 | 0.5245 | 0.0517 [-0.0172, 0.1234] |
| LORO Spearman gap | -0.0732 | -0.0439 | 0.0293 [-0.0221, 0.0817] |
| Adjacent AUC, clear reject vs borderline reject | 0.6671 | 0.6910 | 0.0239 [-0.0412, 0.0914] |
| Adjacent AUC, borderline reject vs borderline accept | 0.7235 | 0.7363 | 0.0128 [-0.0343, 0.0634] |
| Adjacent AUC, borderline accept vs clear accept | 0.6325 | 0.6514 | 0.0188 [-0.0545, 0.0915] |

Anonymized inputs, n=191.

| Metric | Main | Candidate | Candidate − main |
| --- | ---: | ---: | ---: |
| Spearman | 0.6518 | 0.6425 | -0.0093 [-0.0594, 0.0403] |
| IPW Spearman | 0.5506 | 0.5347 | -0.0159 [-0.0772, 0.0453] |
| Accept/reject AUC | 0.8679 | 0.8700 | 0.0021 [-0.0298, 0.0342] |
| MAE | 1.1172 | 1.1249 | 0.0078 [-0.0479, 0.0613] |
| Cohen kappa, venue bin | 0.2328 | 0.1694 | -0.0633 [-0.1402, 0.0136] |
| Quadratic weighted kappa, venue bin | 0.5258 | 0.5218 | -0.0040 [-0.0648, 0.0584] |
| LORO Spearman gap | -0.0440 | -0.0528 | -0.0088 [-0.0576, 0.0411] |
| Adjacent AUC, clear reject vs borderline reject | 0.6816 | 0.6341 | -0.0475 [-0.1061, 0.0078] |
| Adjacent AUC, borderline reject vs borderline accept | 0.7478 | 0.7391 | -0.0087 [-0.0688, 0.0510] |
| Adjacent AUC, borderline accept vs clear accept | 0.6543 | 0.6673 | 0.0131 [-0.0472, 0.0782] |

On the raw scores, Spearman is 0.0390 [-0.0140, 0.0938]; the interval includes zero. MAE is -0.0668 [-0.1216, -0.0109]; the interval lies where the candidate scores better. On the anonymized scores, Spearman is -0.0093 [-0.0594, 0.0403]; the interval includes zero. MAE is 0.0078 [-0.0479, 0.0613]; the interval includes zero.

## Conclusion

Mapped 69, unmatched 1, missing 0. Emails, named GitHub links, acknowledgement headings, and the venue banner are cleared. A few papers still have an affiliation word in the first 40 lines. Characters removed by the anonymizer, after the small-caps join: published median 1450 characters (mean 1545, n=10); anonymous median 925 characters (mean 956, n=58); neither median 564 characters (mean 564, n=1).

In this batch the banner and the venue decision are the same split: all 10 published PDFs are accepts, and all 58 anonymous PDFs are rejects. The fresh raw scores are the ones that still contain that banner. After it is removed, candidate AUC is -0.0373 [-0.1363, 0.0164]; the interval includes zero. Main AUC is -0.0203 [-0.0599, 0.0094]; the interval includes zero.

The camera-ready author block is the published slice. On those copies the candidate level shift is negative, so the anonymized text scored lower. The main level shift is negative, so the anonymized text scored lower. Candidate bins change on 5/10 of them, against 5/58 of the anonymous submissions; the flip-rate gap is 0.4138 [0.1138, 0.7138]. The main flip-rate gap is 0.2448 [-0.0552, 0.5793]. Candidate Spearman on the published slice is -0.7139 [-1.5426, 0.2821]; the interval includes zero. Candidate MAE on the published slice is 0.1817 [-0.0767, 0.4473]; the interval includes zero. The published slice has 10 papers, so those rank intervals are wide.

The anonymous submissions are the rejects in this batch. Ranking inside that band can move while the mean score stays put. The candidate level shift includes zero. The main level shift includes zero. Candidate Spearman on the anonymous slice is -0.1543 [-0.2879, -0.0402]; the interval lies where the raw text scores better. Candidate MAE on the anonymous slice is 0.1051 [0.0194, 0.1963]; the interval lies where the raw text scores better. Main Spearman on the anonymous slice is -0.0190 [-0.0922, 0.0452]; the interval includes zero. Main MAE on the anonymous slice is 0.0012 [-0.0664, 0.0758]; the interval includes zero. The Spearman and MAE comparison is inside the reject band. It is not the camera-ready author block. The anonymous clear-accept tier in this batch is one paper, so the adjacent AUC against that tier is a one-paper comparison. The two calls are independent draws, and the anonymizer also removes the shared banner plus any named GitHub link or acknowledgement heading.

On the pooled set the raw score for 123 papers is the original Gate 1 draw, so that anonymized-minus-raw interval also contains a change of session. The fresh tables are the shortcut estimate. Pooled candidate Spearman is -0.0101 [-0.0635, 0.0392]; the interval includes zero. Pooled candidate MAE is 0.0096 [-0.0514, 0.0696]; the interval includes zero. Pooled main MAE is -0.0617 [-0.1116, -0.0122]; the interval lies where the anonymized text scores better. Candidate minus main on the pooled raw scores: Spearman is 0.0390 [-0.0140, 0.0938]; the interval includes zero. MAE is -0.0668 [-0.1216, -0.0109]; the interval lies where the candidate scores better. On the anonymized scores: Spearman is -0.0093 [-0.0594, 0.0403]; the interval includes zero. MAE is 0.0078 [-0.0479, 0.0613]; the interval includes zero.
