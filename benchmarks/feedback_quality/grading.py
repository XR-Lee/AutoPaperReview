"""Arm-blind adjudication and scenario-paired feedback metrics."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import random
import re


RUBRIC = (
    "Read the review, manuscript and target mechanism. A target is raised if the review "
    "actually alleges this mechanism is a shortcoming, or raises a concrete unresolved "
    "concern with the same causal or logical explanation. Generic requests, a correct "
    "description of a safeguard, an unrelated limitation, and a hypothetical concern "
    "explicitly resolved by the review do not count. A challenge that wrongly alleges "
    "this mechanism despite a manuscript safeguard DOES count as raised. Localization "
    "requires an accurate, case-specific pointer or quotation that identifies the "
    "decisive passage, operation, quantity or counterexample. A section number or "
    "general topic alone is insufficient. Actionability requires a specific feasible "
    "change that fixes the mechanism or accurately restricts the claim. Both qualities "
    "must attach to the same diagnosed concern, not unrelated comments. Where multiple "
    "comments raise the target, select the single best-supported comment, prioritizing "
    "a comment with both location and repair, then a located one. For a target allow "
    "one or more short exact review excerpts as support. Grade only what was written."
)


def read_jsonl(path):
    records = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_no}: expected a JSON object")
        records.append(record)
    return records


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def indexed(rows, key):
    by_key = {}
    for row in rows:
        name = row[key]
        if not isinstance(name, str) or not name or name in by_key:
            raise ValueError(f"Missing or duplicate {key}: {name!r}")
        by_key[name] = row
    return by_key


def prepare(dataset: Path, arms: dict[str, Path], out: Path, seed: int, first=1, last=None):
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["format"] != "feedback-quality-v1":
        raise ValueError("Unexpected dataset format")
    all_cases = manifest["scenarios"]
    last = len(all_cases) if last is None else last
    if not 1 <= first <= last <= len(all_cases):
        raise ValueError("Expected 1 <= first <= last <= number of scenario pairs")
    cases = all_cases[first - 1:last]
    known = set(manifest["input_order"])
    material = []
    missing = []
    for arm, path in arms.items():
        rows = indexed(read_jsonl(path), "item_id")
        if unknown := rows.keys() - known:
            raise ValueError(f"Unknown manuscript IDs in {arm}: {sorted(unknown)}")
        for case in cases:
            for variant, info in case["variants"].items():
                item = info["item_id"]
                if not re.fullmatch(r"[0-9a-f]{16}", item):
                    raise ValueError("Invalid manuscript identifier")
                body = (dataset / "inputs" / f"{item}.txt").read_bytes()
                if hashlib.sha256(body).hexdigest() != info["sha256"]:
                    raise ValueError(f"Manuscript digest mismatch: {item}")
                identity = {"arm": arm, "scenario": case["scenario"], "variant": variant, "item_id": item}
                if item not in rows:
                    missing.append({**identity, "reason": "no review submitted"})
                    continue
                row = rows[item]
                if "error" in row:
                    if "review" in row or not isinstance(row["error"], str) or not row["error"].strip():
                        raise ValueError(f"Expected exactly one review or nonempty error for {item}")
                    missing.append({**identity, "reason": row["error"]})
                    continue
                review = row["review"]
                if not isinstance(review, str) or not review.strip():
                    raise ValueError(f"Empty review for {item}; use an explicit error for failed output")
                packet = {"manuscript": body.decode(), "review": review, "target": case["diagnosis"],
                          "decisive_evidence": case["evidence"], "acceptable_repair": case["repair"]}
                material.append((identity, packet))
    random.Random(seed).shuffle(material)
    packets = []
    keys = []
    labels = []
    for number, (identity, packet) in enumerate(material, 1):
        grade_id = f"G{number:04d}"
        packets.append({"grade_id": grade_id, **packet})
        keys.append({"grade_id": grade_id, **identity})
        labels.append({"grade_id": grade_id, "target_raised": None,
                       "localized": None, "actionable": None, "support": []})
    # Refuse to overwrite adjudication work on a rerun.
    out.mkdir(parents=True, exist_ok=True)
    for name in ("packet.jsonl", "key.json", "labels.jsonl", "rubric.txt"):
        if (out / name).exists():
            raise ValueError(f"{out / name} already exists; choose a fresh packet directory")
    write_jsonl(out / "packet.jsonl", packets)
    write_jsonl(out / "labels.jsonl", labels)
    (out / "rubric.txt").write_text(RUBRIC + "\n", encoding="utf-8")
    key = {"format": "feedback-grading-v1", "packet_sha256": digest(out / "packet.jsonl"),
           "manifest_sha256": digest(manifest_path), "seed": seed, "arms": list(arms),
           "scenarios": [case["scenario"] for case in cases], "missing": missing, "reviews": keys}
    (out / "key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Grade:
    target: bool
    located: bool
    actionable: bool

    @classmethod
    def from_label(cls, row, review):
        flags = [row[name] for name in ("target_raised", "localized", "actionable")]
        if any(type(flag) is not bool for flag in flags):
            raise ValueError(f"Unfinished or invalid boolean grade: {row['grade_id']}")
        target, localized, actionable = flags
        if not target and (localized or actionable):
            raise ValueError(f"Location and action require a target concern: {row['grade_id']}")
        excerpts = row.get("support", [])
        if isinstance(excerpts, str):
            excerpts = [excerpts] if excerpts else []
        if not isinstance(excerpts, list) or (target and not excerpts):
            raise ValueError(f"A target needs exact review support: {row['grade_id']}")
        normalized = " ".join(review.split())
        for excerpt in excerpts:
            if not isinstance(excerpt, str) or not excerpt.strip() or " ".join(excerpt.split()) not in normalized:
                raise ValueError(f"Unmatched support excerpt in {row['grade_id']}")
        return cls(target, localized, actionable)


@lru_cache(maxsize=None)
def exact_lower(k, n, alpha):
    """Lower endpoint of an equal-tailed Clopper-Pearson interval via binomial inversion."""
    if k == 0:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(55):
        p = (lo + hi) / 2
        tail = sum(math.comb(n, hits) * p**hits * (1 - p)**(n - hits) for hits in range(k, n + 1))
        if tail < alpha / 2:
            lo = p
        else:
            hi = p
    return (lo + hi) / 2


def exact_interval(k, n, alpha=0.05):
    if not n:
        return [None, None]
    return [exact_lower(k, n, alpha), 1 - exact_lower(n - k, n, alpha)]


def rate(values):
    observed = [value for value in values if value is not None]
    k, n = sum(observed), len(observed)
    return {"k": k, "n": n, "estimate": k / n if n else None, "ci95": exact_interval(k, n)}


def paired_delta(left, right):
    pairs = [(a, b) for a, b in zip(left, right) if a is not None and b is not None]
    n = len(pairs)
    gains = sum(not a and b for a, b in pairs)
    losses = sum(a and not b for a, b in pairs)
    if n:
        # Each discordance proportion gets a 97.5% exact interval. The union bound
        # gives simultaneous coverage >=95%, including when every pair is tied.
        gain_lo, gain_hi = exact_interval(gains, n, alpha=0.025)
        loss_lo, loss_hi = exact_interval(losses, n, alpha=0.025)
        interval = [gain_lo - loss_hi, gain_hi - loss_lo]
    else:
        interval = [None, None]
    return {"n": n, "improved": gains, "worsened": losses, "tied": n - gains - losses,
            "estimate": (gains - losses) / n if n else None, "ci95": interval}


def measures(variants):
    clean, defect = variants.get("clean"), variants.get("defect")
    complete = clean is not None and defect is not None
    recalled = defect.target if defect else None
    located = defect.located if defect else None
    actionable = defect.actionable if defect else None
    both = (located and actionable) if defect else None
    specific = not clean.target if clean else None
    discriminates = (recalled and specific) if complete else None
    quality = (both and specific) if complete else None
    return {"scheduled_grounded_actionable_discrimination": bool(quality), "complete_pair_rate": complete,
            "defect_recall": recalled, "localized_recall": located, "actionable_recall": actionable,
            "grounded_actionable_recall": both, "targeted_specificity": specific,
            "discrimination": discriminates, "grounded_actionable_discrimination": quality}


def summarize(packet_dir: Path, labels_path: Path, baseline: str | None):
    key = json.loads((packet_dir / "key.json").read_text(encoding="utf-8"))
    if key["format"] != "feedback-grading-v1" or digest(packet_dir / "packet.jsonl") != key["packet_sha256"]:
        raise ValueError("Packet has changed or belongs to an unsupported format")
    packets = indexed(read_jsonl(packet_dir / "packet.jsonl"), "grade_id")
    labels = indexed(read_jsonl(labels_path), "grade_id")
    identities = indexed(key["reviews"], "grade_id")
    if labels.keys() != identities.keys() or packets.keys() != identities.keys():
        raise ValueError("Expected exactly one label for every blinded packet")
    arms, scenarios = key["arms"], key["scenarios"]
    baseline = arms[0] if baseline is None else baseline
    if baseline not in arms:
        raise ValueError(f"Unknown baseline arm: {baseline}")
    grades = {arm: {case: {} for case in scenarios} for arm in arms}
    for grade_id, identity in identities.items():
        grade = Grade.from_label(labels[grade_id], packets[grade_id]["review"])
        grades[identity["arm"]][identity["scenario"]][identity["variant"]] = grade
    rows = {arm: [measures(grades[arm][case]) for case in scenarios] for arm in arms}
    metrics = list(measures({}))
    vectors = {arm: {name: [row[name] for row in cases] for name in metrics} for arm, cases in rows.items()}
    rates = {arm: {name: rate(values) for name, values in columns.items()} for arm, columns in vectors.items()}
    deltas = {arm: {name: paired_delta(vectors[baseline][name], vectors[arm][name]) for name in metrics}
              for arm in arms if arm != baseline}
    return {
        "format": "feedback-scores-v1", "manifest_sha256": key["manifest_sha256"],
        "packet_sha256": key["packet_sha256"], "labels_sha256": digest(labels_path),
        "n_scenarios": len(scenarios), "variants_per_scenario": 2, "baseline": baseline,
        "primary": "scheduled_grounded_actionable_discrimination",
        "interval_method": "Scenario-level equal-tailed Clopper-Pearson; paired differences use simultaneous "
                           "97.5% exact intervals on discordant proportions (Bonferroni).",
        "rates": rates, "paired_difference_from_baseline": deltas, "missing": key["missing"],
        "scenario_measures": {arm: dict(zip(scenarios, arm_rows)) for arm, arm_rows in rows.items()},
    }
