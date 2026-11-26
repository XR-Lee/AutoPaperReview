"""Build separate candidate and review packets from public notes or APD candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from statistics import mean
from typing import Any

from ..iclr_anonymize import anonymize_iclr_submission
from ..pipeline import _atomic_write
from ..public_calibration import (
    content_value,
    is_official_review,
    latest_official_reviews,
    parse_rating,
)
from ..rating_scales import RatingScale, classic_scale, scale_for_key, scale_for_year


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


# Venue outcome, a numeric rating, an award, or an identity marker. Scientific
# prose such as "we accept the assumption" does not match. The pattern is
# applied to folded text: full-width punctuation, spelled-out scores, and
# obfuscated emails are normalized first.
LABEL_LEAK = re.compile(
    r"\boverall\s+ratings?\b"
    r"|\b(?:final|meta|reviewer|my|our|average)\s+(?:ratings?|scores?|recommendations?)\b"
    r"|\b(?:ratings?|scores?)\s*(?:[:=]\s*|\s+of\s+)\d+"
    r"|\b\d+\s+out\s+of\s+10\b"
    r"|\bdecision\s*[:=]\s*(?:accept|reject)\w*"
    r"|\b(?:accept|reject)(?:ed|ance)?\s+as\s+(?:an?\s+)?(?:oral|spotlight|poster)\b"
    r"|\b(?:strong|weak|clear|marginal|obvious)\s+(?:accept|reject)(?:ance)?\b"
    r"|\b(?:best|outstanding)\s+paper\b"
    r"|\bpaper\s+award\b"
    r"|\b(?:was|were|been)\s+(?:accept|reject)ed\b"
    r"|\b(?:should|would|must)\s+be\s+(?:accepted|rejected|published)\b"
    r"|\bdeserves?\s+to\s+be\s+(?:accepted|rejected|published)\b"
    r"|\brecommend(?:ing|ation)?\s+(?:acceptance|rejection)\b"
    r"|\bvote\s+(?:to\s+)?(?:accept|reject)\b"
    r"|\b(?:i|we)\s+(?:will|would|do)\s+(?:accept|reject)\s+(?:this|the)\s+(?:paper|submission)\b"
    r"|\bacceptance\s+(?:decision|rate|notification)\b"
    r"|\bcamera[- ]ready\b"
    r"|\brecommend(?:ation)?\s*[:=]?\s*(?:to\s+)?(?:accept|reject)\b"
    r"|\bcorresponding\s+authors?\b"
    r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    r"|\bgithub\.com/[A-Za-z0-9_.\-]+",
    re.IGNORECASE,
)
_LABEL_SPLIT = re.compile(
    r"(?<=[.!?。！？．])\s+|\n+"
    r"|(?=\bOverall\s+Ratings?\b)"
    r"|(?=\bDecision\s*[:：])"
    r"|(?=\b(?:Final|Meta|Reviewer|My|Our)\s+(?:Rating|Score)s?\b)",
    re.IGNORECASE,
)
_FOLD = str.maketrans(
    {
        "：": ":",
        "；": ";",
        "＠": "@",
        "．": ".",
        "（": "(",
        "）": ")",
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
    }
)
_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_SPELLED_NUMBER = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.IGNORECASE,
)
_OBFUSCATED_EMAIL = re.compile(
    r"(?i)([A-Za-z0-9._%+\-]+)\s*(?:@|\[at\]|\(at\))\s*([A-Za-z0-9\-]+)\s*(?:\.|\[dot\]|\(dot\))\s*([A-Za-z]{2,})"
)
_SPELLED_EMAIL = re.compile(
    r"(?i)\b([A-Za-z0-9._%+\-]+)\s+at\s+([A-Za-z0-9\-]+)\s+dot\s+([A-Za-z]{2,})\b"
)
# Score and identity fields are never copied into a model packet. "contribution"
# is not listed: APD uses that key for prose, and the ICLR numeric score is
# excluded by the review-prose allowlist below.
SCORE_FIELDS = frozenset(
    {
        "rating",
        "ratings",
        "recommendation",
        "recommendations",
        "decision",
        "decisions",
        "confidence",
        "soundness",
        "presentation",
        "award",
        "awards",
        "authors",
        "authorids",
        "author_ids",
        "email",
        "emails",
        "venue",
        "venueid",
        "final_rating",
        "accept",
        "meta_review",
    }
)
_REVIEW_PROSE = (
    "strengths",
    "weaknesses",
    "strengths_and_weaknesses",
    "questions",
)


def _fold_leak_text(text: str) -> str:
    folded = text.translate(_FOLD)
    folded = _OBFUSCATED_EMAIL.sub(r"\1@\2.\3", folded)
    folded = _SPELLED_EMAIL.sub(r"\1@\2.\3", folded)
    return _SPELLED_NUMBER.sub(lambda match: _NUMBER_WORDS[match.group(0).lower()], folded)


def _score_field(key: object) -> bool:
    return str(key).strip().lower().replace(" ", "_") in SCORE_FIELDS


def cleaning_rules() -> dict[str, str]:
    """Patterns that define prospective-text cleaning. Included in protocol digests."""
    return {
        "label_leak": LABEL_LEAK.pattern,
        "outcome": OUTCOME.pattern,
        "reported_finding": REPORTED_FINDING.pattern,
    }


def label_leak(text: str) -> bool:
    return bool(isinstance(text, str) and text and LABEL_LEAK.search(_fold_leak_text(text)))


def label_leak_in(value: Any) -> bool:
    if isinstance(value, str):
        return label_leak(value)
    if isinstance(value, dict):
        return any(label_leak_in(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(label_leak_in(item) for item in value)
    return False


def assert_no_label_leak(value: Any, *, where: str) -> None:
    """Refuse a packet that still carries a label or an identity marker.

    The check fails closed: the packet is not rewritten and is not sent on.
    """
    if label_leak_in(value):
        raise ValueError(
            f"{where} contains a rating, decision, award, acceptance, or identity leak"
        )


def neutralize_label_text(text: str) -> str:
    """Drop rating, decision, award, acceptance, and identity sentences."""
    if not isinstance(text, str) or not text.strip():
        return ""
    kept = []
    for part in _LABEL_SPLIT.split(text):
        sentence = part.strip(" \t-*")
        if sentence and not label_leak(sentence):
            kept.append(sentence)
    return " ".join(kept).strip()


def filter_conclusion_sentences(text: str) -> str:
    """Drop label leaks and reported-result sentences from audited prose."""
    if not isinstance(text, str) or not text.strip():
        return ""
    kept = []
    for part in _LABEL_SPLIT.split(text):
        sentence = part.strip(" \t-*")
        if sentence and not label_leak(sentence) and not reported_result(sentence):
            kept.append(sentence)
    return " ".join(kept).strip()


_GROUP_YEAR = re.compile(r"ICLR\.cc/(20\d{2})/")
_ABSTRACT_LINE = re.compile(r"(?i)^abstract\b[ \t]*[:.]?[ \t]*(.*)$")


def venue_year(group: str) -> int | None:
    match = _GROUP_YEAR.search(group or "")
    return int(match.group(1)) if match else None


def anonymized_submission_text(text: Any) -> str:
    """Anonymize one submission field before it becomes proposal source units.

    The field is framed under an Abstract heading so the shared ICLR anonymizer
    can drop a camera-ready banner, an email, or a named GitHub link without
    deleting a scientific sentence that mentions a university. The frame is not
    returned.
    """
    if not isinstance(text, str):
        return ""
    body = text.strip()
    if not body:
        return ""
    framed = "Submission\n\nAbstract\n" + body + "\n"
    cleaned, _removed = anonymize_iclr_submission(framed)
    kept: list[str] = []
    seen = False
    for line in cleaned.splitlines():
        if not seen:
            match = _ABSTRACT_LINE.match(line.strip())
            if match:
                seen = True
                rest = match.group(1).strip()
                if rest:
                    kept.append(rest)
            continue
        kept.append(line)
    return "\n".join(kept).strip() if seen else ""


def scale_for_reference(row: dict) -> RatingScale:
    """Scale for an already-averaged official mean on a reference record.

    When both ``year`` and ``rating_scale`` are set they must resolve to the
    same scale. A 2026 mean of 0 is legal on ``iclr2026`` and illegal on the
    2024 bins.
    """
    key = row.get("rating_scale")
    named = None
    if isinstance(key, str) and key:
        try:
            named = scale_for_key(key)
        except KeyError as exc:
            raise ValueError(f"unknown rating scale {key!r}") from exc
    year = row.get("year")
    yearly = None
    if year is not None:
        if type(year) is not int:
            raise ValueError("year must be an integer venue year")
        yearly = scale_for_year(year)
    if named is not None and yearly is not None and named.key != yearly.key:
        raise ValueError(
            f"year {year} resolves to {yearly.key}, which does not match rating_scale {named.key}"
        )
    if named is not None:
        return named
    if yearly is not None:
        return yearly
    return classic_scale()


def require_official_mean(value: Any, scale: RatingScale) -> None:
    """Keep a finite official mean inside the scale's inclusive range.

    ``parse_rating`` checks one reviewer's discrete score. A mean of those
    scores is not itself a scale point, so 7 is allowed on the 2024 scale and
    0 is allowed on the 2026 scale.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError("final_rating must be a finite number")
    if not scale.minimum <= float(value) <= scale.maximum:
        raise ValueError(f"final_rating {value} is outside {scale.label()}")


