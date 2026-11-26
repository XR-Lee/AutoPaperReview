"""Local, date-bounded retrieval of earlier scientific abstracts for proposal assessment."""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from contextlib import closing
from math import log
from pathlib import Path
from urllib.parse import unquote

from ..public_calibration import _finite_int

STOP = set(
    "the and for that with from this are our into over under method methods approach model models data learning using propose proposed problem work paper also where prior study show research likely would could should existing framework their than through which these can how will such within based more both between other new use only here class two does its each has analysis".split()  # noqa: SIM905
)


def words(text):
    return [w for w in re.findall("[a-z][a-z0-9-]{2,}", text.lower()) if w not in STOP]


def canonical_text(text: str) -> str:
    """NFC/NFD-insensitive text with surrounding and repeated whitespace removed."""
    folded = unicodedata.normalize("NFKC", text or "")
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", folded) if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", folded).strip()


def canonical_key(value: object) -> str:
    """The identity used when a record key is stored, excluded, or looked up."""
    if type(value) is bool or type(value) not in (str, int):
        raise ValueError("prior record key must be a string or integer")
    return unicodedata.normalize("NFC", str(value)).strip()


def _content_tokens(text: str) -> list[str]:
    """Letters and numbers from every script. Punctuation does not become a token."""
    return re.findall(r"[^\W_]+", canonical_text(text).casefold())


def norm_title(text: str) -> str:
    return " ".join(_content_tokens(text))


def _identity(text: str) -> str | None:
    """Comparable text. None when characters remain but every token was dropped.

    Two empty results are not the same document. Callers treat None as
    unusable and refuse the row instead of skipping the comparison.
    """
    if not str(text or "").strip():
        return ""
    identity = norm_title(text)
    if not identity:
        return None
    return identity


def _token_signature(text: str) -> tuple[str, ...]:
    return tuple(sorted(_content_tokens(text)))


_UNCOMPARABLE = "\x00uncomparable"


def _field_segments(value) -> tuple[str, ...] | None:
    """One normalized segment per text field. Field names do not enter the identity."""
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, Mapping):
        texts = [item for item in value.values() if isinstance(item, str)]
    else:
        texts = []
    parts = []
    for text in texts:
        if not str(text).strip():
            continue
        identity = _identity(text)
        if identity is None:
            return None
        parts.append(identity)
    return tuple(sorted(parts))


_SPLIT_FIELDS = ("split", "partition", "cohort")
_DEVELOPMENT_SPLITS = {"dev", "development"}
_ARXIV_FIELDS = ("arxiv", "arxiv_id")
_REVISION_FIELDS = ("revision", "revision_ms")
_GRADE_TIME_FIELDS = ("target_ms", "grade_ms", "opinion_ms")
_HISTORY_TIME_FIELDS = (
    *_REVISION_FIELDS, *_GRADE_TIME_FIELDS, "review_ms", "timestamp_ms", "created_ms", "modified_ms",
    "cdate", "mdate", "tcdate", "tmdate", "mtime", "snapshot_ms", "available_ms", "submission_ms",
)
_ARXIV_HOST = re.compile(
    r"^(?:(?:https?|ftp):)?(?://)?(?:[a-z0-9-]+\.)*arxiv\.org\.?(?::\d+)?/"
)
_ARXIV_DOI = re.compile(r"^(?:(?:(?:https?:)?//)?(?:dx\.)?doi\.org/|doi:\s*)?10\.48550/arxiv\.")
_ARXIV_DIRECTORIES = {"abs", "arxiv", "e-print", "format", "ftp", "html", "papers", "pdf", "ps", "pub", "src"}
_ARXIV_ARCHIVE = re.compile(r"[a-z][a-z0-9.-]*")


def norm_arxiv(value: str) -> str:
    """Resolve an arXiv identifier from a bare ID, DOI, URL, or archive path."""
    if not isinstance(value, str):
        return ""
    text = unquote(value).strip(" <>\t\r\n").casefold()
    text = re.split(r"[?#]", text, maxsplit=1)[0].strip()
    text = re.sub(r"^arxiv:\s*", "", text)
    text = _ARXIV_DOI.sub("", _ARXIV_HOST.sub("", text))
    if ":" in text or "//" in text:
        return ""
    *directories, stem = text.strip("/").split("/")
    archives = [part for part in directories if part not in _ARXIV_DIRECTORIES and not re.fullmatch(r"[0-9]{4}", part)]
    if len(archives) > 1 or any(not _ARXIV_ARCHIVE.fullmatch(part) for part in archives):
        return ""
    stem = re.sub(r"v[0-9]+$", "", re.sub(r"(?:\.(?:pdf|ps|gz|html|tar|zip))+$", "", stem))
    if re.fullmatch(r"[0-9]{4}\.[0-9]{4,5}", stem):
        return stem
    if archives and re.fullmatch(r"[0-9]{7}", stem):
        return f"{archives[0]}/{stem}"
    return ""


