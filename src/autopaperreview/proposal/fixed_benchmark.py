"""Score a locked proposal cohort and, for prediction, the sealed 100-paper test."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from ..public_calibration import assert_local_only
from .data import digest, read, require_official_mean, scale_for_reference, write
from .overall_rank import STRATIFIED_DRAWS, STRATIFIED_SEED, evaluate, stratified_ci, top_fraction
from .priority import add_screen_provider, load_profile, rank, screen_rank_inputs, text_packet
from .stages import invoke


def index(rows, field):
    if not isinstance(rows, list):
        raise TypeError("expected a list of records")
    lookup = {r[field]: r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError("duplicate forum ID")
    return lookup


def cohort_cases(manifest):
    """Any locked test list. The digest is the sorted forum ids, not a fixed n."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("manifest needs a cases list")
    all_ids = index(manifest["cases"], "forum")
    test = {fid: c for fid, c in all_ids.items() if c.get("split", "test") == "test"}
    if not test:
        raise ValueError("manifest has no test cases")
    expected = manifest.get("ids_sha256")
    if not isinstance(expected, str) or digest(sorted(test)) != expected:
        raise ValueError("forum IDs do not match the manifest digest")
    return test


def sealed_cases(manifest):
    test = cohort_cases(manifest)
    if len(test) != 100:
        raise ValueError("fixed test must match the sealed 100 forum IDs")
    return test


def evaluate_fixed(
    manifest,
    predictions,
    references,
    bootstrap=2000,
    stratified_draws=STRATIFIED_DRAWS,
):
    test = set(cohort_cases(manifest))
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
        accept = g.get("accept", None)
        if type(score) not in (float, int) or not math.isfinite(float(score)):
            raise ValueError("missing or nonfinite prediction")
        if type(target) not in (float, int) or not 1 <= target <= 5:
            raise ValueError("incomplete reference")
        if accept is not None and accept not in (0, 1):
            raise ValueError("incomplete reference")
        require_official_mean(rating, scale_for_reference(g))
        row = {
            "key": fid,
            "target": target,
            "accept": None if accept is None else int(accept),
            "rating": rating,
            "features": {"score": score},
        }
        if g.get("stratum") is not None:
            row["stratum"] = g["stratum"]
        rows.append(row)
    spec = {"method": "mean", "names": ["score"]}
    result = evaluate(rows, spec, bootstrap)
    if stratified_draws:
        result["stratified_ci95"] = stratified_ci(
            rows, spec, draws=stratified_draws, seed=STRATIFIED_SEED
        )
        result["stratified_draws"] = stratified_draws
        result["stratified_seed"] = STRATIFIED_SEED
    decided = [row for row in rows if row["accept"] in (0, 1)]
    result["decision_n"] = len(decided)
    result["top_fraction"] = top_fraction(
        [row["features"]["score"] for row in rows],
        [row["rating"] for row in rows],
    )
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
        "evaluate-rank",
        help="score a locked proposal cohort of any size against its manifest digest",
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
            "year",
            "arxiv",
            "arxiv_id",
            "cutoff_ms",
            "submission_ms",
        }:
            raise ValueError("expected blind proposal fields only")
        title, abstract = row.get("title", ""), row.get("abstract", "")
        if not isinstance(title, str) or not isinstance(abstract, str):
            raise TypeError("invalid retrieval metadata")
        case = cases[fid]
        metadata = {
            "title": title,
            "abstract": abstract,
            "exclude_key": case.get("key", fid),
            "forum": fid,
            "legacy": text_packet(row["short_proposal"])
            if "short_proposal" in row
            else None,
        }
        for source, field, dest in (
            (row, "year", "year"),
            (case, "year", "year"),
            (row, "arxiv", "arxiv"),
            (row, "arxiv_id", "arxiv"),
            (case, "arxiv", "arxiv"),
            (case, "arxiv_id", "arxiv"),
            (row, "cutoff_ms", "cutoff_ms"),
            (case, "cutoff_ms", "cutoff_ms"),
            (row, "submission_ms", "cutoff_ms"),
            (case, "submission_ms", "cutoff_ms"),
        ):
            if dest in metadata or field not in source or source[field] in (None, ""):
                continue
            value = source[field]
            if dest == "year" and type(value) is not int:
                raise ValueError("year must be an integer venue year")
            if dest == "cutoff_ms" and type(value) is not int:
                raise ValueError("cutoff_ms must be an integer millisecond timestamp")
            if dest == "arxiv" and not isinstance(value, str):
                raise ValueError("arxiv must be a string")
            metadata[dest] = value
        inputs.append((fid, text_packet(row["proposal"]), metadata))
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
