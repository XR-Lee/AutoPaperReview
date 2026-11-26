"""Run a frozen aggregate scorer on an authored proposal or candidate export."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from statistics import mean

from ..public_calibration import assert_local_only
from . import contrast, merit, outcome_screen, source_evidence
from .data import apd_cases, assert_no_label_leak, digest, read, write
from .leak_audit import apply_leak_audit, model_leak_judge, require_leak_judge
from .sanitized import sanitize_proposal, seal
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
    """Allowlisted flat proposal. Nested objects and score fields do not pass."""
    return sanitize_proposal(doc)


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
    forum="",
    arxiv="",
    year=None,
    cutoff_ms=None,
    timeout=240,
    call=None,
    cached_only=False,
    screen_receipt=None,
    leak_judge=None,
):
    require_leak_judge(leak_judge)
    packet = sanitize_proposal(packet)
    if legacy is not None:
        legacy = sanitize_proposal(legacy)
    if not any(packet.values()):
        raise ValueError("empty proposal")
    from .data import reported_result

    def _has_reported(doc):
        return isinstance(doc, Mapping) and any(
            isinstance(value, str) and value and reported_result(value) for value in doc.values()
        )

    def _cleared(field, doc):
        return bool(screen_receipt) and screen_receipt.get(field) == digest(doc)

    # Main, fallback, and legacy packets can each be the text a stage sends.
    # A screen receipt does not clear a rating, decision, award, or identity.
    # Reported results in the legacy packet are checked even when no stage
    # selects that packet, and a receipt for the main packet does not cover it.
    assert_no_label_leak(packet, where="proposal")
    if legacy is not None:
        assert_no_label_leak(legacy, where="fallback proposal")
    if _has_reported(packet) and not _cleared("cleared_packet", packet):
        raise ValueError("remove reported outcomes before ranking")
    if _has_reported(legacy) and not _cleared("cleared_legacy", legacy):
        raise ValueError("remove reported outcomes before ranking")
    apply_leak_audit(packet, leak_judge)
    if legacy is not None:
        apply_leak_audit(legacy, leak_judge)

    def send(stage, body, model, command):
        body = seal(body)
        if call is not None:
            apply_leak_audit(body, leak_judge)
            return call(stage, body, root / "cache", model, command, timeout)
        return invoke(
            stage, body, root / "cache", model, command, timeout,
            leak_judge=leak_judge, cached_only=cached_only,
        )

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
        qp = send("search_terms", packet, qm, qa)
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
                path = send(stage, source, model, argv)
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
                packet,
                read(folder / "contrast-bank.json"),
                query_packet,
                exclude_key=exclude_key,
                exclude_forum=forum,
                exclude_arxiv=arxiv,
                exclude_title=title,
                target_year=year,
                cutoff_ms=cutoff_ms,
            )
        elif stage == "forecast":
            source = legacy or packet
            assert_no_label_leak(source, where="forecast proposal")
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
                    folder / "prior.sqlite",
                    query_packet,
                    title,
                    abstract,
                    exclude_forum=forum,
                    exclude_arxiv=arxiv,
                    target_year=year,
                    cutoff_ms=cutoff_ms,
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
            peer_path = send("peer", source, pm, pa)
            judgments["peer"] = read(peer_path)["output"]
            source = merit.opinion_packet(judgments["peer"])
        path = send(stage, source, model, argv)
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


def screen_packet(packet, root, *, cached_only=False, provider=None, leak_judge=None):
    require_leak_judge(leak_judge)
    packet = sanitize_proposal(packet)
    assert_no_label_leak(packet, where="proposal")
    source = outcome_screen.units(packet)
    if not source:
        clean, audit = outcome_screen.filter_units(packet, source, {"exclude": []})
        return sanitize_proposal(clean), audit
    if provider in (None, "grok"):
        first_model, argv = route("outcome_screen")
    elif provider == "claude":
        first_model, argv = "claude-sonnet-5-5", claude_argv()
    else:
        raise ValueError(f"unknown outcome screen provider: {provider}")
    options = {"cached_only": cached_only, "leak_judge": leak_judge}
    receipt = invoke(
        "outcome_screen", seal(source), root / "cache", first_model, argv, 240, **options
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
        second = invoke(stage, seal(check), root / "cache", model, argv, 240, **options)
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
    return sanitize_proposal(clean), audit


def screen_rank_inputs(packet, legacy, root, profile, *, cached_only=False, provider=None, leak_judge=None):
    """Screen exactly the proposals that can enter this profile's scoring stages."""
    require_leak_judge(leak_judge)
    options = {"cached_only": cached_only, "leak_judge": leak_judge}
    if provider is not None:
        options["provider"] = provider
    packet, audit = screen_packet(packet, root, **options)
    evidence = {"proposal": audit}
    assessable = any(packet.values())
    if legacy is not None and any(
        stage_for_feature(name) == "forecast" for name in profile["ranker"]["names"]
    ):
        legacy, legacy_audit = screen_packet(legacy, root, **options)
        evidence["short_proposal"] = legacy_audit
        if legacy_audit.get("cleared_packet"):
            evidence["proposal"]["cleared_legacy"] = legacy_audit["cleared_packet"]
        assessable = assessable and any(legacy.values())
    return packet, legacy, evidence, assessable


def dispatch(args):
    profile, folder = load_profile(args.profile)
    root = assert_local_only(args.work)
    judge = require_leak_judge(model_leak_judge(root / "cache", "claude-opus-5-5", claude_argv(), 240))
    suffix = args.proposal.suffix.lower()
    doc = read(args.proposal) if suffix == ".json" else None
    batch = suffix in (".yaml", ".yml") or isinstance(doc, dict) and "candidates" in doc
    is_apd = batch
    include_audit = getattr(args, "include_source_audit", False)
    provider = getattr(args, "outcome_screen_provider", None)
    screen_options = {"leak_judge": judge}
    if provider:
        screen_options["provider"] = provider
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
            candidate = seal(c["candidate"])
            design = read(
                invoke("design", candidate, root / "cache", m, argv, 240, leak_judge=judge)
            )["output"]
            item["proposal"] = merit.compact(design)
            if any(
                stage_for_feature(n) == "forecast" for n in profile["ranker"]["names"]
            ):
                legacy = read(
                    invoke("compress", candidate, root / "cache", m, argv, 240, leak_judge=judge)
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
                citation_audit = source_evidence.audit(source, packet, root / "cache", leak_judge=judge)
                citation_audit["source_units"] = source
            if not assessable:
                results.append(
                    {
                        "id": item.get("id") if isinstance(item, dict) else None,
                        "proposal": dict(packet),
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
            leak_judge=judge,
        )
        if include_audit:
            result["source_audit"] = citation_audit
        if evidence:
            result["outcome_screen"] = evidence
        results.append(
            {
                "id": item.get("id") if isinstance(item, dict) else None,
                "proposal": dict(packet),
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
