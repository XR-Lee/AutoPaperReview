"""Score a locked proposal cohort and, for prediction, the sealed 100-paper test."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..public_calibration import assert_local_only
from .cohort import case_split, index_by_forum, validate_manifest, validate_predictions, validate_references
from .data import digest, read, require_distinct_output, require_official_mean, scale_for_reference, write
from .file_io import local_output, map_with_input_tracking, protect_cli_output
from .overall_rank import STRATIFIED_DRAWS, STRATIFIED_SEED, evaluate, stratified_ci, top_fraction
from .leak_audit import model_leak_judge, require_leak_judge
from .priority import (
    add_screen_provider, claude_argv, load_profile, needs_historical_bounds, profile_inputs, rank, screen_rank_inputs, text_packet,
)
from .prior_index import historical_bounds, norm_arxiv


def _present(source, field):
    if field not in source:
        return None
    value = source[field]
    if value in (None, ""):
        return None
    if field in {"arxiv", "arxiv_id"}:
        if not isinstance(value, str):
            raise ValueError("arxiv must be a string")
        identifier = norm_arxiv(value)
        if value.strip() and not identifier:
            raise ValueError("invalid arxiv identifier")
        return identifier or None
    if field in {"year", "cutoff_ms", "submission_ms"} and type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _locked(case, row, dest, fields):
    locked = None
    for field in fields:
        value = _present(case, field)
        if value is None:
            continue
        if locked is not None and value != locked:
            raise ValueError(f"cohort lock has conflicting {dest}")
        locked = value
    supplied = None
    for field in fields:
        value = _present(row, field)
        if value is None:
            continue
        if supplied is not None and value != supplied:
            raise ValueError(f"proposal has conflicting {dest}")
        supplied = value
    if locked is not None and supplied is not None and supplied != locked:
        raise ValueError(f"{dest} on the proposal disagrees with the cohort lock")
    if locked is None and supplied is not None:
        raise ValueError(f"{dest} must be set on the cohort lock")
    return locked


def cohort_cases(manifest):
    """Any locked test list. The digest is the sorted forum ids, not a fixed n."""
    validate_manifest(manifest, allow_legacy=True)
    all_ids = index_by_forum(manifest["cases"], "manifest cases")
    test = {fid: c for fid, c in all_ids.items() if case_split(c, legacy="version" not in manifest) == "test"}
    if not test:
        raise ValueError("manifest has no test cases")
    expected = manifest.get("ids_sha256")
    if digest(sorted(test)) != expected:
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
    cases = cohort_cases(manifest)
    test = set(cases)
    pred = index_by_forum(validate_predictions(predictions), "predictions")
    gold = index_by_forum(
        validate_references(references, allow_legacy="version" not in manifest), "references"
    )
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
        if not 1 <= target <= 5:
            raise ValueError("prospective must be from 1 to 5")
        case = cases[fid]
        require_official_mean(rating, scale_for_reference(g, lock=case))
        row = {
            "key": fid,
            "target": target,
            "accept": g.get("accept"),
            "rating": rating,
            "features": {"score": score},
        }
        stratum = case.get("stratum", case.get("tier", g.get("stratum")))
        if stratum is not None:
            row["stratum"] = stratum
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


def _content_block(exc: BaseException) -> bool:
    """A per-forum leak or reported-result block, not an I/O or configuration error."""
    text = str(exc)
    return (
        "leak audit blocked" in text
        or "identity leak" in text
        or "remove reported outcomes before ranking" in text
    )


def blind_inputs(manifest, proposals):
    cases = sealed_cases(manifest)
    records = index_by_forum(proposals, "blind proposals")
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
            "legacy": None,
        }
        if "short_proposal" in row:
            try:
                metadata["legacy"] = text_packet(row["short_proposal"])
            except ValueError as exc:
                if not _content_block(exc):
                    raise
                metadata["legacy"] = row["short_proposal"]
        # The cohort lock wins. A row may repeat the locked year, cutoff, or
        # arXiv id, but it cannot replace them or introduce one the lock omitted.
        for dest, fields in (
            ("year", ("year",)),
            ("cutoff_ms", ("cutoff_ms", "submission_ms")),
            ("arxiv", ("arxiv", "arxiv_id")),
        ):
            metadata[dest] = _locked(case, row, dest, fields)
            if metadata[dest] is None:
                del metadata[dest]
        # A label leak is decided per forum. Sanitizing here used to abort the batch.
        try:
            packet = text_packet(row["proposal"])
        except ValueError as exc:
            if not _content_block(exc):
                raise
            packet = row["proposal"]
        inputs.append((fid, packet, metadata))
    return inputs


@protect_cli_output
def predict_dispatch(args):
    output = local_output(args.output)
    require_distinct_output(output, args.manifest, args.proposals, args.profile)
    inputs = blind_inputs(read(args.manifest), read(args.proposals))
    profile, folder = load_profile(args.profile)
    require_distinct_output(output, *(folder / name for name in profile.get("assets", {})))
    require_distinct_output(output, *profile_inputs(profile, folder))
    if needs_historical_bounds(profile):
        for _, _, metadata in inputs:
            historical_bounds(metadata.get("year"), metadata.get("cutoff_ms"))
    root = assert_local_only(args.work)
    judge = require_leak_judge(model_leak_judge(
        root / "cache", "claude-opus-5-5", claude_argv(), 240, cached_only=args.cached_only,
    ))

    provider = getattr(args, "outcome_screen_provider", None)

    def one(item):
        fid, proposal, metadata = item
        try:
            audit = {}
            if provider or getattr(args, "screen_outcomes", False):
                proposal, legacy, evidence, assessable = screen_rank_inputs(
                    proposal,
                    metadata["legacy"],
                    root,
                    profile,
                    cached_only=args.cached_only,
                    provider=provider,
                    leak_judge=judge,
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
            judged = rank(
                proposal,
                root,
                profile,
                folder,
                cached_only=args.cached_only,
                leak_judge=judge,
                **metadata,
            )
            return {"forum": fid, "overall": judged["overall"], **audit}
        except ValueError as exc:
            # One frozen leak rule must not discard the rest of the batch.
            if not _content_block(exc):
                raise
            return {"forum": fid, "overall": None, "status": "leak_blocked"}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        predictions = map_with_input_tracking(pool, one, inputs)
    write(output, predictions)
    return 0


@protect_cli_output
def dispatch(args):
    output = local_output(args.output)
    require_distinct_output(output, args.manifest, args.predictions, args.references)
    result = evaluate_fixed(
        read(args.manifest), read(args.predictions), read(args.references)
    )
    write(output, result)
    return 0
