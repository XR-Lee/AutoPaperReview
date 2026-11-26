"""CLI entry points for proposal review and its isolated calibration benchmark."""

from __future__ import annotations

import json
from pathlib import Path

from ..command_transport import command_argv
from ..hashing import sha256_file
from ..pipeline import run_lock
from ..public_calibration import assert_local_only
from . import evaluation
from .data import apd_cases, prepare, read, reported_result, snapshot_cases, write
from .fetch import SNAPSHOTS, download, live_cases
from .runner import run_batch, stage_result
from .leak_audit import apply_leak_audit, model_leak_judge
from .sanitized import sanitize_proposal, seal
from .stages import SECTIONS, calibrated, check, claude_argv, invoke, scored


def register(parent):
    cli = parent.add_parser(
        "proposal-review", help="review research ideas before experiments"
    )
    sub = cli.add_subparsers(dest="action", required=True)
    from .claim_review import register as register_claim_review
    from .clarification import register as register_clarification
    from .fixed_benchmark import register as register_fixed_benchmark
    from .pair_compare import register as register_pair_compare
    from .priority import register as register_priority
    from .reviewer_panel import register as register_panel

    register_claim_review(sub)
    register_panel(sub)
    register_priority(sub)
    register_clarification(sub)
    register_pair_compare(sub)
    register_fixed_benchmark(sub)
    fetch = sub.add_parser("fetch", help="prepare a public ICLR calibration sample")
    fetch.add_argument("--source", choices=["snapshot", "api"], default="snapshot")
    fetch.add_argument(
        "--papers", type=Path, help="local OpenReview submission snapshot JSON"
    )
    fetch.add_argument(
        "--forums", type=Path, help="local forum bundles JSONL, each with a notes array"
    )
    fetch.add_argument("--year", type=int, default=2024)
    fetch.add_argument("--limit", type=int, default=36)
    fetch.add_argument(
        "--holdout-after",
        type=Path,
        help="exclude every forum in a previous manifest and reserve all newly selected forums for testing",
    )
    fetch.add_argument(
        "--pages", type=int, default=1, help="number of API listing pages to fetch"
    )
    fetch.add_argument("--seed", default="prospective-v1")
    fetch.add_argument(
        "--area", action="append", default=[], help="exact primary area, repeatable"
    )
    fetch.add_argument(
        "--allow-late",
        action="store_true",
        help="also allow revised submissions for retrospective diagnostics",
    )
    apd = sub.add_parser(
        "import-apd", help="prepare every candidate in APD contribution.yaml or JSON"
    )
    apd.add_argument("document", type=Path)
    run = sub.add_parser(
        "run", help="compress all candidates, then review each proposal"
    )
    run.add_argument(
        "--stage",
        choices=[
            "ideas",
            "compress",
            "review",
            "label",
            "audit",
            "references",
            "forecast",
        ],
        default="ideas",
        help="references runs label and audit; each stage can use a different model",
    )
    score = sub.add_parser(
        "score",
        help="review one authored proposal (.txt, .md) or a structured compressor output (.json)",
    )
    score.add_argument("proposal", type=Path)
    export = sub.add_parser(
        "export", help="export the proposals and their individual reviews"
    )
    fit = sub.add_parser("fit", help="fit four shrinkage offsets on development papers")
    report = sub.add_parser(
        "report", help="evaluate frozen calibration on holdout papers"
    )
    for item in (fetch, apd, run, score, export, fit, report):
        item.add_argument(
            "--work", type=Path, required=True, help="private local run directory"
        )
        item.set_defaults(func=dispatch)
    for item in (run, score):
        item.add_argument("--model", default="claude-opus-5-5")
        item.add_argument(
            "--command",
            help="alternative no-tools CLI command with optional {model} and {system_file}",
        )
        item.add_argument("--timeout", type=float, default=180.0)
    run.add_argument("--split", choices=["dev", "holdout", "unlabeled"])
    for item in (fit, score, export):
        item.add_argument(
            "--reviewer", choices=["review", "forecast"], default="review"
        )
    run.add_argument("--jobs", type=int, default=2)
    for item in (export, score, report):
        item.add_argument(
            "--calibration", type=Path, help="previously fitted calibration.json"
        )
    for item in (export, score):
        item.add_argument(
            "--output", type=Path, help="write JSON here instead of stdout"
        )


