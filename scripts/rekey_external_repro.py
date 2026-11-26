#!/usr/bin/env python3
"""Rewrite external proposal-row identifiers with HMAC-SHA256.

Reads APR_PROPOSAL_REPRO_DIR and APR_PROPOSAL_REPRO_SALT. Writes to the
independent directory given by --output. Use --published-only for the files
required by the official reproduction script.

Identifier fields, including nlp84 row keys and a mapping.csv ID column,
are replaced. The salt is not written. Raw identifiers are not printed.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from autopaperreview.proposal.data import digest
from autopaperreview.proposal.fixed_benchmark import cohort_cases
from autopaperreview.proposal.repro_metrics import SALT_ENV, identifier_key, published_external_files

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
_EXTENSIONS = {".json", ".jsonl", ".csv", ".tsv"}


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
            elif name in _ID_FIELDS and isinstance(item, dict) and isinstance(item.get("value"), str):
                rewritten[key] = _walk(item, salt, cache)
                if item["value"].strip():
                    rewritten[key]["value"] = _keyed(item["value"], salt, cache)
            elif name in _ID_LISTS and isinstance(item, list):
                rewritten[key] = [
                    _keyed(entry, salt, cache)
                    if isinstance(entry, str) and entry.strip()
                    else _walk(entry, salt, cache)
                    for entry in item
                ]
            else:
                rewritten[key] = _walk(item, salt, cache)
        return rewritten
    if isinstance(value, list):
        return [_walk(item, salt, cache) for item in value]
    return value


def _rekey_csv(text: str, delimiter: str, salt: str, cache: dict[str, str]) -> str:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=delimiter, strict=True))
    header = next((i for i, row in enumerate(rows) if any(cell.strip() for cell in row)), None)
    if header is None:
        return text
    names = [cell.strip().lower() for cell in rows[header]]
    columns = [index for index, name in enumerate(names) if name in _ID_FIELDS]
    for row in rows[header + 1 :]:
        for index in columns:
            if index < len(row) and row[index].strip():
                row[index] = _keyed(row[index].strip(), salt, cache)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue()


def _rekey_json(value, salt, cache):
    rewritten = _walk(value, salt, cache)
    if isinstance(value, dict) and "ids_sha256" in value and "cases" in value:
        test_ids = cohort_cases(value)
        rewritten["ids_sha256"] = digest(sorted(_keyed(key, salt, cache) for key in test_ids))
    return rewritten


def _rekey_text(path, salt, cache):
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = _rekey_json(json.loads(text), salt, cache)
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if suffix == ".jsonl":
        return "".join(
            json.dumps(_rekey_json(json.loads(line), salt, cache), ensure_ascii=False) + "\n"
            for line in text.split("\n")
            if line.strip()
        )
    return _rekey_csv(text, "\t" if suffix == ".tsv" else ",", salt, cache)


def _check_directory(path):
    for directory in (*reversed(path.parents), path):
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError("output directory and its parents must be real directories")


def _check_destination(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("output files must be regular files without other links")


def _open_output_directory(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
    except BaseException:
        os.close(directory)
        raise
    return directory


def _identity(info):
    return info.st_dev, info.st_ino


def _verify_directory(path, directory, source_directories):
    expected = _identity(os.fstat(directory))
    if expected in source_directories or _identity(path.lstat()) != expected:
        raise ValueError("output directory changed or aliases an input directory")


def _verify_destination(directory, name):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _check_destination(info)


def _remove_temporary(directory, name):
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        pass


def _stage_output(directory, content, cleanup):
    temp_name = f".rekey-{uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(temp_name, flags, 0o600, dir_fd=directory)
    cleanup.callback(_remove_temporary, directory, temp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    return temp_name


def rekey_tree(source: Path, output: Path, salt: str, *, published_only: bool = False) -> int:
    source = source.resolve()
    source_identity = _identity(source.stat())
    raw_output = output.absolute()
    output = Path(os.path.abspath(raw_output))
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("input and output directories must not overlap")
    if not isinstance(salt, str) or not salt:
        raise ValueError("an HMAC salt is required")
    if published_only:
        paths = [source / name for name in published_external_files()]
    else:
        paths = [path for path in source.rglob("*") if path.suffix.lower() in _EXTENSIONS and path.is_file()]

    plan = []
    source_paths = {source}
    for path in sorted(paths):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("input must contain all required regular files within its directory")
        source_paths.update(parent for parent in path.parents if parent.is_relative_to(source))
        dest = output / path.relative_to(source)
        plan.append((path, dest))
    source_identities = {path: _identity(path.stat()) for path in source_paths}
    if source_identities[source] != source_identity:
        raise ValueError("input directory changed during preflight")
    source_directories = set(source_identities.values())

    _check_directory(raw_output)
    for _, dest in plan:
        _check_directory(dest.parent)
        try:
            info = dest.lstat()
        except FileNotFoundError:
            pass
        else:
            _check_destination(info)

    with ExitStack() as cleanup:
        directories = {}
        for folder in sorted({dest.parent for _, dest in plan}):
            directory = _open_output_directory(folder)
            cleanup.callback(os.close, directory)
            _verify_directory(folder, directory, source_directories)
            directories[folder] = directory

        if any(_identity(path.stat()) != identity for path, identity in source_identities.items()):
            raise ValueError("input directory changed during preflight")
        cache: dict[str, str] = {}
        rewritten = [(dest, _rekey_text(path, salt, cache)) for path, dest in plan]
        staged = []
        for dest, content in rewritten:
            directory = directories[dest.parent]
            _verify_directory(dest.parent, directory, source_directories)
            _verify_destination(directory, dest.name)
            staged.append((dest, _stage_output(directory, content, cleanup)))
        # Stage every output before replacing any existing file. Keep every
        # rename relative to the original directory, even if its path moves.
        for dest, _ in staged:
            directory = directories[dest.parent]
            _verify_directory(dest.parent, directory, source_directories)
            _verify_destination(directory, dest.name)
        for dest, temporary in staged:
            directory = directories[dest.parent]
            _verify_directory(dest.parent, directory, source_directories)
            _verify_destination(directory, dest.name)
            os.replace(temporary, dest.name, src_dir_fd=directory, dst_dir_fd=directory)
        for folder, directory in directories.items():
            _verify_directory(folder, directory, source_directories)
    return len(plan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--published-only", action="store_true")
    args = parser.parse_args(argv)
    root = args.root or (Path(os.environ[DIR_ENV]) if os.environ.get(DIR_ENV) else None)
    salt = os.environ.get(SALT_ENV, "")
    if root is None or not root.is_dir():
        print(f"Set {DIR_ENV} to the external row directory.", file=sys.stderr)
        return 2
    if not salt:
        print(f"Set {SALT_ENV}. The salt is not stored.", file=sys.stderr)
        return 2
    try:
        count = rekey_tree(root, args.output, salt, published_only=args.published_only)
    except (csv.Error, KeyError, OSError, RuntimeError, TypeError, ValueError):
        print("Could not safely rekey the external files. Check the input files and output path.", file=sys.stderr)
        return 2
    print(f"rewrote {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
