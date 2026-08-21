# Security and privacy

AutoPaperReview treats unpublished manuscripts, reviewer identities, prompts, responses, and credentials as sensitive data.

- The default network policy rejects stages that declare incompatible network requirements. It is a policy gate, not packet-level or operating-system isolation.
- Command stages execute argument arrays with `shell=False`.
- Command stages and plugins are trusted local code. Run untrusted adapters inside an external container or sandbox with explicit filesystem and egress controls.
- Secrets must come from the runtime environment and must not be written to config, manifests, logs, or telemetry.
- Configuration is snapshotted into each run, so configuration files must never contain secret values. Command records retain environment variable names but not values.
- Command stages use an explicit `inherit_env` allowlist. Secret values inherited at runtime are hashed into the cache signature but are not persisted individually.
- Generated release packages should contain relative paths and content hashes, not cookies, tokens, home-directory paths, or cloud-session identifiers.
- Parser and model adapters should run with the least filesystem and network access possible.

Report vulnerabilities privately to the repository owner before opening a public issue.
