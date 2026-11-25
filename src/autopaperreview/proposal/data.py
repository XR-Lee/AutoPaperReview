"""Build separate candidate and review packets from public notes or APD candidates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..pipeline import _atomic_write
from ..public_calibration import content_value, latest_official_reviews


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        path, json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )


# Conservative first pass. The compressor and label coder also check the meaning of every unit.
OUTCOME = re.compile(
    r"\b(outperform\w*|achiev(?:e[ds]?|ing) (?:a |the )?(?:new |higher |better |state.of.the.art)|"
    r"ablations?|empirically|empirical (?:result|evaluat|evidence|performance)|experimental (?:result|evaluat|evidence)|"
    r"(?:experiments?|evaluations?|results?|benchmarks?) (?:on .{0,75}? )?(?:show|confirm|demonstrat|reveal|indicat)|"
    r"(?:we|authors|paper) (?:also )?(?:demonstrate|prove|show|establish|find that)|"
    r"state.of.the.art (?:performance|results)|(?:was|were) (?:accept|reject)ed|rebuttal|camera.ready|"
    r"acceptance|recommend (?:accept|reject)|my (?:rating|score)|after (?:the )?(?:discussion|response))\b|"
    r"\d\s*(?:%|percentage points)|实验(?:结果|表明|显示)|取得了|优于|提升了|录用|拒稿",
    re.IGNORECASE,
)
LINK = re.compile(r"https?://\S+|\b(?:github\.com|arxiv\.org)/\S+", re.IGNORECASE)


REPORTED_FINDING = re.compile(
    r"\b(?:our |the )?(?:experiments?|results?|evaluations?)\s+(?:have |also )?"
    r"(?:demonstrat(?:e[ds]?|ing)|show(?:n|ed|s)?|confirm(?:ed|s)?|indicat(?:e[ds]?|ing)|reveal(?:ed|s)?)\b|"
    r"\b(?:achieved|obtained|attained)\s+(?:a |an |the )?(?:new |best |higher |lower |accuracy|score)|"
    r"实验(?:证明|验证了)|结果(?:显示|表明)",
    re.IGNORECASE,
)


def reported_result(text: str) -> bool:
    return bool(OUTCOME.search(text) or REPORTED_FINDING.search(text))


def sentences(text: str) -> list[str]:
    clean = LINK.sub("", text)
    parts = re.split(
        r"(?<=[.!?。！？])\s+(?=[A-Z0-9*(\[])|[\r\n]+|(?<=[。！？])(?=\S)", clean
    )
    return [
        p.strip(" \t-*")
        for p in parts
        if (len(p.strip(" \t-*")) >= 8 or re.search(r"[\u3400-\u9fff]{3}", p))
        and not OUTCOME.search(p)
    ]


def units(fields: dict) -> dict[str, str]:
    lines = [s for v in fields.values() if isinstance(v, str) for s in sentences(v)]
    return {f"s{i}": s for i, s in enumerate(dict.fromkeys(lines), 1)}


def official(sub: dict, notes: list[dict], group: str) -> list[dict]:
    fid = sub["id"]
    invitation = f"{group}/Submission{sub['number']}/-/Official_Review"
    return latest_official_reviews(
        [
            n
            for n in notes
            if n.get("forum") == fid
            and n.get("replyto") == fid
            and invitation in n.get("invitations", [])
            and not n.get("ddate")
        ]
    )


def iclr_case(
    sub: dict, notes: list[dict], group: str, *, allow_late: bool = False
) -> dict | None:
    if f"{group}/-/Submission" not in sub.get("invitations", []) or sub.get(
        "id"
    ) != sub.get("forum"):
        return None
    reviews = sorted(official(sub, notes, group), key=lambda n: n["id"])
    if not reviews:
        return None
    stamp = sub.get("tmdate") or sub.get("mdate")
    first = min(n.get("tcdate") or n.get("cdate") or 0 for n in reviews)
    early = bool(stamp and first and stamp < first)
    if not early and not allow_late:
        return None
    packet = units({"abstract": content_value(sub, "abstract")})
    if not packet:
        return None
    sources, comments = {}, {}
    for i, note in enumerate(reviews, 1):
        alias = f"r{i}"
        sources[alias] = note["id"]
        comments[alias] = units(
            {
                key: content_value(note, key)
                for key in (
                    "strengths",
                    "weaknesses",
                    "strengths_and_weaknesses",
                    "questions",
                )
            }
        )
    return {
        "id": sub["id"],
        "candidate": packet,
        "comments": comments,
        "provenance": {
            "forum": sub["id"],
            "submission_modified_ms": stamp,
            "first_review_created_ms": first,
            "pre_review": early,
            "area": content_value(sub, "primary_area"),
            "review_ids": sources,
        },
    }


def archived_notes(path: Path):
    """Stream the leading notes array of the submission snapshot, one note at a time."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as f:
        buf = f.read(65536)
        prefix = re.match(r'\s*\{\s*"notes"\s*:\s*\[', buf)
        if not prefix:
            raise ValueError(
                "expected an OpenReview object beginning with a notes array"
            )
        buf = buf[prefix.end() :]
        while True:
            buf = buf.lstrip()
            if buf.startswith("]"):
                return
            try:
                item, end = decoder.raw_decode(buf)
            except json.JSONDecodeError:
                more = f.read(65536)
                if not more:
                    raise ValueError("incomplete submission snapshot") from None
                buf += more
                continue
            yield item
            buf = buf[end:].lstrip()
            while not buf:
                buf = f.read(65536).lstrip()
                if not buf:
                    raise ValueError("missing end of notes array")
            if buf.startswith(","):
                buf = buf[1:]
            elif not buf.startswith("]"):
                raise ValueError("invalid notes array")


