"""Main-PDF input: page extraction, appendix detection, and the main-only project. Synthetic PDFs only."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from autopaperreview.cli import main
from autopaperreview.config import load_config
from autopaperreview.main_pdf import (
    ManuscriptPdfError,
    parse_main_pages,
    read_manuscript_pdf,
    strip_page_furniture,
)

try:
    import pymupdf
except ImportError:  # pragma: no cover - the pdf extra is installed in CI
    pymupdf = None

TITLE = "Synthetic Bead Counting with a Local Rule"
BODY = [
    [
        TITLE,
        "1. Introduction",
        "We count synthetic beads in generated images.",
        "The rule uses a fixed window and a threshold.",
        "Our contributions are a counting rule and an evaluation.",
    ],
    [
        "2. Method",
        "The window slides over the synthetic image.",
        "A bead is counted when the mean intensity passes the threshold.",
    ],
    [
        "3. Results",
        "The rule counts every bead in the generated scenes.",
        "Errors appear only where two beads overlap.",
    ],
]
REFERENCES = [
    "References",
    "[1] Synthetic Author. A synthetic reference about bead counting in images. 2020.",
    "[2] Another Synthetic Author. A second synthetic reference about thresholds. 2021.",
    "[3] Third Synthetic Author. A third synthetic reference about generated scenes. 2022.",
]
APPENDIX = [
    ["A. Additional derivations", "The window size follows from the bead radius."],
    ["B. Extra tables", "Table five lists synthetic counts for every scene."],
]
OUTLINE = [
    [1, "Introduction", 1],
    [1, "Method", 2],
    [1, "Results", 3],
    [1, "Additional derivations", 5],
    [1, "Extra tables", 6],
]


def _write_pdf(path: Path, pages: list[list[str]], toc: list[list] | None = None) -> Path:
    document = pymupdf.open()
    for lines in pages:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 90), "\n".join(lines), fontsize=10)
    if toc:
        document.set_toc(toc)
    document.save(str(path))
    document.close()
    return path


def _paper(path: Path, *, outline: bool = True, appendix: bool = True) -> Path:
    pages = [*BODY, REFERENCES]
    if appendix:
        pages.extend(APPENDIX)
    toc = OUTLINE if outline and appendix else (OUTLINE[:3] if outline else None)
    return _write_pdf(path, pages, toc)


def _init(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(["init", *argv])
    return code, (json.loads(out.getvalue()) if code == 0 else {})


@unittest.skipIf(pymupdf is None, "PyMuPDF is needed to write synthetic PDFs")
class MainPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_outline_entry_after_the_references_ends_the_main_paper(self) -> None:
        result = read_manuscript_pdf(_paper(self.root / "paper.pdf"), main_only=True, extractor="pymupdf")
        self.assertEqual(result.detection, "outline")
        self.assertEqual(result.references_page, 4)
        self.assertEqual(result.appendix_page, 5)
        self.assertEqual(result.last_page, 4)
        self.assertFalse(result.cut_inside_page)
        self.assertIn("[Page 4]", result.text)
        self.assertIn("A bead is counted when", result.text)
        self.assertIn("A third synthetic reference", result.text)
        self.assertNotIn("bead radius", result.text)
        self.assertNotIn("[Page 5]", result.text)
        record = result.record()
        self.assertEqual(record["pages_kept"], [1, 4])
        self.assertEqual(record["pages_dropped"], [5, 6])

    def test_appendix_heading_ends_the_main_paper_without_an_outline(self) -> None:
        pdf = _paper(self.root / "paper.pdf", outline=False)
        result = read_manuscript_pdf(pdf, main_only=True, extractor="pymupdf")
        self.assertEqual(result.detection, "heading")
        self.assertEqual(result.appendix_heading, "A. Additional derivations")
        self.assertEqual(result.last_page, 4)
        self.assertNotIn("bead radius", result.text)

    def test_appendix_inside_the_reference_page_cuts_the_text(self) -> None:
        pages = [*BODY, [*REFERENCES, "Appendix", "The window size follows from the bead radius."]]
        pdf = _write_pdf(self.root / "paper.pdf", pages)
        result = read_manuscript_pdf(pdf, main_only=True, extractor="pymupdf")
        self.assertEqual(result.detection, "heading")
        self.assertTrue(result.cut_inside_page)
        self.assertEqual(result.last_page, 4)
        self.assertIn("A third synthetic reference", result.text)
        self.assertNotIn("bead radius", result.text)

    def test_explicit_pages_override_detection(self) -> None:
        pdf = _paper(self.root / "paper.pdf")
        result = read_manuscript_pdf(pdf, main_only=True, main_pages="1-3", extractor="pymupdf")
        self.assertEqual(result.detection, "explicit")
        self.assertEqual(result.last_page, 3)
        self.assertNotIn("References", result.text)

    def test_a_paper_without_an_appendix_keeps_every_page(self) -> None:
        pdf = _paper(self.root / "paper.pdf", appendix=False)
        result = read_manuscript_pdf(pdf, main_only=True, extractor="pymupdf")
        self.assertEqual(result.detection, "none")
        self.assertEqual(result.last_page, 4)
        self.assertIsNone(result.record()["pages_dropped"])

    def test_without_main_only_every_page_is_kept(self) -> None:
        result = read_manuscript_pdf(_paper(self.root / "paper.pdf"), extractor="pymupdf")
        self.assertEqual(result.detection, "all_pages")
        self.assertEqual(result.last_page, 6)
        self.assertIn("bead radius", result.text)
        with self.assertRaises(ManuscriptPdfError):
            read_manuscript_pdf(self.root / "paper.pdf", main_pages="1-3")

    def test_a_file_that_is_not_a_pdf_is_rejected(self) -> None:
        text = self.root / "paper.pdf"
        text.write_text("plain text\n", encoding="utf-8")
        with self.assertRaises(ManuscriptPdfError):
            read_manuscript_pdf(text, main_only=True)


class FurnitureTests(unittest.TestCase):
    def test_running_header_line_numbers_and_page_numbers_are_removed(self) -> None:
        def body(index: int) -> list[str]:
            return [f"Body line {index} keeps a window of 128 pixels.", str(40 + index), f"Synthetic page {index} ends."]

        pages = []
        for index in range(5):
            numbers = [f"{index * 25 + offset:03d}" for offset in range(25)]
            lines = ["Synthetic Workshop Review Copy", *numbers[:12], *body(index), *numbers[12:], str(index + 1)]
            pages.append("\n".join(lines))
        cleaned, removed = strip_page_furniture(pages)
        self.assertEqual(removed, {"line_number": 125, "page_number": 5, "running_header": 5})
        for index, page in enumerate(cleaned):
            self.assertEqual(page.splitlines(), body(index))

    def test_sparse_three_digit_lines_stay(self) -> None:
        pages = ["Table of synthetic counts\n128\n256\nThe counts are synthetic."] * 3
        cleaned, removed = strip_page_furniture(pages)
        self.assertEqual(removed, {})
        self.assertEqual(cleaned, [page.strip() for page in pages])


class MainPagesSpecTests(unittest.TestCase):
    def test_page_ranges(self) -> None:
        self.assertEqual(parse_main_pages("10", 18), 10)
        self.assertEqual(parse_main_pages(" 1 - 10 ", 18), 10)
        for spec in ("2-5", "0", "19", "1-", "pages"):
            with self.subTest(spec=spec), self.assertRaises(ManuscriptPdfError):
                parse_main_pages(spec, 18)


@unittest.skipIf(pymupdf is None, "PyMuPDF is needed to write synthetic PDFs")
class MainPdfProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.pdf = _paper(self.root / "paper.pdf")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_main_only_project_has_one_route_and_no_artifacts_check(self) -> None:
        project = self.root / "review"
        code, result = _init(str(project), "--project-id", "synthetic-main", "--pdf", str(self.pdf), "--main-only")
        self.assertEqual(code, 0)
        self.assertEqual(result["template"], "main-pdf")
        self.assertEqual(result["manuscript_source"]["pages_kept"], [1, 4])
        self.assertEqual(result["manuscript_source"]["manuscript_pdf"], "manuscript.pdf")
        self.assertFalse((project / "artifacts").exists())
        with pymupdf.open(str(project / "manuscript.pdf")) as trimmed:
            self.assertEqual(trimmed.page_count, 4)
        text = (project / "manuscript.txt").read_text(encoding="utf-8")
        self.assertIn("A bead is counted when", text)
        self.assertNotIn("bead radius", text)
        record = json.loads((project / "source_pdf.json").read_text(encoding="utf-8"))
        self.assertEqual(record["detection"], "outline")
        loaded = load_config(project / "review.toml")
        self.assertEqual([route.id for route in loaded.config.routes], ["M0"])
        stage_types = {stage.type for stage in loaded.config.stages}
        self.assertNotIn("workspace_inspect", stage_types)
        self.assertNotIn("integrity", stage_types)
        self.assertIn("execute_review", stage_types)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["validate", str(project / "review.toml")]), 0)
        self.assertEqual(json.loads(out.getvalue())["errors"], [])

    def test_pdf_without_main_only_keeps_the_default_project(self) -> None:
        project = self.root / "review"
        code, result = _init(str(project), "--pdf", str(self.pdf))
        self.assertEqual(code, 0)
        self.assertEqual(result["template"], "default")
        self.assertTrue((project / "artifacts" / "results.json").is_file())
        self.assertIn("bead radius", (project / "manuscript.txt").read_text(encoding="utf-8"))
        self.assertEqual((project / "manuscript.pdf").read_bytes(), self.pdf.read_bytes())
        self.assertGreater(len(load_config(project / "review.toml").config.routes), 1)

    def test_main_pages_imply_main_only(self) -> None:
        project = self.root / "review"
        code, result = _init(str(project), "--pdf", str(self.pdf), "--main-pages", "1-3")
        self.assertEqual(code, 0)
        self.assertEqual(result["template"], "main-pdf")
        self.assertEqual(result["manuscript_source"]["detection"], "explicit")
        with pymupdf.open(str(project / "manuscript.pdf")) as trimmed:
            self.assertEqual(trimmed.page_count, 3)

    def test_main_only_without_a_pdf_fails_before_creating_files(self) -> None:
        project = self.root / "review"
        code, _result = _init(str(project), "--main-only")
        self.assertEqual(code, 2)
        self.assertFalse(project.exists())
        code, _result = _init(str(project), "--pdf", str(self.root / "missing.pdf"), "--main-only")
        self.assertEqual(code, 2)
        self.assertFalse(project.exists())


if __name__ == "__main__":
    unittest.main()
