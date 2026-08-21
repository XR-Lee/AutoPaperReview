# Maintenance policy

This policy applies to the `0.1.x` line of AutoPaperReview.

## Supported runtimes

- CI tests CPython 3.11, 3.12, and 3.13.
- Python versions newer than 3.13 are best-effort until added to the CI matrix.
- Pydantic `2.x` is the supported validation runtime.
- Linux is the required CI platform. macOS and Windows are supported when the core tests and synthetic example pass, but optional adapters may have narrower support.

Dropping a tested Python version requires a documented release note. Adding support requires the full CI matrix, not only a local import check.

## Compatibility promises

### Canonical schemas

- Additive optional fields may ship in a minor release.
- New required fields, changed meanings, removed fields, or changed identifiers require a new major schema version.
- Every breaking schema change requires a pure, idempotent, non-destructive migration and fixtures covering the previous format.
- Unknown legacy fields must be retained under a documented metadata namespace when they cannot be mapped safely.
- Generated JSON Schema changes must be reviewed as part of the pull request.

### Configuration

- Existing valid `review.toml` files should remain valid within the `0.1.x` line.
- New stage parameters must have backward-compatible defaults or require an explicit stage/config version.
- Cache-signature changes are allowed when correctness requires invalidation, but the reason must be recorded in the changelog.

### Plugins and adapters

The `autopaperreview.stages` entry point is usable in v0.1 but is not yet a stable cross-major ABI. Plugins must pin a compatible AutoPaperReview range and test against every supported Python version they claim. Every handler must maintain a meaningful `version`; parameterized handlers should expose a strict Pydantic `params_model` so invalid configuration fails before execution.

Formal adapter contracts and capability manifests are roadmap work. Until then, review plugin changes against the invariants in [Architecture](architecture.md#plugin-boundary) and the checklist in [Open-source reference stack](oss-reference-stack.md#adapter-acceptance-checklist).

## Dependency policy

- Keep the core dependency set small and constrained by compatible major versions.
- Optional parser, model, retrieval, telemetry, and platform integrations belong in extras or separate adapter packages.
- Do not persist third-party object models.
- Review code licenses, model/data licenses, transitive dependencies, and redistribution rules separately.
- Pin external services, containers, prompts, and model identifiers in run metadata when they can affect an output.
- Security updates may override the normal release cadence and may invalidate cached artifacts when output semantics could change.

When updating Pydantic or a hashing/serialization dependency, run schema diffs, migration fixtures, and the synthetic pipeline twice. When updating an adapter, run its contract fixtures and compare normalized output, not only process exit status.

## Determinism and cache maintenance

A stage cache entry is reusable only when:

- Its stage signature matches.
- Every dependency artifact hash matches.
- Every declared output exists and matches its recorded hash.
- The previous stage status is successful or cache-skipped.

Signatures must include every input that can change behavior: handler type/version, stage parameters, source and dependency hashes, prompt/config hashes, command input-file hashes, adapter version or code digest, model/seed where relevant, network policy, and relevant runtime material.

Do not include timestamps, run IDs, absolute workspace paths, or secrets in a deterministic signature. Stochastic external responses must be snapshotted and addressed by hash before downstream reuse.

## Release checklist

1. Run CI on Python 3.11, 3.12, and 3.13.
2. Compile package and synthetic check sources.
3. Run the full unittest suite.
4. Validate the synthetic TOML configuration.
5. Remove prior synthetic run output, execute the example, then execute it again to exercise resume/cache behavior.
6. Verify that the second execution records cache hits for all deterministic stages.
7. Verify the run manifest binds the hashed `config.snapshot.json` artifact.
8. For command stages, verify command records use logical paths and include input-file hashes without environment values or secrets.
9. Export JSON Schemas to a temporary directory and diff them against the committed files under `src/autopaperreview/schemas/`.
10. Build a wheel and verify that packaged prompts and committed schemas are present in it.
11. Run legacy migration fixtures and confirm the input files are unchanged.
12. Review `CHANGELOG.md`, security implications, dependency/license changes, and documentation.
13. Build artifacts from a clean checkout and ensure release manifests contain relative paths and hashes.

## Security and privacy maintenance

- Default new network-capable stages to denied until configuration explicitly grants the required scope.
- Treat network declarations as policy validation, not an OS sandbox guarantee.
- Never write manuscripts, reviewer identities, credentials, cookies, complete prompts, or complete model responses into telemetry attributes.
- Redact command errors before including them in a portable release.
- Keep external responses used as evidence as immutable artifacts with retrieval metadata.
- Test that release packages reject or warn on home-directory paths, tokens, and unlinked QA assertions.
- Follow `SECURITY.md` for private vulnerability reporting.

## Test maintenance

The test suite should contain four layers:

- Unit tests for models, DAG validation, hashing, migration, consensus grouping, and artifact resolution.
- Golden tests for normalized canonical JSON and Markdown output.
- Integration tests for successful, resumed, invalidated, and failed stage runs.
- Adapter contract tests for optional components, including policy denial and malformed output.

Tests must not require network access unless they are explicitly isolated from the default CI job. Network tests should replay immutable fixtures where possible.

## Documentation maintenance

Update documentation in the same change when:

- A canonical model or schema changes.
- A stage type, signature rule, or network behavior changes.
- A new optional integration is recommended.
- A roadmap capability becomes implemented.
- Supported Python versions or release checks change.

The architecture document describes released behavior. Future designs belong in the roadmap until code and tests exist.
