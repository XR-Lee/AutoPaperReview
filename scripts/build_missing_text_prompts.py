#!/usr/bin/env python3
"""Build raw and anonymized forecast prompts for newly supplied dev texts.

Reads gitignored files under workdir/. Writes prompts and a numeric assignment
there. Prints counts only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from autopaperreview.anchors import FULL_TEXT_MAX_CHARS  # noqa: E402
from autopaperreview.iclr_anonymize import (  # noqa: E402
    _AFFILIATION,
    _EMAIL,
    _GITHUB,
    anonymize_iclr_submission,
    residual_markers,
)
from autopaperreview.model_exec import forecast_task_text  # noqa: E402
from benchmarks.promotion.pdftotext_norm import collapse_smallcaps  # noqa: E402

ARCHIVE = ROOT / "workdir/missing-text"
OUT = ROOT / "workdir/full-rerun"
HEADER_SOURCE = ROOT / "workdir/anon-rerun/prompts/candidate/001.txt"


def _norm(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def _header() -> str:
    text = HEADER_SOURCE.read_text(encoding="utf-8")
    start = text.index("## First-Read Writing Contract")
    end = text.index("## Source Text")
    return text[start:end].rstrip() + "\n"


def _load_assignment() -> tuple[dict[str, int], str | None, list[dict]]:
    manifest = json.loads((ROOT / "benchmarks/fixed_eval/manifest.json").read_text(encoding="utf-8"))
    papers = manifest["iclr"]["papers"]
    urls = []
    for line in (ARCHIVE / "urls.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        urls.append(line.split("id=", 1)[1].split("&", 1)[0] if "id=" in line else line.split("/")[-1])
    by_forum = {paper["forum_id"]: index for index, paper in enumerate(papers)}
    targets = [(by_forum[forum], papers[by_forum[forum]]) for forum in urls]
    rows = []
    for line in (ARCHIVE / "filemap.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, name = line.split("\t", 1)
        stem = re.sub(r"\s*\(\d+\)", "", Path(name).name)
        stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
        match = re.match(r"^\d+_(.+)$", stem)
        words = (match.group(1) if match else stem).replace("_", " ")
        rows.append((sha.strip(), _norm(words)))

    def hits_for(prefix: str) -> list[int]:
        hits = []
        for index, paper in targets:
            title = _norm(paper["title"])
            if title.startswith(prefix) or (prefix and prefix.startswith(title)):
                hits.append(index)
                continue
            prefix_tokens = prefix.split()
            title_tokens = title.split()
            if (
                prefix_tokens
                and len(prefix_tokens) <= len(title_tokens)
                and prefix_tokens[:-1] == title_tokens[: len(prefix_tokens) - 1]
                and title_tokens[len(prefix_tokens) - 1].startswith(prefix_tokens[-1])
            ):
                hits.append(index)
        return hits

    assigned: dict[str, int] = {}
    extra: str | None = None
    for sha, prefix in rows:
        hits = hits_for(prefix)
        if len(hits) == 1:
            assigned[sha] = hits[0]
        else:
            extra = sha
    records = []
    for sha, index in assigned.items():
        raw = (ARCHIVE / "txt" / f"{sha}.txt").read_text(encoding="utf-8", errors="replace")
        text = collapse_smallcaps(raw)
        head = "\n".join(text.splitlines()[:25]).lower()
        if "published as a conference paper" in head and "anonymous authors" not in head:
            kind = "published"
        elif "anonymous authors" in head:
            kind = "anonymous"
        else:
            kind = "neither"
        records.append({"index": index, "sha": sha, "kind": kind, "tier": papers[index]["tier"]})
    return assigned, extra, records


def _packet(source: str, *, grounded: bool) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    shown = source[:FULL_TEXT_MAX_CHARS]
    return (
        "# Review Route M0: Structured closed-book review\n\n"
        f"- Source SHA-256: `{digest}`\n"
        "- Network policy: `deny`\n"
        "- Prompt version: `1.1`\n"
        "- Manuscript access: `true`\n"
        "- Route kind: `prompt`\n\n"
        f"{_header()}\n"
        "## Source Text\n\n"
        f"{shown}\n\n"
        f"{forecast_task_text(grounded_weaknesses=grounded)}\n"
    )


def main() -> int:
    papers = json.loads((ROOT / "benchmarks/fixed_eval/manifest.json").read_text(encoding="utf-8"))["iclr"]["papers"]
    assigned, extra, records = _load_assignment()
    if len(assigned) + (1 if extra else 0) != 70 or len(set(assigned.values())) != len(assigned):
        raise SystemExit(f"assignment {len(assigned)} unique {len(set(assigned.values()))} extra {extra is not None}")
    header = _header()
    if "Source SHA-256" in header or "## Source Text" in header:
        raise SystemExit("header still contains a manuscript section")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "assignment.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    leak = Counter()
    tier_kind = Counter()
    for record in records:
        sha = record["sha"]
        raw = collapse_smallcaps((ARCHIVE / "txt" / f"{sha}.txt").read_text(encoding="utf-8", errors="replace"))
        anon, _removed = anonymize_iclr_submission(raw)
        record_counts = residual_markers(raw)
        after = residual_markers(anon)
        head = "\n".join(raw.splitlines()[:40])
        head_after = "\n".join(anon.splitlines()[:40])
        flags = {
            "email": int(record_counts["email"] > 0),
            "github": int(record_counts["github_named"] > 0),
            "ack": int(record_counts["acknowledgement_heading"] > 0),
            "status": int(record_counts["camera_ready_or_status"] > 0),
            "aff40": int(_AFFILIATION.search(head) is not None),
            "email_after": int(_EMAIL.search(anon) is not None),
            "github_after": int(_GITHUB.search(anon) is not None),
            "ack_after": int(after["acknowledgement_heading"] > 0),
            "status_after": int(after["camera_ready_or_status"] > 0),
            "aff40_after": int(_AFFILIATION.search(head_after) is not None),
        }
        for key, value in flags.items():
            leak[(record["tier"], record["kind"], key)] += value
        tier_kind[(record["tier"], record["kind"])] += 1
        for condition, text in (("raw", raw), ("anon", anon)):
            for arm, grounded in (("candidate", True), ("main", False)):
                packet = _packet(text, grounded=grounded)
                path = OUT / "prompts" / condition / arm / f"{record['index']:03d}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(packet, encoding="utf-8")
    summary = {
        "mapped": len(assigned),
        "extra_unmatched_files": int(extra is not None),
        "missing_targets": 69 - len(assigned),
        "tier_kind": {f"{tier}|{kind}": count for (tier, kind), count in sorted(tier_kind.items())},
        "markers": {f"{tier}|{kind}|{key}": count for (tier, kind, key), count in sorted(leak.items())},
    }
    (OUT / "coverage.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    prompts = list((OUT / "prompts").rglob("*.txt"))
    print("mapped", len(assigned), "extra", int(extra is not None), "prompts", len(prompts))
    print("candidate grounded", sum("Keep the original diagnosis" in path.read_text(encoding="utf-8") for path in prompts if "/candidate/" in str(path)))
    print("main grounded", sum("Keep the original diagnosis" in path.read_text(encoding="utf-8") for path in prompts if "/main/" in str(path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
