# Manifest: `top20`

Cross-cohort outcome association & top-20% precision/hit analysis across `fixed100` (n=100, k=20, cut=3.55), `confirm100` (n=100, k=20, cut=3.60), and `replications199` (n=199, k=40, cut=3.325, hitting 23 with final_rating >= 7 vs 10.05 random expected). Includes the exact analysis script (`analyze.py` with 3000-rep stratified bootstrap), distribution script (`plot.py`), and JSON outputs (`逐队列统计.json`, `分布统计.json`).

**Commit safety:** Safe to commit (aggregate statistics and public ICLR 2024 scripts only).

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `top20/analyze.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/outcome-association/analyze.py` | 170 lines | 8610 | `5c34cc89097fecad20d166374ec502e87e132584ed60766f7b3d87df5bbbbe13` |
| `top20/逐队列统计.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/提案分与论文终评/逐队列统计.json` | dict (3 cohorts) | 7242 | `ca7af1892b6369e094da9effb4d8f4a4fd72263d24df975f46f54f954cecadf7` |
| `top20/分布统计.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/outputs/Proposal评分分布/分布统计.json` | dict (5 groups) | 6307 | `1de49047ec71c1a4c6f4546aea67d3c6befd6d0df9b22141a5ce8e2b247d4fa5` |
| `top20/plot.py` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/score-distributions/plot.py` | 123 lines | 8007 | `ee20148d2c5765ae3611ffeec01a0b13a6ee37c5f08232f9e108fbcbd2b46fc5` |
