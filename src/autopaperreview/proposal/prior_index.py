"""Local, date-bounded retrieval of earlier scientific abstracts for proposal assessment."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from math import log
from pathlib import Path

STOP = set(
    "the and for that with from this are our into over under method methods approach model models data learning using propose proposed problem work paper also where prior study show research likely would could should existing framework their than through which these can how will such within based more both between other new use only here class two does its each has analysis".split()  # noqa: SIM905
)


def words(text):
    return [w for w in re.findall("[a-z][a-z0-9-]{2,}", text.lower()) if w not in STOP]


def norm_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


_SPLIT_FIELDS = ("split", "partition", "cohort")
_DEVELOPMENT_SPLITS = {"dev", "development"}
_ARXIV_FIELDS = ("arxiv", "arxiv_id")


def norm_arxiv(value: str) -> str:
    text = (value or "").strip().casefold()
    text = text.split("#", 1)[0].split("?", 1)[0].strip()
    text = re.sub(r"^arxiv:\s*", "", text)
    text = re.sub(
        r"^(?:(?:https?:)?//)?(?:[a-z0-9-]+\.)*arxiv\.org(?::\d+)?/"
        r"(?:abs|pdf|html|ps|format)/",
        "",
        text,
    )
    text = re.sub(r"\.pdf$", "", text.rstrip("/"))
    return re.sub(r"v\d+$", "", text)


def _whole_int(value: object) -> int | None:
    """Integer value, or None. Non-finite numbers do not raise OverflowError."""
    if isinstance(value, bool):
        return None
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            return None
        try:
            whole = int(value)
        except (OverflowError, ValueError):
            return None
        return whole if whole == value else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+", text):
            try:
                return int(text)
            except ValueError:
                return None
    return None


def _positive_stamp(value: object) -> int | None:
    number = _whole_int(value)
    if number is None or not 0 < number < 2 ** 63:
        return None
    return number


def development_row(row) -> bool:
    """Every non-blank split alias must identify a development record."""
    names = []
    for field in _SPLIT_FIELDS:
        value = row.get(field)
        if value is None or isinstance(value, str) and not value.strip():
            continue
        if not isinstance(value, str):
            return False
        names.append(value.strip().casefold())
    return bool(names) and all(name in _DEVELOPMENT_SPLITS for name in names)


def historical_bounds(target_year, cutoff_ms) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    year = now.year if target_year is None else _positive_stamp(target_year)
    if year is None:
        raise ValueError("target year must be a positive integer")
    cutoff = int(now.timestamp() * 1000) if cutoff_ms is None else _positive_stamp(cutoff_ms)
    if cutoff is None:
        raise ValueError("cutoff must be a finite positive timestamp")
    return year, cutoff


def earlier_record(year, revision, bounds) -> bool:
    year_limit, cutoff = bounds
    year = _positive_stamp(year)
    revision = _positive_stamp(revision)
    return year is not None and year < year_limit and revision is not None and revision < cutoff


def arxiv_identifiers(row) -> set[str]:
    return {identifier for field in _ARXIV_FIELDS if (identifier := norm_arxiv(str(row.get(field) or "")))}


def _connect(db, *, readonly=False):
    import sqlite3

    if readonly:
        return sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)
    return sqlite3.connect(db)


def build(corpus: Path, db: Path):
    with corpus.open(encoding="utf-8") as source, _connect(db) as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE papers USING fts5("
            "title, abstract, year UNINDEXED, revision UNINDEXED, forum UNINDEXED, "
            "arxiv UNINDEXED, split UNINDEXED)"
        )
        for line in source:
            p = json.loads(line)
            if re.search(
                r"pathogen|viral vector|malware|weaponi|exploit development|weapon design|protein engineering",
                p["title"] + " " + p["abstract"],
                re.IGNORECASE,
            ):
                continue
            year = _positive_stamp(p.get("year"))
            revision = _positive_stamp(p.get("revision_ms"))
            arxiv = arxiv_identifiers(p)
            if not development_row(p) or year is None or revision is None or len(arxiv) > 1:
                continue
            conn.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?,?,?)",
                (p["title"], p["abstract"], year, revision, p.get("forum"), next(iter(arxiv), ""), "dev"),
            )
        conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
        conn.commit()


def _columns(conn):
    info = conn.execute("PRAGMA table_info(papers)").fetchall()
    if info:
        return [row[1] for row in info]
    cursor = conn.execute("SELECT * FROM papers LIMIT 0")
    return [item[0] for item in cursor.description]


def retrieve(
    db,
    proposal,
    exclude_title="",
    exclude_abstract="",
    limit=4,
    *,
    exclude_forum="",
    exclude_arxiv="",
    target_year=None,
    cutoff_ms=None,
):
    from .sanitized import sanitize_proposal

    proposal = sanitize_proposal(proposal)
    bounds = historical_bounds(target_year, cutoff_ms)
    content = " ".join(x for x in proposal.values() if isinstance(x, str))
    counts = Counter(words(content))
    seen = set(words(exclude_title))
    ab = set(words(exclude_abstract))
    target_title = norm_title(exclude_title)
    target_arxiv = norm_arxiv(exclude_arxiv)
    if limit <= 0:
        return []
    with _connect(db, readonly=True) as conn:
        columns = set(_columns(conn))
        split_fields = tuple(k for k in _SPLIT_FIELDS if k in columns)
        revision = "revision" if "revision" in columns else "revision_ms"
        required = ("title", "abstract", "year", revision, "forum")
        if not split_fields or not set(required).issubset(columns):
            return []
        selected = ", ".join(required)
        arxiv_fields = tuple(k for k in _ARXIV_FIELDS if k in columns)
        eligibility_args = ", ".join((*required, *split_fields, *arxiv_fields))
        size = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
        terms = []
        for w, n in counts.most_common(150):
            freq = conn.execute("SELECT doc FROM vocab WHERE term=?", (w,)).fetchone()
            if freq and 2 <= freq[0] < 0.30 * size:
                terms.append((min(3, n) * log((size + 1) / (freq[0] + 1)), w))
        q = " OR ".join('"' + w + '"' for _, w in sorted(terms, reverse=True)[:24])
        if not q:
            return []

        def eligible(title, abstract, year, rev, forum, *aliases):
            row = dict(zip((*split_fields, *arxiv_fields), aliases, strict=True))
            if not development_row(row) or not earlier_record(year, rev, bounds):
                return False
            if not isinstance(title, str) or not isinstance(abstract, str):
                return False
            if exclude_forum and str(forum) == str(exclude_forum):
                return False
            if target_title and norm_title(title) == target_title:
                return False
            if target_arxiv and (
                target_arxiv in arxiv_identifiers(row)
                or target_arxiv in f"{title} {abstract} {forum}".casefold()
            ):
                return False
            t = set(words(title))
            a = set(words(abstract))
            return not (
                seen
                and len(t & seen) / max(1, len(t | seen)) > 0.49
                or ab
                and len(a & ab) / max(1, len(a | ab)) > 0.42
            )

        # Filter before ranking and limiting so ineligible rows cannot crowd out eligible hits.
        conn.create_function("eligible_paper", -1, eligible, deterministic=True)
        rows = conn.execute(
            f"SELECT {selected} FROM papers WHERE papers MATCH ? AND eligible_paper({eligibility_args}) "
            "ORDER BY bm25(papers, 3, 1), rowid LIMIT ?",
            (q, limit),
        )
        return [
            {
                "title": title,
                "abstract": abstract[:2400],
                "year": _positive_stamp(year),
                "forum": forum,
                "revision_ms": _positive_stamp(rev),
                "split": "dev",
            }
            for title, abstract, year, rev, forum in rows
        ]
