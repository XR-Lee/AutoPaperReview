"""Source-checked distributions of possible initial idea-stage reviewer judgments."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from ..command_transport import recover_json, reply_text, run_command
from ..pipeline import run_lock
from ..public_calibration import assert_local_only
from .clarification_protocol import outside_scope
from .data import digest, read, reported_result, require_distinct_output, write
from .file_io import local_output, protect_cli_output
from .leak_audit import apply_leak_audit, model_leak_judge, payload_sha256, require_leak_judge
from .material import material
from .panel_prompts import FORECAST, SOURCE
from .sanitized import require_model_input, seal
from .stages import claude_argv

AXES = ("importance", "novelty", "value", "mechanism", "testability", "reach")


def validate_source(output, body):
    if not isinstance(output, dict) or output.keys() != body["statements"].keys():
        raise ValueError("expected a source judgment for every proposed statement")
    for entry in output.values():
        if not isinstance(entry, dict) or set(entry) != {"label", "evidence"}:
            raise ValueError("invalid source judgment fields")
        label, spans = entry["label"], entry["evidence"]
        if label not in ("full", "partial", "absent", "outcome") or not isinstance(spans, list) or len(spans) > 2:
            raise ValueError("invalid source judgment or number of supporting spans")
        if label in ("full", "partial") and not spans:
            raise ValueError("support requires a cited span")
        for item in spans:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("expected a source ID and literal quotation")
            unit, quote = item
            if not isinstance(unit, str) or unit not in body["sources"] or not isinstance(quote, str):
                raise ValueError("invalid source ID or quote")
            if len(quote.strip()) < 5 or quote not in body["sources"][unit]:
                raise ValueError("quote an exact source span")


def validate_forecast(output, statements):
    if not isinstance(output, list) or not 1 <= len(output) <= 6:
        raise ValueError("expected one to six reviewer profiles")
    for row in output:
        if not isinstance(row, dict) or set(row) != {"n", "grade", "pro", "con"}:
            raise ValueError("invalid reviewer profile fields")
        if type(row["n"]) != int or not 1 <= row["n"] <= 20:
            raise ValueError("invalid reviewer profile mass")
        if type(row["grade"]) not in (int, float) or row["grade"] not in [i/2 for i in range(2, 11)]:
            raise ValueError("expected a grade from 1 to 5 in half points")
        for reason in (row["pro"], row["con"]):
            if reason is None:
                continue
            if not isinstance(reason, list) or len(reason) != 3:
                raise ValueError("invalid reviewer reason")
            axis, impact, tag = reason
            if axis not in AXES or type(impact) != int or not 1 <= impact <= 3 or not isinstance(tag, str) or tag not in statements:
                raise ValueError("invalid dimension, impact or statement ID")
    if sum(row["n"] for row in output) != 20:
        raise ValueError("reviewer masses must sum to twenty")


def request(stage, body, cache, offline, *, leak_judge=None):
    require_leak_judge(leak_judge)
    body = require_model_input(seal(body))
    message = apply_leak_audit(body, leak_judge)
    source_stage = stage == "source-review"
    prompt, validate = (SOURCE, validate_source) if source_stage else (FORECAST, validate_forecast)
    model, command = "claude-sonnet-5-5", claude_argv()
    descriptor = dict(model=model, command=digest(command), input=payload_sha256(message), prompt=digest(prompt))
    if not source_stage:
        descriptor["version"] = 1
    key = digest(descriptor)
    path = cache / stage / (key + ".json")
    empty = {} if source_stage else None
    missing = None
    if source_stage:
        if not body["statements"]:
            missing = "no_proposal"
        elif not body["sources"]:
            missing = "no_source"
    if path.is_file():
        r = read(path)
        states = {missing} if missing else {"response", "refusal", "invalid"}
        if not isinstance(r, dict) or r.get("request") != descriptor or r.get("state") not in states or "output" not in r:
            raise ValueError("invalid reviewer panel receipt")
        if r["state"] == "response":
            validate(r["output"], body)
        elif r["output"] != empty:
            raise ValueError("incomplete receipt contains unexpected judgments")
        return key, r
    if offline and not missing:
        raise ValueError(f"missing cached {stage} receipt {key}")
    r = dict(request=descriptor, state=missing or "invalid", output=empty)
    if missing:
        if not offline:
            write(path, r)
        return key, r
    for attempt in range(3):
        try:
            raw, _ = run_command(command, model=model, system=prompt, prompt=message, timeout=240)
        except RuntimeError as exc:
            if re.search(r'"stop_reason"\s*:\s*"refusal"', str(exc)):
                r["state"] = "refusal"
                break
            raise
        text = reply_text(raw)[0]
        if re.match(r"^\s*(?:sorry|I (?:cannot|can't|won't|am unable to)|I'm unable to)", text, re.I):
            r["state"] = "refusal"
            break
        try:
            answer = json.loads(recover_json(text))
            validate(answer, body)
        except (ValueError, KeyError, TypeError):
            if attempt < 2:
                feedback = "\nReturn the entire corrected JSON with valid references."
                message = apply_leak_audit(body, leak_judge, feedback=feedback)
            continue
        r.update(state="response", output=answer)
        break
    write(path, r)
    return key, r


def evaluate(row, cache, offline, *, leak_judge=None):
    require_leak_judge(leak_judge)
    texts = material(row)
    src = (row.get("source_audit") or {}).get("source_units", {})
    if not isinstance(src, dict) or any(not isinstance(v, str) for v in src.values()):
        raise ValueError("expected source units containing text")
    source = {t:v for t,v in src.items() if not outside_scope(v)}
    if any(outside_scope(t) or reported_result(t) for t in texts):
        texts = []
    original = {f"s{i}":v for i,v in enumerate(texts, 1)}
    req, checked = request("source-review", dict(sources=source, statements=original), cache, offline, leak_judge=leak_judge)
    retained, decisions = [], {}
    for tag, text in original.items():
        item = checked["output"].get(tag)
        decisions[tag] = item["label"] if item else checked["state"]
        if decisions[tag] != "full":
            continue
        if any(outside_scope(v) or reported_result(v) for v in [text, *[source[u] for u,q in item["evidence"]]]):
            decisions[tag] = "full_but_outcome_or_scope_marker"
            continue
        evidence = []
        for u, q in item["evidence"]:
            start = source[u].index(q)
            evidence.append(dict(unit=u, quote=q, start=start, end=start+len(q)))
        retained.append(dict(text=text, original_id=tag, evidence=evidence))
    out = dict(id=row["id"], state="insufficient_verified_material", source_request=req, source_state=checked["state"],
               statement_decisions=decisions, retained_statements=retained, forecast=None, incumbent_score=row.get("overall"),
               profiles=[], reason_distribution={}, simulated_grade=None)
    if not retained:
        return out
    body = {f"s{i}":v["text"] for i,v in enumerate(retained, 1)}
    out["forecast"], pred = request("forecast", body, cache, offline, leak_judge=leak_judge)
    out["state"] = pred["state"]
    if pred["state"] != "response":
        return out
    probs = {leg:dict(probabilities=dict.fromkeys((*AXES,"none"),0.), decisive_probability=0.) for leg in ("pro","con")}
    for v in pred["output"]:
        p = v["n"] / 20
        profile = dict(probability=p, simulated_grade=v["grade"])
        for leg in ("pro", "con"):
            detail = v[leg]
            profile[leg] = None
            if detail is None:
                probs[leg]["probabilities"]["none"] += p
                continue
            axis, impact, tag = detail
            probs[leg]["probabilities"][axis] += p
            probs[leg]["decisive_probability"] += p * (impact == 3)
            profile[leg] = dict(axis=axis, impact=impact, statement=tag, **retained[int(tag[1:])-1])
        out["profiles"].append(profile)
    mean = sum(r["probability"]*r["simulated_grade"] for r in out["profiles"])
    variance = sum(r["probability"]*(r["simulated_grade"]-mean)**2 for r in out["profiles"])
    out.update(reason_distribution=probs, simulated_grade=dict(mean=mean, sd=math.sqrt(variance)))
    return out


@protect_cli_output
def dispatch(args):
    source, cache = assert_local_only(args.input), assert_local_only(args.work)
    dest = local_output(args.output) if args.output else None
    if dest is not None and dest.is_relative_to(cache):
        raise ValueError("choose an output separate from input and cache")
    if dest is not None:
        require_distinct_output(dest, source)
    rows = read(source)
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rows):
        raise ValueError("expected audited proposals with string IDs")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("expected distinct proposal IDs")
    judge = require_leak_judge(model_leak_judge(
        cache, "claude-sonnet-5-5", claude_argv(), 240, cached_only=args.cached_only,
    ))
    if args.cached_only:
        result = [evaluate(r, cache, True, leak_judge=judge) for r in rows]
    else:
        cache.mkdir(parents=True, exist_ok=True)
        with run_lock(cache):
            result = [evaluate(r, cache, False, leak_judge=judge) for r in rows]
    if dest:
        write(dest, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def register(parent):
    p = parent.add_parser("panel", help="forecast idea-stage reviewer reasons from source-checked proposal statements")
    p.add_argument("input", type=Path, help="audited candidate JSON from prioritize --include-source-audit")
    p.add_argument("--work", type=Path, required=True, help="directory for source and panel receipts")
    p.add_argument("--output", type=Path, help="write local JSON instead of stdout")
    p.add_argument("--cached-only", action="store_true")
    p.set_defaults(func=dispatch)
