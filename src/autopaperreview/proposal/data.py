"""Build separate candidate and review packets from public notes or APD candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from itertools import chain
from pathlib import Path
from statistics import mean
from typing import Any

from ..iclr_anonymize import anonymize_iclr_submission
from ..public_calibration import (
    _finite_int,
    content_value,
    is_official_review,
    latest_official_reviews,
    parse_rating,
)
from ..rating_scales import RatingScale, classic_scale, scale_for_key, scale_for_year
from .file_io import open_input, read_text, require_distinct_output as require_distinct_output, write_text
from .sanitized import (
    contains_factual_leak,
    factual_leak,
    factual_patterns,
    normalized_fact_text,
    score_field,
    to_json_data,
)


def read(path: Path) -> Any:
    from .leak_audit import decode_json

    return decode_json(read_text(path))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=to_json_data).encode()
    ).hexdigest()


def write(path: Path, value: Any) -> None:
    write_text(
        path,
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=to_json_data) + "\n",
    )


LINK = re.compile(r"https?://\S+|\b(?:github\.com|arxiv\.org)/\S+", re.IGNORECASE)

_RESULT_CLAUSE = re.compile(
    r"(?<=[.!?])\s+|[;；。！？\n]+|,\s*(?:but|however|yet|while)\s+"
)
_EVALUATION = r"(?:experiments?|results?|evaluations?|pilots?|trials?|benchmarks?|ablations?|tests?)"
_METRIC = (
    r"(?:(?:search|query|inference|training|sorting|response|waiting|execution)\s+)?"
    r"(?:accuracy|precision|recall|f1|auc|error(?: rate)?|latency|runtime|throughput|"
    r"speed|time|memory|loss|efficiency|performance|success rate)"
)
_QUANTITY = r"[+-]?\d+(?:[.,]\d+)?(?:\s*(?:%|x\b|percentage points?\b)|\b)"
_COMPARATIVE = r"(?:faster|slower|better|worse|higher|lower|fewer|shorter|longer|more effective)"
_COMPLETED_RESULT = re.compile(
    rf"\b{_EVALUATION}\b(?:\s+(?:on|with)\s+[^.!?]{{1,50}}?)?\s+(?:have\s+|also\s+)?"
    r"(?:showed|confirmed|demonstrated|indicated|revealed|found|ended\s+with|yielded|returned|proved)\b"
    r"|\b(?:we|(?:the\s+)?authors?)\s+(?:(?:have|had|also|then|already|repeatedly)\s+){0,3}"
    r"(?:measured|observed|recorded|ran|tested|evaluated|demonstrated|proved|verified|confirmed|conducted|showed)\b"
    rf"|\b(?:completed|ran|conducted|performed)\s+(?:(?:a|an|the|our|preliminary|initial)\s+){{0,3}}{_EVALUATION}\b"
    r"|\b(?:was|were|has been|have been)\s+(?:measured|observed|achieved|verified|confirmed|proven)\b"
    rf"|\b{_METRIC}\s+(?:was|reached|fell to|rose to|increased to|decreased to)\s+{_QUANTITY}"
    rf"|\b(?:outperformed|surpassed|beat|exceeded)\s+(?:(?:the|a|all|our)\s+)?"
    r"(?:baseline\w*|control\w*|prior|existing|previous|manual|comparators?)\b"
    rf"|\b(?:achieved|attained|obtained|recorded)\s+(?:(?:a|an|the|new|best|higher|lower)\s+){{0,3}}"
    rf"(?:{_METRIC}\b|{_QUANTITY})"
    rf"|\b(?:yielded|produced)\s+(?:a |an |the )?(?:measured|observed|empirical|statistically significant)\b"
    rf"|\b(?:made|were|was|became)\b[^.!?]{{0,65}}\b(?:{_COMPARATIVE}|easier)\b[^.!?]{{0,65}}"
    rf"\b(?:during|in|on|across)\s+(?:(?:the|a|our)\s+)?{_EVALUATION}\b"
    r"|(?:实验|试验|测试)(?:证明|验证了|显示)|结果(?:显示|表明)|观察到|测得|取得了|提升了"
)
_ASSERTED_RESULT = re.compile(
    rf"\b{_EVALUATION}\s+(?:on\s+[^.!?]{{1,65}}?\s+)?(?:also\s+)?"
    r"(?:shows?|confirms?|demonstrates?|indicates?|reveals?|proves?)\b"
    rf"|\b{_METRIC}\s*(?:[:=]|\b(?:is|of|at|reaches)\b)\s*{_QUANTITY}"
    rf"|\b(?:achieves?|attains?|obtains?|yields?|measures?)\s+(?:an?\s+)?{_QUANTITY}"
    rf"|\b(?:yielded|produced|returned|observed|recorded|measured)\s+{_QUANTITY}"
    rf"|\b(?:improves?|improved|reduces?|reduced|cuts?|raised|raises?|increases?|increased|lowers?|lowered)"
    rf"\s+(?:the\s+)?{_METRIC}\b[^.!?]{{0,26}}{_QUANTITY}"
    rf"|\b(?:outperform(?:s|ed)?|surpass(?:es|ed)?|beats?)\b|\b{_COMPARATIVE}\s+than\b"
    rf"|\b{_QUANTITY}\s*(?:times\s+)?{_COMPARATIVE}\b"
    r"|\b(?:we|authors|paper)\s+(?:also\s+)?(?:demonstrate|prove|show|establish|find that)\b"
    r"|\bstate.of.the.art (?:performance|results)\b|优于|实验结果"
)
_RETROSPECTIVE_REVIEW = re.compile(
    r"\b(?:(?:was|were) (?:accept|reject)ed(?!\s+(?:practice|standard|meaning|usage|convention|sense|definition|use)\b)"
    r"|rebuttal|camera.ready|"
    r"acceptance (?:for publication|(?:at|to|by) (?:(?:a|the) )?(?:venue|conference|journal|workshop|iclr|cvpr))|"
    r"recommend (?:accept|reject)|my (?:rating|score)|after (?:the )?(?:discussion|response))\b|录用|拒稿"
)
_BARE_METRIC = re.compile(
    rf"\b{_METRIC}\s*(?:[:=]|\b(?:is|of|at|reaches)\b)\s*{_QUANTITY}"
)
_FORMULA_CONTEXT = re.compile(r"\b(?:formula|equation|synthetic)\b")
_PROSPECTIVE = re.compile(
    r"\b(?:will|would|could|may|might|should|can|planned|intend(?:s)?|aim(?:s)?|hypothesiz(?:e|es)|"
    r"expect(?:s|ed)?|predict(?:s|ed)?|whether)\b|"
    r"\b(?:plan(?:s)? to|propos(?:e|es|ed) to|to be measured|to be tested|if|assuming|design goal|"
    r"target(?:ed)? (?:accuracy|precision|recall|latency|throughput|performance|error))\b|"
    r"^\s*(?:estimate|test|measure|compare|simulate|evaluate|check|assess|target|plan|assume|set)\b|"
    r"将会?|计划|拟议|假设|如果|是否|预期|目标"
)


def reported_result(text: str) -> bool:
    """Flag retrospective evidence while retaining proposals, targets and hypotheses."""
    for clause in _RESULT_CLAUSE.split(normalized_fact_text(text)):
        if _COMPLETED_RESULT.search(clause) or _RETROSPECTIVE_REVIEW.search(clause):
            return True
        formula = _FORMULA_CONTEXT.search(clause) is not None
        for finding in _ASSERTED_RESULT.finditer(clause):
            if _PROSPECTIVE.search(clause[: finding.end()]):
                continue
            # A quantity on a formula is a definition, not a completed measurement.
            if formula and _BARE_METRIC.fullmatch(finding.group(0)):
                continue
            return True
    return False


# Factual leaks live in sanitized.py. Indirect acceptance is not a regex.
_LABEL_SPLIT = re.compile(
    r"(?<=[.!?。！？．])\s+|\n+"
    r"|(?=\bOverall\s+Ratings?\b)"
    r"|(?=\bDecision\s*[:：])"
    r"|(?=\b(?:Final|Meta|Reviewer|My|Our)\s+(?:Rating|Score)s?\b)",
    re.IGNORECASE,
)
_LABEL_TRIM = " \t-*"
_LABEL_JOIN = " "
# Bump when the review-span cleaning or offset mapping algorithm changes.
REVIEW_CLEANING_VERSION = 3
# Score and identity fields are never copied into a model packet. "contribution"
# is not listed: APD uses that key for prose, and the ICLR numeric score is
# excluded by the review-prose allowlist below.
_REVIEW_PROSE = (
    "strengths",
    "weaknesses",
    "strengths_and_weaknesses",
    "questions",
)


def cleaning_rules() -> dict[str, Any]:
    """Patterns that define prospective-text cleaning. Included in protocol digests.

    The factual patterns are defense in depth. Paraphrase is not one of them.
    """
    return {
        **factual_patterns(),
        "review_cleaning_version": REVIEW_CLEANING_VERSION,
        "sentence_boundary": {"pattern": _LABEL_SPLIT.pattern, "flags": _LABEL_SPLIT.flags},
        "trim_chars": _LABEL_TRIM,
        "sentence_join": _LABEL_JOIN,
        "result_patterns": {
            name: {"pattern": pattern.pattern, "flags": pattern.flags}
            for name, pattern in {
                "clause_boundary": _RESULT_CLAUSE,
                "completed_result": _COMPLETED_RESULT,
                "asserted_result": _ASSERTED_RESULT,
                "retrospective_review": _RETROSPECTIVE_REVIEW,
                "prospective": _PROSPECTIVE,
            }.items()
        },
    }


def label_leak(text: str) -> bool:
    """Factual leak only. See ``leak_audit`` for paraphrase and cross-field hints."""
    return factual_leak(text)


def label_leak_in(value: Any) -> bool:
    return contains_factual_leak(value)


def assert_no_label_leak(value: Any, *, where: str) -> None:
    """Refuse a packet that still carries a label or an identity marker.

    The check fails closed: the packet is not rewritten and is not sent on.
    """
    if label_leak_in(value):
        raise ValueError(
            f"{where} contains a rating, decision, award, acceptance, or identity leak"
        )


def _retained_review_spans(text: str, *, screen_results: bool = False):
    if not isinstance(text, str) or not text.strip():
        return
    start = 0
    for separator in chain(_LABEL_SPLIT.finditer(text), (None,)):
        end = separator.start() if separator is not None else len(text)
        part = text[start:end]
        sentence = part.strip(_LABEL_TRIM)
        if sentence and not label_leak(sentence) and not (
            screen_results and reported_result(sentence)
        ):
            left = start + len(part) - len(part.lstrip(_LABEL_TRIM))
            yield left, left + len(sentence)
        if separator is not None:
            start = separator.end()


def neutralize_label_text(text: str) -> str:
    """Drop rating, decision, award, acceptance, and identity sentences."""
    return _LABEL_JOIN.join(
        text[start:end] for start, end in _retained_review_spans(text)
    ).strip()


def retained_label_text(
    text: str, *, screen_results: bool = False
) -> tuple[str, tuple[int | None, ...]]:
    """Clean a review unit and map each retained character to the source."""
    parts = []
    offsets: list[int | None] = []
    previous_end = None
    for start, end in _retained_review_spans(text, screen_results=screen_results):
        if previous_end is not None:
            # A join is part of a verbatim quote only if it already existed
            # between these two retained source fragments.
            original = previous_end if text[previous_end:start] == _LABEL_JOIN else None
            offsets.append(original)
        parts.append(text[start:end])
        offsets.extend(range(start, end))
        previous_end = end
    joined = _LABEL_JOIN.join(parts)
    stripped = joined.strip()
    start = len(joined) - len(joined.lstrip())
    return stripped, tuple(offsets[start : start + len(stripped)])


def filter_conclusion_sentences(text: str) -> str:
    """Drop label leaks and reported-result sentences from audited prose."""
    return retained_label_text(text, screen_results=True)[0]


def contiguous_quote(text: str, offsets, quote: str, *, start_at: int = 0):
    """Return the cleaned start and original offset of one contiguous occurrence."""
    if not isinstance(text, str) or not isinstance(quote, str) or not quote:
        return None
    start = text.find(quote, start_at)
    while start >= 0:
        window = tuple(offsets[start : start + len(quote)])
        if (
            len(window) == len(quote)
            and window[0] is not None
            and all(offset == window[0] + index for index, offset in enumerate(window))
        ):
            return start, window[0]
        start = text.find(quote, start + 1)
    return None


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


def declared_rating_scale(row: dict, *, allow_null: bool = False) -> RatingScale | None:
    """Resolve scale metadata when a year or an explicit scale is present."""
    named = None
    key = row.get("rating_scale")
    if "rating_scale" in row and (key is not None or not allow_null):
        if not isinstance(key, str) or not key.strip():
            raise ValueError("rating_scale must be a non-empty string")
        try:
            named = scale_for_key(key)
        except KeyError as exc:
            raise ValueError("unknown rating_scale") from exc
    year = row.get("year")
    yearly = scale_for_year(year) if year is not None else None
    if named is not None and yearly is not None and named.key != yearly.key:
        raise ValueError(
            f"year {year} resolves to {yearly.key}, which does not match rating_scale {named.key}"
        )
    return named or yearly


def scale_for_reference(row: dict, *, lock: dict | None = None) -> RatingScale:
    """Resolve the scale for an official mean, inheriting a cohort lock."""
    reference = declared_rating_scale(row, allow_null=True)
    if lock is None:
        return reference or classic_scale()
    locked = declared_rating_scale(lock)
    year, locked_year = row.get("year"), lock.get("year")
    if year is not None and locked_year is not None and year != locked_year:
        raise ValueError("reference year disagrees with the cohort lock")
    if reference is not None and locked is not None and reference.key != locked.key:
        raise ValueError("reference rating_scale disagrees with the cohort lock")
    return locked or reference or classic_scale()


def require_finite_number(value: Any, field: str) -> None:
    if type(value) in (int, float):
        try:
            if math.isfinite(value):
                return
        except OverflowError:
            pass
    raise ValueError(f"{field} must be a finite number")


def require_official_mean(value: Any, scale: RatingScale) -> None:
    """Keep a finite official mean inside the scale's inclusive range.

    ``parse_rating`` checks one reviewer's discrete score. A mean of those
    scores is not itself a scale point, so 7 is allowed on the 2024 scale and
    0 is allowed on the 2026 scale.
    """
    require_finite_number(value, "final_rating")
    if not scale.minimum <= value <= scale.maximum:
        raise ValueError(f"final_rating is outside {scale.label()}")


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
        and not reported_result(p)
        and not label_leak(p)
    ]


def units(fields: dict) -> dict[str, str]:
    lines = [
        sentence
        for key, value in fields.items()
        if isinstance(value, str) and not score_field(key)
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
    """Drop score fields, label facts and completed results before reference coding."""
    if not isinstance(comments, dict):
        raise TypeError("comments must be an object")
    cleaned: dict = {}
    for reviewer, body in comments.items():
        if score_field(reviewer):
            continue
        if isinstance(body, str):
            text = filter_conclusion_sentences(body)
            if text and not label_leak(text):
                cleaned[reviewer] = text
            continue
        if not isinstance(body, dict):
            continue
        kept = {}
        for key, value in body.items():
            if score_field(key) or not isinstance(value, str):
                continue
            text = filter_conclusion_sentences(value)
            if text and not label_leak(text):
                kept[key] = text
        cleaned[reviewer] = kept
    assert_no_label_leak(cleaned, where="review comments")
    return cleaned


def _millis(note: dict, *keys: str) -> int:
    for key in keys:
        value = _finite_int(note.get(key))
        if value is not None and value > 0:
            return value
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
                key=lambda note: (
                    _millis(note, "tmdate", "mdate"),
                    str(note.get("id") or ""),
                    digest(note),
                ),
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


def _reject_duplicate_root_key(buf: str, handle, key: str) -> None:
    """Raise when the root object repeats ``key`` after its first member."""
    data = buf
    index = 0
    depth = 1
    in_string = False
    escaped = False
    capturing = False
    pending = None
    while True:
        if index >= len(data):
            more = handle.read(65536)
            if not more:
                return
            data = data[index:] + more
            index = 0
            continue
        char = data[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                capturing = False
            elif capturing:
                pending = (pending or "") + char
            index += 1
            continue
        if char == '"':
            in_string = True
            capturing = depth == 1
            if capturing:
                pending = ""
            index += 1
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth <= 0:
                return
        elif char == ":" and depth == 1 and pending is not None:
            if pending == key:
                raise ValueError(f"duplicate JSON key {key!r} on the root object")
            pending = None
        elif char == "," and depth == 1:
            pending = None
        index += 1


def archived_notes(path: Path):
    """Stream the leading notes array of the submission snapshot, one note at a time."""
    decoder = json.JSONDecoder()
    with open_input(path) as f:
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
                rest = buf[1:]
                while True:
                    more = f.read(65536)
                    if not more:
                        break
                    rest += more
                # Re-parse the tail as a complete object so escaped duplicate
                # keys and a truncated root fail closed.
                from .leak_audit import decode_json

                decode_json('{"notes":[]' + rest)
                return
            try:
                item, end = decoder.raw_decode(buf)
            except json.JSONDecodeError:
                more = f.read(65536)
                if not more:
                    raise ValueError("incomplete submission snapshot") from None
                buf += more
                continue
            from .leak_audit import decode_json

            item = decode_json(buf[:end])
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
    with open_input(forums) as stream:
        for line in stream:
            from .leak_audit import decode_json

            notes = decode_json(line)["notes"]
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
            doc = yaml.safe_load(read_text(path))
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
