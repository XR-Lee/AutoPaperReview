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
_LANGS = {"en": ("English", "#4338ca"), "zh-Hans": ("中文", "#0f766e")}
_STATUS_COLOR = {
    "fixture": "#a16207",
    "unresolved": "#a16207",
    "no-id": "#a16207",
    "match": "#15803d",
    "mismatch": "#b91c1c",
    "no-results": "#a16207",
    "no-CI/seed": "#b91c1c",
    "CI+seed": "#15803d",
    "no-excerpt": "#a16207",
    "not-matched": "#a16207",
    "overlap": "#0369a1",
    "distinct": "#15803d",
    "unchecked": "#a16207",
}
_STATUS_LEGEND_RE = re.compile(r"^\*\*([^*]+)\*\*(?:\s+(\S+))?\s+[—–-]\s+(.*)$")
_OUTCOME_RE = re.compile(r"^\*\*Outcome:\*\*\s*`?([A-Za-z_]+)`?(.*)$")
_SCORE_RE = re.compile(r"^\*\*([a-z_]+):\*\*\s*([0-9]+(?:\.[0-9]+)?)/10(.*)$")
_LANG_PREFIX_RE = re.compile(r"^\*\*(en|zh-Hans):\*\*\s*(.*)$")
_META_KEY_RE = re.compile(
    r"^\*\*(Location|Confidence|Routes|Sources|Evidence IDs|Old ID):\*\*\s*(.*)$"
)
_ISSUE_RE = re.compile(r"^[A-Z]{1,3}\d+\b")
_LANG_LINE_RE = re.compile(r"^\*\*(en|zh-Hans):\*\*\s*(.*)$")
_KICKER_RE = re.compile(r"^\*\*[^*]+\.\*\*\s*$")
_OUTCOME_COLOR = {
    "reject": "#b91c1c",
    "weak_reject": "#c2410c",
    "major_revision": "#c2410c",
    "minor_revision": "#a16207",
    "borderline": "#0369a1",
    "accept": "#15803d",
    "weak_accept": "#15803d",
    "not_a_fit": "#57534e",
}
_SECTION_ACCENT = {
    "Critical": "#b91c1c",
    "Major": "#c2410c",
    "Moderate": "#a16207",
    "Minor": "#57534e",
    "Editorial": "#78716c",
}

_FONT_CANDIDATES = (
    (
        ("/System/Library/Fonts/STHeiti Light.ttc", 1),
        ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
    ),
    (
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 6),
        ("/System/Library/Fonts/Supplemental/Songti.ttc", 2),
    ),
    (
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    ),
    (
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ),
    (
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ),
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

    for (reg_path, reg_idx), (bold_path, bold_idx) in _FONT_CANDIDATES:
        if not Path(reg_path).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("APRBody", reg_path, subfontIndex=reg_idx))
            if Path(bold_path).is_file():
                pdfmetrics.registerFont(TTFont("APRBold", bold_path, subfontIndex=bold_idx))
            else:
                pdfmetrics.registerFont(TTFont("APRBold", reg_path, subfontIndex=reg_idx))
            pdfmetrics.registerFontFamily(
                "APRBody",
                normal="APRBody",
                bold="APRBold",
                italic="APRBody",
                boldItalic="APRBold",
            )
        except Exception:
            continue
        return "APRBody", "APRBold"
    return "Helvetica", "Helvetica-Bold"


def _destination(name: str):
    from reportlab.platypus.flowables import AnchorFlowable

    return AnchorFlowable(name)


def _plain_heading(text: str) -> str:
    stripped = _HTML_ID.sub("", text)
    stripped = _INLINE.sub(lambda match: match.group(1) or match.group(2) or "", stripped)
    return " ".join(stripped.replace("/", " / ").split())


