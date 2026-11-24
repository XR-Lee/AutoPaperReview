"""Build paired manuscripts, blind review arms and score adjudicated feedback."""

import argparse
import hashlib
import json
from pathlib import Path

from .cases import SCENARIOS
from .grading import prepare, summarize


def item_id(name, variant):
    return hashlib.sha256(f"feedback-v1/{name}/{variant}".encode()).hexdigest()[:16]


def build(out, limit=None):
    if limit is not None and not 1 <= limit <= len(SCENARIOS):
        raise ValueError(f"--limit must be between 1 and {len(SCENARIOS)}")
    cases = []
    texts = {}
    for ordinal, case in enumerate(SCENARIOS[:limit], 1):
        variants = {}
        for variant in ("clean", "defect"):
            key = item_id(case.name, variant)
            manuscript = case.manuscript(variant)
            texts[f"{key}.txt"] = manuscript
            variants[variant] = {"item_id": key, "sha256": hashlib.sha256(manuscript.encode()).hexdigest()}
        cases.append({"ordinal": ordinal, "scenario": case.name, "variants": variants,
                      "diagnosis": case.diagnosis, "evidence": case.evidence, "repair": case.repair})
    manifest = {"format": "feedback-quality-v1", "scenarios": cases,
                "input_order": sorted(v["item_id"] for case in cases for v in case["variants"].values())}
    manifest_path = out / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("The target directory contains a different benchmark build; choose a fresh directory")
    inputs = out / "inputs"
    if extra := {p.name for p in inputs.glob("*.txt")} - texts.keys():
        raise ValueError(f"The input directory contains extra manuscript files: {sorted(extra)}")
    inputs.mkdir(parents=True, exist_ok=True)
    for name, manuscript in texts.items():
        (inputs / name).write_text(manuscript, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build", help="Write manuscripts and a separate adjudicator manifest")
    make.add_argument("--out", required=True, type=Path)
    make.add_argument("--limit", type=int, help="Include just the first N scenario pairs")
    packet = commands.add_parser("packet", help="Construct a shuffled, arm-blind adjudication packet")
    packet.add_argument("--dataset", required=True, type=Path, help="Directory produced by build")
    packet.add_argument("--arm", action="append", required=True, metavar="NAME=REVIEWS.jsonl")
    packet.add_argument("--out", required=True, type=Path)
    packet.add_argument("--seed", default=74819, type=int)
    packet.add_argument("--first", default=1, type=int, help="First included scenario ordinal, inclusive")
    packet.add_argument("--last", type=int, help="Last included scenario ordinal, inclusive")
    score = commands.add_parser("score", help="Score completed blinded labels against their packet")
    score.add_argument("--packet", required=True, type=Path)
    score.add_argument("--labels", required=True, type=Path)
    score.add_argument("--baseline", help="Reference arm for paired differences (default: first --arm)")
    score.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "build":
        build(args.out, args.limit)
    elif args.command == "packet":
        arms = {}
        for entry in args.arm:
            name, sep, path = entry.partition("=")
            if not sep or not name or not path or name in arms:
                parser.error("Each --arm needs a unique nonempty NAME and a JSONL file")
            arms[name] = Path(path)
        prepare(args.dataset, arms, args.out, args.seed, args.first, args.last)
    elif args.command == "score":
        result = summarize(args.packet, args.labels, args.baseline)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
