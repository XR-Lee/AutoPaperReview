"""Generate a proposal-stage question and verify an answer against the author's source."""

import json
import re
from pathlib import Path

from ..command_transport import recover_json, reply_text, run_command
from ..pipeline import run_lock
from ..public_calibration import assert_local_only
from . import clarification_protocol as protocol
from .data import digest, read, reported_result, write
from .outcome_screen import units
from .stages import claude_argv

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
STAGES = {
    "ordinary-plan": (SONNET, protocol.CONFIRM, {"ordinary": False}),
    "source-screen": (SONNET, protocol.SCREEN, {"keep": []}),
    "question": (OPUS, protocol.QUEST, {"abstain": True}),
    "resolve": (SONNET, protocol.READ, {"answer": "unknown", "unit": "", "quote": ""}),
    "verify": (OPUS, protocol.VERIFY, {"answer": "unknown"}),
}


def judgment(stage, data, cache, *, cached_only, timeout):
    model, system, abstention = STAGES[stage]
    command = claude_argv()
    request = dict(stage=stage, model=model, prompt=digest(system), command=digest(command), input=digest(data))
    key = digest(request)
    path = cache / stage / (key + ".json")
    if path.exists():
        entry = read(path)
        if entry["request"] != request:
            raise ValueError("clarification cache fingerprint mismatch")
        protocol.validate(stage, entry["output"], data)
        return key, entry["output"]
    if cached_only:
        raise ValueError(f"missing cached {stage} judgment for request {key}")
    feedback = ""
    disposition = "response"
    for attempt in range(3):
        try:
            raw, _ = run_command(command, system=system, model=model, prompt=json.dumps(data, ensure_ascii=False) + feedback, timeout=timeout)
        except RuntimeError as exc:
            if not re.search(r'"stop_reason"\s*:\s*"refusal"', str(exc)):
                raise
            output, disposition = abstention, "refusal"
            break
        body, _ = reply_text(raw)
        if re.match(r"^\s*(?:sorry|I (?:cannot|can't|won't|am unable to)|I'm unable to)", body, re.I):
            output, disposition = abstention, "refusal"
            break
        try:
            output = json.loads(recover_json(body))
            protocol.validate(stage, output, data)
        except (ValueError, TypeError, KeyError) as exc:
            if attempt == 2:
                output, disposition = abstention, "invalid"
                break
            feedback = "\nReturn the requested JSON and valid identifiers. " + str(exc)[:100]
            continue
        if stage == "question" and not output.get("abstain") and protocol.outside_scope(" ".join(protocol.interpretations(output).values())):
            output, disposition = abstention, "scope"
        break
    write(path, dict(request=request, output=output, disposition=disposition))
    return key, output


def plan_units(proposal):
    if any(protocol.outside_scope(t) for t in proposal.values() if t):
        return {}
    return {
        f"s{i}": entry["text"]
        for i, entry in enumerate(units(dict(sorted(proposal.items()))).values(), 1)
        if not protocol.ID.search(entry["text"])
    }


def source_units(source):
    def usable(text):
        return (
            15 <= len(text) <= 1050 and not reported_result(text) and not protocol.outside_scope(text)
            and not any(rule.search(text) for rule in (protocol.ID, protocol.EX, protocol.RESULT, protocol.RISK))
        )
    values = list(dict.fromkeys(t.strip() for t in source.values() if usable(t)))
    if len(values) > 52:
        values = [values[round(i * (len(values) - 1) / 51)] for i in range(52)]
    return {f"s{i}": t for i, t in enumerate(values, 1)}


def cited_answer(material, interpretations, label, call):
    if material:
        answer = call(label + "_reader", "resolve", dict(interpretations=interpretations, text=material))
        if answer["answer"] != "unknown":
            text, quote = material[answer["unit"]], answer["quote"]
            result = call(label + "_verification", "verify", dict(interpretations=interpretations, source_unit=text, excerpt=quote))
            if answer["answer"] == result["answer"]:
                return dict(answer=result["answer"], evidence=dict(unit=text, quote=quote))
    return dict(answer="unknown", evidence=None)


