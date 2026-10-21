"""Page rendering, the pinned ICLR format checklist, and the visual-adapter contract.

Rendering uses the optional `visual` extra (pypdfium2). The core does not call a
vision model. An adapter, when declared, must return `VisualFinding` records
whose page-image hash matches a rendered PNG and whose visible mark is not the caption.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .figures import caption_inventory, sibling_pdf
from .hashing import sha256_file
from .models import FatalClass, FormatCheck, LocalizedText, VisualFinding, VisualFindingKind

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CAPTION_LINE = re.compile(r"^(?:Figure|Fig\.|Table)\s+\d+\b", re.IGNORECASE)
_REFERENCES = re.compile(r"^(?:references|bibliography)\s*$", re.IGNORECASE)
_ANON_AUTHORS = {"", "anonymous", "anonymous authors", "anon.", "anon"}


class VisualPageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    page_image_sha256: str
    image_path: str
    captions: list[str] = Field(default_factory=list)
    width_px: int
    height_px: int


class VisualInspector(Protocol):
    def inspect(self, request: VisualPageRequest, scratch: Path) -> list[VisualFinding]:
        """Return findings for one rendered page. Do not invent pixels."""


def load_profile(name: str = "iclr2027") -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "profiles" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"unknown venue profile {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"venue profile {name} is not an object")
    return payload


def profile_path(name: str = "iclr2027") -> Path:
    return Path(__file__).resolve().parent / "profiles" / f"{name}.json"


def load_visual_inspector(spec: str) -> VisualInspector:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not attribute:
        raise ValueError("visual inspector must be a module:attribute reference")
    module = importlib.import_module(module_name)
    inspector = getattr(module, attribute)
    if isinstance(inspector, type):
        inspector = inspector()
    if not hasattr(inspector, "inspect"):
        raise TypeError(f"visual inspector {spec!r} does not provide inspect()")
    return inspector


def _pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "page rendering requires the visual extra: pip install 'autopaperreview[visual]'"
        ) from exc
    return pdfium


def resolve_manuscript_pdf(source: Path) -> Path | None:
    return sibling_pdf(source)


def render_pdf_pages(pdf_path: Path, out_dir: Path, *, dpi: int = 150) -> dict[str, Any]:
    """Write `page-NNN.png` plus `manifest.json`. Same PDF and DPI, same hashes."""
    pdfium = _pdfium()
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    document = pdfium.PdfDocument(str(pdf_path))
    pages: list[dict[str, Any]] = []
    try:
        for index, page in enumerate(document):
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            filename = f"page-{index + 1:03d}.png"
            target = out_dir / filename
            image.save(target, format="PNG")
            width_pt, height_pt = page.get_size()
            pages.append(
                {
                    "page": index + 1,
                    "filename": filename,
                    "sha256": sha256_file(target),
                    "width_px": image.width,
                    "height_px": image.height,
                    "width_pt": width_pt,
                    "height_pt": height_pt,
                    "dpi": dpi,
                }
            )
    finally:
        document.close()
    manifest = {
        "pdf_sha256": sha256_file(pdf_path),
        "dpi": dpi,
        "page_count": len(pages),
        "pages": pages,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _localized(english: str, chinese: str) -> LocalizedText:
    return LocalizedText(primary=english, language="en", translations={"zh-Hans": chinese})


def _page_text(page) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_bounded() or ""
    finally:
        textpage.close()


def _char_boxes(page) -> list[tuple[float, float, float, float]]:
    textpage = page.get_textpage()
    boxes: list[tuple[float, float, float, float]] = []
    try:
        count = textpage.count_chars()
        for index in range(count):
            box = textpage.get_charbox(index)
            if not box:
                continue
            left, bottom, right, top = box
            if right - left < 0.2 and top - bottom < 0.2:
                continue
            boxes.append((left, bottom, right, top))
    finally:
        textpage.close()
    return boxes


def _content_page_count(document, profile: dict[str, Any]) -> tuple[int, int | None]:
    """Pages before the first References heading. The heading page does not count."""
    del profile
    for index in range(len(document)):
        for line in _page_text(document[index]).splitlines():
            if _REFERENCES.match(line.strip()):
                return index, index + 1
    return len(document), None


def _font_embedding(page) -> tuple[int, int]:
    embedded = 0
    missing = 0
    seen: set[tuple[str, bool]] = set()
    for obj in page.get_objects():
        if not hasattr(obj, "get_font"):
            continue
        try:
            font = obj.get_font()
        except Exception:
            continue
        if font is None:
            continue
        key = (str(font.get_base_name() or ""), bool(font.is_embedded))
        if key in seen:
            continue
        seen.add(key)
        if font.is_embedded:
            embedded += 1
        else:
            missing += 1
    return embedded, missing


def _graphics_count(page) -> int:
    count = 0
    for obj in page.get_objects():
        if type(obj).__name__ == "PdfTextObj":
            continue
        count += 1
    return count


def _ink_fraction(image) -> float:
    gray = image.convert("L")
    sample = gray.resize((80, 100))
    pixels = list(sample.getdata())
    if not pixels:
        return 0.0
    ink = sum(1 for value in pixels if value < 250)
    return ink / len(pixels)


def format_check_pdf(
    pdf_path: Path,
    manifest: dict[str, Any],
    profile: dict[str, Any],
    *,
    page_dir: Path | None = None,
) -> list[FormatCheck]:
    """Deterministic checklist. Details never include extracted manuscript sentences."""
    pdfium = _pdfium()
    checks: list[FormatCheck] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        content_pages, references_at = _content_page_count(document, profile)
        limit = int(profile["max_content_pages"])
        over = content_pages > limit
        page_limit_fatal = bool(profile.get("page_limit_fatal", True))
        where = f"references heading on page {references_at}" if references_at else "no references heading"
        checks.append(
            FormatCheck(
                id="FMT-pages",
                check="content_page_limit",
                passed=not over,
                fatal_class=FatalClass.format_desk_reject if over and page_limit_fatal else None,
                page=content_pages,
                detail=_localized(
                    f"Main-text pages before references: {content_pages} (limit {limit}; {where}).",
                    f"参考文献之前的正文页数：{content_pages}（上限 {limit}；{where}）。",
                ),
            )
        )
        max_total = profile.get("max_total_pages")
        if max_total is not None:
            total_pages = len(document)
            total_over = total_pages > int(max_total)
            checks.append(
                FormatCheck(
                    id="FMT-total-pages",
                    check="total_page_limit",
                    passed=not total_over,
                    fatal_class=FatalClass.format_desk_reject if total_over and page_limit_fatal else None,
                    page=total_pages,
                    detail=_localized(
                        f"PDF page count: {total_pages} (limit {int(max_total)}).",
                        f"PDF 总页数：{total_pages}（上限 {int(max_total)}）。",
                    ),
                )
            )
        width = float(profile["page_width_pt"])
        height = float(profile["page_height_pt"])
        tolerance = float(profile["page_size_tolerance_pt"])
        bad_size_page = None
        for index in range(len(document)):
            page = document[index]
            page_width, page_height = page.get_size()
            if abs(page_width - width) > tolerance or abs(page_height - height) > tolerance:
                bad_size_page = index + 1
                break
        checks.append(
            FormatCheck(
                id="FMT-pagesize",
                check="page_size",
                passed=bad_size_page is None,
                clause_id=None if bad_size_page is None else "wc-4",
                page=bad_size_page,
                detail=_localized(
                    "Every page is US Letter."
                    if bad_size_page is None
                    else f"Page {bad_size_page} is not US Letter within {tolerance} pt.",
                    "每一页都是 US Letter。"
                    if bad_size_page is None
                    else f"第 {bad_size_page} 页不是容差 {tolerance} pt 内的 US Letter。",
                ),
            )
        )
        meta = document.get_metadata_dict() or {}
        author = str(meta.get("Author") or "").strip()
        author_bad = author.casefold() not in _ANON_AUTHORS
        metadata_fatal = bool(profile.get("anonymity_metadata_fatal", True))
        checks.append(
            FormatCheck(
                id="FMT-author-meta",
                check="anonymity_metadata",
                passed=not author_bad,
                fatal_class=FatalClass.format_desk_reject if author_bad and metadata_fatal else None,
                detail=_localized(
                    "PDF Author metadata is empty or anonymous."
                    if not author_bad
                    else "PDF Author metadata is non-empty.",
                    "PDF Author 元数据为空或为匿名。" if not author_bad else "PDF Author 元数据非空。",
                ),
            )
        )
        first_text = _page_text(document[0]) if len(document) else ""
        email = bool(_EMAIL.search(first_text))
        checks.append(
            FormatCheck(
                id="FMT-email",
                check="anonymity_email",
                passed=not email,
                fatal_class=FatalClass.format_desk_reject if email else None,
                page=1 if email else None,
                detail=_localized(
                    "Page 1 has no email address." if not email else "Page 1 contains an email address.",
                    "第 1 页没有电子邮件地址。" if not email else "第 1 页含有电子邮件地址。",
                ),
            )
        )
        markers = [str(item).casefold() for item in profile.get("template_markers", ["iclr"])]
        folded = first_text.casefold()
        template_ok = any(marker and marker in folded for marker in markers)
        marker_label = ", ".join(markers) if markers else "none"
        checks.append(
            FormatCheck(
                id="FMT-template",
                check="venue_template_marker",
                passed=template_ok,
                detail=_localized(
                    f"Page 1 names a venue template marker ({marker_label})."
                    if template_ok
                    else f"Page 1 does not name a venue template marker ({marker_label}).",
                    f"第 1 页写明了会场模板标记（{marker_label}）。"
                    if template_ok
                    else f"第 1 页没有写明会场模板标记（{marker_label}）。",
                ),
            )
        )
        margin = float(profile["min_margin_pt"])
        header_band = float(profile["header_band_pt"])
        footer_band = float(profile["footer_band_pt"])
        tight_page = None
        tight_edge = None
        for index in range(len(document)):
            page = document[index]
            page_width, page_height = page.get_size()
            for left, bottom, right, top in _char_boxes(page):
                if top > page_height - header_band or bottom < footer_band:
                    continue
                edges = {
                    "left": left,
                    "right": page_width - right,
                    "bottom": bottom,
                    "top": page_height - top,
                }
                for edge, gap in edges.items():
                    if gap < margin:
                        tight_page = index + 1
                        tight_edge = edge
                        break
                if tight_page is not None:
                    break
            if tight_page is not None:
                break
        checks.append(
            FormatCheck(
                id="FMT-margin",
                check="margins",
                passed=tight_page is None,
                clause_id=None if tight_page is None else "wc-4",
                page=tight_page,
                detail=_localized(
                    f"Body ink stays at least {margin:.0f} pt from the trimmed edges."
                    if tight_page is None
                    else f"Page {tight_page} places ink inside the {margin:.0f} pt {tight_edge} margin.",
                    f"正文墨迹距裁切边至少 {margin:.0f} pt。"
                    if tight_page is None
                    else f"第 {tight_page} 页的墨迹进入了 {margin:.0f} pt 的{tight_edge}页边距。",
                ),
            )
        )
        embedded, missing = _font_embedding(document[0]) if len(document) else (0, 0)
        fonts_ok = not (embedded == 0 and missing > 0)
        checks.append(
            FormatCheck(
                id="FMT-fonts",
                check="font_embedding",
                passed=fonts_ok,
                clause_id=None if fonts_ok else "wc-4",
                detail=_localized(
                    "Page 1 embeds at least one font."
                    if fonts_ok
                    else "Page 1 uses only non-embedded fonts.",
                    "第 1 页至少嵌入了一种字体。" if fonts_ok else "第 1 页只使用未嵌入的字体。",
                ),
            )
        )
        min_px = int(profile["min_page_image_px"])
        small_page = None
        for item in manifest.get("pages", []):
            if min(int(item["width_px"]), int(item["height_px"])) < min_px:
                small_page = int(item["page"])
                break
        checks.append(
            FormatCheck(
                id="FMT-legibility",
                check="page_raster_legibility",
                passed=small_page is None,
                clause_id=None if small_page is None else "wc-4",
                page=small_page,
                detail=_localized(
                    f"Rendered pages are at least {min_px} px on the short side."
                    if small_page is None
                    else f"Rendered page {small_page} is below {min_px} px on the short side.",
                    f"渲染页的短边至少 {min_px} 像素。"
                    if small_page is None
                    else f"渲染后的第 {small_page} 页短边低于 {min_px} 像素。",
                ),
            )
        )
        blank_caption_page = None
        if page_dir is not None:
            by_page = {int(item["page"]): item for item in manifest.get("pages", [])}
            for index in range(len(document)):
                page = document[index]
                lines = [line.strip() for line in _page_text(page).splitlines()]
                has_caption = any(_CAPTION_LINE.match(line) for line in lines)
                if not has_caption or _graphics_count(page) > 0:
                    continue
                record = by_page.get(index + 1)
                if record is None:
                    continue
                image_path = page_dir / record["filename"]
                if not image_path.is_file():
                    continue
                from PIL import Image

                with Image.open(image_path) as image:
                    fraction = _ink_fraction(image)
                if fraction < 0.02:
                    blank_caption_page = index + 1
                    break
        checks.append(
            FormatCheck(
                id="FMT-figure-ink",
                check="figure_table_legibility",
                passed=blank_caption_page is None,
                clause_id=None if blank_caption_page is None else "wc-4",
                page=blank_caption_page,
                detail=_localized(
                    "Caption pages contain graphics or enough ink to read."
                    if blank_caption_page is None
                    else f"Page {blank_caption_page} captions a figure or table and is nearly blank.",
                    "带图注或表注的页面含有图形或足够的墨迹。"
                    if blank_caption_page is None
                    else f"第 {blank_caption_page} 页有图注或表注，但页面几乎是空白。",
                ),
            )
        )
    finally:
        document.close()
    return checks


def captions_on_text(text: str) -> list[str]:
    return caption_inventory(text)


def finding_matches_caption(finding: VisualFinding) -> bool:
    caption = (finding.caption_excerpt or "").strip().casefold()
    mark = finding.visible_mark.strip().casefold()
    return bool(caption) and mark == caption
