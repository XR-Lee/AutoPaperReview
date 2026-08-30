from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from autopaperreview.export_gate import ExportGateParams, export_gate_errors
from autopaperreview.figures import caption_inventory, sibling_pdf
from autopaperreview.models import RouteKind
from autopaperreview.pipeline import run_pipeline

from tests.support import make_artifact, make_issue
from autopaperreview.models import ReviewPackage


class CaptionInventoryTests(unittest.TestCase):
    def test_collects_unique_figure_and_table_ids(self) -> None:
        text = "See Figure 1 and Fig. 1, then Table 2 and Figure 3."
        self.assertEqual(caption_inventory(text), ["Figure 1", "Table 2", "Figure 3"])

    def test_sibling_pdf_next_to_text_extract(self) -> None:
        with self._temporary() as workspace:
            source = workspace / "manuscript.txt"
            source.write_text("Figure 1\n", encoding="utf-8")
            self.assertIsNone(sibling_pdf(source))
            pdf = workspace / "manuscript.pdf"
            pdf.write_bytes(b"%PDF-1.1\n")
            self.assertEqual(sibling_pdf(source), pdf)

    def _temporary(self):
        import tempfile
        from contextlib import contextmanager

        @contextmanager
        def inner():
            with tempfile.TemporaryDirectory() as temporary:
                yield Path(temporary)

        return inner()


class FigurePacketTests(unittest.TestCase):
    def test_prompt_packet_requires_pdf_page_inspection_when_sibling_pdf_exists(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "manuscript.txt").write_text("See Figure 2 and Table 1.\n", encoding="utf-8")
            (workspace / "manuscript.pdf").write_bytes(b"%PDF-1.1\n")
            (workspace / "prompt.md").write_text("Review the paper.\n", encoding="utf-8")
            config_path = workspace / "review.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "figure-packet"
                    title = "Figure packet"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M6"
                    name = "Figure-grounded visual review"
                    kind = "visual"
                    prompt = "prompt.md"

                    [[stages]]
                    id = "packet"
                    type = "prompt_packet"
                    [stages.params]
                    route_ids = ["M6"]
                    include_source_text = true
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config_path)
            packet = (workspace / ".runs" / manifest.run_id / "stages" / "packet" / "M6.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("## Figures (mandatory inspection)", packet)
            self.assertIn("manuscript.pdf", packet)
            self.assertIn("Figure 2", packet)
            self.assertIn("Table 1", packet)
            self.assertIn("visible mark that is not in the caption", packet)


class FigureExportGateTests(unittest.TestCase):
    def test_visual_route_requires_a_figure_citation(self) -> None:
        package = ReviewPackage(
            project_id="gate",
            manuscript=make_artifact(),
            issues=[make_issue()],
        )
        errors = export_gate_errors(
            package,
            ExportGateParams(require_figures_if_visual_route=True),
            route_kinds=[RouteKind.visual],
        )
        self.assertTrue(any("figure/table" in item for item in errors))

        package.issues[0].location = "Figure 2, page 4"
        errors = export_gate_errors(
            package,
            ExportGateParams(require_figures_if_visual_route=True),
            route_kinds=[RouteKind.visual],
        )
        self.assertFalse(any("figure/table" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