def _positive_stamp(value: object) -> int | None:
    number = _finite_int(value)
    if number is None or not 0 < number < 2 ** 63:
        return None
    return number


def _collect_splits(value, names: list[str]) -> bool:
    """Append every declared split. Return false when one is not text."""
    if isinstance(value, dict):
        for field in _SPLIT_FIELDS:
            item = value.get(field)
            if item is None or isinstance(item, str) and not item.strip():
                continue
            if not isinstance(item, str):
                return False
            names.append(item.strip().casefold())
        for child in value.values():
            if isinstance(child, (dict, list)) and not _collect_splits(child, names):
                return False
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)) and not _collect_splits(child, names):
                return False
    return True


def development_row(row) -> bool:
    """Every declared split, including nested ones, must be development."""
    if not isinstance(row, dict):
        return False
    names: list[str] = []
    if not _collect_splits(row, names):
        return False
    top = [
        row[field].strip().casefold()
        for field in _SPLIT_FIELDS
        if isinstance(row.get(field), str) and row[field].strip()
    ]
    return bool(top) and all(name in _DEVELOPMENT_SPLITS for name in names)


def proposal_body(value) -> str:
    """Normalized segments of a proposal, ignoring key names and key order.

    The same three passages in different allowed fields are one document.
    Text that survives only as punctuation has no identity. The sentinel is
    not equal to another document's real text, and a requested exclusion of
    that sentinel does not pass.
    """
    segments = _field_segments(value)
    if segments is None:
        return _UNCOMPARABLE
    return "\n".join(segments)


def _text_conflict(wanted: str, found: str) -> bool:
    """True when a requested comparison matches or cannot be formed.

    An empty wanted string is not a constraint. A wanted or found string that
    still has characters but no tokens does not silently pass. A missing found
    string is not a match.
    """
    if not str(wanted or "").strip():
        return False
    left = _identity(wanted)
    if left is None or not left:
        return True
    right = _identity(found)
    if right is None:
        return True
    if not right:
        return False
    return left == right


def _uncomparable(*texts: str) -> bool:
    return any(_identity(text) is None for text in texts if str(text or "").strip())


def same_document(proposal, title, abstract) -> bool:
    """True when a prior row repeats the proposal title, abstract, or full text.

    An empty normalized string never compares equal. Callers that must not
    let that row through use ``conflicts_with_target``.
    """
    if not isinstance(proposal, Mapping):
        return False
    if _uncomparable(
        *(value for value in proposal.values() if isinstance(value, str)),
        title or "",
        abstract or "",
    ):
        return False
    parts = [
        norm_title(value)
        for value in proposal.values()
        if isinstance(value, str) and value.strip() and norm_title(value)
    ]
    if not parts:
        return False
    joined = proposal_body(proposal)
    row_title = norm_title(title or "")
    row_abstract = norm_title(abstract or "")
    if row_title and row_title in parts:
        return True
    if row_abstract and (row_abstract in parts or row_abstract == joined):
        return True
    row_joined = norm_title(f"{title or ''} {abstract or ''}".strip())
    if row_joined and row_joined == joined and joined != _UNCOMPARABLE:
        return True
    proposal_signature = _token_signature(" ".join(
        value for value in proposal.values() if isinstance(value, str)
    ))
    row_signature = _token_signature(f"{title or ''} {abstract or ''}")
    return bool(proposal_signature) and proposal_signature == row_signature


def conflicts_with_target(proposal, title, abstract) -> bool:
    """The row repeats the target, or one side has no comparable tokens."""
    texts = [title or "", abstract or ""]
    if isinstance(proposal, Mapping):
        texts.extend(value for value in proposal.values() if isinstance(value, str))
    if _uncomparable(*texts):
        return True
    return same_document(proposal, title, abstract)