def _outline_key(kind: str, text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", _plain_heading(text)).strip("-").lower()
    return f"toc-{kind}-{(slug or 'section')[:72]}"


def _peek_issue_title(tokens: list[tuple[str, str, int]], start: int, heading: str) -> str:
    title = _plain_heading(heading)
    cursor = start + 1
    while cursor < len(tokens) and tokens[cursor][0] in {"blank", "bullet"}:
        kind, text, _indent = tokens[cursor]
        if kind == "bullet":
            match = _LANG_PREFIX_RE.match(text)
            if match and match.group(1) == "en" and match.group(2).strip():
                extra = match.group(2).strip()
                return f"{title}  {extra[:90]}"
        cursor += 1
    return title


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
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00b1", "+/-")
        .replace("\u2212", "-")
    )
    pieces: list[str] = []
    cursor = 0
    for match in _LINK.finditer(stripped):
        pieces.append(html.escape(stripped[cursor : match.start()]))
        pieces.append(
            f'<link href="{html.escape(match.group(2))}" color="#1d4ed8">'
            f"{html.escape(match.group(1))}</link>"
        )
        cursor = match.end()
    pieces.append(html.escape(stripped[cursor:]))
    escaped = "".join(pieces)

    def code_or_bold(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return f"<b>{match.group(1)}</b>"
        return f'<font color="#57534e">{match.group(2)}</font>'

    return dests, _INLINE.sub(code_or_bold, escaped)


def _chip_html(language: str) -> str:
    label, color = _LANGS.get(language, (language, "#57534e"))
    return f'<font color="{color}" size="8"><b>{html.escape(label)}</b></font>'


def _score_color(score: float) -> str:
    if score < 4.5:
        return "#b91c1c"
    if score < 6.5:
        return "#a16207"
    return "#15803d"


def _status_legend_markup(text: str) -> tuple[list[str], str] | None:
    match = _STATUS_LEGEND_RE.match(text)
    if not match:
        return None
    code, zh, rest = match.group(1), match.group(2), match.group(3)
    if code in {"en", "zh-Hans"} or code.endswith(":"):
        return None
    dests, rest_html = _markup(rest)
    color = _STATUS_COLOR.get(code, "#57534e")
    badge = f'<font color="{color}" size="9"><b>{html.escape(code)}</b></font>'
    if zh:
        badge += f'&nbsp;<font color="#57534e" size="9">{html.escape(zh)}</font>'
    return dests, badge + "&nbsp;&nbsp;" + rest_html


def _bullet_markup(text: str) -> tuple[list[str], str]:
    status = _status_legend_markup(text)
    if status is not None:
        return status
    match = _LANG_PREFIX_RE.match(text)
    if match:
        dests, body = _markup(match.group(2))
        return dests, _chip_html(match.group(1)) + "&nbsp;&nbsp;" + body
    match = _OUTCOME_RE.match(text)
    if match:
        token, rest = match.group(1), match.group(2)
        dests, rest_html = _markup(rest)
        color = _OUTCOME_COLOR.get(token, "#1c1917")
        label = html.escape(token.replace("_", " "))
        return dests, f'<font color="{color}" size="11"><b>{label}</b></font>{rest_html}'
    match = _SCORE_RE.match(text)
    if match:
        dim, raw_score, rest = match.group(1), match.group(2), match.group(3)
        dests, rest_html = _markup(rest)
        color = _score_color(float(raw_score))
        name = html.escape(dim.replace("_", " "))
        return (
            dests,
            f'<b>{name}</b>&nbsp;&nbsp;<font color="{color}" size="12"><b>{raw_score}</b></font>'
            f'<font color="#78716c" size="9"> / 10</font>{rest_html}',
        )
    match = _META_KEY_RE.match(text)
    if match:
        dests, rest_html = _markup(match.group(2))
        return dests, f'<font color="#78716c">{html.escape(match.group(1))}</font>&nbsp;&nbsp;{rest_html}'
    return _markup(text)


def _tokens(markdown: str) -> list[tuple[str, str, int]]:
    tokens: list[tuple[str, str, int]] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index].rstrip()
        if not raw.strip():
            tokens.append(("blank", "", 0))
            index += 1
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)
        if stripped.startswith("# "):
            tokens.append(("title", stripped[2:].strip(), indent))
        elif stripped.startswith("#### "):
            tokens.append(("h4", stripped[5:].strip(), indent))
        elif stripped.startswith("### "):
            heading = _HTML_ID.sub("", stripped[4:].strip())
            if heading in _LANGS:
                tokens.append(("lang", heading, indent))
            elif _ISSUE_RE.match(heading):
                tokens.append(("issue", stripped[4:].strip(), indent))
            else:
                tokens.append(("h3", stripped[4:].strip(), indent))
        elif stripped.startswith("## "):
            tokens.append(("h2", stripped[3:].strip(), indent))
        elif stripped.startswith("- "):
            tokens.append(("bullet", stripped[2:].strip(), indent))
        elif _LANG_LINE_RE.match(stripped):
            tokens.append(("langline", stripped, indent))
        else:
            buf = [stripped]
            while (
                index + 1 < len(lines)
                and lines[index + 1].strip()
                and not lines[index + 1].lstrip().startswith(("#", "- "))
                and not _LANG_LINE_RE.match(lines[index + 1].lstrip())
            ):
                index += 1
                buf.append(lines[index].strip())
            tokens.append(("body", " ".join(buf), indent))
        index += 1
    return tokens


