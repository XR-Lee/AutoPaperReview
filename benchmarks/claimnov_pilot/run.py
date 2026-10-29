"""Run APR on the ClaimNov pilot sample.

The model call needs ``APR_MODEL_API_KEY``. This script does not invent a key
and does not regenerate ClaimNov proposals. Proposal text and APR outputs stay
under a gitignored directory.

Exit 3 when the model secret is unset. Exit 4 when that secret is set but the
checkout has no proposal text or no pool abstracts. Exit 2 when both are
present: this revision does not call the model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REQUIRED_SECRET = "APR_MODEL_API_KEY"
# Names only. Values are never printed.
INPUT_SECRETS = (
    "S2_API_KEY",
    "OPENREVIEW_USERNAME",
    "OPENREVIEW_PASSWORD",
    "ANT_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
)
DEFAULT_MODEL = "grok-4.7"
DEFAULT_BASE_URL = "https://api.x.ai/v1"
_PROPOSAL_FILES = {
    "disguises": ("data/disguises.json", "eval/data/disguises.json"),
    "hybrids": ("data/hybrids.json", "eval/data/hybrids.json"),
    "claim_disputed": ("eval/data/claim_disputed/testset_clean.jsonl",),
}


def secret_is_set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def blocker_message() -> str | None:
    """Return the reason the pilot cannot call the model, or None."""
    if secret_is_set(REQUIRED_SECRET):
        return None
    return (
        f"{REQUIRED_SECRET} is not set. "
        "APR's OpenAI-compatible adapter reads that variable. "
        f"The pilot model is {DEFAULT_MODEL} at APR_MODEL_BASE_URL "
        f"(default {DEFAULT_BASE_URL}). "
        "Pool abstracts are not in the public ClaimNov files; hydrate needs "
        + ", ".join(INPUT_SECRETS[:3])
        + ". Disguise, hybrid, and claim-disputed proposal text is not published; "
        "rebuilding it needs ANT_API_KEY or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, "
        "and the new text would not match the published per-item rows."
    )


def _pool_papers(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    papers = payload.get("pool") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and not isinstance(papers, list):
        papers = payload.get("papers")
    if not isinstance(papers, list):
        return []
    return [paper for paper in papers if isinstance(paper, dict)]


def _pool_paths(root: Path) -> list[Path]:
    data = root / "data"
    paths = list((data / "disguises" / "pools").glob("*.json"))
    paths += list((data / "claim_disputed" / "pools").glob("*.json"))
    paths += list((data / "later_realized").rglob("pool_preT.json"))
    return paths


def input_inventory(root: Path) -> dict:
    """Count inputs the checkout can supply. No titles or proposal text."""
    root = root.resolve()
    present = {
        name: any((root / relative).is_file() for relative in relatives)
        for name, relatives in _PROPOSAL_FILES.items()
    }
    described = 0
    set_size = 0
    set_path = root / "data" / "later_realized" / "later_realized_set.json"
    if set_path.is_file():
        raw_ids = json.loads(set_path.read_text(encoding="utf-8"))
        if isinstance(raw_ids, list):
            wanted = {str(item) for item in raw_ids}
            set_size = len(wanted)
            found: set[str] = set()
            for path in (root / "data" / "later_realized").rglob("ideas.json"):
                try:
                    ideas = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(ideas, list):
                    continue
                slug = path.parent.name
                for idea in ideas:
                    if not isinstance(idea, dict) or not isinstance(idea.get("idx"), int):
                        continue
                    description = idea.get("description")
                    if isinstance(description, str) and description.strip():
                        found.add(f"{slug}::{idea['idx']}")
            described = len(wanted & found)
    pool_records = 0
    missing_abstract = 0
    for path in _pool_paths(root):
        for paper in _pool_papers(path):
            pool_records += 1
            abstract = str(paper.get("abstract") or paper.get("summary") or "").strip()
            if not abstract:
                missing_abstract += 1
    return {
        "claimnov_root": root.is_dir(),
        "later_realized_set": set_size,
        "later_realized_described": described,
        "pool_records": pool_records,
        "pools_missing_abstract": missing_abstract,
        "proposal_text_present": present,
    }


def inventory_blocks(inventory: dict) -> list[str]:
    """Reasons a scored run would not match the published rows."""
    if not inventory.get("claimnov_root"):
        return ["claimnov root is missing"]
    blocks: list[str] = []
    missing_abstract = int(inventory.get("pools_missing_abstract") or 0)
    if missing_abstract:
        blocks.append(f"{missing_abstract} prior-work records have no abstract")
    present = inventory.get("proposal_text_present") or {}
    for name in ("disguises", "hybrids", "claim_disputed"):
        if not present.get(name):
            blocks.append(f"{name} proposal text is not in the checkout")
    described = int(inventory.get("later_realized_described") or 0)
    set_size = int(inventory.get("later_realized_set") or 0)
    if described < set_size:
        blocks.append("later-realized descriptions do not cover the published set")
    return blocks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claimnov", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args(argv)
    inventory = input_inventory(args.claimnov)
    message = blocker_message()
    if message:
        print(message, file=sys.stderr)
        print(json.dumps({"input_inventory": inventory}, sort_keys=True), file=sys.stderr)
        return 3
    blocks = inventory_blocks(inventory)
    if blocks:
        print("scored run refused: " + "; ".join(blocks), file=sys.stderr)
        print(json.dumps({"input_inventory": inventory}, sort_keys=True), file=sys.stderr)
        return 4
    print(
        "proposal text and pool abstracts are present; "
        "this revision does not call the model",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