def intake(args, root):
    if args.action == "import-apd":
        return prepare(
            apd_cases(args.document),
            root,
            seed="apd",
            limit=None,
            source={"type": "apd", "sha256": sha256_file(args.document)},
        )
    if args.source == "api":
        check(
            not (args.papers or args.forums) and args.pages >= 1,
            "API intake requires positive pages and no snapshot files",
        )
        cases = live_cases(args.year, args.pages, args.area, args.allow_late)
        source = {
            "type": "api",
            "year": args.year,
            "pages": args.pages,
            "areas": args.area,
            "allow_late": args.allow_late,
        }
    else:
        check(
            bool(args.papers) == bool(args.forums), "supply both --papers and --forums"
        )
        if args.papers:
            papers, forums = args.papers, args.forums
        else:
            check(
                args.year == 2024,
                "bundled snapshot URLs are for ICLR 2024; supply files for other years",
            )
            papers, forums = download(root / "downloads")
        hashes = [sha256_file(p) for p in (papers, forums)]
        names = [
            url if h == known else path.name
            for (url, known), h, path in zip(SNAPSHOTS, hashes, (papers, forums))
        ]
        source = {
            "type": "snapshot",
            "year": args.year,
            "areas": args.area,
            "files": dict(zip(names, hashes)),
        }
        if args.allow_late:
            source["allow_late"] = True
        cases = snapshot_cases(papers, forums, args.year, args.area, args.allow_late)
    return prepare(
        cases,
        root,
        seed=args.seed,
        limit=args.limit,
        source=source,
        previous=read(args.holdout_after) if args.holdout_after else None,
    )


def with_calibration(result, record, path):
    if path:
        calibration = read(path)
        check(
            evaluation.signature(record) == calibration["config"]["review"],
            "calibration needs the same reviewer and rubric",
        )
        result["calibrated"] = calibrated(scored(record), calibration["dimensions"])
    return result


def dispatch(args):
    root = assert_local_only(args.work)
    root.mkdir(parents=True, exist_ok=True)
    if args.action in ("fetch", "import-apd"):
        with run_lock(root):
            manifest = intake(args, root)
        result = {
            "cases": len(manifest["cases"]),
            "eligible": manifest["eligible"],
            "manifest": str(root / "manifest.json"),
        }
    elif args.action == "run":
        stages = {
            "ideas": ["compress", "review"],
            "references": ["label", "audit"],
        }.get(args.stage, [args.stage])
        command = command_argv(args.command) if args.command else claude_argv()
        judge = model_leak_judge(root / "cache", args.model, command, args.timeout)
        for stage in stages:
            lockdir = root / "locks" / stage
            lockdir.mkdir(parents=True, exist_ok=True)
            with run_lock(lockdir):
                run_batch(
                    root,
                    stage=stage,
                    model=args.model,
                    command=command,
                    jobs=args.jobs,
                    timeout=args.timeout,
                    split=args.split,
                    leak_judge=judge,
                )
        result = {"completed": stages}
    elif args.action == "fit":
        name = (
            "calibration.json"
            if args.reviewer == "review"
            else "forecast-calibration.json"
        )
        write(root / name, evaluation.fit(root, args.reviewer))
        result = {"calibration": str(root / name)}
    elif args.action == "report":
        calibration = read(args.calibration or root / "calibration.json")
        metrics, rows = evaluation.evaluate(root, calibration)
        prefix = (
            "forecast-"
            if calibration["config"]["review"]["stage"] == "forecast"
            else ""
        )
        write(root / (prefix + "holdout.json"), rows)
        write(root / (prefix + "metrics.json"), metrics)
        result = metrics
    elif args.action == "export":
        result = []
        for case in read(root / "manifest.json")["cases"]:
            provenance = read(root / "inputs" / case["bundle"] / "provenance.json")
            record = stage_result(root, case, args.reviewer)
            proposal = stage_result(root, case, "compress")["output"]
            result.append(
                with_calibration(
                    {
                        "id": provenance.get("candidate_id") or provenance["forum"],
                        "proposal": proposal,
                        "review": scored(record),
                    },
                    record,
                    args.calibration,
                )
            )
    else:
        if args.proposal.suffix.lower() == ".json":
            doc = read(args.proposal)
            check(isinstance(doc, dict), "expected a JSON proposal object")
            sections = doc.get("proposal", doc)
            check(
                isinstance(sections, dict) and set(sections) == set(SECTIONS),
                "expected five proposal sections",
            )
            packet = sanitize_proposal(
                {
                    k: v.get("text") if isinstance(v, dict) and set(v) <= {"text"} else v
                    for k, v in sections.items()
                }
            )
        else:
            packet = sanitize_proposal(args.proposal.read_text(encoding="utf-8").strip())
        check(any(packet.values()), "empty proposal")
        check(
            not any(reported_result(v) for v in packet.values() if v),
            "remove reported outcomes before scoring the proposal",
        )
        command = command_argv(args.command) if args.command else claude_argv()
        apply_leak_audit(
            packet,
            model_leak_judge(root / "cache", args.model, command, args.timeout),
        )
        path = invoke(
            args.reviewer,
            seal(packet),
            root / "cache",
            args.model,
            command,
            args.timeout,
        )
        record = read(path)
        result = with_calibration({"review": scored(record)}, record, args.calibration)
    if getattr(args, "output", None):
        write(assert_local_only(args.output), result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
