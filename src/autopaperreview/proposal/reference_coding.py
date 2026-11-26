"""Code the prospective opinions in initial review notes using the fixed benchmark's reference route."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from statistics import mean

from ..pipeline import run_lock
from ..public_calibration import assert_local_only
from .cohort import validate_decision
from .data import (
    cleaning_rules,
    digest,
    read,
    require_official_mean,
    retained_label_text,
    scale_for_reference,
    write,
)
from .leak_audit import PROMPT as AUDIT_PROMPT
from .leak_audit import REPLY_PROTOCOL, model_leak_judge, require_leak_judge
from .merit import reference_packet
from .sanitized import seal
from .stages import PROMPTS, claude_argv, invoke

MODELS = {"ref_extract": "claude-sonnet-5-5", "ref_grade": "claude-opus-5-5"}


def protocol(command):
    return {
        "version": 2,
        "models": dict(MODELS),
        "prompt_hashes": {stage: digest(PROMPTS[stage]) for stage in MODELS},
        "command_sha256": digest(command),
        "aggregation": "mean_available_reviewers",
        "positive_threshold": 3.5,
        "evidence_binding": "contiguous_retained_source",
        "audit_prompt_sha256": digest(AUDIT_PROMPT),
        "audit_reply_protocol": REPLY_PROTOCOL,
        "text_cleaning_sha256": digest(cleaning_rules()),
    }


def _validate_labels(row):
    rating = row.get("final_rating")
    scale = scale_for_reference(row)
    if rating is not None:
        require_official_mean(rating, scale)
    validate_decision(row.get("accept"))


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
            raise ValueError("expected reviews in their original order")
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
        _validate_labels(row)


def independent_protocol(command):
    return {**protocol(command), "grade_context": "one_reviewer_per_call"}


def bind_evidence(units, extracted):
    """Bind each extracted excerpt to a contiguous retained source span."""
    spans = []
    cursor: dict[tuple[str, str], int] = {}
    for evidence in extracted:
        unit, quote = evidence["id"], evidence["quote"]
        if unit not in units or not isinstance(quote, str) or not quote:
            raise ValueError("invalid reference evidence")
        text, offsets = units[unit]
        occurrence = (unit, quote)
        start = text.find(quote, cursor.get(occurrence, 0))
        while start >= 0:
            original = offsets[start]
            if original is not None and all(
                offset == original + i
                for i, offset in enumerate(offsets[start : start + len(quote)])
            ):
                break
            start = text.find(quote, start + 1)
        else:
            raise ValueError("evidence quote must match a contiguous retained review span")
        cursor[occurrence] = start + len(quote)
        spans.append(
            {"unit": unit, "start": original, "end": original + len(quote), "quote": quote}
        )
    return spans


def reviewer_result(note, spans, grade):
    return {
        "id": note["id"],
        "score": grade["score"],
        "evidence": [spans[index] for index in grade["used"]],
    }


def coding_units(units):
    """Remove label sentences while retaining each unit's source offsets."""
    cleaned = {}
    for key, text in units.items():
        kept, offsets = retained_label_text(text)
        if kept:
            cleaned[key] = (kept, offsets)
    return cleaned


def code_paper(
    row, cache, command, *, timeout=240, cached_only=False,
    include_independent_grades=False,
    leak_judge=None,
):
    require_leak_judge(leak_judge)
    _validate_labels(row)
    mapped_comments = {
        f"r{i}": coding_units(note["units"])
        for i, note in enumerate(row["reviews"], 1)
    }
    comments = {
        alias: {unit: text for unit, (text, _) in units.items()}
        for alias, units in mapped_comments.items()
    }
    requests = {}
    bound = {}
    grades = {}

    def stage(name, data, receipts, key):
        p = invoke(
            name, data, cache, MODELS[name], command, timeout,
            leak_judge=leak_judge, cached_only=cached_only,
        )
        receipts[key] = p.stem
        return read(p)["output"]

    source = {}
    payload = {key: units for key, units in comments.items() if units}
    if payload:
        extracted = stage("ref_extract", seal(payload), requests, "ref_extract")
        bound = {
            alias: bind_evidence(mapped_comments[alias], evidence)
            for alias, evidence in extracted.items()
        }
        source = reference_packet(comments, extracted)
        if any(source.values()):
            grades = stage("ref_grade", seal(source), requests, "ref_grade")
    default_grade = {"score": None, "used": []}
    reviewers = []
    independent = []
    independent_requests = {}
    for i, review in enumerate(row["reviews"], 1):
        alias = f"r{i}"
        evidence = bound.get(alias, [])
        reviewers.append(
            reviewer_result(review, evidence, grades.get(alias, default_grade))
        )
        if include_independent_grades:
            grade = default_grade
            if source.get(alias):
                response = stage(
                    "ref_grade",
                    seal({"r1": source[alias]}),
                    independent_requests,
                    review["id"],
                )
                grade = response.get("r1", default_grade)
            independent.append(reviewer_result(review, evidence, grade))
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
    judge = require_leak_judge(model_leak_judge(
        root / "cache", MODELS["ref_extract"], command, args.timeout, cached_only=args.cached_only,
    ))
    config = protocol(command)
    code = partial(
        code_paper,
        cache=root / "cache",
        command=command,
        timeout=args.timeout,
        cached_only=args.cached_only,
        include_independent_grades=args.include_independent_grades,
        leak_judge=judge,
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
