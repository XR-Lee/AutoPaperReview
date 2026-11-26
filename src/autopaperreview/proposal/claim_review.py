"""Review specific, source-backed proposal claims before experiments."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

from ..command_transport import CommandTransportError, reply_text, run_command
from ..pipeline import run_lock
from ..public_calibration import assert_local_only
from .claim_prompts import FORECAST, GROUND, PROSPECTIVE
from .clarification import plan_units
from .clarification_protocol import outside_scope
from .data import digest, read, require_distinct_output, write
from .file_io import local_output, protect_cli_output
from .leak_audit import (
    apply_leak_audit, hard_model_failure, model_leak_judge, payload_sha256, require_leak_judge,
    same_model_value, strict_model_value,
)
from .material import ORDER, material
from .outcome_screen import units
from .priority import route
from .sanitized import require_model_input, seal
from .stages import claude_argv

FIELDS = (*ORDER, "unclassified")
BINS = {0, 5, 15, 30, 50, 70, 90}
JUDGMENTS = {"full_prospective", "partial", "completed_work", "absent"}
PROMPTS = {"forecast": FORECAST, "ground": GROUND, "prospective": PROSPECTIVE}


def validate(stage, output, body):
    if not isinstance(output, dict):
        raise ValueError("expected a JSON object")
    if stage == "ground":
        if set(output) - body["candidates"].keys():
            raise ValueError("unknown candidate ID")
    elif output.keys() != body.keys():
        raise ValueError("return all and only the supplied IDs")
    for key, item in output.items():
        if stage == "forecast":
            if (not isinstance(item, list) or len(item) != 3
                    or any(type(v) is not int for v in item)
                    or item[0] not in BINS or item[1] not in BINS
                    or sum(item[:2]) > 100 or item[2] not in (0, 1)):
                raise ValueError("invalid probability bins or central indicator")
            continue
        if stage == "ground":
            if (not isinstance(item, dict) or set(item) != {"source", "quote"}
                    or item["source"] not in body["sources"]):
                raise ValueError("invalid source or quote")
            text = body["sources"][item["source"]]
            minimum = 12
        else:
            if (not isinstance(item, dict) or set(item) != {"judgment", "quote"}
                    or item["judgment"] not in JUDGMENTS):
                raise ValueError("invalid independent source judgment")
            text = body[key]["source"]
            minimum = 8 if item["judgment"] in {"full_prospective", "partial"} else 0
        quote = item["quote"]
        if not isinstance(quote, str) or quote not in text or len(quote.strip()) < min(minimum, len(text.strip())):
            raise ValueError("return a literal substring of the cited source")


def request(stage, body, cache, offline, *, leak_judge=None):
    require_leak_judge(leak_judge)
    body = require_model_input(seal(body))
    message = apply_leak_audit(body, leak_judge)
    model, command = route("outcome_screen") if stage == "prospective" else ("claude-sonnet-5-5", claude_argv())
    prompt = PROMPTS[stage]
    descriptor = {"model": model, "command": digest(command), "input": payload_sha256(message), "prompt": digest(prompt)}
    if stage == "forecast":
        descriptor.update(version=1, mode="forecast")
    key = digest(descriptor)
    path = cache / "claims" / stage / (key + ".json")
    if not path.exists():
        legacy = cache / stage / (key + ".json")
        if legacy.is_file() and same_model_value(read(legacy).get("request"), descriptor):
            path = legacy
    if path.exists():
        result = read(path)
        if (not same_model_value(result.get("request"), descriptor)
                or result.get("state") not in {"response", "refusal", "invalid"}
                or not isinstance(result.get("output"), dict)):
            raise ValueError("invalid model receipt")
        if result["state"] != "response":
            if result["output"]:
                raise ValueError("incomplete model receipt contains predictions")
            return key, result
        reply = result.get("reply")
        if isinstance(reply, str) and payload_sha256(reply) == result.get("reply_sha256"):
            parsed = strict_model_value(reply)
            validate(stage, parsed, body)
            if not same_model_value(result.get("output"), parsed):
                raise ValueError("cached output does not match the strict reply")
            return key, result
        if offline:
            raise ValueError(f"missing cached {stage} receipt {key}")
    if offline:
        raise ValueError(f"missing cached {stage} receipt {key}")
    result = {"request": descriptor, "state": "invalid", "output": {}}
    for attempt in range(3):
        try:
            raw, _ = run_command(command, model=model, system=prompt,
                                 prompt=message, timeout=240, strict=True)
        except RuntimeError as exc:
            if re.search(r'"stop_reason"\s*:\s*"refusal"', str(exc)):
                result["state"] = "refusal"
                break
            raise
        try:
            parsed = strict_model_value(raw)
        except CommandTransportError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            if hard_model_failure(exc):
                raise
            try:
                text = reply_text(raw)[0]
            except CommandTransportError:
                raise
            if re.match(r"^\s*(?:sorry|I (?:cannot|can't|won't|am unable to)|I'm unable to)", text, re.I):
                result["state"] = "refusal"
                break
            if attempt < 2:
                feedback = "\nReturn the complete JSON again, following the required schema."
                message = apply_leak_audit(body, leak_judge, feedback=feedback)
            continue
        try:
            validate(stage, parsed, body)
        except (ValueError, TypeError, KeyError):
            if attempt < 2:
                feedback = "\nReturn the complete JSON again, following the required schema."
                message = apply_leak_audit(body, leak_judge, feedback=feedback)
            continue
        result.update(state="response", output=parsed, reply=raw, reply_sha256=payload_sha256(raw))
        break
    write(path, result)
    return key, result


def candidates(item):
    initial = material(item)
    if not initial or any(outside_scope(t) for t in initial):
        return [], [], {}
    plan = item["proposal"]
    pool = list(units(dict(sorted(plan.items()))).values())
    names = defaultdict(set)
    for row in pool:
        names[row["text"]].add(row["field"])
    original = [
        {"text": t, "field": next((f for f in FIELDS if f in names[t]), "unclassified"), "origin": "original"}
        for t in initial
    ]
    allowed = set(plan_units(plan).values())
    seen = set(initial)
    extra = []
    for row in pool:
        t = row["text"]
        if t not in seen and t in allowed and not outside_scope(t):
            extra.append({"text": t, "field": row["field"]})
            seen.add(t)
    source = {k: t for k, t in item["source_audit"]["source_units"].items() if t.strip() and not outside_scope(t)}
    return original, extra, source


def priority_view(key, original, available, ratings):
    def priority(tag):
        support, objection, central = ratings[tag]
        p, q = support / 100, objection / 100
        return -(p + q) * (1 + central) - 2 * p * q, digest(["claim-compressor", key, available[tag]["text"]])
    ordered = sorted(available, key=priority)
    selected = []
    for field in FIELDS:
        same = [t for t in ordered if available[t]["field"] == field]
        if same and len(selected) < len(original):
            selected.append(same[0])
    selected.extend([t for t in ordered if t not in selected][:len(original) - len(selected)])
    groups = [[available[t] for t in ordered if t in selected and available[t]["field"] == field] for field in FIELDS]
    return [g[i] for i in range(max(map(len, groups), default=0)) for g in groups if i < len(g)]


def select_sources(item, cache, offline, *, leak_judge=None):
    require_leak_judge(leak_judge)
    original, extra, source = candidates(item)
    selected = original
    metadata = {"ground_request": None, "pool_request": None, "supported_additions": 0}
    if extra and source:
        body = {"candidates": {f"x{i}": v["text"] for i, v in enumerate(extra, 1)}, "sources": source}
        metadata["ground_request"], ground = request("ground", body, cache, offline, leak_judge=leak_judge)
        additions = []
        for tag, ref in sorted(ground["output"].items(), key=lambda t: int(t[0][1:])):
            text = source[ref["source"]]
            start = text.index(ref["quote"])
            additions.append(dict(extra[int(tag[1:]) - 1], origin="additional", **ref,
                                  start=start, end=start + len(ref["quote"])))
        metadata["supported_additions"] = len(additions)
        if additions:
            merged = sorted(original + additions, key=lambda v: digest(["blind-pool", item["id"], v["text"]]))
            pool = {f"s{i}": v for i, v in enumerate(merged, 1)}
            metadata["pool_request"], ratings = request(
                "forecast", {t: v["text"] for t, v in pool.items()}, cache, offline, leak_judge=leak_judge,
            )
            if ratings["state"] == "response":
                selected = priority_view(item["id"], original, pool, ratings["output"])
    extras = [v for v in selected if v["origin"] == "additional"]
    audit = {"request": None, "state": "no_additions", "decisions": []}
    if not extras:
        return original, metadata, audit
    body = {f"a{i}": {"claim": v["text"], "source": source[v["source"]]} for i, v in enumerate(extras, 1)}
    audit["request"], second = request("prospective", body, cache, offline, leak_judge=leak_judge)
    audit["state"] = second["state"]
    accepted = {}
    for tag, row in zip(body, extras, strict=True):
        judgment = second["output"].get(tag, {"judgment": "unverified", "quote": ""})
        audit["decisions"].append({"text": row["text"], "source": row["source"], **judgment})
        if judgment["judgment"] == "full_prospective":
            accepted[row["text"]] = dict(row, second_reader_quote=judgment["quote"])
    if not accepted:
        return original, metadata, audit
    retained = {v["text"] for v in selected if v["origin"] == "original"}
    unused = [v for v in original if v["text"] not in retained]
    verified = []
    for v in selected:
        if v["origin"] == "original":
            verified.append(v)
        elif v["text"] in accepted:
            verified.append(accepted[v["text"]])
        else:
            replacement = next((r for r in unused if r["field"] == v["field"]), unused[0])
            unused.remove(replacement)
            verified.append(replacement)
    return verified, metadata, audit


def coefficients(profile):
    event = profile.get("simple_event") if isinstance(profile, dict) else None
    rows = event.get("coef") if isinstance(event, dict) else None
    if (not isinstance(rows, list) or len(rows) != 1 + len(FIELDS) + 6
            or any(not isinstance(r, list) or len(r) != 2
                   or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000 for v in r)
                   for r in rows)):
        raise ValueError("invalid fitted claim-event coefficients")
    return rows


def estimate(statement, bins, coef):
    support, objection, central = bins
    p, q = support / 100 * 4, objection / 100 * 4
    features = [1, *(float(statement["field"] == f) for f in FIELDS), p, q, central, p*q, p*central, q*central]
    logits = [sum(x * r[i] for x, r in zip(features, coef, strict=True)) for i in (0, 1)] + [0.]
    shift = max(logits)
    exp = [math.exp(x - shift) for x in logits]
    s, o = (x / sum(exp) for x in exp[:2])
    return dict(statement, central=bool(central), estimated_linked_support=s,
                estimated_linked_objection=o, independent_review_opposition=2*s*o)


def evaluate(rows, profile, cache, offline, *, leak_judge=None):
    require_leak_judge(leak_judge)
    coef = coefficients(profile)
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rows)
            or len({r["id"] for r in rows}) != len(rows)):
        raise ValueError("expected audited proposals with unique string IDs")
    output = []
    for row in rows:
        statements, source, audit = select_sources(row, cache, offline, leak_judge=leak_judge)
        view = dict(forecast_request=None, state="insufficient_material", deployed_score=row.get("overall"), statements=[])
        if statements:
            body = {f"s{i}": r["text"] for i, r in enumerate(statements, 1)}
            view["forecast_request"], result = request("forecast", body, cache, offline, leak_judge=leak_judge)
            if result["state"] == "response":
                view["state"] = "available"
                view["statements"] = [estimate(r, result["output"][t], coef) for t, r in zip(body, statements, strict=True)]
            else:
                view["state"] = result["state"]
        output.append(dict(id=row["id"], source=source, second_reader=audit, views={"verified": view},
                           endpoint="research_claim_opinions"))
    return output


@protect_cli_output
def dispatch(args):
    source = assert_local_only(args.input)
    profile = assert_local_only(args.model)
    cache = assert_local_only(args.work)
    target = local_output(args.output) if args.output else None
    if target is not None and target.is_relative_to(cache):
        raise ValueError("choose an output separate from inputs, fitted parameters and model receipts")
    if target is not None:
        require_distinct_output(target, source, profile)
    rows, fitted = read(source), read(profile)
    judge = require_leak_judge(model_leak_judge(
        cache, "claude-sonnet-5-5", claude_argv(), 240, cached_only=args.cached_only,
    ))
    if args.cached_only:
        results = evaluate(rows, fitted, cache, True, leak_judge=judge)
    else:
        cache.mkdir(parents=True, exist_ok=True)
        with run_lock(cache):
            results = evaluate(rows, fitted, cache, False, leak_judge=judge)
    if target:
        write(target, results)
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def register(parent):
    parser = parent.add_parser("claims", help="estimate reviewer support and objections for prospective proposal claims")
    parser.add_argument("input", type=Path, help="audited candidate JSON from prioritize --include-source-audit")
    parser.add_argument("--model", type=Path, required=True, help="local fitted claim-model.json")
    parser.add_argument("--work", type=Path, required=True, help="directory for verifiable model receipts")
    parser.add_argument("--output", type=Path, help="local JSON output (default stdout)")
    parser.add_argument("--cached-only", action="store_true", help="use previous matching receipts offline")
    parser.set_defaults(func=dispatch)
