"""Run a frozen aggregate scorer on an authored proposal or candidate export."""

from __future__ import annotations

from pathlib import Path
from statistics import mean

from ..public_calibration import assert_local_only
from . import contrast, merit, outcome_screen, source_evidence
from .data import apd_cases, digest, read, write
from .exemplars import packet as example_packet
from .overall_rank import expectation, features, predict
from .preference import panel, score_panel
from .prior_index import retrieve, words
from .stages import PROMPTS, SECTIONS, claude_argv, invoke, validate


def grok_command():
    return [
        "grok",
        "--reasoning-effort",
        "low",
        "-m",
        "{model}",
        "--tools",
        "",
        "--no-memory",
        "--disable-web-search",
        "--no-subagents",
        "--max-turns",
        "1",
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "plain",
        "--system-prompt-file",
        "{system_file}",
        "-p",
    ]


def route(stage):
    if stage in ("delta", "outcome_screen"):
        return "grok-4.7-build-fast", grok_command()
    cmd = claude_argv()
    if stage in ("learned", "challenge", "match", "peer", "field", "contrast"):
        cmd[cmd.index("low")] = "medium"
    return "claude-opus-5-5", cmd


def stage_for_feature(name):
    if name.startswith(("relative_", "wins_")):
        return "slate"
    if name.startswith("old_"):
        return "forecast"
    if name.startswith("pair_"):
        return "contrast"
    if name.startswith("field_"):
        return "field"
    if name.startswith("peer_"):
        return "opinion"
    if name.startswith("learned_"):
        return "learned"
    if name.startswith("delta_"):
        return "delta"
    if name.startswith("crit_") or name == "bottleneck":
        return "challenge"
    if name in ("anchored", "win_mean", "win_top"):
        return "match"
    if name in {"holistic", "specialist", "neighbor", "chair", *merit.AXES}:
        return "merit"
    raise ValueError(f"unknown scoring feature: {name}")


def stage_features(stage, verdict):
    if stage == "forecast":
        return {
            "old_" + k: mean(expectation(p) for p in r["panel"]) if r["panel"] else None
            for k, r in verdict.items()
        }
    return {
        "merit": features,
        "field": merit.field_features,
        "opinion": merit.opinion_features,
        "learned": merit.learned_features,
        "challenge": merit.challenge_features,
        "match": merit.match_features,
        "delta": merit.delta_features,
    }[stage](verdict)


def text_packet(doc):
    if isinstance(doc, str):
        return {"proposal": doc.strip()}
    if not isinstance(doc, dict):
        raise TypeError("expected proposal text or JSON object")
    fields = doc.get("proposal", doc)
    if isinstance(fields, str):
        return {"proposal": fields.strip()}
    if not isinstance(fields, dict):
        raise TypeError("invalid proposal object")
    allowed = set(merit.DESIGN_FIELDS) | set(SECTIONS) | {"proposal"}
    if not set(fields) <= allowed:
        raise ValueError("provide proposal fields only")
    result = {
        k: (v.get("text") if isinstance(v, dict) else v) for k, v in fields.items()
    }
    if not all(v is None or isinstance(v, str) for v in result.values()):
        raise ValueError("invalid proposal section")
    return result


