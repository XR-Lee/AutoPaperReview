"""Verify a sealed 100-paper test and evaluate exactly one prediction per forum."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from ..public_calibration import assert_local_only
from .data import digest, read, require_official_mean, scale_for_reference, write
from .overall_rank import evaluate
from .priority import add_screen_provider, load_profile, rank, screen_rank_inputs, text_packet
from .stages import invoke


def index(rows, field):
    if not isinstance(rows, list):
        raise TypeError("expected a list of records")
    lookup = {r[field]: r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError("duplicate forum ID")
    return lookup


def sealed_cases(manifest):
    all_ids = index(manifest["cases"], "forum")
    test = {fid: c for fid, c in all_ids.items() if c["split"] == "test"}
    if len(test) != 100 or digest(sorted(test)) != manifest["ids_sha256"]:
        raise ValueError("fixed test must match the sealed 100 forum IDs")
    return test


def evaluate_fixed(manifest, predictions, references, bootstrap=2000):
    test = set(sealed_cases(manifest))
    pred = index(predictions, "forum")
    gold = index(references, "forum")
    if set(pred) != test or set(gold) != test:
        raise ValueError(
            "predictions and references must cover exactly the complete fixed test"
        )
    rows = []
    for fid in sorted(test):
        g = gold[fid]
        score = pred[fid]["overall"]
        target = g["prospective"]
        rating = g["final_rating"]
        accept = g["accept"]
        if type(score) not in (float, int) or not math.isfinite(score):
            raise ValueError("missing or nonfinite prediction")
        if (
            type(target) not in (float, int)
            or not 1 <= target <= 5
            or accept not in (0, 1)
        ):
            raise ValueError("incomplete reference")
        require_official_mean(rating, scale_for_reference(g))
        rows.append(
            {
                "key": fid,
                "target": target,
                "accept": int(accept),
                "rating": rating,
                "features": {"score": score},
            }
        )
    result = evaluate(rows, {"method": "mean", "names": ["score"]}, bootstrap)
    if any("reference_protocol" in g for g in gold.values()):
        recorded = [g.get("reference_protocol") for g in gold.values()]
        known = {p for p in recorded if isinstance(p, str) and p}
        result["reference_protocols"] = sorted(known)
        result["reference_protocol_consistent"] = len(known) == 1 and all(
            isinstance(p, str) and p in known for p in recorded
        )
    result["threshold"] = 3.5
    result["ids_sha256"] = manifest["ids_sha256"]
    result["passed"] = all(
        result[k] is not None and result[k] >= 0.70 for k in ("rho", "auc")
    )
    return result


def register(sub):
    from . import reference_coding

    reference_coding.register(sub)
    p = sub.add_parser(
        "evaluate-rank", help="verify and score a locked 100-forum proposal benchmark"
    )
    for key in ("manifest", "predictions", "references", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.set_defaults(func=dispatch)
    p = sub.add_parser(
        "predict-rank", help="score all sealed blind proposals with a frozen profile"
    )
    for key in ("manifest", "proposals", "profile", "work", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--cached-only", action="store_true")
    p.add_argument(
        "--screen-outcomes",
        action="store_true",
        help="apply the same outcome screen as prioritize before scoring each proposal",
    )
    add_screen_provider(p)
    p.set_defaults(func=predict_dispatch)


def blind_inputs(manifest, proposals):
    cases = sealed_cases(manifest)
    records = index(proposals, "forum")
    if records.keys() != cases.keys():
        raise ValueError("blind proposals must cover exactly the complete fixed test")
    inputs = []
    for fid, row in sorted(records.items()):
        if "proposal" not in row or set(row) - {
            "forum",
            "proposal",
            "title",
            "abstract",
            "short_proposal",
        }:
            raise ValueError("expected blind proposal fields only")
        title, abstract = row.get("title", ""), row.get("abstract", "")
        if not isinstance(title, str) or not isinstance(abstract, str):
            raise TypeError("invalid retrieval metadata")
        inputs.append(
            (
                fid,
                text_packet(row["proposal"]),
                {
                    "title": title,
                    "abstract": abstract,
                    "exclude_key": cases[fid].get("key", fid),
                    "legacy": text_packet(row["short_proposal"])
                    if "short_proposal" in row
                    else None,
                },
            )
        )
    return inputs


def predict_dispatch(args):
    inputs = blind_inputs(read(args.manifest), read(args.proposals))
    profile, folder = load_profile(args.profile)
    root = assert_local_only(args.work)
    output = assert_local_only(args.output)
    call = partial(invoke, cached_only=args.cached_only)

    provider = getattr(args, "outcome_screen_provider", None)

    def one(item):
        fid, proposal, metadata = item
        audit = {}
        if provider or getattr(args, "screen_outcomes", False):
            proposal, legacy, evidence, assessable = screen_rank_inputs(
                proposal,
                metadata["legacy"],
                root,
                profile,
                cached_only=args.cached_only,
                provider=provider,
            )
            audit["outcome_screen"] = evidence
            if not assessable:
                return {
                    "forum": fid,
                    "overall": None,
                    "status": "insufficient_prospective_text",
                    **audit,
                }
            metadata = {
                **metadata,
                "legacy": legacy,
                "screen_receipt": evidence["proposal"],
            }
        judged = rank(proposal, root, profile, folder, call=call, **metadata)
        return {"forum": fid, "overall": judged["overall"], **audit}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        predictions = list(pool.map(one, inputs))
    write(output, predictions)
    return 0


def dispatch(args):
    result = evaluate_fixed(
        read(args.manifest), read(args.predictions), read(args.references)
    )
    write(assert_local_only(args.output), result)
    return 0
