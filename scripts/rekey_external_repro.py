#!/usr/bin/env python3
"""Rewrite external proposal-row identifiers with HMAC-SHA256.

Reads APR_PROPOSAL_REPRO_DIR and APR_PROPOSAL_REPRO_SALT. Writes a new
directory given by --output. The originals are not modified, so the
checksums in benchmarks/proposal/repro/external-sha256.json still match.

Identifier fields, including nlp84 row keys and a mapping.csv ID column,
are replaced. The salt is not written. Raw identifiers are not printed.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

from autopaperreview.proposal.repro_metrics import SALT_ENV, identifier_key

DIR_ENV = "APR_PROPOSAL_REPRO_DIR"
_ID_FIELDS = frozenset(
    {
        "forum",
        "forum_id",
        "note_id",
        "review_id",
        "replyto",
        "reply_to",
        "key",
        "id",
    }
)
_ID_LISTS = frozenset(
    {
        "note_ids",
        "review_ids",
        "review_note_ids",
        "initial_review_note_ids",
        "forum_ids",
    }
)


def _keyed(value: str, salt: str, cache: dict[str, str]) -> str:
    if value not in cache:
        cache[value] = identifier_key(value, salt)
    return cache[value]


def _walk(value, salt: str, cache: dict[str, str]):
    if isinstance(value, dict):
        rewritten = {}
        for key, item in value.items():
            name = str(key).strip().lower()
            if name in _ID_FIELDS and isinstance(item, str) and item.strip():
                rewritten[key] = _keyed(item, salt, cache)
            elif name in _ID_LISTS and isinstance(item, list):
                rewritten[key] = [
                    _keyed(entry, salt, cache) if isinstance(entry, str) and entry.strip() else entry
                    for entry in item
                ]
            else:
                rewritten[key] = _walk(item, salt, cache)
        return rewritten
    if isinstance(value, list):
        return [_walk(item, salt, cache) for item in value]
    return value


def _rekey_csv(text: str, salt: str, cache: dict[str, str]) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    header = cleaned.split("\n", 1)[0]
    delimiter = "\t" if header.count("\t") > header.count(",") else ","
    rows = list(csv.reader(io.StringIO(cleaned), delimiter=delimiter))
    if not rows:
        return text
    names = [cell.strip().lower() for cell in rows[0]]
    columns = [index for index, name in enumerate(names) if name in {"id", "key", "forum", "forum_id"}]
    for row in rows[1:]:
        for index in columns:
            if index < len(row) and row[index].strip():
                row[index] = _keyed(row[index].strip(), salt, cache)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue()


def rekey_tree(source: Path, output: Path, salt: str) -> int:
    if output.resolve() == source.resolve():
        raise ValueError("write the rekeyed rows to a different directory")
    cache: dict[str, str] = {}
    files = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        dest = output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix == ".json":
            dest.write_text(
                json.dumps(_walk(json.loads(path.read_text(encoding="utf-8")), salt, cache), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            files += 1
        elif suffix in {".csv", ".tsv"}:
            dest.write_text(_rekey_csv(path.read_text(encoding="utf-8"), salt, cache), encoding="utf-8")
            files += 1
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root or (Path(os.environ[DIR_ENV]) if os.environ.get(DIR_ENV) else None)
    salt = os.environ.get(SALT_ENV, "")
    if root is None or not root.is_dir():
        print(f"Set {DIR_ENV} to the external row directory.", file=sys.stderr)
        return 2
    if not salt:
        print(f"Set {SALT_ENV}. The salt is not stored.", file=sys.stderr)
        return 2
    count = rekey_tree(root, args.output, salt)
    print(f"rewrote {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