def rank(
    packet,
    root,
    profile,
    folder,
    *,
    legacy=None,
    title="",
    abstract="",
    exclude_key="",
    timeout=240,
    call=invoke,
    screen_receipt=None,
):
    if not any(packet.values()):
        raise ValueError("empty proposal")
    from .data import reported_result

    if any(reported_result(v) for v in packet.values() if v) and (
        not screen_receipt or screen_receipt.get("cleared_packet") != digest(packet)
    ):
        raise ValueError("remove reported outcomes before ranking")
    scores = {}
    judgments = {}
    sources = []
    packet_hashes = {}
    spec = profile["ranker"]
    stages = sorted({stage_for_feature(k) for k in spec["names"]})
    for key, expected in profile.get("routes", {}).items():
        model, command = route(key)
        if expected != {"model": model, "command_sha256": digest(command)} or digest(
            PROMPTS[key]
        ) != profile["prompts"].get(key):
            raise ValueError(
                f"model route or prompt differs from frozen {key} configuration"
            )
    query_packet = packet
    uses_english_index = bool(
        set(stages) & {"challenge", "delta", "opinion", "field", "contrast"}
    )
    if uses_english_index and len(words(" ".join(v for v in packet.values() if v))) < 5:
        qm, qa = route("search_terms")
        qp = call("search_terms", packet, root / "cache", qm, qa, timeout)
        query_packet = read(qp)["output"]
    for stage in stages:
        if digest(PROMPTS[stage]) != profile["prompts"][stage]:
            raise ValueError(f"prompt differs from frozen {stage} prompt")
        model, argv = route(stage)
        if stage == "slate":
            rounds = []
            details = []
            requests = []
            bank = read(folder / "bank.json")
            for i in range(2):
                source, reference = panel(packet, bank, i, exclude_key)
                path = call(stage, source, root / "cache", model, argv, timeout)
                verdict = read(path)["output"]
                validate(stage, verdict, source)
                rounds.append(score_panel(verdict, reference))
                target = next(k for k, n in reference.items() if n is None)
                details.append(verdict[target])
                requests.append(digest(source))
            scores.update({k: mean(r[k] for r in rounds) for k in rounds[0]})
            judgments[stage] = details
            packet_hashes[stage] = requests
            continue
        if stage == "contrast":
            source, anchors = contrast.packet(
                packet, read(folder / "contrast-bank.json"), query_packet
            )
        elif stage == "forecast":
            source = legacy or packet
        elif stage == "merit":
            source = packet
        elif stage == "learned":
            source = {
                "proposal": packet,
                "calibration_questions": read(folder / "lessons.json")["criteria"],
            }
        elif stage == "match":
            source = example_packet(packet, read(folder / "bank.json"), exclude_key)
        else:
            if not sources:
                sources = retrieve(
                    folder / "prior.sqlite", query_packet, title, abstract
                )
            source = {
                "proposal": packet,
                "prior_work": [
                    {k: h[k] for k in ("title", "abstract", "year")} for h in sources
                ],
            }
        if stage == "field":
            source = merit.field_packet(
                packet, sources, read(folder / "prior-opinions.json")
            )
        if stage == "opinion":
            pm, pa = route("peer")
            peer_path = call("peer", source, root / "cache", pm, pa, timeout)
            judgments["peer"] = read(peer_path)["output"]
            source = merit.opinion_packet(judgments["peer"])
        path = call(stage, source, root / "cache", model, argv, timeout)
        verdict = read(path)["output"]
        validate(stage, verdict, source)
        if stage == "contrast":
            scores.update(contrast.features(verdict, anchors))
        else:
            scores.update(stage_features(stage, verdict))
        judgments[stage] = verdict
        packet_hashes[stage] = digest(source)
    return {
        "overall": predict(scores, spec),
        "features": scores,
        "judgments": judgments,
        "input_hashes": packet_hashes,
        "profile_sha256": digest(profile),
        "prior_work": sources,
    }


def add_screen_provider(parser):
    parser.add_argument(
        "--outcome-screen-provider",
        choices=("grok", "claude"),
        help="enable outcome screening with the selected first-pass provider (default when screening: grok)",
    )


def register(sub):
    p = sub.add_parser(
        "prioritize",
        help="apply a frozen idea-stage overall ranker to a proposal or APD proposal export",
    )
    p.add_argument("proposal", type=Path)
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--screen-outcomes",
        action="store_true",
        help="screen each proposal for already achieved results before ranking",
    )
    p.add_argument(
        "--include-source-audit",
        action="store_true",
        help="screen outcomes and attach per-sentence source support for APD or paired source/proposal JSON",
    )
    add_screen_provider(p)
    p.set_defaults(func=dispatch)


def load_profile(path):
    from ..hashing import sha256_file

    folder = path.resolve().parent
    profile = read(path)
    for name, expected in profile.get("assets", {}).items():
        if Path(name).name != name or sha256_file(folder / name) != expected:
            raise ValueError("profile asset mismatch")
    return profile, folder


def screen_packet(packet, root, *, cached_only=False, provider=None):
    source = outcome_screen.units(packet)
    if not source:
        return outcome_screen.filter_units(packet, source, {"exclude": []})
    if provider in (None, "grok"):
        first_model, argv = route("outcome_screen")
    elif provider == "claude":
        first_model, argv = "claude-sonnet-5-5", claude_argv()
    else:
        raise ValueError(f"unknown outcome screen provider: {provider}")
    options = {"cached_only": True} if cached_only else {}
    receipt = invoke(
        "outcome_screen", source, root / "cache", first_model, argv, 240, **options
    )
    first = read(receipt)["output"]
    clean, audit = outcome_screen.filter_units(packet, source, first)
    matches = [
        item["unit"] for item in audit["removed"] if item["kind"] == "reported_result"
    ]
    check = outcome_screen.attribution_request(
        source, {item["unit"] for item in audit["removed"]}
    )
    checks = []
    if check:
        checks.append(("outcome_attribution", check, "attribution_key"))
    if matches:
        checks.append(
            (
                "outcome_context",
                {"context": source, "candidates": matches},
                "context_key",
            )
        )
    findings = list(first["exclude"])
    receipts = {}
    for stage, check, key in checks:
        model, argv = route(stage)
        second = invoke(stage, check, root / "cache", model, argv, 240, **options)
        confirmed = read(second)["output"]
        outcome_screen.validate_attribution(confirmed, check)
        findings.extend(confirmed["exclude"])
        receipts[key] = second.stem
    if checks:
        clean, audit = outcome_screen.filter_units(
            packet, source, {"exclude": findings}, cleared=matches
        )
        audit.update(receipts)
        if matches and any(
            outcome_screen.reported_result(v) for v in clean.values() if v
        ):
            audit["cleared_packet"] = digest(clean)
    audit["cache_key"] = receipt.stem
    if provider is not None:
        audit["screen_model"] = first_model
    return clean, audit


