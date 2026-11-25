"""Local, date-bounded retrieval of earlier scientific abstracts for proposal assessment."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from math import log
from pathlib import Path

STOP = set(
    "the and for that with from this are our into over under method methods approach model models data learning using propose proposed problem work paper also where prior study show research likely would could should existing framework their than through which these can how will such within based more both between other new use only here class two does its each has analysis".split()  # noqa: SIM905
)


def words(text):
    return [w for w in re.findall("[a-z][a-z0-9-]{2,}", text.lower()) if w not in STOP]


def build(corpus: Path, db: Path):
    with corpus.open(encoding="utf-8") as source, sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE papers USING fts5(title, abstract, year UNINDEXED, revision UNINDEXED, forum UNINDEXED)"
        )
        for line in source:
            p = json.loads(line)
            if re.search(
                r"pathogen|viral vector|malware|weaponi|exploit development|weapon design|protein engineering",
                p["title"] + " " + p["abstract"],
                re.IGNORECASE,
            ):
                continue
            conn.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?)",
                (p["title"], p["abstract"], p["year"], p["revision_ms"], p["forum"]),
            )
        conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
        conn.commit()


def retrieve(db, proposal, exclude_title="", exclude_abstract="", limit=4):
    content = " ".join(x for x in proposal.values() if isinstance(x, str))
    counts = Counter(words(content))
    seen = set(words(exclude_title))
    ab = set(words(exclude_abstract))
    with sqlite3.connect(
        f"file:{Path(db).resolve().as_posix()}?mode=ro", uri=True
    ) as conn:
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
        for title, abstract, year, rev, forum in conn.execute(
            "SELECT title,abstract,year,revision,forum FROM papers WHERE papers MATCH ? ORDER BY bm25(papers,3,1) LIMIT 32",
            (q,),
        ):
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
