from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .data import digest, read, write
from .stages import PROMPTS, audit_packet, invoke, proposal_text, validate


def run_batch(
    root: Path,
    *,
    stage: str,
    model: str,
    command: list[str],
    jobs: int,
    timeout: float,
    split: str | None = None,
):
    manifest = read(root / "manifest.json")
    if jobs < 1 or timeout <= 0:
        raise ValueError("jobs and timeout must be positive")

    def run_one(case):
        bundle = root / "inputs" / case["bundle"]
        target = root / "runs" / case["key"]
        if stage in ("label", "audit"):
            packet = read(bundle / "comments.json")
            if stage == "audit":
                packet = audit_packet(
                    stage_result(root, case, "label")["output"], packet
                )
        elif stage == "compress":
            packet = read(bundle / "candidate.json")
        else:
            previous = stage_result(root, case, "compress")
            packet = proposal_text(previous["output"])
        path = invoke(stage, packet, root / "cache", model, command, timeout)
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
    if stage in ("review", "forecast"):
        source = proposal_text(stage_result(root, case, "compress")["output"])
    elif stage == "audit":
        source = audit_packet(
            stage_result(root, case, "label")["output"], read(bundle / "comments.json")
        )
    else:
        source = read(
            bundle / ("candidate.json" if stage == "compress" else "comments.json")
        )
    req = entry["request"]
    if (
        req["stage"] != stage
        or req["input_sha256"] != digest(source)
        or req["system_sha256"] != digest(PROMPTS[stage])
    ):
        raise ValueError(f"stale {stage} result; rerun this stage")
    validate(stage, entry["output"], source)
    return entry
