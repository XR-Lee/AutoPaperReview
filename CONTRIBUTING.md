# Contributing

## Compatibility rules

- Do not persist objects owned by parser, agent, workflow, telemetry, or journal-platform libraries.
- Additive schema changes require a minor release. Breaking schema changes require a new major schema version and a pure migration.
- New stage plugins must declare a stable handler version and deterministic inputs, keep outputs inside their stage directory, and return hashable artifacts.
- Network access must be declared in configuration and tested under `deny`, `metadata_only`, and `allow` policies where applicable.
- Never place manuscript text, author identities, credentials, or full prompts in telemetry attributes.

## Checks

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m autopaperreview validate examples/synthetic/review.toml
PYTHONPATH=src python3 -m autopaperreview run examples/synthetic/review.toml
PYTHONPATH=src python3 -m autopaperreview schema src/autopaperreview/schemas
```
