# Proposal-rank cohort

The fixed100, confirm100, replications199, top-20%, and 84-NLP headline numbers are reported by the original Codex run, not yet reproducible from repo artifacts.

This directory holds the schema for a later public bundle. It does not hold that bundle. A manifest lists forum ids and the SHA-256 of the sorted test ids. Predictions are one overall score per forum. References are the coded prospective mean, the official final mean, and an optional accept bit. Public ICLR 2024 forum ids may be added later. Do not add private holdout ids, review text, or manuscript text.

When those three files exist locally:

```bash
python3 scripts/reproduce_proposal_rank.py \
  --manifest path/manifest.json \
  --predictions path/predictions.json \
  --references path/references.json \
  --output workdir/proposal-rank-metrics.json
```

`evaluate-rank` accepts any cohort size. The interval is a 3000-draw stratified percentile bootstrap inside final-score strata (seed 61124). That procedure is not a reconstruction of the published intervals.
