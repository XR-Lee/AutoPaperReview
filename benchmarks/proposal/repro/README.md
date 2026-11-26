# Proposal-rank reproduction

Headline numbers for fixed100, confirm100, replications199, the top 20%, and the 84 NLP proposals are committed in `aggregates.json`. Per-paper rows are not in git. Forum ids, review-note ids, ratings, decisions, proposal titles, and local paths stay in a directory you keep outside the repository.

```bash
export APR_PROPOSAL_REPRO_DIR=/path/outside/git
python3 scripts/reproduce_proposal_rank.py
```

`APR_PROPOSAL_REPRO_DIR` must contain the row files named in `external-sha256.json`. The script checks those SHA-256 checksums before it recomputes. The checksums are of the row files that the scorer reads (review quotes are not in those copies). They are not of the earlier local originals that still contained quotes.

Without that directory, the script prints `aggregates.json` and does not fail. The unit test that recomputes the headlines skips in the same case. A synthetic fixture covers the Spearman, AUC, top-fifth, and HMAC arithmetic.

Optional `APR_PROPOSAL_REPRO_SALT` is an HMAC-SHA256 salt for identifiers. The salt is not in git, and the script does not write the keys. See `key-scheme.json`.

The command needs numpy (`pip install 'numpy>=1.24,<3'`, or `pip install -e '.[repro]'`). It does not download papers and it does not call a model.

`evaluate-rank` scores a locked cohort of any size when you pass its manifest, predictions, and references. `validate_manifest` requires schema version 1. `cohort_cases` checks the sorted-id digest and also accepts a lock that has no version field. A year below 1 is rejected in both.

The published Spearman intervals for the three ICLR cohorts resample inside final-score tiers: 3000 draws, numpy Generator, linear quantile, seeds 241125 (fixed), 241126 (confirm), and 241127 (replications). The top 20% count uses `ceil(n / 5)` and reports ties as a range. The 84-proposal interval is a different procedure: 2000 draws from `random.Random(11752)`, index percentile, positive class at 6.625/10. It is not the 3000-draw tier bootstrap. `evaluate-rank`'s own interval is a third procedure: 3000 draws, seed 61124, and each stratum is sorted by row key before the draw so the interval does not depend on input order.

CSV or TSV read for an external table uses universal newlines, and each cell is stripped. A trailing space or a CR LF line ending does not change the field.

The `*.py` files under the cohort directories are the original analysis programs. They read local files. CI does not run them.
