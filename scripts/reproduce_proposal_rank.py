#!/usr/bin/env python3
"""Score a local proposal-rank cohort.

The fixed100, confirm100, replications199, top-20%, and 84-NLP headline
numbers are reported by the original Codex run, not yet reproducible from
repo artifacts. This script scores only the manifest, predictions, and
references you pass. It does not download papers and it does not call a model.
"""

from __future__ import annotations

import argparse
import json
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

HEADLINE = (
    "reported by the original Codex run, not yet reproducible from repo artifacts"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap", type=int, default=0)
    parser.add_argument("--stratified-draws", type=int, default=3000)
    args = parser.parse_args(argv)
    paths = (args.manifest, args.predictions, args.references)
    if any(path is None for path in paths):
        print(
            "The fixed100, confirm100, replications199, top-20%, and 84-NLP "
            f"headline numbers are {HEADLINE}.",
            file=sys.stderr,
        )
        print(
            "Pass --manifest, --predictions, and --references. "
            "This script does not download papers and does not call a model.",
            file=sys.stderr,
        )
        return 2
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        print("missing cohort artifact: " + ", ".join(missing), file=sys.stderr)
        print(f"Published headline numbers are {HEADLINE}.", file=sys.stderr)
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
    result["published_headlines"] = HEADLINE
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
