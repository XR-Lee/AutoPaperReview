#!/usr/bin/env python3
"""Fail when tracked files look like confidential review material.

The check is generic on purpose. It does not store manuscript sentences.
Exact strings, when needed, are blocked by SHA-256 of a normalized form.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent

FORBIDDEN_PREFIXES = (
    "workdir/",
    "reviews/",
    "papers/",
    "uploads/",
    "page_renders/",
    "page-renders/",
    "review_output/",
    "review_outputs/",
)

FORBIDDEN_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
    ".zip",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".odt",
    ".rtf",
}

MAGIC = (
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"PK\x03\x04", "zip"),
)

PAGE_RENDER = re.compile(
    r"(^|/)(pages?/|page[-_]?\d+\.(png|jpe?g|pdf|webp|tiff?)$)",
    re.IGNORECASE,
)

MARKER_SPLIT = re.compile(
    r"\s*(?:\(\d{1,2}\)|\([ivx]{1,6}\)|\d{1,2}[.)]|[•●▪])\s+",
    re.IGNORECASE,
)
LEADING_MARKER = re.compile(
    r"^(?:\(\d{1,2}\)|\([ivx]{1,6}\)|\d{1,2}[.)]|[•●▪])\s+",
    re.IGNORECASE,
)

SKIP_CONTENT = {
    "scripts/confidentiality_hashes.txt",
    "scripts/confidentiality_patterns.txt",
    "scripts/confidentiality_allowlist.txt",
    "scripts/check_confidentiality.py",
}


def normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def candidates(line: str) -> set[str]:
    """Normalized strings whose hashes are compared to the denylist."""
    norm = normalize(line)
    if not norm:
        return set()
    values = {norm, norm.rstrip(".").strip()}
    unquoted = norm.strip("\"'").strip()
    values.add(unquoted)
    values.add(unquoted.rstrip(".").strip())
    stripped = LEADING_MARKER.sub("", unquoted).strip()
    values.add(stripped)
    values.add(stripped.rstrip(".").strip())
    for part in MARKER_SPLIT.split(unquoted):
        part = part.strip(" ;.")
        if len(part.split()) >= 6:
            values.add(part)
            values.add(part.rstrip(".").strip())
    return {value for value in values if len(value) >= 24}


def load_hashes(path: Path) -> set[str]:
    hashes: set[str] = set()
    if not path.is_file():
        return hashes
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            hashes.add(line)
    return hashes


def load_patterns(path: Path) -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    if not path.is_file():
        return patterns
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, expression = line.split(maxsplit=1)
        patterns.append((name, re.compile(expression)))
    return patterns


def load_allowlist(path: Path) -> set[str]:
    allowed: set[str] = set()
    if not path.is_file():
        return allowed
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            allowed.add(line.replace("\\", "/").lstrip("./"))
    return allowed


def tracked_files(root: Path) -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    return [root / part.decode("utf-8") for part in output.split(b"\0") if part]


_SKIP_WALK = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runs",
    "node_modules",
}


def walked_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(piece in _SKIP_WALK for piece in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def sniff_magic(header: bytes) -> str | None:
    for prefix, kind in MAGIC:
        if header.startswith(prefix):
            return kind
    return None


def scan_file(
    path: Path,
    relative: str,
    *,
    allowlist: set[str],
    patterns: list[tuple[str, re.Pattern[str]]],
    hashes: set[str],
) -> list[str]:
    findings: list[str] = []
    posix = relative.replace("\\", "/")
    allowed = posix in allowlist

    if any(posix == prefix.rstrip("/") or posix.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        findings.append(f"{posix}: forbidden review path")
        return findings

    if not allowed and PAGE_RENDER.search(posix):
        findings.append(f"{posix}: page render is not allowed in the repository")

    suffix = path.suffix.lower()
    header = b""
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
            blob = header + handle.read()
    except OSError as exc:
        findings.append(f"{posix}: unreadable ({exc.strerror})")
        return findings

    magic = sniff_magic(header)
    if not allowed and (suffix in FORBIDDEN_EXTENSIONS or magic):
        kind = magic or suffix.lstrip(".")
        findings.append(f"{posix}: forbidden document or image ({kind})")
        return findings

    if posix in SKIP_CONTENT or magic:
        return findings

    text = blob.decode("utf-8", "replace")
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in patterns:
            if pattern.search(line):
                findings.append(f"{posix}:{number}: denylist pattern {name}")
                break
        if not hashes:
            continue
        for value in candidates(line):
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if digest in hashes:
                findings.append(f"{posix}:{number}: blocked content hash")
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Scan this directory instead of git ls-files (used by tests).",
    )
    parser.add_argument("--hashes", type=Path, default=SCRIPT_DIR / "confidentiality_hashes.txt")
    parser.add_argument("--patterns", type=Path, default=SCRIPT_DIR / "confidentiality_patterns.txt")
    parser.add_argument("--allowlist", type=Path, default=SCRIPT_DIR / "confidentiality_allowlist.txt")
    args = parser.parse_args(argv)

    root = (args.root or REPO_ROOT).resolve()
    hashes = load_hashes(args.hashes)
    patterns = load_patterns(args.patterns)
    allowlist = load_allowlist(args.allowlist)

    if args.root is None:
        files = tracked_files(root)
    else:
        files = walked_files(root)

    findings: list[str] = []
    for path in files:
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            relative = path.as_posix()
        findings.extend(
            scan_file(
                path,
                relative,
                allowlist=allowlist,
                patterns=patterns,
                hashes=hashes,
            )
        )

    if findings:
        print("confidentiality check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print(f"confidentiality check passed ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
