from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .data import digest, prospective_comments, read, write
from .leak_audit import (
    _contains_reported_result, payload_sha256, require_leak_judge, serialize_payload, strict_model_value,
)
from .sanitized import seal
from .stages import PROMPTS, audit_packet, audit_spans, invoke, proposal_text, validate


def run_batch(
    root: Path,
    *,
    stage: str,
    model: str,
    command: list[str],
    jobs: int,
    timeout: float,
    split: str | None = None,
    leak_judge=None,
):
    require_leak_judge(leak_judge)
    manifest = read(root / "manifest.json")
    if jobs < 1 or timeout <= 0:
        raise ValueError("jobs and timeout must be positive")

    def run_one(case):
        bundle = root / "inputs" / case["bundle"]
        target = root / "runs" / case["key"]
        spans = None
        if stage in ("label", "audit"):
            originals = read(bundle / "comments.json")
            packet = prospective_comments(originals)
            if stage == "audit":
                labels = stage_result(root, case, "label")["output"]
                packet = audit_packet(labels, packet)
                spans = audit_spans(originals, packet, labels)
        elif stage == "compress":
            packet = read(bundle / "candidate.json")
        else:
            previous = stage_result(root, case, "compress")
            packet = proposal_text(previous["output"])
        packet = seal(packet, stage=stage)
        if stage == "compress" and _contains_reported_result(packet):
            raise ValueError("leak audit blocked a reported result")
        path = invoke(
            stage, packet, root / "cache", model, command, timeout,
            leak_judge=leak_judge, spans=spans,
        )
        write(
            target / f"{stage}.json",
            {"bundle": case["bundle"], "cache": str(path.relative_to(root))},
        )

    selected = [
        case for case in manifest["cases"] if split is None or case["split"] == split
    ]
    if stage in ("label", "audit"):
        selected = [case for case in selected if case["split"] != "unlabeled"]
    if not selected:
        raise ValueError(f"no eligible cases for {stage} in the selected split")
    failures = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, case): case for case in selected}
        for future in as_completed(futures):
            try:
                future.result()
            except (ValueError, RuntimeError, OSError) as exc:
                failures.append(f"{futures[future]['key']}: {str(exc)[:480]}")
    if failures:
        raise ValueError(
            f"{len(failures)} failed {stage} cases. Rerun to reuse completed cases. "
            + "\n".join(failures)
        )


def stage_result(root, case, stage):
    ptr = read(root / "runs" / case["key"] / (stage + ".json"))
    if ptr["bundle"] != case["bundle"]:
        raise ValueError(f"{stage} from a different input bundle")
    entry = read(root / ptr["cache"])
    bundle = root / "inputs" / case["bundle"]
    spans = None
    if stage in ("review", "forecast"):
        source = proposal_text(stage_result(root, case, "compress")["output"])
    elif stage == "audit":
        originals = read(bundle / "comments.json")
        labels = stage_result(root, case, "label")["output"]
        source = audit_packet(labels, prospective_comments(originals))
        spans = audit_spans(originals, source, labels)
    else:
        source = read(bundle / "candidate.json") if stage == "compress" else prospective_comments(
            read(bundle / "comments.json")
        )
    req = entry["request"]
    if (
        req["stage"] != stage
        or req["input_sha256"] != payload_sha256(serialize_payload(source))
        or req["system_sha256"] != digest(PROMPTS[stage])
    ):
        raise ValueError(f"stale {stage} result; rerun this stage")
    reply = entry.get("reply")
    if not isinstance(reply, str) or payload_sha256(reply) != entry.get("reply_sha256"):
        raise ValueError(f"stale {stage} result; rerun this stage")
    parsed = strict_model_value(reply)
    validated = validate(stage, parsed, source, spans=spans)
    if entry.get("output") != validated:
        raise ValueError(f"stale {stage} result; rerun this stage")
    return entry