def snapshot_cases(
    papers: Path, forums: Path, year: int, areas: list[str], allow_late: bool
):
    group = f"ICLR.cc/{year}/Conference"
    # Keep only the fields used downstream. Details in the public dump are large.
    submissions = {}
    for n in archived_notes(papers):
        if areas and content_value(n, "primary_area") not in areas:
            continue
        sub = {
            k: n.get(k)
            for k in ("id", "forum", "invitations", "number", "tmdate", "mdate")
        }
        sub["content"] = {k: n["content"].get(k) for k in ("abstract", "primary_area")}
        submissions[n["id"]] = sub
    with forums.open(encoding="utf-8") as stream:
        for line in stream:
            notes = json.loads(line)["notes"]
            for fid in {n.get("forum") for n in notes} & submissions.keys():
                case = iclr_case(submissions[fid], notes, group, allow_late=allow_late)
                if case:
                    yield case


def apd_cases(path: Path, *, review_filtered_fields=False):
    if path.suffix.lower() == ".json":
        doc = read(path)
    else:
        try:
            import yaml
        except ImportError:
            raise ValueError(
                "install autopaperreview[proposal-yaml] to read APD YAML, or supply JSON"
            ) from None
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid APD YAML: {exc}") from exc
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("candidates"), list)
        or not doc["candidates"]
    ):
        raise ValueError("expected a non-empty APD candidates list")
    motivation = doc.get("motivation") or {}
    if not isinstance(motivation, dict):
        raise ValueError("APD motivation must be a mapping")  # noqa: TRY004 -- invalid input document
    for candidate in doc["candidates"]:
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("id"), str)
            or not candidate["id"].strip()
        ):
            raise ValueError("every candidate needs a non-empty string id")
        fields = {
            k: motivation.get(k)
            for k in ("statement", "intended_contribution", "standpoint")
        }
        fields.update(
            {
                k: candidate.get(k)
                for k in ("contribution", "novelty", "serves_motivation")
            }
        )
        usable_contribution = units({"contribution": candidate.get("contribution")})
        if not usable_contribution and not review_filtered_fields:
            raise ValueError(
                f"candidate {candidate['id']!r} has no prospective contribution text"
            )
        for i, exp in enumerate(candidate.get("missing_experiments") or []):
            fields[f"plan{i}"] = (
                exp
                if isinstance(exp, str)
                else exp.get("description")
                if isinstance(exp, dict)
                else None
            )
        case = {
            "id": candidate["id"],
            "candidate": units(fields),
            "provenance": {"candidate_id": candidate["id"]},
        }
        if review_filtered_fields and (
            not usable_contribution
            or any(reported_result(v) for v in fields.values() if isinstance(v, str))
        ):
            # Only prospective input fields are eligible. Evidence, decisions and table cells stay outside the compressor.
            case["fields_to_screen"] = {
                k: LINK.sub("", v).strip()
                for k, v in fields.items()
                if isinstance(v, str) and v.strip()
            }
        yield case


def prepare(
    cases,
    root: Path,
    *,
    seed: str,
    limit: int | None,
    source: dict,
    previous: dict | None = None,
) -> dict:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    excluded = {c["key"] for c in previous["cases"]} if previous else set()
    indexed = {}
    for case in cases:
        fid = case["id"]
        if digest(["case", fid])[:24] in excluded:
            continue
        if fid in indexed:
            raise ValueError(f"duplicate candidate {fid!r}")
        indexed[fid] = case
    if not indexed:
        raise ValueError("no eligible candidates")
    ordered = sorted(indexed, key=lambda s: digest(["sample", seed, s]))[:limit]
    development = set(
        sorted(ordered, key=lambda s: digest(["split", seed, s]))[: len(ordered) // 2]
    )
    manifest = {
        "version": 1,
        "source": source,
        "seed": seed,
        "eligible": len(indexed),
        "cases": [],
    }
    for fid in ordered:
        case = indexed[fid]
        key = digest(["case", fid])[:24]
        # Immutable input bundles let partially completed or concurrent imports resume safely.
        folder = root / "inputs" / digest(case)
        for name in ("candidate", "comments", "provenance"):
            if name in case:
                write(folder / f"{name}.json", case[name])
        split = (
            ("dev" if fid in development else "holdout")
            if "comments" in case
            else "unlabeled"
        )
        manifest["cases"].append({"key": key, "bundle": folder.name, "split": split})
    if previous:
        manifest["excluded_manifest_sha256"] = digest(previous)
        for case in manifest["cases"]:
            case["split"] = "holdout"
    manifest_path = root / "manifest.json"
    if manifest_path.exists() and read(manifest_path) != manifest:
        raise ValueError(
            "different sample already present; use a fresh output directory"
        )
    write(manifest_path, manifest)
    return manifest