def screen_rank_inputs(packet, legacy, root, profile, *, cached_only=False, provider=None):
    """Screen exactly the proposals that can enter this profile's scoring stages."""
    options = {"cached_only": True} if cached_only else {}
    if provider is not None:
        options["provider"] = provider
    packet, audit = screen_packet(packet, root, **options)
    evidence = {"proposal": audit}
    assessable = any(packet.values())
    if legacy is not None and any(
        stage_for_feature(name) == "forecast" for name in profile["ranker"]["names"]
    ):
        legacy, evidence["short_proposal"] = screen_packet(legacy, root, **options)
        assessable = assessable and any(legacy.values())
    return packet, legacy, evidence, assessable


def dispatch(args):
    profile, folder = load_profile(args.profile)
    root = assert_local_only(args.work)
    suffix = args.proposal.suffix.lower()
    doc = read(args.proposal) if suffix == ".json" else None
    batch = suffix in (".yaml", ".yml") or isinstance(doc, dict) and "candidates" in doc
    is_apd = batch
    include_audit = getattr(args, "include_source_audit", False)
    provider = getattr(args, "outcome_screen_provider", None)
    screen_options = {"provider": provider} if provider else {}
    screen = bool(provider or include_audit or getattr(args, "screen_outcomes", False))
    if is_apd:
        items = []
        for c in apd_cases(
            args.proposal,
            review_filtered_fields=screen,
        ):
            item = {"id": c["id"]}
            if "fields_to_screen" in c:
                kept, item["source_screen"] = screen_packet(
                    c["fields_to_screen"], root, **screen_options
                )
                if not kept.get("contribution"):
                    items.append({**item, "proposal": {}, "source": {}})
                    continue
                c["candidate"] = {
                    f"s{i}": text
                    for i, text in enumerate(
                        dict.fromkeys(
                            v["text"] for v in outcome_screen.units(kept).values()
                        ),
                        1,
                    )
                }
            if include_audit:
                item["source"] = c["candidate"]
            m, argv = route("design")
            design = read(
                invoke("design", c["candidate"], root / "cache", m, argv, 240)
            )["output"]
            item["proposal"] = merit.compact(design)
            if any(
                stage_for_feature(n) == "forecast" for n in profile["ranker"]["names"]
            ):
                legacy = read(
                    invoke("compress", c["candidate"], root / "cache", m, argv, 240)
                )["output"]
                item["short_proposal"] = {k: r["text"] for k, r in legacy.items()}
            items.append(item)
    else:
        if doc is None:
            doc = args.proposal.read_text(encoding="utf-8")
        batch = isinstance(doc, list)
        items = doc if batch else [doc]
    if include_audit and not is_apd:
        for item in items:
            if (
                not isinstance(item, dict)
                or "source" not in item
                or "proposal" not in item
            ):
                raise ValueError(
                    "source audit needs an APD export or explicit source and proposal fields"
                )
            source_evidence.packet(item["source"], text_packet(item))
    results = []
    for item in items:
        packet = text_packet(item)
        legacy = (
            text_packet(item["short_proposal"])
            if isinstance(item, dict) and item.get("short_proposal")
            else None
        )
        evidence = (
            {"candidate": item["source_screen"]}
            if is_apd and "source_screen" in item
            else {}
        )
        if screen:
            packet, legacy, screened, assessable = screen_rank_inputs(
                packet, legacy, root, profile, **screen_options
            )
            evidence.update(screened)
            if include_audit:
                source = item["source"]
                if not is_apd:
                    source, evidence["authored_source"] = screen_packet(
                        source, root, **screen_options
                    )
                    source = {k: v for k, v in source.items() if v}
                citation_audit = source_evidence.audit(source, packet, root / "cache")
                citation_audit["source_units"] = source
            if not assessable:
                results.append(
                    {
                        "id": item.get("id") if isinstance(item, dict) else None,
                        "proposal": packet,
                        "overall": None,
                        "status": "insufficient_prospective_text",
                        **({"source_audit": citation_audit} if include_audit else {}),
                        "outcome_screen": evidence,
                    }
                )
                continue
        result = rank(
            packet,
            root,
            profile,
            folder,
            legacy=legacy,
            screen_receipt=evidence.get("proposal"),
        )
        if include_audit:
            result["source_audit"] = citation_audit
        if evidence:
            result["outcome_screen"] = evidence
        results.append(
            {
                "id": item.get("id") if isinstance(item, dict) else None,
                "proposal": packet,
                **result,
            }
        )
    if batch:
        results.sort(
            key=lambda row: (row["overall"] is not None, row["overall"]), reverse=True
        )
        positions = {}
        for position, row in enumerate(results, 1):
            row["rank"] = (
                positions.setdefault(row["overall"], position)
                if row["overall"] is not None
                else None
            )
    write(assert_local_only(args.output), results if batch else results[0])
    return 0