def clarify(item, cache, *, cached_only=False, timeout=240):
    proposal = item.get("proposal", {})
    audit = item.get("source_audit", {})
    screen = item.get("outcome_screen", {})
    if not isinstance(audit, dict) or not isinstance(screen, dict):
        raise ValueError("expected source and outcome audit objects")
    source = audit.get("source_units", {})
    base = item.get("overall")
    if not isinstance(proposal, dict) or any(t is not None and not isinstance(t, str) for t in proposal.values()):
        raise ValueError("proposal fields must contain text or null")
    result = dict(id=item.get("id"), deployed_overall=base, state="insufficient_prospective_text", question=None)
    if base is None and not any(proposal.values()):
        return result
    if base is not None and not (protocol.finite_number(base) and 1 <= base <= 5):
        raise ValueError("overall must be a finite grade from 1 to 5")
    if not isinstance(source, dict) or any(not isinstance(t, str) for t in source.values()):
        raise ValueError("expected numbered source passages")
    if ("proposal" not in screen or audit.get("source_sha256") != digest(source)
        or audit.get("proposal_sha256") != digest(proposal)):
        raise ValueError("expected matching proposal and source fingerprints from prioritize --include-source-audit")
    requests = {}
    def call(label, stage, data):
        key, value = judgment(stage, data, cache, cached_only=cached_only, timeout=timeout)
        requests[label] = key
        return value
    plan = plan_units(proposal)
    if plan and any(protocol.AMBIGUOUS.search(t) for t in plan.values()):
        if not call("ordinary_use", "ordinary-plan", proposal)["ordinary"]:
            plan = {}
    result.update(state="no_material_question", requests=requests)
    if not plan or base is None:
        return result
    question = call("question", "question", plan)
    if question.get("abstain"):
        return result
    result["question"] = question
    source_text = source_units(source)
    if source_text:
        kept = set(call("source_screen", "source-screen", source_text)["keep"])
        source_text = {k: t for k, t in source_text.items() if k in kept}
    meanings = protocol.interpretations(question)
    result["plan"] = cited_answer(plan, meanings, "plan", call)
    result["source"] = cited_answer(source_text, meanings, "source", call)
    condition = result["source"]["answer"]
    score = base
    if condition == "unknown":
        result["state"] = "author_clarification_needed"
    else:
        result["state"] = "resolved_from_source"
        evidence = result["source"]["evidence"]
        evidence["original_passage_ids"] = [k for k, t in source.items() if t.strip() == evidence["unit"]]
        yes, no = question["yes_grade"], question["no_grade"]
        prior = question["p_yes"] * yes + (1 - question["p_yes"]) * no
        grades = {"unknown": prior, "yes": yes, "no": no}
        delta = grades[condition] - grades[result["plan"]["answer"]]
        score = min(5, max(1, base + min(1, max(-1, delta))))
    result["experimental_source_score"] = score
    return result


def register(sub):
    parser = sub.add_parser("clarify", help="ask a proposal-stage question and verify the author's evidence")
    parser.add_argument("input", type=Path, help="results of prioritize --include-source-audit")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--cached-only", action="store_true")
    parser.set_defaults(func=dispatch)


def dispatch(args):
    root, output = assert_local_only(args.work), assert_local_only(args.output)
    if output == args.input.resolve() or not protocol.finite_number(args.timeout) or args.timeout <= 0:
        raise ValueError("use a separate output path and positive finite timeout")
    document = read(args.input)
    batch = isinstance(document, list)
    items = document if batch else [document]
    if any(not isinstance(r, dict) for r in items):
        raise ValueError("expected a source-audited APR result or array of results")
    lockdir = root / "locks" / "clarification"
    lockdir.mkdir(parents=True, exist_ok=True)
    with run_lock(lockdir):
        answers = [clarify(r, root / "cache", cached_only=args.cached_only, timeout=args.timeout) for r in items]
        write(output, answers if batch else answers[0])
    return 0
