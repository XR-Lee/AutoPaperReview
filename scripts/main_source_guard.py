#!/usr/bin/env python3
"""Allow a pull request into main only from this repository's pre-release branch.

`BASE_REF` is `pull_request.base.ref`. `HEAD_REF` is `pull_request.head.ref`.
`HEAD_REPO` is `pull_request.head.repo.full_name`. `THIS_REPO` is the repository
that runs the workflow. Any base other than main passes without a source check.
"""

from __future__ import annotations

import argparse
import os
import sys

MAIN_BRANCH = "main"
PROMOTION_BRANCH = "pre-release"


def allows_pull_request(base_ref: str, head_ref: str, head_repo: str, this_repo: str) -> bool:
    """Return whether the pull request satisfies the main source rule."""
    if base_ref != MAIN_BRANCH:
        return True
    return bool(this_repo) and head_ref == PROMOTION_BRANCH and head_repo == this_repo


def explain(base_ref: str, head_ref: str, head_repo: str, this_repo: str) -> str:
    if base_ref != MAIN_BRANCH:
        shown = base_ref or "<missing>"
        return f"Base branch is {shown}. main-source-guard passes without a source check."
    if allows_pull_request(base_ref, head_ref, head_repo, this_repo):
        return f"Head {this_repo}:{PROMOTION_BRANCH} is a promotion into {MAIN_BRANCH}."
    required = this_repo or "<missing>"
    actual_repo = head_repo or "<missing>"
    actual_ref = head_ref or "<missing>"
    return (
        f"Pull requests that target {MAIN_BRANCH} must come from "
        f"{required}:{PROMOTION_BRANCH}. "
        f"This head is {actual_repo}:{actual_ref}."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default=os.environ.get("BASE_REF", ""))
    parser.add_argument("--head-ref", default=os.environ.get("HEAD_REF", ""))
    parser.add_argument("--head-repo", default=os.environ.get("HEAD_REPO", ""))
    parser.add_argument("--this-repo", default=os.environ.get("THIS_REPO", ""))
    args = parser.parse_args(argv)
    allowed = allows_pull_request(args.base_ref, args.head_ref, args.head_repo, args.this_repo)
    print(f"main-source-guard: {'pass' if allowed else 'fail'}")
    print(
        explain(args.base_ref, args.head_ref, args.head_repo, args.this_repo),
        file=sys.stdout if allowed else sys.stderr,
    )
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