def historical_bounds(target_year, cutoff_ms) -> tuple[int, int]:
    year = _positive_stamp(target_year)
    if year is None:
        raise ValueError("historical retrieval requires an explicit positive target year")
    cutoff = _positive_stamp(cutoff_ms)
    if cutoff is None:
        raise ValueError("historical retrieval requires an explicit positive millisecond cutoff")
    return year, cutoff


def _append_arxiv(found: set[str], value) -> bool:
    if value is None or isinstance(value, str) and not value.strip():
        return True
    if not isinstance(value, str):
        return False
    identifier = norm_arxiv(value)
    if not identifier:
        return False
    found.add(identifier)
    return True


def _collect_arxiv_ids(value, found: set[str], *, skip_opinions: bool = False) -> bool:
    """Collect arXiv ids from the row and nested metadata. An unreadable id fails closed."""
    if isinstance(value, dict):
        for field in _ARXIV_FIELDS:
            if field in value and not _append_arxiv(found, value[field]):
                return False
        for key, child in value.items():
            if key in _ARXIV_FIELDS:
                continue
            if skip_opinions and key in _OPINION_FIELDS:
                continue
            if isinstance(child, (dict, list)) and not _collect_arxiv_ids(child, found, skip_opinions=skip_opinions):
                return False
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)) and not _collect_arxiv_ids(child, found, skip_opinions=skip_opinions):
                return False
    return True


def _arxiv_identifiers(row) -> set[str] | None:
    identifiers: set[str] = set()
    if not _collect_arxiv_ids(row, identifiers, skip_opinions=True):
        return None
    return identifiers


def _forum_text(value) -> str:
    return str(value or "").strip().casefold()


def latest_stamp(values) -> int | None:
    stamps = [_positive_stamp(value) for value in values if value is not None]
    if not stamps or None in stamps:
        return None
    return max(stamps)


def eligible_history(
    row, bounds=None, *, exclude_key="", exclude_forum="", exclude_arxiv="",
    exclude_title="", exclude_abstract="", exclude_text="",
):
    """Check development provenance, all known availability clocks and self-matches."""
    if not isinstance(row, dict) or not development_row(row):
        return False
    body = proposal_body(row.get("proposal"))
    if exclude_text and (
        exclude_text == _UNCOMPARABLE or body == _UNCOMPARABLE or body == exclude_text
    ):
        return False
    years: list = []
    _collect_years(row, years, skip_opinions=True)
    parsed_years = [_positive_stamp(value) for value in years]
    if years and any(value is None for value in parsed_years):
        return False
    year = _positive_stamp(row.get("year"))
    revision = latest_stamp(row.get(field) for field in _REVISION_FIELDS)
    times: list = []
    _collect_times(row, times, skip_opinions=True)
    available = latest_stamp(times)
    if year is None or revision is None or available is None:
        return False
    if bounds is not None and (
        year >= bounds[0]
        or any(value >= bounds[0] for value in parsed_years)
        or available >= bounds[1]
    ):
        return False
    identifiers = _arxiv_identifiers(row)
    if identifiers is None or len(identifiers) > 1:
        return False
    row_key = row.get("key")
    if exclude_key not in ("", None) and row_key is not None:
        try:
            left, right = canonical_key(row_key), canonical_key(exclude_key)
            if left and right and left.casefold() == right.casefold():
                return False
        except ValueError:
            return False
    if exclude_forum and _forum_text(row.get("forum")) == _forum_text(exclude_forum):
        return False
    if _text_conflict(exclude_title, str(row.get("title") or "")):
        return False
    if _text_conflict(exclude_abstract, str(row.get("abstract") or "")):
        return False
    arxiv = norm_arxiv(exclude_arxiv)
    if arxiv:
        searchable = " ".join(str(row.get(field) or "") for field in ("title", "abstract", "forum"))
        if arxiv in identifiers or arxiv in unquote(searchable).casefold():
            return False
    return True


_OPINION_FIELDS = frozenset({"opinions", "excerpts", "reviewer_excerpts", "comments", "reviews"})
INDEX_CLOCK_VERSION = 2
_CURRENT_INDEX_COLUMNS = frozenset({
    "title", "abstract", "year", "revision", "forum", "arxiv", "split", "available_ms", "key",
})


def _collect_years(value, found: list, *, skip_opinions: bool = False) -> None:
    if isinstance(value, dict):
        if "year" in value:
            found.append(value["year"])
        for key, child in value.items():
            if skip_opinions and key in _OPINION_FIELDS:
                continue
            if isinstance(child, (dict, list)):
                _collect_years(child, found, skip_opinions=skip_opinions)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                _collect_years(child, found, skip_opinions=skip_opinions)


