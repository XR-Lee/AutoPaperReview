# APR Proposal-Review Reproducibility Artifacts Manifest

Collected from Geek's Mac mini (`DA-GJ32CQ33YN`, `/Users/geeklee/Documents/Codex/2026-11-24/neng/`) on 2026-11-26 (UTC).
All SHA-256 checksums were verified identically after transfer to `/workspace/apr-proposal-viz/repro-artifacts/`.

## Security & Privacy Audit
- **Private holdout / S24-C1 / S24-C2:** None included. All 400 scored/enrolled ICLR 2024 papers (`fixed100` + `confirm100` + `replications199`) and 144 dev/val papers were verified present in public `iclr2024-openreview-submissions/raw_paperlist.json` (0 missing).
- **84 NLP proposals (`nlp84`):** Verified public from `https://github.com/NoviScl/AI-Researcher` (`data_points_all_anonymized.json`, `id_title_mapping.csv`, `ai.zip`, `human.zip`), filtered to `consent == 'yes'` and excluding `Safety`/sensitive items.
- **Credentials/Tokens:** Regex scan across all staged files found 0 tokens or private keys.

## Recomputed Headline Numbers Verification
- `fixed100` (n=100): `apr_to_final` rho = `0.676850` (0.677), `apr_accept_auc` = `0.812098` (0.812); `idea_rho` = `0.525302`, `idea_auc` = `0.840049`.
- `confirm100` (n=100): `apr_to_final` rho = `0.597206` (0.597), `apr_accept_auc` = `0.794294` (0.794); `idea_rho` = `0.594972`, `idea_auc` = `0.882488`.
- `replications199` (enrolled=200, scored=199, decisions=141): `apr_to_final` rho = `0.521529` (0.522), `apr_accept_auc` = `0.804545` (0.805); `idea_rho` = `0.497012`, `idea_auc` = `0.764699`.
- `top20` (`top20pct_final_ge_7`): `replications199` k=40 (cut >= 3.325) hits `23` papers with final_rating >= 7 vs `10.05` random expected (~10); `fixed100` k=20 hits `12` vs `5.00`; `confirm100` k=20 hits `11` vs `5.00`.
- `nlp84` (n=84, 195 reviews): `rho` = `0.272605` (0.273), `auc` = `0.680556` (0.681), `same_topic_concordance` = `0.594783` (575 pairs).

## Cohort: `fixed100`

Fixed 100-paper ICLR 2024 test cohort (25 per tier: clear_accept, borderline_accept, borderline_reject, clear_reject). All 100 forum IDs are public ICLR 2024 OpenReview submissions (verified against `iclr2024-openreview-submissions/raw_paperlist.json`). Includes frozen profile predictions (`fixed-predictions.json`), locked split manifest (`lock.json`, 244 total cases: 96 dev, 48 val, 100 test), sealed reference labels (`references.json`), official headline metrics & 95% CIs (`incumbent-benchmark.json`, `evaluation.json`), and evaluation scripts (`final_compare.py`, `fixed_benchmark.py`, `public_calibration.py`).

