"""Code the prospective opinions in initial review notes using the fixed benchmark's reference route."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from statistics import mean

from ..pipeline import run_lock
from ..public_calibration import assert_local_only
from .data import (
    cleaning_rules,
    digest,
    neutralize_label_text,
    read,
    require_official_mean,
    scale_for_reference,
    write,
)
from .merit import reference_packet
from .stages import PROMPTS, claude_argv, invoke

MODELS = {"ref_extract": "claude-sonnet-5-5", "ref_grade": "claude-opus-5-5"}


def protocol(command):
    return {
        "version": 1,
        "models": dict(MODELS),
        "prompt_hashes": {stage: digest(PROMPTS[stage]) for stage in MODELS},
        "command_sha256": digest(command),
        "aggregation": "mean_available_reviewers",
        "positive_threshold": 3.5,
        "text_cleaning_sha256": digest(cleaning_rules()),
    }


def validate_cases(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected a nonempty array of paper records")
    forums = set()
    notes = set()
    for row in rows:
        forum = row.get("forum") if isinstance(row, dict) else None
        if not isinstance(forum, str) or not forum or forum in forums:
            raise ValueError("each paper needs a unique forum ID")
        forums.add(forum)
        reviews = row.get("reviews")
        if not isinstance(reviews, list):
            raise TypeError("expected reviews in their original order")
        for review in reviews:
            note = review.get("id") if isinstance(review, dict) else None
            if not isinstance(note, str) or not note or note in notes:
                raise ValueError("each review needs a unique note ID")
            notes.add(note)
            units = review.get("units")
            if not isinstance(units, dict) or not all(
                isinstance(k, str) and k and isinstance(s, str) and s.strip()
                for k, s in units.items()
            ):
                raise ValueError("expected numbered review units containing text")
        rating = row.get("final_rating")
        scale = scale_for_reference(row)
        if rating is not None:
            require_official_mean(rating, scale)
        if "accept" in row and (
            type(row["accept"]) not in (int, bool) or row["accept"] not in (0, 1)
        ):
            raise ValueError("accept must be 0 or 1")


def independent_protocol(command):
    return {**protocol(command), "grade_context": "one_reviewer_per_call"}


def reviewer_result(note, units, extracted, grade):
    """Offsets refer to the original unit, not the cleaned text sent to the model."""
    original = note.get("units") if isinstance(note.get("units"), dict) else units
    spans = []
    for index in grade["used"]:
        evidence = extracted[index]
        unit, quote = evidence["id"], evidence["quote"]
        source = original.get(unit, "")
        if not isinstance(source, str) or not isinstance(quote, str) or not quote or quote not in source:
            raise ValueError("evidence quote is not in the original review unit")
        start = source.index(quote)
        spans.append(
            {"unit": unit, "start": start, "end": start + len(quote), "quote": quote}
        )
    return {"id": note["id"], "score": grade["score"], "evidence": spans}


def coding_units(units):
    """Remove acceptance and overall-rating sentences before a coder sees them."""
    cleaned = {}
    for key, text in units.items():
        kept = neutralize_label_text(text)
        if kept:
            cleaned[key] = kept
    return cleaned


def code_paper(
    row, cache, command, *, timeout=240, cached_only=False,
    include_independent_grades=False,
):
    comments = {
        f"r{i}": coding_units(note["units"])
        for i, note in enumerate(row["reviews"], 1)
    }
    requests = {}
    extracted = {}
    grades = {}

    def stage(name, data, receipts, key):
        p = invoke(
            name, data, cache, MODELS[name], command, timeout, cached_only=cached_only
        )
        receipts[key] = p.stem
        return read(p)["output"]

    source = {}
    payload = {key: units for key, units in comments.items() if units}
    if payload:
        extracted = stage("ref_extract", payload, requests, "ref_extract")
        source = reference_packet(comments, extracted)
        if any(source.values()):
            grades = stage("ref_grade", source, requests, "ref_grade")
    default_grade = {"score": None, "used": []}
    reviewers = []
    independent = []
    independent_requests = {}
    for i, review in enumerate(row["reviews"], 1):
        alias = f"r{i}"
        units = comments[alias]
        evidence = extracted.get(alias, [])
        reviewers.append(
            reviewer_result(review, units, evidence, grades.get(alias, default_grade))
        )
        if include_independent_grades:
            grade = default_grade
            if source.get(alias):
                response = stage(
                    "ref_grade", {"r1": source[alias]}, independent_requests, review["id"]
                )
                grade = response.get("r1", default_grade)
            independent.append(reviewer_result(review, units, evidence, grade))
    valid = [r["score"] for r in reviewers if r["score"] is not None]
    result = {
        "forum": row["forum"],
        "prospective": mean(valid) if valid else None,
        "reviewers": reviewers,
        "input_sha256": digest(comments),
        "reference_protocol": digest(protocol(command)),
        "requests": requests,
        **{
            k: row[k]
            for k in ("final_rating", "accept", "year", "rating_scale")
            if k in row
        },
    }
    if include_independent_grades:
        scores = [r["score"] for r in independent if r["score"] is not None]
        result["independent_reference"] = {
            "prospective": mean(scores) if scores else None,
            "reviewers": independent,
            "grade_requests_by_note": independent_requests,
            "reference_protocol": digest(independent_protocol(command)),
        }
    return result


def register(sub):
    p = sub.add_parser(
        "code-idea-references",
        help="code initial reviewer opinions using the fixed benchmark route",
    )
    for name in ("inputs", "work", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--timeout", type=float, default=240)
    p.add_argument("--cached-only", action="store_true")
    p.add_argument(
        "--include-independent-grades",
        action="store_true",
        help="also grade each review separately using the same extracted evidence",
    )
    p.set_defaults(func=dispatch)


def dispatch(args):
    if args.jobs < 1 or args.timeout <= 0:
        raise ValueError("jobs and timeout must be positive")
    root = assert_local_only(args.work)
    output = assert_local_only(args.output)
    if args.inputs.resolve() == output:
        raise ValueError("use a separate output file for coded references")
    rows = read(args.inputs)
    validate_cases(rows)
    command = claude_argv()
    config = protocol(command)
    code = partial(
        code_paper,
        cache=root / "cache",
        command=command,
        timeout=args.timeout,
        cached_only=args.cached_only,
        include_independent_grades=args.include_independent_grades,
    )
    lockdir = root / "locks" / "idea-references"
    lockdir.mkdir(parents=True, exist_ok=True)
    with run_lock(lockdir):
        write(root / "reference-protocols" / (digest(config) + ".json"), config)
        if args.include_independent_grades:
            independent_config = independent_protocol(command)
            config_path = root / "reference-protocols" / (digest(independent_config) + ".json")
            write(config_path, independent_config)
        with ThreadPoolExecutor(max_workers=args.jobs) as workers:
            references = list(workers.map(code, rows))
        write(output, references)
    return 0
