"""Build the public sample from a local ClaimNov checkout.

The checkout is not committed. Title-bearing ids are hashed before they are written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.claimnov_pilot.select import PER_DATASET, SEED, digest, draw_sample


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def disguised_catalog(root: Path) -> list[dict]:
    seen: set[str] = set()
    items: list[dict] = []
    sources = (
        root / "eval" / "results" / "coupled_veto_smuggle_profile.jsonl",
        root / "eval" / "results" / "smuggle_v2.jsonl",
        root / "eval" / "results" / "smuggle_v2_api.jsonl",
    )
    for path in sources:
        for row in _jsonl(path):
            if path.name != "coupled_veto_smuggle_profile.jsonl" and row.get("gate") != "claimnov":
                continue
            item_id = str(row["item"])
            if item_id in seen:
                continue
            seen.add(item_id)
            condition = str(row["cond"])
            items.append(
                {
                    "key": item_id,
                    "item_id": item_id,
                    "label": "non_novel" if condition == "B_in" else "src_out",
                    "condition": condition,
                    "rung": int(row["rung"]),
                }
            )
    return items


def claim_disputed_catalog(root: Path) -> list[dict]:
    pairs = [
        json.loads(line)
        for line in (root / "data" / "claim_disputed" / "pairs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    have = {str(row["item"]) for row in _jsonl(root / "eval" / "results" / "coupled_veto_neg_profile.jsonl")}
    items = []
    for index, pair in enumerate(pairs):
        item_id = f"n2f_{index}"
        decision = pair.get("iclr_decision")
        if item_id not in have or decision not in {"accept", "reject"}:
            continue
        items.append(
            {
                "key": item_id,
                "item_id": item_id,
                "label": "claim_disputed",
                "decision": decision,
            }
        )
    return items


def _verdict_ids(root: Path, names: tuple[str, ...], allowed: set[str]) -> set[str]:
    found: set[str] = set()
    for name in names:
        for row in _jsonl(root / "eval" / "results" / name):
            if name == "hybrids_eval.jsonl" and row.get("gate") != "claimnov":
                continue
            item_id = str(row["item"])
            if item_id in allowed:
                found.add(item_id)
    return found


def later_realized_catalog(root: Path) -> list[dict]:
    allowed = set(json.loads((root / "data" / "later_realized" / "later_realized_set.json").read_text(encoding="utf-8")))
    buckets: dict[str, str] = {}
    for row in _jsonl(root / "eval" / "results" / "claim_sort_full_indep.jsonl"):
        item_id = str(row["item"])
        if item_id in allowed and item_id not in buckets:
            buckets[item_id] = str(row["bucket"])
    covered = _verdict_ids(
        root,
        ("coupled_veto_fa_profile.jsonl", "coupled_veto_ac_profile.jsonl"),
        allowed,
    )
    items = []
    for item_id in sorted(allowed):
        if item_id not in covered or item_id not in buckets:
            continue
        item_digest = digest(item_id)
        items.append(
            {
                "key": item_digest,
                "item_sha256": item_digest,
                "label": "novel",
                "bucket": buckets[item_id],
            }
        )
    return items


def hybrids_catalog(root: Path) -> list[dict]:
    items = []
    seen: set[str] = set()
    for row in _jsonl(root / "eval" / "results" / "hybrids_eval.jsonl"):
        if row.get("gate") != "claimnov":
            continue
        item_id = str(row["item"])
        if item_id in seen:
            continue
        seen.add(item_id)
        item_digest = digest(item_id)
        items.append(
            {
                "key": item_digest,
                "item_sha256": item_digest,
                "label": "spliced",
            }
        )
    return items


def build_catalogs(root: Path) -> dict[str, list[dict]]:
    return {
        "disguised": disguised_catalog(root),
        "claim_disputed": claim_disputed_catalog(root),
        "later_realized": later_realized_catalog(root),
        "hybrids": hybrids_catalog(root),
    }


def claimnov_commit(root: Path) -> str:
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return ""
    text = head.read_text(encoding="utf-8").strip()
    if text.startswith("ref:"):
        ref = root / ".git" / text.split(" ", 1)[1].strip()
        if ref.is_file():
            return ref.read_text(encoding="utf-8").strip()
    return text


def assert_public(document: dict) -> None:
    """Refuse a sample that carries prose or an unexpected field."""
    allowed = {
        "dataset",
        "item_id",
        "item_sha256",
        "label",
        "condition",
        "rung",
        "decision",
        "bucket",
    }
    for item in document["items"]:
        extra = set(item) - allowed
        if extra:
            raise ValueError(f"sample record has a non-public field: {sorted(extra)}")
        for value in item.values():
            if isinstance(value, str):
                if len(value) > 64 or any(character.isspace() for character in value):
                    raise ValueError("sample string is not an opaque id")
            elif not isinstance(value, int):
                raise ValueError("sample values must be strings or integers")


def sample_document(root: Path, *, seed: int = SEED, per_dataset: int = PER_DATASET) -> dict:
    catalogs = build_catalogs(root)
    document = {
        "seed": seed,
        "per_dataset": per_dataset,
        "claimnov_commit": claimnov_commit(root),
        "id_digest": "sha256 of the ClaimNov item id encoded as UTF-8",
        "items": draw_sample(catalogs, seed=seed, per_dataset=per_dataset),
    }
    assert_public(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claimnov", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    document = sample_document(args.claimnov)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for item in document["items"]:
        counts[item["dataset"]] = counts.get(item["dataset"], 0) + 1
    print(json.dumps({"items": len(document["items"]), "datasets": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
