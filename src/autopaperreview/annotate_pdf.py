"""Annotate a submission PDF with in-paper evidence markers.

Highlights and numbered labels are drawn with PyMuPDF when it is installed
(`pip install 'autopaperreview[annotate]'`). The annotated file is an export
output. Callers must not commit it.

External retrieved records are not drawn on the submission.
"""

from __future__ import annotations

from pathlib import Path

from .markers import EvidenceMarker


def pymupdf_available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def _needles(excerpt: str) -> list[str]:
    text = " ".join(excerpt.split())
    if not text:
        return []
    needles = [text[:180]]
    words = text.split()
    if len(words) > 6:
        needles.append(" ".join(words[:6]))
    if len(words) > 12:
        needles.append(" ".join(words[:12]))
    unique: list[str] = []
    for needle in needles:
        if needle and needle not in unique:
            unique.append(needle)
    return unique


def _rect_from_bbox(page, bbox: list[float]):
    import pymupdf

    width = float(page.rect.width)
    height = float(page.rect.height)
    x0, y0, x1, y1 = bbox
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
        # Normalized top-left boxes, the form visual findings use.
        rect = pymupdf.Rect(x0 * width, y0 * height, x1 * width, y1 * height)
    else:
        rect = pymupdf.Rect(x0, y0, x1, y1)
    return rect & page.rect


def locate_markers(pdf_path: Path, markers: dict[str, EvidenceMarker]) -> dict[str, EvidenceMarker]:
    """Fill missing pages by searching each in-paper excerpt."""
    import pymupdf

    document = pymupdf.open(pdf_path)
    try:
        located: dict[str, EvidenceMarker] = {}
        for evidence_id, marker in markers.items():
            if marker.external or marker.page or not marker.excerpt:
                located[evidence_id] = marker
                continue
            found_page: int | None = None
            for index, page in enumerate(document, start=1):
                if any(page.search_for(needle) for needle in _needles(marker.excerpt)):
                    found_page = index
                    break
            located[evidence_id] = marker.with_page(found_page) if found_page else marker
        return located
    finally:
        document.close()


def write_annotated_pdf(
    pdf_path: Path,
    markers: dict[str, EvidenceMarker],
    output_path: Path,
) -> dict[str, int]:
    """Write a copy with highlights, boxes, and marker labels. Returns counts."""
    import pymupdf

    document = pymupdf.open(pdf_path)
    placed = 0
    unlocated = 0
    try:
        in_paper = [marker for marker in markers.values() if not marker.external]
        for marker in in_paper:
            page_index = (marker.page or 0) - 1
            rects = []
            page = None
            if 0 <= page_index < document.page_count:
                page = document[page_index]
                if marker.bbox:
                    rects = [_rect_from_bbox(page, marker.bbox)]
                elif marker.excerpt:
                    for needle in _needles(marker.excerpt):
                        rects = page.search_for(needle)
                        if rects:
                            break
            elif marker.excerpt:
                for index, candidate in enumerate(document):
                    for needle in _needles(marker.excerpt):
                        found = candidate.search_for(needle)
                        if found:
                            page = candidate
                            rects = found
                            marker = marker.with_page(index + 1)
                            break
                    if rects:
                        break
            if page is None or not rects:
                unlocated += 1
                continue
            rect = rects[0]
            highlight = page.add_highlight_annot(rect)
            highlight.set_colors(stroke=(1, 0.85, 0.2))
            highlight.set_info(title=marker.evidence_id, content=marker.cite)
            highlight.update()
            box = page.add_rect_annot(rect)
            box.set_colors(stroke=(0.75, 0.15, 0.15))
            box.set_border(width=1.2)
            box.set_info(title=marker.evidence_id, content=marker.cite)
            box.update()
            label_x = max(rect.x0, 36)
            label_y = max(rect.y0 - 2, 12)
            page.insert_text(
                pymupdf.Point(label_x, label_y),
                marker.evidence_id,
                fontsize=8,
                color=(0.7, 0.1, 0.1),
            )
            placed += 1
        index = document.new_page(width=document[0].rect.width, height=document[0].rect.height)
        lines = ["Evidence markers", ""]
        for marker in in_paper:
            page_label = f"p.{marker.page}" if marker.page else "page unknown"
            pointer = next(iter(marker.pointers.values()), "")
            lines.append(f"[{marker.cite}]  {page_label}  {pointer}".rstrip())
        if not in_paper:
            lines.append("No in-paper markers.")
        index.insert_text(pymupdf.Point(54, 72), "\n".join(lines), fontsize=11)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path, garbage=4, deflate=True)
        return {
            "source_pages": document.page_count - 1,
            "output_pages": document.page_count,
            "markers_placed": placed,
            "markers_unlocated": unlocated,
        }
    finally:
        document.close()