def official_rating_summary(reviews: list[dict], year: int | None) -> dict[str, Any]:
    """Parse official ratings with the venue scale. Raw rating text is not stored.

    One off-scale rating leaves ``final_mean`` unset. Dropping it would change
    the mean the harness compares with APR scores.
    """
    scale = scale_for_year(year) if year is not None else classic_scale()
    parsed: list[int] = []
    unparsed = 0
    missing = 0
    for note in reviews:
        raw = content_value(note, "rating")
        if raw is None or raw == "":
            missing += 1
            continue
        value = parse_rating(raw, scale=scale)
        if value is None:
            unparsed += 1
            continue
        parsed.append(value)
    return {
        "final_mean": mean(parsed) if parsed and unparsed == 0 else None,
        "rating_scale": scale.key,
        "ratings_parsed": len(parsed),
        "ratings_unparsed": unparsed,
        "ratings_missing": missing,
    }


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
        and not label_leak(p)
    ]


def units(fields: dict) -> dict[str, str]:
    lines = [
        sentence
        for key, value in fields.items()
        if isinstance(value, str) and not _score_field(key)
        for sentence in sentences(value)
    ]
    return {f"s{i}": s for i, s in enumerate(dict.fromkeys(lines), 1)}


def review_prose(note: dict) -> dict[str, str]:
    """The four ICLR prose fields, with rating and decision sentences removed.

    Rating, decision, confidence, and identity fields are not read. A decision
    written inside one of the four prose fields is dropped by the same cleaner
    before the text can reach a label or an audit.
    """
    fields = {}
    for key in _REVIEW_PROSE:
        raw = content_value(note, key)
        if not isinstance(raw, str):
            continue
        cleaned = neutralize_label_text(raw)
        if cleaned and not label_leak(cleaned):
            fields[key] = cleaned
    return fields


