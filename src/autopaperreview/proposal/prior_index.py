"""Local, date-bounded retrieval of earlier scientific abstracts for proposal assessment."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from math import log
from pathlib import Path

STOP = set(
    "the and for that with from this are our into over under method methods approach model models data learning using propose proposed problem work paper also where prior study show research likely would could should existing framework their than through which these can how will such within based more both between other new use only here class two does its each has analysis".split()  # noqa: SIM905
)


def words(text):
    return [w for w in re.findall("[a-z][a-z0-9-]{2,}", text.lower()) if w not in STOP]


def norm_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


# Without a target year, a sentinel this large is not treated as prior work.
_FUTURE_YEAR = 2099


def norm_arxiv(value: str) -> str:
    text = (value or "").strip().lower()
    text = text.split("#", 1)[0].split("?", 1)[0].strip()
    text = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/", "", text)
    text = re.sub(r"^arxiv:", "", text)
    text = re.sub(r"\.pdf$", "", text)
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
    if number is None or number <= 0:
        return None
    return number


def _split_name(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().casefold()
    text = re.sub(r"[\s_\-]+", "", text)
    return text or None


def _connect(db, *, readonly=False):
    import sqlite3

    if readonly:
        return sqlite3.connect(f"file:{Path(db).resolve().as_posix()}?mode=ro", uri=True)
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
            # Rows without a split or a year are not prior work. A non-finite
            # or non-positive revision is dropped here and does not raise.
            split = _split_name(p.get("split"))
            year = _whole_int(p.get("year"))
            revision = _positive_stamp(p.get("revision_ms"))
            if split is None or split in {"test", "holdout"} or year is None or year < 1:
                continue
            if year >= _FUTURE_YEAR or revision is None:
                continue
            arxiv = norm_arxiv(str(p.get("arxiv") or p.get("arxiv_id") or ""))
            conn.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?,?,?)",
                (p["title"], p["abstract"], year, revision, p.get("forum"), arxiv, split),
            )
        conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
        conn.commit()


def _columns(conn):
    info = conn.execute("PRAGMA table_info(papers)").fetchall()
    if info:
        return [row[1] for row in info]
    cursor = conn.execute("SELECT * FROM papers LIMIT 0")
    return [item[0] for item in cursor.description]


def _blocked_hit(
    title,
    abstract,
    year,
    rev,
    forum,
    arxiv,
    split,
    *,
    exclude_title,
    exclude_forum,
    exclude_arxiv,
    target_year,
    cutoff_ms,
):
    if exclude_forum and str(forum) == str(exclude_forum):
        return True
    if exclude_title and norm_title(title) and norm_title(title) == norm_title(exclude_title):
        return True
    target_arxiv = norm_arxiv(exclude_arxiv)
    row_arxiv = norm_arxiv(arxiv)
    if target_arxiv and (
        row_arxiv == target_arxiv
        or target_arxiv in f"{title} {abstract} {forum}".lower()
    ):
        return True
    name = _split_name(split)
    if name is None or name in {"test", "holdout"}:
        return True
    year_i = _whole_int(year)
    rev_i = _positive_stamp(rev)
    if year_i is None or year_i < 1 or rev_i is None:
        return True
    if target_year is None and year_i >= _FUTURE_YEAR:
        return True
    if target_year is not None and year_i >= int(target_year):
        return True
    cutoff = None if cutoff_ms is None else _positive_stamp(cutoff_ms)
    if cutoff_ms is not None and cutoff is None:
        return True
    if cutoff is not None and rev_i >= cutoff:
        return True
    return False


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
    if cutoff_ms is not None and _positive_stamp(cutoff_ms) is None:
        raise ValueError("cutoff must be a finite positive timestamp")
    content = " ".join(x for x in proposal.values() if isinstance(x, str))
    counts = Counter(words(content))
    seen = set(words(exclude_title))
    ab = set(words(exclude_abstract))
    with _connect(db, readonly=True) as conn:
        columns = set(_columns(conn))
        has_arxiv = "arxiv" in columns
        size = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
        terms = []
        for w, n in counts.most_common(150):
            freq = conn.execute("SELECT doc FROM vocab WHERE term=?", (w,)).fetchone()
            if freq and 2 <= freq[0] < 0.30 * size:
                terms.append((min(3, n) * log((size + 1) / (freq[0] + 1)), w))
        q = " OR ".join('"' + w + '"' for _, w in sorted(terms, reverse=True)[:24])
        picked = []
        if not q:
            return picked
        # Forum, year, and submission-date cutoffs are applied before the hit
        # limit so the target and later papers do not crowd out earlier work.
        filters = ["papers MATCH ?"]
        params: list = [q]
        if exclude_forum and "forum" in columns:
            filters.append("ifnull(forum, '') != ?")
            params.append(str(exclude_forum))
        if "year" in columns:
            if target_year is not None:
                filters.append("CAST(year AS INTEGER) > 0 AND CAST(year AS INTEGER) < ?")
                params.append(int(target_year))
            else:
                filters.append(
                    f"CAST(year AS INTEGER) > 0 AND CAST(year AS INTEGER) < {_FUTURE_YEAR}"
                )
        if "revision" in columns:
            filters.append("CAST(revision AS INTEGER) > 0")
            if cutoff_ms is not None:
                filters.append("CAST(revision AS INTEGER) < ?")
                params.append(_positive_stamp(cutoff_ms))
        if "split" in columns:
            filters.append(
                "lower(trim(split)) != '' AND lower(trim(split)) NOT IN ('test', 'holdout')"
            )
        else:
            return []
        selected = "title, abstract, year, revision, forum"
        if has_arxiv:
            selected += ", arxiv"
        if "split" in columns:
            selected += ", split"
        rows = conn.execute(
            f"SELECT {selected} FROM papers WHERE {' AND '.join(filters)} "
            "ORDER BY bm25(papers, 3, 1) LIMIT 64",
            params,
        )
        for row in rows:
            title, abstract, year, rev, forum = row[:5]
            cursor = 5
            arxiv = ""
            if has_arxiv:
                arxiv = row[cursor]
                cursor += 1
            split = row[cursor] if "split" in columns else None
            if _blocked_hit(
                title,
                abstract,
                year,
                rev,
                forum,
                arxiv,
                split,
                exclude_title=exclude_title,
                exclude_forum=exclude_forum,
                exclude_arxiv=exclude_arxiv,
                target_year=target_year,
                cutoff_ms=cutoff_ms,
            ):
                continue
            t = set(words(title))
            a = set(words(abstract))
            if (
                seen
                and len(t & seen) / max(1, len(t | seen)) > 0.49
                or ab
                and len(a & ab) / max(1, len(a | ab)) > 0.42
            ):
                continue
            # Venue outcome, reviewer comments and paper identity of the target are not fields in the critic packet.
            year_i = _whole_int(year)
            rev_i = _positive_stamp(rev)
            if year_i is None or rev_i is None:
                continue
            picked.append(
                {
                    "title": title,
                    "abstract": abstract[:2400],
                    "year": year_i,
                    "forum": forum,
                    "revision_ms": rev_i,
                    "split": _split_name(split),
                }
            )
            if len(picked) == limit:
                break
    return picked