**Commit safety:** Safe to commit (public ICLR 2024 OpenReview IDs & scores only; zero private holdout / S24-C1 / S24-C2 data).

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `fixed100/lock.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/fixed100/lock.json` | 244 cases | 33733 | `0296743c0c376b0033575861422c0bb9d6e7a71fa3d3d818299824cd8f36e410` |
| `fixed100/fixed-predictions.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/fixed-predictions.json` | 100 list items | 5646 | `105253f784ab3a8f2db210d77881bc79729443b70b1525f3e1fd6029490a1ce0` |
| `fixed100/references.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/fixed100/sealed/references.json` | 100 list items | 236838 | `e5a430212a610cd8c3524b99e7c535222cc96057a8c06abdf6ba74410d7e5542` |
| `fixed100/source-manifest.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/fixed100/source-manifest.json` | dict (5 top-level keys) | 1192 | `9fecd0d227e1a93aece283885f04a808e3a2ddf1d5f3fcc8492ed088533f3c18` |
| `fixed100/followup-lock.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/followup-lock.json` | dict (12 top-level keys) | 2548 | `5d8855097377e6f3705ae2ce3ee8f395503c02b0a37c59c183376c284f3d8382` |
| `fixed100/deployed-replay.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/deployed-replay.json` | dict (6 top-level keys) | 236 | `ca304ced3f1127e372c47ed1b4822efbbfc29bb0a9d3f4e516dc1ef746a636f7` |
| `fixed100/incumbent-benchmark.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/trait-review/incumbent-benchmark.json` | dict (2 top-level keys) | 895 | `6dc47095e6d6bbe857dea9a2615e0aff5f097e35af74d44244b3acd13330f3d3` |
| `fixed100/evaluation.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/evaluation.json` | dict (2 top-level keys) | 3702 | `7047038aa8887e3b9b7061aea146ad1c90dc4847285a483a5dcef568a5961df4` |
| `fixed100/final_compare.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/final_compare.py` | 27 lines | 2012 | `b110bd6c975446830482c67565ed9e6c9a649183eb89142d107541253e5d1e6f` |
| `fixed100/profile.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/立项排序模型/profile.json` | dict (6 top-level keys) | 1604 | `4ec92786cf3cd2545ee518862e3ae4e151cd23853d3769bc8a0021ad5736060d` |
| `fixed100/fixed_benchmark.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/publish-apr-harness/src/autopaperreview/proposal/fixed_benchmark.py` | 193 lines | 6856 | `755780a8b0cb47fcb5506f3dc58079f6de450feb43676d84919b7c7c1221aed6` |
| `fixed100/public_calibration.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/publish-apr-harness/src/autopaperreview/public_calibration.py` | 1742 lines | 67819 | `25ce16a70d2bd52e8d6f6fdf52ff00d0823e3ac121eb2ea70de93bc85c32cd50` |

## Cohort: `confirm100`

Independent confirmation 100-paper ICLR 2024 cohort (`fresh` / `next/confirmation`, 25 per tier across 4 strata). All 100 forum IDs are public ICLR 2024 OpenReview submissions (disjoint from `fixed100` and `replications199`). Includes frozen profile predictions (`fresh-predictions.json`), earlier baseline predictions (`original-fresh-predictions.json`), locked manifest (`lock.json`), and reference labels (`references.json`). Headline evaluation & CIs are computed alongside `fixed100` by `fixed100/final_compare.py` into `fixed100/incumbent-benchmark.json` and `fixed100/evaluation.json`.

**Commit safety:** Safe to commit (public ICLR 2024 OpenReview IDs & scores only; zero private holdout / S24-C1 / S24-C2 data).

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `confirm100/lock.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/confirmation/lock.json` | 100 cases | 13854 | `a1c66aa7627754ba09be8b67902d3c75965e48fd788eb958b1c45fbf27453d17` |
| `confirm100/fresh-predictions.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/fresh-predictions.json` | 100 list items | 5690 | `8e15748f1a43d5dd4d966e8b5fb504751c8debb87b64f856a5cd381073df2d50` |
| `confirm100/references.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/confirmation/sealed/references.json` | 100 list items | 219023 | `0e4a7e7d62ff0a3bfd00197eb2047a4e8ec6edb40df7dfe9be8d53fa9113bc1b` |
| `confirm100/original-fresh-predictions.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/original-fresh-predictions.json` | 100 list items | 6495 | `b3cce069eecdd4b17bfeca7dece4e6cd9d51d15d3a963822e51d0e7539310b46` |

## Cohort: `replications199`

Two independent replication cohorts (`replication_A`: 100 enrolled, 99 scored; `replication_B`: 100 enrolled, 100 scored; pooled: 200 enrolled, 199 scored, 141 with public accept/reject decisions, 75 accepted). 1 missing APR score in `replication_A`: forum `nR1EEDuov7` (`key=cac5a151e0ea1f6970448e2e72abd601730f9c3e521455e1fed05e164ff5c1db`, `borderline_reject`, rating=5, accept=0, `base=None`). All 200 replication forums (plus 144 dev/val forums in `blind-evaluation.json` and `joined.json`) are public ICLR 2024 OpenReview submissions. Includes `blind-evaluation.json`, `joined.json`, `membership.json`, `results.json` (with 2000-rep stratified bootstrap CIs), `apr-separate.json`, `ICLR-2024数据快照清单.json`, `ICLR-2024逐篇数据索引.jsonl`, and scripts (`grading.py`, `secondary_separate.py`, `estimator.py`, `wio.py`; note `grading.py` imports `verbal_fit.ARMS` for the optional verbal mode).