def _collect_times(value, found: list, *, skip_opinions: bool = False) -> None:
    if isinstance(value, dict):
        for field in _HISTORY_TIME_FIELDS:
            if field in value:
                found.append(value[field])
        for key, child in value.items():
            if skip_opinions and key in _OPINION_FIELDS:
                continue
            if isinstance(child, (dict, list)):
                _collect_times(child, found, skip_opinions=skip_opinions)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                _collect_times(child, found, skip_opinions=skip_opinions)


def _opinion_splits_allowed(item) -> bool:
    """A nested split counts. No split fields is not a conflict."""
    names: list[str] = []
    if not _collect_splits(item, names):
        return False
    return all(name in _DEVELOPMENT_SPLITS for name in names)


def historical_opinions(items, cutoff_ms, *, snapshot_ms=None, target_year=None):
    cutoff = _positive_stamp(cutoff_ms)
    if cutoff is None:
        return []
    if isinstance(items, (str, dict)):
        items = [items]
    if not isinstance(items, list):
        return []
    kept = []
    for item in items:
        if isinstance(item, str):
            text, available = item, _positive_stamp(snapshot_ms)
        elif isinstance(item, dict):
            years: list = []
            _collect_years(item, years)
            parsed_years = [_positive_stamp(value) for value in years]
            if years and (
                any(value is None for value in parsed_years)
                or target_year is not None and any(value >= target_year for value in parsed_years)
            ):
                continue
            if not _opinion_splits_allowed(item):
                continue
            text = item.get("opinion", item.get("text"))
            times: list = []
            _collect_times(item, times)
            available = latest_stamp(times)
        else:
            continue
        if isinstance(text, str) and text.strip() and available is not None and available < cutoff:
            kept.append(text)
    return kept


def calibration_rows(bank, bounds, *, excerpt_field, **exclusions):
    """Yield distinct examples with a dated grade and only eligible opinion text."""
    keys = set()
    for row in bank:
        if not eligible_history(row, bounds, **exclusions) or "key" not in row or "target" not in row:
            continue
        key = row["key"]
        if type(key) not in (int, str) or key in keys:
            continue
        opinions = row.get(excerpt_field)
        dated = historical_opinions(opinions, bounds[1], snapshot_ms=row.get("opinion_ms"), target_year=bounds[0])
        grade = latest_stamp(row.get(field) for field in _GRADE_TIME_FIELDS)
        if grade is None and (not dated or len(dated) != (len(opinions) if isinstance(opinions, list) else 1)):
            # Undated aggregates may only rely on excerpts with independent dates.
            continue
        keys.add(key)
        yield row, dated


def _connect(db, *, readonly=False):
    import sqlite3

    if readonly:
        return sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)
    return sqlite3.connect(db)


def _record_key(row) -> str:
    key = row.get("key")
    if key is None:
        return ""
    return canonical_key(key)


def build(corpus: Path, db: Path):
    from .leak_audit import decode_json

    partial = Path(str(db) + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        _build_into(corpus, partial, decode_json)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, db)


def _build_into(corpus: Path, db: Path, decode_json) -> None:
    with corpus.open(encoding="utf-8") as source, closing(_connect(db)) as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE papers USING fts5("
            "title, abstract, year UNINDEXED, revision UNINDEXED, forum UNINDEXED, "
            "arxiv UNINDEXED, split UNINDEXED, available_ms UNINDEXED, key UNINDEXED)"
        )
        for line in source:
            if not line.strip():
                continue
            p = decode_json(line)
            if re.search(
                r"pathogen|viral vector|malware|weaponi|exploit development|weapon design|protein engineering",
                p["title"] + " " + p["abstract"],
                re.IGNORECASE,
            ):
                continue
            if not eligible_history(p):
                continue
            times: list = []
            _collect_times(p, times, skip_opinions=True)
            years: list = []
            _collect_years(p, years, skip_opinions=True)
            parsed_years = [value for value in (_positive_stamp(item) for item in years) if value is not None]
            conn.execute(
                "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    p["title"], p["abstract"],
                    max(parsed_years) if parsed_years else _positive_stamp(p["year"]),
                    latest_stamp(p.get(field) for field in _REVISION_FIELDS), p.get("forum"),
                    next(iter(_arxiv_identifiers(p)), ""), "dev",
                    latest_stamp(times),
                    _record_key(p),
                ),
            )
        conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(papers,row)")
        conn.execute(f"PRAGMA user_version = {INDEX_CLOCK_VERSION}")
        conn.commit()


