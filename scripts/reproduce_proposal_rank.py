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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.proposal.data import read  # noqa: E402
from autopaperreview.proposal.file_io import protected_output, write_text  # noqa: E402
from autopaperreview.proposal.fixed_benchmark import evaluate_fixed  # noqa: E402
from autopaperreview.proposal.repro_metrics import (  # noqa: E402
    EXTERNAL_DIR_ENV,
    PUBLISHED,
    committed_aggregates,
    external_root,
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
    try:
        with protected_output(args.output):
            return _run(args, supplied)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"could not write reproduction result: {exc}", file=sys.stderr)
        return 2


def _run(args, supplied) -> int:
    if all(path is not None for path in supplied):
        missing = [str(path) for path in supplied if not path.is_file()]
        if missing:
            print("missing cohort artifact: " + ", ".join(missing), file=sys.stderr)
            return 2
        try:
            if args.bootstrap < 0 or args.stratified_draws < 0:
                raise ValueError("resampling draws must be non-negative")
            result = evaluate_fixed(
                read(args.manifest),
                read(args.predictions),
                read(args.references),
                bootstrap=args.bootstrap,
                stratified_draws=args.stratified_draws,
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"invalid cohort artifact: {exc}", file=sys.stderr)
            return 2
    else:
        external = args.root if args.root is not None else external_root()
        try:
            expected = committed_aggregates()
            result = expected if external is None else reproduce_headlines(external)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            print(
                "Per-paper rows are not in the repository. "
                f"Set {EXTERNAL_DIR_ENV} to a directory outside git. "
                f"The headline numbers are {PUBLISHED}.",
                file=sys.stderr,
            )
            return 2
        if external is not None and not headlines_match(result, expected):
            print("recomputed headlines differ from the committed aggregates", file=sys.stderr)
            sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
            return 1
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        write_text(args.output, text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
