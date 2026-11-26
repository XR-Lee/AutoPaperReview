#!/usr/bin/env python3
"""Regenerate the published proposal-rank headlines, or score a supplied cohort.

With no arguments, this prints the committed headline aggregates. Set
``APR_PROPOSAL_REPRO_DIR`` (or pass ``--root``) to recompute them from
per-paper rows kept outside git. The script does not download papers and it
does not call a model.

``--manifest``, ``--predictions``, and ``--references`` score a different
locked cohort of any size through ``evaluate-rank``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.proposal.cohort import (  # noqa: E402
    validate_manifest,
    validate_predictions,
    validate_references,
)
from autopaperreview.proposal.data import read  # noqa: E402
from autopaperreview.proposal.fixed_benchmark import evaluate_fixed  # noqa: E402
from autopaperreview.proposal.repro_metrics import (  # noqa: E402
    EXTERNAL_DIR_ENV,
    PUBLISHED,
    committed_aggregates,
    headlines_match,
    reproduce_headlines,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"external per-paper directory (default: ${EXTERNAL_DIR_ENV})",
    )
    parser.add_argument("--bootstrap", type=int, default=0)
    parser.add_argument("--stratified-draws", type=int, default=3000)
    args = parser.parse_args(argv)
    supplied = (args.manifest, args.predictions, args.references)
    if any(path is not None for path in supplied) and not all(path is not None for path in supplied):
        print(
            "Pass --manifest, --predictions, and --references together.",
            file=sys.stderr,
        )
        return 2
    if all(path is not None for path in supplied):
        missing = [str(path) for path in supplied if not path.is_file()]
        if missing:
            print("missing cohort artifact: " + ", ".join(missing), file=sys.stderr)
            return 2
        manifest = validate_manifest(read(args.manifest))
        predictions = validate_predictions(read(args.predictions))
        references = validate_references(read(args.references))
        result = evaluate_fixed(
            manifest,
            predictions,
            references,
            bootstrap=args.bootstrap,
            stratified_draws=args.stratified_draws,
        )
    else:
        external = args.root
        if external is None and os.environ.get(EXTERNAL_DIR_ENV):
            external = Path(os.environ[EXTERNAL_DIR_ENV])
        if external is None:
            result = committed_aggregates()
        else:
            try:
                result = reproduce_headlines(external)
            except (FileNotFoundError, RuntimeError) as exc:
                print(str(exc), file=sys.stderr)
                print(
                    "Per-paper rows are not in the repository. "
                    f"Set {EXTERNAL_DIR_ENV} to a directory outside git. "
                    f"The headline numbers are {PUBLISHED}.",
                    file=sys.stderr,
                )
                return 2
            if not headlines_match(result):
                print(
                    "recomputed headlines differ from the committed aggregates",
                    file=sys.stderr,
                )
                text = json.dumps(result, indent=2, sort_keys=True) + "\n"
                sys.stdout.write(text)
                return 1
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
