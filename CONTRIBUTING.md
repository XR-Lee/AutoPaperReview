# Contributing

## Branches

All pull requests target `pre-release` and merge only when CI is fully green. `main` only takes promotions from `pre-release`. A promotion is allowed only when a regression report shows that `pre-release` scores higher than or equal to `main` on the same fixed benchmark. Both branches run with the same model, grok-4.7 at reasoning_effort `xhigh`, via Cursor. The benchmark definition is a TODO in [Branching](docs/branching.md).

## Compatibility rules

- Do not persist objects owned by parser, agent, workflow, telemetry, or journal-platform libraries.
- Additive schema changes require a minor release. Breaking schema changes require a new major schema version and a pure migration.
- New stage plugins must declare a stable handler version and deterministic inputs, keep outputs inside their stage directory, and return hashable artifacts.
- Network access must be declared in configuration and tested under `deny`, `metadata_only`, and `allow` policies where applicable.
- Never place manuscript text, author identities, credentials, or full prompts in telemetry attributes.

## Confidentiality

This repository is public. Do not commit manuscripts, reviews, page renders, submission or forum ids, or sentences taken from a paper. Keep those files in a gitignored `workdir/`, `reviews/`, `papers/`, or `uploads/` directory. The policy is [CONFIDENTIALITY.md](CONFIDENTIALITY.md). Agents must follow [AGENTS.md](AGENTS.md).

Before opening a pull request:

```bash
python3 scripts/check_confidentiality.py
```

Optional git hook (requires the `pre-commit` tool locally):

```bash
pre-commit install
```

## Checks

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m autopaperreview validate examples/synthetic/review.toml
PYTHONPATH=src python3 -m autopaperreview run examples/synthetic/review.toml
PYTHONPATH=src python3 -m autopaperreview schema src/autopaperreview/schemas
```