def _index_version(conn) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


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
    exclude_key="",
    target_year=None,
    cutoff_ms=None,
    identity=None,
):
    from .sanitized import sanitize_proposal

    proposal = sanitize_proposal(proposal)
    identity = proposal if identity is None else sanitize_proposal(identity)
    bounds = historical_bounds(target_year, cutoff_ms)
    content = " ".join(x for x in proposal.values() if isinstance(x, str))
    counts = Counter(words(content))
    seen = set(words(exclude_title))
    ab = set(words(exclude_abstract))
    if limit <= 0 or not counts:
        return []
    with closing(_connect(db, readonly=True)) as conn:
        columns = set(_columns(conn))
        split_fields = tuple(k for k in _SPLIT_FIELDS if k in columns)
        revision_fields = tuple(k for k in _REVISION_FIELDS if k in columns)
        required = ("title", "abstract", "year", "forum")
        if not split_fields or not revision_fields or not set(required).issubset(columns):
            return []
        if exclude_key and "key" not in columns:
            return []
        # Same columns cannot show that an older build dropped nested clocks.
        if _CURRENT_INDEX_COLUMNS.issubset(columns) and _index_version(conn) != INDEX_CLOCK_VERSION:
            raise ValueError("prior index is stale; rebuild it before retrieval")
        arxiv_fields = tuple(k for k in _ARXIV_FIELDS if k in columns)
        timeline_fields = tuple(k for k in _HISTORY_TIME_FIELDS if k in columns)
        aliases = (*timeline_fields, *split_fields, *arxiv_fields)
        key_field = ("key",) if "key" in columns else ()
        eligibility_args = ", ".join((*required, *aliases, *key_field))
        revisions = ", ".join(revision_fields)

        def eligible(title, abstract, year, forum, *values):
            if key_field:
                *alias_values, key = values
            else:
                alias_values, key = values, ""
            row = dict(zip(aliases, alias_values, strict=True))
            if not isinstance(title, str) or not isinstance(abstract, str):
                return False
            row.update(title=title, abstract=abstract, year=year, forum=forum, key=key)
            if conflicts_with_target(identity, title, abstract):
                return False
            if not eligible_history(
                row, bounds, exclude_key=exclude_key, exclude_forum=exclude_forum,
                exclude_arxiv=exclude_arxiv, exclude_title=exclude_title,
                exclude_abstract=exclude_abstract,
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

        # FTS computes corpus frequencies and BM25 from its entire table. Reindex
        # the eligible rows in a private TEMP table on the read-only connection.
        conn.create_function("eligible_paper", -1, eligible, deterministic=True)
        conn.create_function("latest_stamp", -1, lambda *notes: latest_stamp(notes), deterministic=True)
        conn.execute(
            "CREATE VIRTUAL TABLE temp.eligible_papers USING fts5("
            "title, abstract, year UNINDEXED, forum UNINDEXED, revision UNINDEXED)"
        )
        conn.execute(
            f"INSERT INTO eligible_papers SELECT title, abstract, year, forum, latest_stamp({revisions}) "
            f"FROM papers WHERE eligible_paper({eligibility_args}) ORDER BY rowid"
        )
        conn.execute("CREATE VIRTUAL TABLE temp.eligible_vocab USING fts5vocab(eligible_papers,row)")
        size = conn.execute("SELECT count(*) FROM eligible_papers").fetchone()[0]
        terms = []
        for w, n in counts.most_common(150):
            freq = conn.execute("SELECT doc FROM eligible_vocab WHERE term=?", (w,)).fetchone()
            if freq and 2 <= freq[0] < 0.30 * size:
                terms.append((min(3, n) * log((size + 1) / (freq[0] + 1)), w))
        q = " OR ".join('"' + w + '"' for _, w in sorted(terms, reverse=True)[:24])
        if not q:
            return []
        rows = conn.execute(
            "SELECT title, abstract, year, forum, revision FROM eligible_papers WHERE eligible_papers MATCH ? "
            "ORDER BY bm25(eligible_papers, 3, 1), rowid LIMIT ?",
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
            for title, abstract, year, forum, rev in rows
        ]
