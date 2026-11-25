"""Manuscript text from a PDF, optionally cut to the main paper.

``read_manuscript_pdf`` extracts every page in reading order, removes page
furniture (running headers and dense margin line numbers), and joins the
pages with ``[Page N]`` markers. With ``main_only`` it also finds where the
main paper ends and keeps only those pages: the body and the reference list,
without the appendix or supplementary material that follows.

The main paper ends at, in this order:

1. an explicit page range (``main_pages``, such as ``1-10``);
2. the PDF outline: an entry titled Appendix or Supplementary Material, or
   the first top-level entry after the reference list;
3. an appendix heading in the text, at or after the reference list.

When none of them finds an appendix, every page is kept and ``detection`` is
``none``. When the appendix starts partway down a page, that page stays in the
PDF and its text is cut at the heading.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hashing import sha256_file

SOURCE_PDF_SCHEMA = "apr-source-pdf-v1"
# The appendix starts "inside" a page when this much text precedes its heading there.
MIN_BODY_CHARS_BEFORE_HEADING = 200
# A running header or footer is a line in the top or bottom window of most pages.
RUNNING_WINDOW = 6
RUNNING_LINE_SHARE = 0.8
RUNNING_LINE_MIN_PAGES = 4
# Review copies print line numbers in the margin. They are removed only when dense.
LINE_NUMBER_MIN_PER_PAGE = 20
HEADING_MAX_CHARS = 80

_REFERENCES_LINE = re.compile(r"^(?:\d+\.?\s+|[A-Z]\.\s+)?(?i:r\s?eferences|b\s?ibliography)$")
_APPENDIX_WORD = re.compile(
    r"^(?:[A-Z]\.?\s+)?(?i:appendix|appendices|supplementary|supplemental)\b"
)
# "A. Extra results" or "A Extra results" near the top of a page after the reference list.
_LETTER_HEADING = re.compile(r"^A\.?\s+[A-Z][^,]{2,78}$")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_LINE_NUMBER = re.compile(r"^\d{3,4}$")
_HEADING_PREFIX = re.compile(r"^(?:[A-Z]\.|\d+(?:\.\d+)*\.?)\s+")


class ManuscriptPdfError(ValueError):
    """The PDF cannot be read, or the page range is invalid."""


@dataclass
class ManuscriptPdf:
    """The extracted manuscript and the part of the PDF it covers."""

    source: Path
    source_sha256: str
    pages: list[str]
    total_pages: int
    last_page: int
    main_only: bool
    detection: str
    extractor: str
    references_page: int | None = None
    appendix_page: int | None = None
    appendix_heading: str | None = None
    cut_inside_page: bool = False
    removed_lines: dict[str, int] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Pages ``1..last_page`` with ``[Page N]`` markers."""
        parts = [
            f"[Page {index + 1}]\n{page.strip()}" for index, page in enumerate(self.pages[: self.last_page])
        ]
        return "\n\n".join(parts).rstrip() + "\n"

    def record(self) -> dict[str, Any]:
        """Provenance written next to the manuscript as ``source_pdf.json``."""
        dropped = [self.last_page + 1, self.total_pages] if self.last_page < self.total_pages else None
        return {
            "schema": SOURCE_PDF_SCHEMA,
            "source_name": self.source.name,
            "source_sha256": self.source_sha256,
            "pages_total": self.total_pages,
            "pages_kept": [1, self.last_page],
            "pages_dropped": dropped,
            "main_only": self.main_only,
            "detection": self.detection,
            "references_page": self.references_page,
            "appendix_page": self.appendix_page,
            "appendix_heading": self.appendix_heading,
            "cut_inside_page": self.cut_inside_page,
            "extractor": self.extractor,
            "removed_lines": dict(sorted(self.removed_lines.items())),
            "text_chars": len(self.text),
        }


def is_pdf(path: Path) -> bool:
    with Path(path).open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def parse_main_pages(spec: str, total: int) -> int:
    """The last main page for ``N`` or ``1-N``. The main paper starts on page 1."""
    match = re.fullmatch(r"\s*(?:(\d+)\s*-\s*)?(\d+)\s*", spec or "")
    if not match:
        raise ManuscriptPdfError(f"main pages must look like 1-10 or 10, not {spec!r}")
    first = int(match.group(1) or 1)
    last = int(match.group(2))
    if first != 1:
        raise ManuscriptPdfError("main pages must start at page 1")
    if not 1 <= last <= total:
        raise ManuscriptPdfError(f"main pages end at {last}, but the PDF has {total} pages")
    return last


