# Proposal-rank reproduction artifacts

The fixed100, confirm100, replications199, top-20%, and 84-NLP headline numbers are reproduced from committed artifacts.

```bash
python3 scripts/reproduce_proposal_rank.py
```

That command needs numpy (`pip install 'numpy>=1.24,<3'`, or `pip install -e '.[repro]'`). It does not download papers and it does not call a model. `evaluate-rank` scores any one of these locked lists when you pass its manifest, predictions, and references.

Each `MANIFEST.md` is the source record from the original run: path, SHA-256, and row count. The production fixed100 predictions are `fixed100/fixed-predictions.json`, the 4-feature file from `work/next/fixed-predictions.json`.

A few source files are not byte-for-byte copies:

- `fixed100/references.json` and `confirm100/references.json` keep the forum, the prospective mean, the official final mean, the accept bit, and numeric reviewer scores. Quoted review sentences are not included.
- `replications199/blind-index.json` keeps forum, key, group, tier, and the frozen score. The proposal plan text from `blind-evaluation.json` is not included.
- `replications199/ICLR-2024逐篇数据索引.jsonl` is not included. Each row embeds an OpenReview forum URL.
- `replications199/grading.py` imports `verbal_fit` only when the optional verbal mode is selected.
- Library snapshots of `fixed_benchmark.py` and `public_calibration.py` are not vendored. The package is the scorer.
- `nlp84/` is the compact set: `lock.json`, `mapping.csv`, `deployed-external-rows.json`, `deployed-summary.json`, and the three scripts. The per-proposal input and cache blobs are not included.

The published Spearman intervals resample inside the original final-score tiers, 3000 draws, numpy Generator seeds 241125 (fixed), 241126 (confirm), and 241127 (replications). The top 20% count uses `ceil(n / 5)` and reports ties as a range. The 84-proposal interval is 2000 draws from `random.Random(11752)`, with the positive class at 6.625/10.