def prospective_comments(comments: dict) -> dict:
    """Drop score fields and label sentences before label or audit sees comments."""
    if not isinstance(comments, dict):
        raise TypeError("comments must be an object")
    cleaned: dict = {}
    for reviewer, body in comments.items():
        if _score_field(reviewer):
            continue
        if isinstance(body, str):
            text = neutralize_label_text(body)
            if text and not label_leak(text):
                cleaned[reviewer] = text
            continue
        if not isinstance(body, dict):
            continue
        kept = {}
        for key, value in body.items():
            if _score_field(key) or not isinstance(value, str):
                continue
            text = neutralize_label_text(value)
            if text and not label_leak(text):
                kept[key] = text
        cleaned[reviewer] = kept
    assert_no_label_leak(cleaned, where="review comments")
    return cleaned


def _millis(note: dict, *keys: str) -> int:
    for key in keys:
        raw = note.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return 0


def _review_pool(sub: dict, notes: list[dict], group: str) -> list[dict]:
    fid = sub["id"]
    invitation = f"{group}/Submission{sub['number']}/-/Official_Review"
    return [
        n
        for n in notes
        if n.get("forum") == fid
        and n.get("replyto") == fid
        and invitation in (n.get("invitations") or [])
        and not n.get("ddate")
        and is_official_review(n)
    ]


def official(sub: dict, notes: list[dict], group: str) -> list[dict]:
    return latest_official_reviews(_review_pool(sub, notes, group))


def _submission_revisions(sub: dict, notes: list[dict], group: str) -> list[dict]:
    invitation = f"{group}/-/Submission"
    revisions = [sub]
    for note in notes:
        if note is sub or note.get("ddate"):
            continue
        invitations = list(note.get("invitations") or [])
        if isinstance(note.get("invitation"), str):
            invitations.append(note["invitation"])
        if invitation not in invitations:
            continue
        if note.get("id") != sub.get("id") and note.get("forum") != sub.get("id"):
            continue
        revisions.append(note)
    return revisions


def iclr_case(
    sub: dict, notes: list[dict], group: str, *, allow_late: bool = False
) -> dict | None:
    if f"{group}/-/Submission" not in sub.get("invitations", []) or sub.get(
        "id"
    ) != sub.get("forum"):
        return None
    pool = _review_pool(sub, notes, group)
    if not pool:
        return None
    # Creation time of every official note, before signature dedup. A later
    # revision of the same review must not hide the first review. A missing
    # or zero timestamp is not evidence that the submission is pre-review:
    # one undated review is enough to refuse that claim.
    stamps = []
    known = True
    for note in pool:
        ms = _millis(note, "tcdate", "cdate")
        if ms > 0:
            stamps.append(ms)
        else:
            known = False
    reviews = sorted(latest_official_reviews(pool), key=lambda n: n["id"])
    if not reviews:
        return None
    if not known or not stamps:
        first = None
        chosen = sub
        early = False
    else:
        first = min(stamps)
        eligible = [
            note
            for note in _submission_revisions(sub, notes, group)
            if 0 < _millis(note, "tmdate", "mdate") < first
        ]
        if eligible:
            chosen = max(
                eligible,
                key=lambda note: (_millis(note, "tmdate", "mdate"), str(note.get("id") or "")),
            )
            early = True
        else:
            chosen = sub
            early = False
    if not early and not allow_late:
        return None
    stamp = _millis(chosen, "tmdate", "mdate") or None
    year = venue_year(group)
    packet = units(
        {"abstract": anonymized_submission_text(content_value(chosen, "abstract"))}
    )
    if not packet:
        return None
    sources, comments = {}, {}
    for i, note in enumerate(reviews, 1):
        alias = f"r{i}"
        sources[alias] = note["id"]
        comments[alias] = units(review_prose(note))
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
            "year": year,
            **official_rating_summary(reviews, year),
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
