"""Local, date-bounded retrieval of earlier scientific abstracts for proposal assessment."""

from __future__ import annotations

import json
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


def norm_arxiv(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", text)
    text = re.sub(r"^arxiv:", "", text)
    text = re.sub(r"\.pdf$", "", text)
    return re.sub(r"v\d+$", "", text)


def _connect(db, *, readonly=False):
    import sqlite3

    if readonly:
        return sqlite3.connect(f"file:{Path(db).resolve().as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(db)


def build(corpus: Path, db: Path):
    with corpus.open(encoding="utf-8") as source, _connect(db) as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE papers USING fts5("
            "title, abstract, year UNINDEXED, revision UNINDEXED, forum UNINDEXED, arxiv UNINDEXED)"
        )
        for line in source:
            p = json.loads(line)
            if re.search(
                r"pathogen|viral vector|malware|weaponi|exploit development|weapon design|protein engineering",
                p["title"] + " " + p["abstract"],
                re.IGNORECASE,
            ):
                continue
            arxiv = norm_arxiv(str(p.get("arxiv") or p.get("arxiv_id") or ""))
            conn.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?,?)",
                (p["title"], p["abstract"], p["year"], p["revision_ms"], p["forum"], arxiv),
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
    if target_year is not None and int(year) >= int(target_year):
        return True
    if cutoff_ms is not None and int(rev) >= int(cutoff_ms):
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
        if target_year is not None and "year" in columns:
            filters.append("CAST(year AS INTEGER) < ?")
            params.append(int(target_year))
        if cutoff_ms is not None and "revision" in columns:
            filters.append("CAST(revision AS INTEGER) < ?")
            params.append(int(cutoff_ms))
        selected = "title, abstract, year, revision, forum"
        if has_arxiv:
            selected += ", arxiv"
        rows = conn.execute(
            f"SELECT {selected} FROM papers WHERE {' AND '.join(filters)} "
            "ORDER BY bm25(papers, 3, 1) LIMIT 64",
            params,
        )
        for row in rows:
            title, abstract, year, rev, forum = row[:5]
            arxiv = row[5] if has_arxiv else ""
            if _blocked_hit(
                title,
                abstract,
                year,
                rev,
                forum,
                arxiv,
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
            picked.append(
                {
                    "title": title,
                    "abstract": abstract[:2400],
                    "year": int(year),
                    "forum": forum,
                    "revision_ms": int(rev),
                }
            )
            if len(picked) == limit:
                break
    return picked
