"""Page rendering and the ICLR format checklist. Synthetic PDFs only."""

from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.models import ReviewPackage, RouteKind, VisualFinding, VisualFindingKind
from autopaperreview.pipeline import run_pipeline
from autopaperreview.visual import format_check_pdf, load_profile, render_pdf_pages
from tests.support import make_artifact, make_issue


def _pdf(path: Path, pages: list[tuple[str, float, float]], *, author: str = "") -> None:
    from reportlab.pdfgen import canvas

    document = canvas.Canvas(str(path), pagesize=(612, 792))
    if author:
        document.setAuthor(author)
    for text, x, y in pages:
        document.drawString(x, y, text)
        document.showPage()
    document.save()


class FormatProfileTests(unittest.TestCase):
    def test_profile_pins_the_nine_page_submission_limit(self) -> None:
        profile = load_profile("iclr2027")
        self.assertEqual(profile["max_content_pages"], 9)
        self.assertEqual(profile["page_width_pt"], 612)
        self.assertTrue(profile["anonymous"])
        self.assertIn("2027", profile["source"])

    def test_over_long_main_text_is_a_desk_reject(self) -> None:
        try:
            import reportlab  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("visual and pdf extras are required")
        with self._temporary() as directory:
            pdf = directory / "manuscript.pdf"
            _pdf(pdf, [("Body line", 72, 400)] * 10)
            manifest = render_pdf_pages(pdf, directory / "pages", dpi=72)
            checks = {item.id: item for item in format_check_pdf(pdf, manifest, load_profile(), page_dir=directory / "pages")}
            self.assertFalse(checks["FMT-pages"].passed)
            self.assertEqual(checks["FMT-pages"].fatal_class.value, "format_desk_reject")

    def test_references_heading_stops_the_page_count(self) -> None:
        try:
            import reportlab  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("visual and pdf extras are required")
        with self._temporary() as directory:
            pdf = directory / "manuscript.pdf"
            _pdf(
                pdf,
                [("Introduction", 72, 400), ("References", 72, 700), ("Appendix note", 72, 400)],
            )
            manifest = render_pdf_pages(pdf, directory / "pages", dpi=72)
            checks = {item.id: item for item in format_check_pdf(pdf, manifest, load_profile(), page_dir=directory / "pages")}
            self.assertTrue(checks["FMT-pages"].passed)

    def test_render_hash_is_stable(self) -> None:
        try:
            import reportlab  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("visual and pdf extras are required")
        with self._temporary() as directory:
            pdf = directory / "manuscript.pdf"
            _pdf(pdf, [("Hello", 72, 400)])
            first = render_pdf_pages(pdf, directory / "a", dpi=72)
            second = render_pdf_pages(pdf, directory / "b", dpi=72)
            self.assertEqual(first["pages"][0]["sha256"], second["pages"][0]["sha256"])

    def test_caption_paraphrase_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            VisualFinding(
                id="V1",
                page=1,
                bbox=[0, 0, 1, 1],
                page_image_sha256="a" * 64,
                kind=VisualFindingKind.legibility,
                visible_mark="same words",
                caption_excerpt="same words",
            )

    def test_page_image_gate_requires_a_hashed_finding(self) -> None:
        package = ReviewPackage(
            project_id="gate",
            manuscript=make_artifact(),
            issues=[make_issue(location="Figure 1, page 2")],
        )
        errors = export_gate_errors(
            package,
            ExportGateParams(require_page_image_on_visual=True),
            route_kinds=[RouteKind.visual],
        )
        self.assertTrue(any("page-image" in item for item in errors))
        package.visual_findings = [
            VisualFinding(
                id="V1",
                page=2,
                bbox=[10, 10, 40, 40],
                page_image_sha256="b" * 64,
                kind=VisualFindingKind.axis_legend,
                visible_mark="a labeled axis",
                figure_id="Figure 1",
                caption_excerpt="Plot of the result.",
            )
        ]
        errors = export_gate_errors(
            package,
            ExportGateParams(require_page_image_on_visual=True),
            route_kinds=[RouteKind.visual],
        )
        self.assertFalse(any("page-image" in item or "paraphrase" in item for item in errors))

    def test_pipeline_render_and_inspect(self) -> None:
        try:
            import reportlab  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("visual and pdf extras are required")
        import textwrap

        with self._temporary() as directory:
            _pdf(directory / "manuscript.pdf", [("Hello from a synthetic page", 72, 400)])
            (directory / "manuscript.txt").write_text("See Figure 1.\n", encoding="utf-8")
            (directory / "prompt.md").write_text("Inspect the page image.\n", encoding="utf-8")
            (directory / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "visual-pipeline"
                    title = "Visual pipeline"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M9"
                    name = "Layout QA"
                    kind = "visual"
                    prompt = "prompt.md"

                    [[stages]]
                    id = "ingest"
                    type = "ingest"

                    [[stages]]
                    id = "pages"
                    type = "render_pages"
                    depends_on = ["ingest"]
                    [stages.params]
                    dpi = 72

                    [[stages]]
                    id = "format"
                    type = "format_check"
                    depends_on = ["pages"]

                    [[stages]]
                    id = "inspect"
                    type = "visual_inspect"
                    depends_on = ["pages"]
                    [stages.params]
                    adapter = "tests.fake_visual:GoodInspector"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(directory / "review.toml")
            run_dir = directory / ".runs" / manifest.run_id
            page = run_dir / "stages" / "pages" / "pages" / "page-001.png"
            self.assertTrue(page.is_file())
            package_text = (run_dir / "stages" / "inspect" / "package.json").read_text(encoding="utf-8")
            self.assertIn("tick labeled with a unit", package_text)
            self.assertIn(page.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def _temporary(self):
        import tempfile
        from contextlib import contextmanager

        @contextmanager
        def inner():
            with tempfile.TemporaryDirectory() as temporary:
                yield Path(temporary)

        return inner()


if __name__ == "__main__":
    unittest.main()