**Commit safety:** Safe to commit (public ICLR 2024 OpenReview IDs & scores only; zero private holdout / S24-C1 / S24-C2 data).

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `replications199/blind-evaluation.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/blind-evaluation.json` | 344 list items | 1012832 | `3f51a799f27489f06a6035decefdb4e2e14604a81ff1df707a64c2a62d553d5e` |
| `replications199/joined.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/available/joined.json` | 344 list items | 95532 | `06e43684cf123ad673e4e6285b64657c90de393b703b747a74f6512426742d68` |
| `replications199/membership.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/available/membership.json` | dict (3 top-level keys) | 102909 | `fe0026549ef4ad5b0399eabb19adcac3040260c59ed2f7b49c3d0af07698971e` |
| `replications199/results.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/available/results.json` | dict (8 groups) | 18383 | `84bae5650fbc7b49c9341cdc3afc9be0630da74c2a62e6dcb5f21fe7fb67966c` |
| `replications199/apr-separate.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/available/apr-separate.json` | dict (8 top-level keys) | 6692 | `257226fba7f3b9398a017e3f2ebf3940138ce5f3d409762c13f60ba44c49fcbd` |
| `replications199/grading.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/grading.py` | 69 lines | 4883 | `d77cae786cff46c2acd2da6671810fa37426494fdec420d91bfb4b3bad13fddd` |
| `replications199/secondary_separate.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/secondary_separate.py` | 56 lines | 3112 | `7795479d66cc99c4cd145fa594cd92e2536138e4e50340821b223a5f6211b5ec` |
| `replications199/estimator.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/estimator.py` | 128 lines | 6763 | `c65eddd057d81e85efd62974351962d050370a04aaecf3e6c8711585501951d2` |
| `replications199/wio.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/reason-transfer/wio.py` | 20 lines | 1030 | `41d78a68bbe9a5bb60313a38920b7932237d63ddff8c8a17a78b128ba177d891` |
| `replications199/ICLR-2024数据快照清单.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/ICLR-2024数据快照清单.json` | dict (13 top-level keys) | 2810 | `8cd96a73d21b06e0afd7e8ecd878c5024f98b112badef7f0386355d7b4873630` |
| `replications199/ICLR-2024逐篇数据索引.jsonl` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/ICLR-2024逐篇数据索引.jsonl` | 400 lines | 296945 | `bf21f41ecf4852a9111e490b35689ef29f9ff73b1bf6c603a34b3f583c588b10` |

## Cohort: `top20`

Cross-cohort outcome association & top-20% precision/hit analysis across `fixed100` (n=100, k=20, cut=3.55), `confirm100` (n=100, k=20, cut=3.60), and `replications199` (n=199, k=40, cut=3.325, hitting 23 with final_rating >= 7 vs 10.05 random expected). Includes the exact analysis script (`analyze.py` with 3000-rep stratified bootstrap), distribution script (`plot.py`), and JSON outputs (`逐队列统计.json`, `分布统计.json`).

**Commit safety:** Safe to commit (aggregate statistics and public ICLR 2024 scripts only).

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `top20/analyze.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/outcome-association/analyze.py` | 170 lines | 8610 | `5c34cc89097fecad20d166374ec502e87e132584ed60766f7b3d87df5bbbbe13` |
| `top20/逐队列统计.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/提案分与论文终评/逐队列统计.json` | dict (3 cohorts) | 7242 | `ca7af1892b6369e094da9effb4d8f4a4fd72263d24df975f46f54f954cecadf7` |
| `top20/分布统计.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/Proposal评分分布/分布统计.json` | dict (5 groups) | 6307 | `1de49047ec71c1a4c6f4546aea67d3c6befd6d0df9b22141a5ce8e2b247d4fa5` |
| `top20/plot.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/score-distributions/plot.py` | 123 lines | 8007 | `ee20148d2c5765ae3611ffeec01a0b13a6ee37c5f08232f9e108fbcbd2b46fc5` |

## Cohort: `nlp84`

84 pre-experiment NLP research proposals (43 Human, 41 AI; 195 consenting expert reviews across 6 topics: Multilingual=20, Factuality=20, Coding=16, Uncertainty=12, Bias=8, Math=8) from Si, Yang, and Hashimoto's public 2024 ideation study (`https://github.com/NoviScl/AI-Researcher`, pinned by git blob SHA-1 in `lock.json`). 14 proposals from the original 98 matched rows were excluded (`Safety` topic or sensitive keywords) before scoring. Headline results: Spearman rho = 0.2726 (95% CI [0.0454, 0.4716]), ROC AUC = 0.6806 (95% CI [0.4342, 0.8846]) at threshold 6.625/10; same-topic pairwise concordance = 0.5948 over 575 comparable pairs.