def _pdftotext_pages(pdf: Path) -> list[str] | None:
    executable = shutil.which("pdftotext")
    if not executable:
        return None
    # Reading order. -layout would interleave the lines of a two-column page.
    completed = subprocess.run(
        [executable, "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=False
    )
    if completed.returncode != 0:
        return None
    pages = completed.stdout.decode("utf-8", errors="replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def _pymupdf_pages(pdf: Path) -> list[str] | None:
    try:
        import pymupdf
    except ImportError:
        return None
    with pymupdf.open(str(pdf)) as document:
        return [page.get_text() for page in document]


def _pypdfium2_pages(pdf: Path) -> list[str] | None:
    try:
        import pypdfium2
    except ImportError:
        return None
    document = pypdfium2.PdfDocument(str(pdf))
    try:
        return [document[index].get_textpage().get_text_bounded() for index in range(len(document))]
    finally:
        document.close()


_EXTRACTORS = {
    "pdftotext": _pdftotext_pages,
    "pymupdf": _pymupdf_pages,
    "pypdfium2": _pypdfium2_pages,
}


def page_count(pdf: Path) -> int | None:
    """Page count from PyMuPDF or pypdfium2, or ``None`` when neither is installed."""
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        with pymupdf.open(str(pdf)) as document:
            return document.page_count
    try:
        import pypdfium2
    except ImportError:
        return None
    document = pypdfium2.PdfDocument(str(pdf))
    try:
        return len(document)
    finally:
        document.close()


def extract_pages(pdf: Path, *, extractor: str | None = None) -> tuple[list[str], str]:
    """Text of every page in reading order, and the extractor that produced it.

    ``pdftotext`` is tried first, then PyMuPDF, then pypdfium2. An extract whose
    page count disagrees with the PDF is skipped.
    """
    names = [extractor] if extractor else list(_EXTRACTORS)
    for name in names:
        if name not in _EXTRACTORS:
            raise ManuscriptPdfError(f"unknown PDF text extractor {name!r}")
    expected = page_count(pdf)
    for name in names:
        pages = _EXTRACTORS[name](pdf)
        if pages is None or (expected is not None and len(pages) != expected):
            continue
        if not any(page.strip() for page in pages):
            raise ManuscriptPdfError("the PDF has no extractable text; it may be a scan")
        return pages, name
    raise ManuscriptPdfError("extracting PDF text needs pdftotext, or the pdf or visual extra")


def _lines(page: str) -> list[str]:
    return [line.strip() for line in page.splitlines()]


def _windows(lines: list[str]) -> set[int]:
    filled = [index for index, line in enumerate(lines) if line]
    return set(filled[:RUNNING_WINDOW]) | set(filled[-RUNNING_WINDOW:])


def strip_page_furniture(pages: list[str]) -> tuple[list[str], dict[str, int]]:
    """Remove running headers and footers, page numbers, and dense margin line numbers.

    Line numbers are lines of three or four digits, removed only when at least
    half of the pages carry ``LINE_NUMBER_MIN_PER_PAGE`` of them. A running line
    is an identical line in the top or bottom ``RUNNING_WINDOW`` lines of at
    least ``RUNNING_LINE_SHARE`` of the pages; it is removed from those windows
    only. A page number is a first or last line equal to the page's own number.
    """
    removed = {"running_header": 0, "line_number": 0, "page_number": 0}
    page_lines = [_lines(page) for page in pages]
    dense = sum(
        1
        for lines in page_lines
        if sum(1 for line in lines if _LINE_NUMBER.fullmatch(line)) >= LINE_NUMBER_MIN_PER_PAGE
    )
    if dense and dense * 2 >= len(pages):
        for index, lines in enumerate(page_lines):
            kept = [line for line in lines if not _LINE_NUMBER.fullmatch(line)]
            removed["line_number"] += len(lines) - len(kept)
            page_lines[index] = kept
    running: set[str] = set()
    if len(pages) >= RUNNING_LINE_MIN_PAGES:
        seen = Counter(
            line for lines in page_lines for line in {lines[index] for index in _windows(lines)}
        )
        running = {line for line, count in seen.items() if count >= RUNNING_LINE_SHARE * len(pages)}
    cleaned: list[str] = []
    for number, lines in enumerate(page_lines, start=1):
        window = _windows(lines)
        header = {index for index in window if lines[index] in running}
        removed["running_header"] += len(header)
        filled = [index for index, line in enumerate(lines) if line and index not in header]
        # A printed page number is the first or last remaining line and equals the page's own number.
        page_number = {index for index in filled[:1] + filled[-1:] if lines[index] == str(number)}
        removed["page_number"] += len(page_number)
        kept = [line for index, line in enumerate(lines) if index not in header and index not in page_number]
        cleaned.append(re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip())
    return cleaned, {key: value for key, value in removed.items() if value}


def outline(pdf: Path) -> list[tuple[int, str, int]]:
    """``(level, title, page)`` entries with 1-based pages. Empty without bookmarks or a PDF library."""
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        with pymupdf.open(str(pdf)) as document:
            entries = document.get_toc(simple=True)
        return [(int(level), str(title).strip(), int(page)) for level, title, page in entries if int(page) >= 1]
    try:
        import pypdfium2
    except ImportError:
        return []
    document = pypdfium2.PdfDocument(str(pdf))
    try:
        return [
            (int(item.level) + 1, str(item.title).strip(), int(item.page_index) + 1)
            for item in document.get_toc()
            if item.page_index is not None
        ]
    finally:
        document.close()


def _normal(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(folded.split())


def _heading_body(line: str) -> str:
    return _normal(_HEADING_PREFIX.sub("", line.strip()))


def _heading_like(line: str) -> bool:
    return 0 < len(line) <= HEADING_MAX_CHARS and not line.endswith(".") and not _YEAR.search(line)


def find_references_page(pages: list[str], entries: list[tuple[int, str, int]]) -> int | None:
    """Page of the reference-list heading, from the outline or else the text."""
    for _level, title, page in entries:
        if _REFERENCES_LINE.fullmatch(title.strip()):
            return page
    for index, page in enumerate(pages):
        if any(_REFERENCES_LINE.fullmatch(line) for line in _lines(page)):
            return index + 1
    return None


def _outline_appendix(entries: list[tuple[int, str, int]], refs_page: int | None) -> tuple[int, str] | None:
    for _level, title, page in entries:
        if _APPENDIX_WORD.match(title) and (refs_page is None or page >= refs_page):
            return page, title
    if refs_page is None or not entries:
        return None
    top = min(level for level, _title, _page in entries)
    after_refs = False
    for level, title, page in entries:
        if _REFERENCES_LINE.fullmatch(title.strip()):
            after_refs = True
            continue
        if level == top and (after_refs or page > refs_page):
            return page, title
    return None


def _text_appendix(pages: list[str], refs_page: int | None) -> tuple[int, str] | None:
    start = max((refs_page or 2) - 1, 1)
    for index in range(start, len(pages)):
        lines = [line for line in _lines(pages[index]) if line]
        for position, line in enumerate(lines):
            if not _heading_like(line):
                continue
            if _APPENDIX_WORD.match(line):
                return index + 1, line
            # A lettered heading counts only near the top of a page after the reference list.
            if refs_page is not None and index + 1 > refs_page and position < 3 and _LETTER_HEADING.fullmatch(line):
                return index + 1, line
    return None


def _heading_offset(page: str, heading: str) -> int | None:
    """Character offset of the heading line on a page: the identical line, else one that starts with it."""
    lines = page.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    for index, line in enumerate(lines):
        if line.strip() == heading.strip():
            return starts[index]
    wanted = _heading_body(heading)[:40]
    if len(wanted) < 12:
        return None
    for index, line in enumerate(lines):
        body = _heading_body(line)
        # A long outline title may wrap onto the next line, or carry a section letter in the text.
        if body and (body.startswith(wanted) or (len(body) >= 12 and wanted.startswith(body[:40]))):
            return starts[index]
    return None


def read_manuscript_pdf(
    pdf: Path,
    *,
    main_only: bool = False,
    main_pages: str | None = None,
    extractor: str | None = None,
) -> ManuscriptPdf:
    """Extract a manuscript PDF. With ``main_only``, keep the main paper only."""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise ManuscriptPdfError(f"PDF does not exist: {pdf}")
    if not is_pdf(pdf):
        raise ManuscriptPdfError(f"not a PDF: {pdf}")
    if main_pages and not main_only:
        raise ManuscriptPdfError("a main page range applies only to a main-only review")
    raw_pages, extractor_name = extract_pages(pdf, extractor=extractor)
    pages, removed = strip_page_furniture(raw_pages)
    result = ManuscriptPdf(
        source=pdf,
        source_sha256=sha256_file(pdf),
        pages=pages,
        total_pages=len(pages),
        last_page=len(pages),
        main_only=main_only,
        detection="all_pages",
        extractor=extractor_name,
        removed_lines=removed,
    )
    if not main_only:
        return result
    entries = outline(pdf)
    result.references_page = find_references_page(pages, entries)
    if main_pages:
        result.last_page = parse_main_pages(main_pages, result.total_pages)
        result.detection = "explicit"
        return result
    found = _outline_appendix(entries, result.references_page)
    detection = "outline"
    if found is None:
        found = _text_appendix(pages, result.references_page)
        detection = "heading"
    if found is None or found[0] <= 1:
        result.detection = "none"
        return result
    page, heading = found
    result.detection = detection
    result.appendix_page = page
    result.appendix_heading = heading
    offset = _heading_offset(pages[page - 1], heading)
    before = pages[page - 1][:offset] if offset is not None else ""
    if len(before.strip()) >= MIN_BODY_CHARS_BEFORE_HEADING:
        result.pages = [*pages[: page - 1], before.rstrip(), *pages[page:]]
        result.last_page = page
        result.cut_inside_page = True
    else:
        result.last_page = page - 1
    return result


def write_page_range(pdf: Path, output: Path, last_page: int) -> bool:
    """Write pages ``1..last_page`` of ``pdf`` to ``output``. False when no PDF library is installed."""
    total = page_count(pdf)
    if total is not None and last_page >= total:
        shutil.copyfile(pdf, output)
        return True
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        with pymupdf.open(str(pdf)) as source, pymupdf.open() as target:
            target.insert_pdf(source, from_page=0, to_page=last_page - 1)
            target.save(str(output), garbage=3, deflate=True)
        return True
    try:
        import pypdfium2
    except ImportError:
        return False
    source = pypdfium2.PdfDocument(str(pdf))
    target = pypdfium2.PdfDocument.new()
    try:
        target.import_pages(source, list(range(last_page)))
        target.save(str(output))
    finally:
        target.close()
        source.close()
    return True
