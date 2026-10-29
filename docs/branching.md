# Branching

`pre-release` is the integration branch. `main` is the stable branch.

## Pull requests

All pull requests target `pre-release`. Merge a pull request into `pre-release` only when CI is fully green.

Dependabot opens dependency updates against `pre-release`.

## Promotions into main

`main` only takes promotions from `pre-release`. A promotion is a pull request whose head ref is `pre-release` and whose head repository is this repository. The `main-source-guard` job enforces that source on pull requests that target `main`. A pull request that targets any other base passes that job without a source check.

A promotion is allowed only when a regression report shows that `pre-release` scores higher than or equal to `main` on the same fixed benchmark. Both branches run with the same model, grok-4.7 at reasoning_effort `xhigh`, via Cursor.

## TODO: benchmark definition

The fixed benchmark used for that regression report will be decided separately. Until then, record which benchmark the report used, and run that same benchmark on both branches with the model above.
