# Manifest: `replications199`

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
