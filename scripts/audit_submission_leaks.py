#!/usr/bin/env python3
"""Count residual camera-ready leak markers per tier.

The report is counts only. It does not print manuscript text, titles, or
forum ids. Texts stay in the directory you pass; this script does not write
them back.

Examples
--------
Synthetic layout, one directory per tier::

    python3 scripts/audit_submission_leaks.py --texts-dir workdir/leak-audit

Locked dev manifest plus a local cache (gitignored)::

    python3 scripts/audit_submission_leaks.py \\
        --manifest benchmarks/fixed_eval/manifest.json \\
        --papers-dir workdir/calibration/papers
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autopaperreview.iclr_anonymize import audit_by_tier  # noqa: E402


def _rows_from_tier_dirs(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.txt")):
        if not path.is_file():
            continue
        tier = path.parent.name if path.parent != root else "unknown"
        rows.append({"tier": tier, "text": path.read_text(encoding="utf-8", errors="replace")})
    return rows


def _rows_from_manifest(manifest_path: Path, papers_dir: Path, filename: str) -> tuple[list[dict[str, str]], int]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    papers = ((payload.get("iclr") or {}).get("papers")) or []
    rows: list[dict[str, str]] = []
    missing = 0
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        forum_id = str(paper.get("forum_id") or "")
        tier = str(paper.get("tier") or "unknown")
        path = papers_dir / forum_id / filename
        if not path.is_file():
            missing += 1
            rows.append({"tier": tier, "text": ""})
            continue
        rows.append({"tier": tier, "text": path.read_text(encoding="utf-8", errors="replace")})
    return rows, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-tier residual leak-marker counts. No paper text.")
    parser.add_argument("--texts-dir", type=Path, help="directory of .txt files; the parent folder name is the tier")
    parser.add_argument("--manifest", type=Path, help="fixed-eval manifest; tiers come from iclr.papers")
    parser.add_argument("--papers-dir", type=Path, help="cache papers directory keyed by forum id")
    parser.add_argument(
        "--filename",
        default="manuscript.txt",
        help="file name inside each paper directory (default: manuscript.txt)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="count markers before anonymization; the default counts residuals after it",
    )
    args = parser.parse_args(argv)
    if args.texts_dir and args.manifest:
        print("pass either --texts-dir or --manifest, not both", file=sys.stderr)
        return 2
    if args.manifest:
        if args.papers_dir is None:
            print("--manifest requires --papers-dir", file=sys.stderr)
            return 2
        rows, missing = _rows_from_manifest(args.manifest, args.papers_dir, args.filename)
    elif args.texts_dir:
        rows = _rows_from_tier_dirs(args.texts_dir)
        missing = 0
    else:
        print("pass --texts-dir or --manifest with --papers-dir", file=sys.stderr)
        return 2
    report = audit_by_tier(rows, apply_anonymizer=not args.raw)
    report["missing_files"] = missing
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
