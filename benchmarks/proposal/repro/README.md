# Proposal-rank reproduction

Headline numbers for fixed100, confirm100, replications199, the top 20%, and the 84 NLP proposals are committed in `aggregates.json`. Per-paper rows are not in git. Forum ids, review-note ids, ratings, decisions, proposal titles, and local paths stay in a directory you keep outside the repository.

```bash
export APR_PROPOSAL_REPRO_DIR=/path/outside/git
python3 scripts/reproduce_proposal_rank.py
```

`APR_PROPOSAL_REPRO_DIR` can point to the original or HMAC bundle. The script reads each of the eight files once, matches the bytes against a single committed SHA-256 manifest, and computes from those bytes. `external-sha256.json` covers the original delivered bytes. `external-hmac-sha256.json` covers the corresponding HMAC bundle. The scorer reads numeric fields and row keys. `replications199` uses `joined.json` directly. Give `--output` a separate file under a directory path without symbolic links. Output is published by an atomic rename relative to the parent directory opened before computation.

To rekey the eight reproduction files, supply `APR_PROPOSAL_REPRO_SALT` through the environment and use a separate output directory. The rekeyer recalculates the cohort locks' test-ID digests after transforming the identifiers. For recursive conversion, it supports known ID fields in JSON and JSONL and recognized ID columns in CSV and TSV. Keep both bundles outside git because the files can contain review quotes and other per-paper data.

```bash
python3 scripts/rekey_external_repro.py --published-only \
  --root /path/to/original/rows --output /separate/path/to/hmac/rows
```

The committed HMAC manifest pins the distributed bundle. Verification and recomputation use the file checksums and require no salt. Rekeying with a different salt produces a bundle with its own checksums. See `key-scheme.json` for the scheme.

With the external directory unset, the script prints `aggregates.json` and the external-recompute test skips. A synthetic fixture covers the Spearman, AUC, top-fifth, and HMAC arithmetic.

The command needs numpy (`pip install 'numpy>=1.24,<3'`, or `pip install -e '.[repro]'`). It does not download papers and it does not call a model.

`evaluate-rank` scores a locked cohort of any size when you pass its manifest, predictions, and references. `validate_manifest` requires schema version 1. `cohort_cases` checks the sorted-id digest and also accepts a lock that has no version field. A year below 1 is rejected in both.

The published Spearman intervals for the three ICLR cohorts resample inside final-score tiers: 3000 draws, numpy Generator, linear quantile, seeds 241125 (fixed), 241126 (confirm), and 241127 (replications). The top 20% count uses `ceil(n / 5)` and reports ties as a range. The 84-proposal interval is a different procedure: 2000 draws from `random.Random(11752)`, index percentile, positive class at 6.625/10. It is not the 3000-draw tier bootstrap. `evaluate-rank`'s own interval is a third procedure: 3000 draws, seed 61124, and each stratum is sorted by row key before the draw so the interval does not depend on input order.

CSV or TSV read for an external table uses universal newlines, and each cell is stripped. A trailing space or a CR LF line ending does not change the field.

The `*.py` files under the cohort directories are the original analysis programs. They read local files. CI does not run them.
