# Manifest: `fixed100`

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