**Commit safety:** Source dataset (`NoviScl/AI-Researcher`) is publicly released with consenting expert reviews; zero private holdout or S24-C1/C2 data. Safe to commit, though `inputs/` (84 files) + `deployed/` (84 files) + `external-gold.json` add ~2.4 MB if committed verbatim.

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `nlp84/lock.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/lock.json` | 84 frozen cases | 31771 | `c0a979d0b4613cc9a52b6f9069d36bd44a6a74971c99ea1a1d7047c52c693e00` |
| `nlp84/mapping.csv` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/mapping.csv` | 98 data rows (+1 header) | 7609 | `abbb845b096681a07c8b3f23e57db399276ba9e87ea7903839e929b4fe3298fd` |
| `nlp84/deployed-lock.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/deployed-lock.json` | dict (2 top-level keys) | 758 | `0789b72b8dd5a8e0d6ecff7510f11ce70977172c2c4c2d8b7a2fe68ff161521f` |
| `nlp84/deployed-protocol.txt` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/deployed-protocol.txt` | 2 lines | 1686 | `aad16a7b8c562e03dc57b3b35e45627fa281a5c69a9f72e9dd83d434cd15827f` |
| `nlp84/protocol.txt` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/protocol.txt` | 2 lines | 2529 | `d5f83b89301877e2b7e5d9ebb4775e6d30b1890c2ee36f4af879003e278c9cfb` |
| `nlp84/deployed-external-rows.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/deployed-external-rows.json` | 84 list items | 27299 | `8268b7828836946d0e37f3c024b43e149fdf2bf9524061ffa4ebd01f0522dac4` |
| `nlp84/deployed-summary.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/deployed-summary.json` | dict (4 top-level keys) | 1051 | `45c80b05bce08b6e1d263fb6145e71aec848599757d9a398e6044d98c9ca42b6` |
| `nlp84/external-gold.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/external-gold.json` | dict (84 top-level keys) | 96256 | `f02dd7a5fb44eae689239ebbc1f56c03b2ad0c5b2e8aab8f1d9386c3eee84b38` |
| `nlp84/deployed.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/deployed.py` | 338 lines | 11566 | `d60cb6287bb15e6c856c69c47e47861ac119706b1cf0375856d8a7e1e420163b` |
| `nlp84/transfer.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/transfer.py` | 342 lines | 13135 | `f6d999ea451241543da4d9da51ef8c5cc25366f60da918e836b9553713aee0ca` |
| `nlp84/external.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/external.py` | 252 lines | 9244 | `615f845bd05a062f67940da238951b23db45b3f4d29192c717a5b15d259e0f9b` |
| `nlp84/inputs/*.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/inputs` | 84 JSON files | 639746 | `caf5c26d3d2110bb8dbd8b9bb222e2d66cf3605a05bf761adee50ac42ee3c807` |
| `nlp84/deployed/*.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/prospective-transfer/deployed` | 84 JSON files | 48774 | `8f969e5bf737c159afcc6e3f4a9fa908ac28cefb1521834ada544a3070590958` |
