# Manifest: `nlp84`

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
