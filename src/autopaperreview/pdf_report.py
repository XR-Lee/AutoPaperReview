"""Optional PDF rendering of Markdown review reports with internal evidence links."""

from __future__ import annotations

import html
import re
from pathlib import Path

# Link text cannot contain brackets, so `[[E2](#record-e2), [E3](#record-e3)]`
# keeps the outer citation brackets and still yields one target per id.
_LINK = re.compile(r"\[([^\[\]\n]+)\]\((#[^)]+)\)")
_HTML_ID = re.compile(r'<a id="([^"]+)"></a>')
_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
_CJK_FONTS = (
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0, 1),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0, 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0, 0),
)


def reportlab_available() -> bool:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


def _register_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, regular_index, bold_index in _CJK_FONTS:
        if not Path(path).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("APRBody", path, subfontIndex=regular_index))
            pdfmetrics.registerFont(TTFont("APRBold", path, subfontIndex=bold_index))
        except Exception:
            continue
        return "APRBody", "APRBold"
    return "Helvetica", "Helvetica-Bold"


def _destination(name: str):
    from reportlab.platypus.flowables import AnchorFlowable

    return AnchorFlowable(name)


def _markup(text: str) -> tuple[list[str], str]:
    dests = _HTML_ID.findall(text)
    stripped = _HTML_ID.sub("", text)
    stripped = (
        stripped.replace("–", "-")
        .replace("—", " - ")
        .replace("×", "x")
        .replace("≈", "~")
        .replace("≥", ">=")
        .replace("≤", "<=")
    )
    pieces: list[str] = []
    cursor = 0
    for match in _LINK.finditer(stripped):
        pieces.append(html.escape(stripped[cursor : match.start()]))
        pieces.append(
            f'<link href="{html.escape(match.group(2))}" color="#1f4e79">'
            f"<u>{html.escape(match.group(1))}</u></link>"
        )
        cursor = match.end()
    pieces.append(html.escape(stripped[cursor:]))
    escaped = "".join(pieces)

    def code_or_bold(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return f"<b>{match.group(1)}</b>"
        return f'<font color="#1f4e79">{match.group(2)}</font>'

    return dests, _INLINE.sub(code_or_bold, escaped)


def write_report_pdf(markdown: str, path: Path, *, title: str) -> None:
    """Write a linked PDF. Requires the optional reportlab extra."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate

    if not reportlab_available():
        raise RuntimeError("reportlab is not installed; pip install 'autopaperreview[pdf]'")

    body_font, bold_font = _register_fonts()
    ink = HexColor("#1a1a1a")
    muted = HexColor("#444444")
    rule = HexColor("#cccccc")
    accent = HexColor("#1f4e79")
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "apr-title",
            parent=base["Title"],
            fontName=bold_font,
            fontSize=16,
            leading=22,
            textColor=accent,
            spaceAfter=8,
            alignment=TA_LEFT,
        ),
        "h1": ParagraphStyle(
            "apr-h1", fontName=bold_font, fontSize=13, leading=18, textColor=accent, spaceBefore=14, spaceAfter=6
        ),
        "h2": ParagraphStyle(
            "apr-h2", fontName=bold_font, fontSize=12, leading=16, textColor=accent, spaceBefore=10, spaceAfter=4
        ),
        "h3": ParagraphStyle(
            "apr-h3", fontName=bold_font, fontSize=11, leading=15, textColor=ink, spaceBefore=8, spaceAfter=3
        ),
        "body": ParagraphStyle(
            "apr-body", fontName=body_font, fontSize=10, leading=15.5, textColor=ink, spaceAfter=6, alignment=TA_LEFT
        ),
        "bullet": ParagraphStyle(
            "apr-bullet",
            fontName=body_font,
            fontSize=10,
            leading=15.5,
            textColor=ink,
            leftIndent=12,
            spaceAfter=3,
            alignment=TA_LEFT,
        ),
        "sub": ParagraphStyle(
            "apr-sub",
            fontName=body_font,
            fontSize=10,
            leading=15.5,
            textColor=ink,
            leftIndent=24,
            spaceAfter=3,
            alignment=TA_LEFT,
        ),
        "lang": ParagraphStyle(
            "apr-lang",
            fontName=bold_font,
            fontSize=9,
            leading=12,
            textColor=HexColor("#666666"),
            spaceBefore=4,
            spaceAfter=2,
        ),
    }

    story: list = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index].rstrip()
        if not raw.strip():
            index += 1
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        if stripped.startswith("# "):
            dests, markup = _markup(stripped[2:].strip())
            for dest in dests:
                story.append(_destination(dest))
            story.append(Paragraph(markup, styles["title"]))
            story.append(HRFlowable(width="100%", thickness=0.6, color=accent, spaceAfter=8))
        elif stripped.startswith("#### "):
            dests, markup = _markup(stripped[5:].strip())
            for dest in dests:
                story.append(_destination(dest))
            story.append(Paragraph(markup, styles["h3"]))
        elif stripped.startswith("### "):
            heading = _HTML_ID.sub("", stripped[4:].strip())
            dests = _HTML_ID.findall(stripped)
            for dest in dests:
                story.append(_destination(dest))
            if heading in {"en", "zh-Hans"}:
                story.append(Paragraph(html.escape(heading), styles["lang"]))
            else:
                _, markup = _markup(heading)
                story.append(Paragraph(markup, styles["h2"]))
        elif stripped.startswith("## "):
            dests, markup = _markup(stripped[3:].strip())
            for dest in dests:
                story.append(_destination(dest))
            story.append(Paragraph(markup, styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=0.4, color=rule, spaceAfter=4))
        elif stripped.startswith("- "):
            dests, markup = _markup(stripped[2:].strip())
            for dest in dests:
                story.append(_destination(dest))
            style = styles["sub"] if indent >= 2 else styles["bullet"]
            story.append(Paragraph("• " + markup, style))
        else:
            buf = [stripped]
            while (
                index + 1 < len(lines)
                and lines[index + 1].strip()
                and not lines[index + 1].lstrip().startswith(("#", "- "))
            ):
                index += 1
                buf.append(lines[index].strip())
            dests, markup = _markup(" ".join(buf))
            for dest in dests:
                story.append(_destination(dest))
            story.append(Paragraph(markup, styles["body"]))
        index += 1

    path.parent.mkdir(parents=True, exist_ok=True)

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(body_font, 8)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 12 * mm, "AutoPaperReview · evidence links · no overall score")
        canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="AutoPaperReview",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
