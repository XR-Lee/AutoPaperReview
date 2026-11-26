# Manifest: `confirm100`

Independent confirmation 100-paper ICLR 2024 cohort (`fresh` / `next/confirmation`, 25 per tier across 4 strata). All 100 forum IDs are public ICLR 2024 OpenReview submissions (disjoint from `fixed100` and `replications199`). Includes frozen profile predictions (`fresh-predictions.json`), earlier baseline predictions (`original-fresh-predictions.json`), locked manifest (`lock.json`), and reference labels (`references.json`). Headline evaluation & CIs are computed alongside `fixed100` by `fixed100/final_compare.py` into `fixed100/incumbent-benchmark.json` and `fixed100/evaluation.json`.

**Commit safety:** Safe to commit (public ICLR 2024 OpenReview IDs & scores only; zero private holdout / S24-C1 / S24-C2 data).

| File | Source Path (Mac mini) | Rows / Items | Bytes | SHA-256 |
|---|---|---|---:|---|
| `confirm100/lock.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/confirmation/lock.json` | 100 cases | 13854 | `a1c66aa7627754ba09be8b67902d3c75965e48fd788eb958b1c45fbf27453d17` |
| `confirm100/fresh-predictions.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/fresh-predictions.json` | 100 list items | 5690 | `8e15748f1a43d5dd4d966e8b5fb504751c8debb87b64f856a5cd381073df2d50` |
| `confirm100/references.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/confirmation/sealed/references.json` | 100 list items | 219023 | `0e4a7e7d62ff0a3bfd00197eb2047a4e8ec6edb40df7dfe9be8d53fa9113bc1b` |
| `confirm100/original-fresh-predictions.json` | `/Users/geeklee/Documents/Codex/2026-11-24/neng/work/next/original-fresh-predictions.json` | 100 list items | 6495 | `b3cce069eecdd4b17bfeca7dece4e6cd9d51d15d3a963822e51d0e7539310b46` |