def _is_lang_token(kind: str, text: str) -> bool:
    return kind == "langline" or (kind == "bullet" and _LANG_PREFIX_RE.match(text) is not None)


def write_report_pdf(markdown: str, path: Path, *, title: str) -> None:
    """Write a linked PDF. Requires the optional reportlab extra."""
    from .bilingual_coverage import require_bilingual_markdown

    require_bilingual_markdown(markdown)
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        CondPageBreak,
        Flowable,
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.platypus.tableofcontents import TableOfContents

    if not reportlab_available():
        raise RuntimeError("reportlab is not installed; pip install 'autopaperreview[pdf]'")

    body_font, bold_font = _register_fonts()
    ink = HexColor("#1c1917")
    muted = HexColor("#78716c")
    rule = HexColor("#e7e5e4")
    navy = HexColor("#1e3a5f")
    page_fill = HexColor("#fafaf9")
    left, right, top, bottom = 20 * mm, 20 * mm, 18 * mm, 18 * mm
    width = A4[0] - left - right

    def style(name: str, **kwargs) -> ParagraphStyle:
        base = dict(
            fontName=body_font,
            fontSize=10.5,
            leading=17.5,
            textColor=ink,
            wordWrap="CJK",
            splitLongWords=True,
            alignment=TA_LEFT,
            spaceAfter=0,
            spaceBefore=0,
        )
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    styles = {
        "title": style("apr-title", fontName=bold_font, fontSize=11, leading=14, textColor=muted, spaceAfter=1),
        "project": style("apr-project", fontName=bold_font, fontSize=20, leading=26, textColor=ink, spaceAfter=4),
        "meta": style("apr-meta", fontSize=9, leading=13, textColor=ink, spaceAfter=2),
        "hash": style("apr-hash", fontSize=7.5, leading=10, textColor=muted, spaceAfter=10),
        "h1": style(
            "apr-h1",
            fontName=bold_font,
            fontSize=13,
            leading=18,
            textColor=navy,
            spaceBefore=16,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h2": style(
            "apr-h2",
            fontName=bold_font,
            fontSize=11.5,
            leading=16,
            textColor=ink,
            spaceBefore=12,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "h3": style(
            "apr-h3",
            fontName=bold_font,
            fontSize=12,
            leading=16,
            textColor=ink,
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "issue": style(
            "apr-issue",
            fontName=bold_font,
            fontSize=12.5,
            leading=16,
            textColor=navy,
            spaceBefore=12,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": style("apr-body", spaceAfter=6),
        "bullet": style("apr-bullet", leftIndent=2, spaceAfter=4, leading=16.5),
        "sub": style("apr-sub", leftIndent=14, spaceAfter=3, fontSize=10, leading=16, textColor=ink),
        "kicker": style(
            "apr-kicker",
            fontName=bold_font,
            fontSize=8.5,
            leading=12,
            textColor=muted,
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "chip": style("apr-chip", fontSize=8, leading=11, spaceAfter=2),
        "card": style("apr-card", fontSize=10.5, leading=17, spaceAfter=2),
        "footer": style("apr-footer", fontSize=8, leading=10, textColor=muted),
    }

    class TocMark(Flowable):
        """Zero-size marker: named dest + outline + TOC notify."""

        def __init__(self, level: int, title: str, key: str) -> None:
            super().__init__()
            self.level = level
            self.title = title
            self.key = key
            self._toc_entry = (level, title, key)

        def wrap(self, availWidth, availHeight):
            return (0, 0)

        def draw(self) -> None:
            self.canv.bookmarkHorizontal(self.key, 0, 0)

    class ReviewDocTemplate(SimpleDocTemplate):
        def afterFlowable(self, flowable) -> None:
            entry = getattr(flowable, "_toc_entry", None)
            if not entry:
                return
            level, text, key = entry
            self.notify("TOCEntry", (level, text, self.page, key))
            try:
                self.canv.addOutlineEntry(text[:120], key, level=level, closed=level >= 1)
            except Exception:
                pass

    def outline_wrap(items: list, level: int, title: str, key: str):
        mark = TocMark(level, title, key)
        if len(items) == 1:
            items[0]._toc_entry = (level, title, key)
            return [mark, *items]
        grouped = KeepTogether([mark, *items])
        grouped._toc_entry = (level, title, key)
        grouped.keepWithNext = getattr(items[0], "keepWithNext", 0)
        return [grouped]

    def flow(kind_markup: tuple[list[str], str], paragraph_style: ParagraphStyle) -> list:
        dests, markup = kind_markup
        items: list = [_destination(dest) for dest in dests]
        items.append(Paragraph(markup, paragraph_style))
        return items

    def lang_card(pairs: list[tuple[str, list]]) -> list:
        if not pairs:
            return []
        fills = {"en": HexColor("#f5f6fb"), "zh-Hans": HexColor("#f4faf7")}
        bars = {"en": HexColor("#4338ca"), "zh-Hans": HexColor("#0f766e")}
        rows = []
        commands = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("BOX", (0, 0), (-1, -1), 0.3, rule),
        ]
        for row_index, (language, cell) in enumerate(pairs):
            rows.append([cell])
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), fills.get(language, HexColor("#f5f5f4"))))
            commands.append(("LINEBEFORE", (0, row_index), (0, row_index), 2.4, bars.get(language, navy)))
            if row_index:
                commands.append(("LINEABOVE", (0, row_index), (-1, row_index), 0.3, rule))
        table = Table(rows, colWidths=[width])
        table.setStyle(TableStyle(commands))
        table.hAlign = "LEFT"
        table.splitByRow = 0
        return [table, Spacer(1, 6)]

    def collect_lang_body(sequence: list[tuple[str, str, int]], start: int) -> tuple[list, int]:
        cell: list = []
        cursor = start
        while cursor < len(sequence) and sequence[cursor][0] in {"body", "bullet", "blank"}:
            kind, text, indent = sequence[cursor]
            if kind == "blank":
                cursor += 1
                continue
            if kind == "bullet":
                cell.extend(flow(_bullet_markup(text), styles["sub"] if indent >= 2 else styles["card"]))
            elif _KICKER_RE.match(text):
                cell.extend(flow(_markup(text), styles["kicker"]))
            else:
                cell.extend(flow(_markup(text), styles["card"]))
            cursor += 1
        return cell, cursor

    def collect_lang_prefix_run(sequence: list[tuple[str, str, int]], start: int) -> tuple[list[tuple[str, list]], int]:
        pairs: list[tuple[str, list]] = []
        cursor = start
        while cursor < len(sequence):
            kind, text, indent = sequence[cursor]
            if kind == "blank":
                cursor += 1
                continue
            match = _LANG_PREFIX_RE.match(text)
            if not _is_lang_token(kind, text) or not match:
                break
            dests, body = _markup(match.group(2))
            cell = [_destination(dest) for dest in dests]
            cell.append(Paragraph(_chip_html(match.group(1)), styles["chip"]))
            cell.append(Paragraph(body, styles["card"]))
            cursor += 1
            while cursor < len(sequence):
                nested_kind, nested_text, nested_indent = sequence[cursor]
                if nested_kind == "blank":
                    cursor += 1
                    continue
                if (
                    nested_kind != "bullet"
                    or nested_indent <= indent
                    or _LANG_PREFIX_RE.match(nested_text)
                ):
                    break
                cell.extend(flow(_markup(nested_text), styles["sub"]))
                cursor += 1
            pairs.append((match.group(1), cell))
        return pairs, cursor

    tokens = _tokens(markdown)
    story: list = []
    index = 0
    section_accent = navy
    current_issue: list | None = None

    def flush_issue() -> None:
        nonlocal current_issue
        if current_issue:
            grouped = KeepTogether(current_issue)
            entry = next((getattr(item, "_toc_entry", None) for item in current_issue), None)
            if entry:
                grouped._toc_entry = entry
            story.append(grouped)
            current_issue = None

    while index < len(tokens) and tokens[index][0] == "blank":
        index += 1

    if index < len(tokens) and tokens[index][0] == "title":
        heading = _HTML_ID.sub("", tokens[index][1])
        if ": " in heading:
            kind, project = heading.split(": ", 1)
        else:
            kind, project = heading, ""
        story.extend(flow(_markup(kind), styles["title"]))
        if project:
            story.extend(flow(_markup(project), styles["project"]))
        index += 1
        meta: dict[str, str] = {}
        while index < len(tokens) and tokens[index][0] in {"bullet", "blank"}:
            if tokens[index][0] == "bullet":
                raw = tokens[index][1]
                if ": " in raw:
                    key, value = raw.split(": ", 1)
                    meta[key.strip()] = value.strip().strip("`")
            index += 1
        bits: list[str] = []
        issues = meta.get("Issues")
        if issues:
            bits.append(f"<b>{html.escape(issues)}</b> issues")
        for label, key in (
            ("critical", "Critical"),
            ("major", "Major"),
            ("moderate", "Moderate"),
            ("minor", "Minor"),
            ("editorial", "Editorial"),
        ):
            value = meta.get(key)
            if value and value != "0":
                bits.append(f"{label} {html.escape(value)}")
        languages = meta.get("Languages", "")
        human = " / ".join(_LANGS[item.strip()][0] for item in languages.split(",") if item.strip() in _LANGS)
        if human:
            bits.append(html.escape(human))
        if bits:
            story.append(Paragraph("  ·  ".join(bits), styles["meta"]))
        sha = meta.get("Source SHA-256")
        if sha:
            story.append(Paragraph("source  " + html.escape(sha), styles["hash"]))
        story.append(HRFlowable(width="100%", thickness=1.15, color=navy, spaceBefore=2, spaceAfter=4))
        toc = TableOfContents()
        toc.dotsMinLevel = 0
        toc.levelStyles = [
            style(
                "toc-0",
                fontName=bold_font,
                fontSize=10.5,
                leading=16,
                leftIndent=0,
                firstLineIndent=0,
                spaceBefore=4,
                spaceAfter=1,
                textColor=ink,
            ),
            style(
                "toc-1",
                fontName=body_font,
                fontSize=9.5,
                leading=14,
                leftIndent=14,
                firstLineIndent=0,
                spaceBefore=1,
                spaceAfter=0,
                textColor=muted,
            ),
        ]
        story.append(Paragraph("Contents / 目录", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=rule, spaceAfter=6))
        story.append(toc)
        story.append(Spacer(1, 8))

    while index < len(tokens):
        kind, text, indent = tokens[index]
        if kind == "blank":
            index += 1
            continue
        if kind == "h2":
            flush_issue()
            story.append(CondPageBreak(88 * mm))
            heading = _HTML_ID.sub("", text)
            section_accent = navy
            for prefix, color in _SECTION_ACCENT.items():
                if heading.startswith(prefix):
                    section_accent = HexColor(color)
                    break
            dests, markup = _markup(text)
            heading_plain = _plain_heading(text)
            heading_key = _outline_key("h2", heading_plain)
            heading_group = KeepTogether(
                [
                    TocMark(0, heading_plain, heading_key),
                    *[_destination(d) for d in dests],
                    Paragraph(markup, styles["h1"]),
                    HRFlowable(width="100%", thickness=0.5, color=rule, spaceAfter=6),
                ]
            )
            heading_group._toc_entry = (0, heading_plain, heading_key)
            heading_group.keepWithNext = True
            story.append(heading_group)
            index += 1
            continue
        if kind == "h3":
            flush_issue()
            story.extend(flow(_markup(text), styles["h2"]))
            index += 1
            continue
        if kind == "h4":
            flush_issue()
            story.append(CondPageBreak(70 * mm))
            heading_items = flow(_markup(text), styles["h3"])
            index += 1
            bundled = list(heading_items)
            while index < len(tokens) and tokens[index][0] in {"bullet", "blank"}:
                if tokens[index][0] == "bullet":
                    bundled.extend(flow(_bullet_markup(tokens[index][1]), styles["bullet"]))
                index += 1
            if index < len(tokens) and tokens[index][0] == "lang":
                pairs: list[tuple[str, list]] = []
                while index < len(tokens) and tokens[index][0] == "lang":
                    language = tokens[index][1]
                    index += 1
                    cell, index = collect_lang_body(tokens, index)
                    pairs.append((language, [Paragraph(_chip_html(language), styles["chip"]), *cell]))
                bundled.extend(lang_card(pairs))
            story.append(KeepTogether(bundled))
            continue
        if kind == "issue":
            flush_issue()
            story.append(CondPageBreak(55 * mm))
            issue_style = ParagraphStyle(
                f"apr-issue-{index}",
                parent=styles["issue"],
                textColor=section_accent,
            )
            issue_title = _peek_issue_title(tokens, index, text)
            issue_key = _outline_key("issue", text)
            current_issue = [
                TocMark(1, issue_title, issue_key),
                *flow(_markup(text), issue_style),
            ]
            current_issue[0]._toc_entry = (1, issue_title, issue_key)
            index += 1
            continue
        if kind == "lang":
            flush_issue()
            pairs: list[tuple[str, list]] = []
            while index < len(tokens) and tokens[index][0] == "lang":
                language = tokens[index][1]
                index += 1
                cell, index = collect_lang_body(tokens, index)
                pairs.append((language, [Paragraph(_chip_html(language), styles["chip"]), *cell]))
            story.extend(lang_card(pairs))
            continue
        if kind == "langline" or (kind == "bullet" and _LANG_PREFIX_RE.match(text)):
            flush_issue()
            pairs, index = collect_lang_prefix_run(tokens, index)
            story.extend(lang_card(pairs))
            continue
        if kind == "bullet":
            items = flow(_bullet_markup(text), styles["sub"] if indent >= 2 else styles["bullet"])
            if current_issue is not None and _META_KEY_RE.match(text):
                meta_line: list[str] = []
                location_html = ""
                evidence_html = ""
                dests_all: list[str] = []
                while index < len(tokens) and tokens[index][0] in {"bullet", "blank"}:
                    if tokens[index][0] == "blank":
                        index += 1
                        continue
                    meta_match = _META_KEY_RE.match(tokens[index][1])
                    if not meta_match:
                        break
                    dests, rest_html = _markup(meta_match.group(2))
                    dests_all.extend(dests)
                    key = meta_match.group(1)
                    if key == "Location":
                        location_html = rest_html
                    elif key == "Evidence IDs":
                        evidence_html = rest_html
                    else:
                        meta_line.append(
                            f'<font color="#78716c">{html.escape(key)}</font> {rest_html}'
                        )
                    index += 1
                for dest in dests_all:
                    current_issue.append(_destination(dest))
                if location_html:
                    current_issue.append(
                        Paragraph(
                            f'<font color="#78716c">Location</font>  {location_html}',
                            styles["meta"],
                        )
                    )
                compact = list(meta_line)
                if evidence_html:
                    compact.append(f'<font color="#78716c">Evidence</font> {evidence_html}')
                if compact:
                    current_issue.append(Paragraph("  ·  ".join(compact), styles["meta"]))
                while index < len(tokens) and tokens[index][0] == "blank":
                    index += 1
                if index < len(tokens) and _is_lang_token(tokens[index][0], tokens[index][1]):
                    pairs, index = collect_lang_prefix_run(tokens, index)
                    current_issue.extend(lang_card(pairs))
                flush_issue()
                continue
            flush_issue()
            if text.startswith("`") and index + 1 < len(tokens):
                lookahead = index + 1
                while lookahead < len(tokens) and tokens[lookahead][0] == "blank":
                    lookahead += 1
                if lookahead < len(tokens) and _is_lang_token(
                    tokens[lookahead][0], tokens[lookahead][1]
                ):
                    block = list(items)
                    index = lookahead
                    pairs, index = collect_lang_prefix_run(tokens, index)
                    block.extend(lang_card(pairs))
                    story.append(KeepTogether(block))
                    continue
            if _SCORE_RE.match(text):
                block = list(items)
                index += 1
                while index < len(tokens) and tokens[index][0] == "blank":
                    index += 1
                if index < len(tokens) and _is_lang_token(tokens[index][0], tokens[index][1]):
                    pairs, index = collect_lang_prefix_run(tokens, index)
                    block.extend(lang_card(pairs))
                story.append(KeepTogether(block))
                continue
            story.extend(items)
            index += 1
            continue
        if kind == "body":
            flush_issue()
            if _KICKER_RE.match(text):
                story.extend(flow(_markup(text), styles["kicker"]))
            else:
                story.extend(flow(_markup(text), styles["body"]))
            index += 1
            continue
        index += 1

    flush_issue()
    path.parent.mkdir(parents=True, exist_ok=True)

    def decorate(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(page_fill)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canvas.setStrokeColor(navy)
        canvas.setLineWidth(2.2)
        canvas.line(0, A4[1], 0, 0)
        canvas.setFillColor(muted)
        canvas.setFont(body_font, 8)
        canvas.drawString(left, 11 * mm, "AutoPaperReview  ·  evidence-linked review  ·  no overall score")
        canvas.drawRightString(A4[0] - right, 11 * mm, str(doc.page))
        if doc.page == 1:
            canvas.showOutline()
        canvas.restoreState()

    ReviewDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=left,
        rightMargin=right,
        topMargin=top,
        bottomMargin=bottom,
        title=title,
        author="AutoPaperReview",
    ).multiBuild(story, onFirstPage=decorate, onLaterPages=decorate)
