"""Annotated manuscript PDFs: highlights, margin tags, comments, and an index."""

from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from autopaperreview.cli import main
from autopaperreview.hashing import sha256_file
from autopaperreview.models import (
    Anchor,
    AnchorKind,
    CommentIntent,
    DimensionScore,
    EvidenceRecord,
    FirstReadNote,
    ReviewClaim,
    ReviewClaimKind,
    ReviewPackage,
    SarDimension,
    SourceRecord,
)
from autopaperreview.pdf_annotate import (
    AUTHOR,
    HIGHLIGHT_COLORS,
    MARKER_COLORS,
    annotate_pdf,
    pymupdf_available,
)
from autopaperreview.pipeline import run_pipeline

from tests.support import localized, make_artifact, make_issue

QUOTE_E1 = "The toy tracker reports one accuracy of 0.91 on the held-out cubes."
QUOTE_E2 = "A second seed of 0.42 was reported for the toy cubes."
QUOTE_E3 = "Figure 9 shows the cube path in a synthetic arena."
QUOTE_E4 = "Uncited control phrase zebra-quartz stays unmarked."
QUOTE_E5 = "stored region quote absent from the page"
QUOTE_E6 = "The algorithm stays inside the toy arena."
NOTE_E1 = "One reported accuracy on toy cubes does not show that 0.91 would repeat."
REGION_BBOX = [72.0, 220.0, 240.0, 248.0]


def _close(actual, expected) -> bool:
    if not actual or len(actual) < 3:
        return False
    return all(abs(float(actual[index]) - expected[index]) < 0.05 for index in range(3))


def _write_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 140), "The toy tracker reports one accuracy")
    first.insert_text((72, 156), "of 0.91 on the held-out cubes.")
    first.insert_text((72, 190), QUOTE_E4)
    second = document.new_page()
    second.insert_text((72, 140), QUOTE_E3)
    second.insert_text((72, 180), "The algo-")
    second.insert_text((72, 196), "rithm stays inside the toy arena.")
    document.save(path)
    document.close()


def _package() -> ReviewPackage:
    source = SourceRecord(id="S1", kind="manuscript", title="Toy manuscript", locator="manuscript.txt")
    evidence = [
        EvidenceRecord(
            id="E1",
            source_id="S1",
            locator="p. 1",
            claim="One toy accuracy is reported.",
            excerpt=QUOTE_E1,
        ),
        EvidenceRecord(
            id="E2",
            source_id="S1",
            locator="p. 2",
            claim="A second seed is not in the toy page.",
            excerpt=QUOTE_E2,
        ),
        EvidenceRecord(
            id="E3",
            source_id="S1",
            locator="page 2",
            claim="A synthetic arena path is drawn.",
            excerpt=QUOTE_E3,
        ),
        EvidenceRecord(
            id="E4",
            source_id="S1",
            locator="p. 1",
            claim="This control sentence is never cited.",
            excerpt=QUOTE_E4,
        ),
        EvidenceRecord(
            id="E5",
            source_id="S1",
            locator="p. 1",
            claim="A blank band is marked by a stored region.",
            excerpt=QUOTE_E5,
            anchor=Anchor(
                kind=AnchorKind.pdf_region,
                display="blank band",
                page=1,
                bbox=REGION_BBOX,
            ),
        ),
        EvidenceRecord(
            id="E6",
            source_id="S1",
            locator="p. 2",
            claim="A hyphenated word still locates.",
            excerpt=QUOTE_E6,
        ),
        EvidenceRecord(
            id="E10",
            source_id="S1",
            locator="Results",
            claim="No page number and no matching sentence.",
            excerpt="This quotation does not occur anywhere in the toy PDF.",
        ),
    ]
    return ReviewPackage(
        project_id="toy-annotate",
        manuscript=make_artifact(),
        sources=[source],
        evidence=evidence,
        issues=[
            make_issue(
                evidence_ids=["E1"],
                source_ids=["S1"],
                first_read=FirstReadNote(
                    paper_said=localized("The toy page reports one accuracy."),
                    explanation=localized(NOTE_E1),
                    quote=QUOTE_E1,
                    intent=CommentIntent.issue,
                    blocking=True,
                ),
            )
        ],
        claims=[
            ReviewClaim(
                id="W1",
                kind=ReviewClaimKind.weakness,
                text=localized("The evaluation reports a single accuracy, so the result is unchecked."),
                evidence_ids=["E1"],
            ),
            ReviewClaim(
                id="Q1",
                kind=ReviewClaimKind.question,
                text=localized("Where would a second toy-cube measurement be reported?"),
                evidence_ids=["E2"],
            ),
            ReviewClaim(
                id="S1",
                kind=ReviewClaimKind.strength,
                text=localized("The synthetic arena path is drawn, which gives the reader a concrete picture."),
                evidence_ids=["E3"],
            ),
            ReviewClaim(
                id="C1",
                kind=ReviewClaimKind.comment,
                text=localized("The stored box marks a blank band on the toy page."),
                evidence_ids=["E5"],
            ),
            ReviewClaim(
                id="S2",
                kind=ReviewClaimKind.strength,
                text=localized("The hyphenated algorithm sentence is still a visible span."),
                evidence_ids=["E6"],
            ),
            ReviewClaim(
                id="Q2",
                kind=ReviewClaimKind.question,
                text=localized("Which page was this sentence supposed to be on?"),
                evidence_ids=["E10"],
            ),
        ],
        dimension_scores=[
            DimensionScore(
                dimension=SarDimension.experimental_soundness,
                score=4,
                rationale=localized("A single toy accuracy is not a repeated measurement."),
                evidence_ids=["E1"],
            )
        ],
    )


def _annots(document):
    import pymupdf

    found = []
    for page_index, page in enumerate(document):
        for annot in page.annots() or []:
            found.append(
                {
                    "page": page_index,
                    "type": annot.type[1],
                    "info": dict(annot.info),
                    "colors": {key: list(value) for key, value in (annot.colors or {}).items()},
                    "rect": pymupdf.Rect(annot.rect),
                }
            )
    return found


def _for_id(rows, evidence_id: str):
    prefix = evidence_id + "\n"
    matched = []
    for row in rows:
        content = row["info"].get("content") or ""
        if content == evidence_id or content.startswith(prefix):
            matched.append(row)
    return matched


@unittest.skipUnless(pymupdf_available(), "pymupdf extra not installed")
class AnnotatedPdfTests(unittest.TestCase):
    def test_highlights_markers_comments_index_and_fallbacks(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manuscript.pdf"
            output = root / "annotated.pdf"
            _write_pdf(source)
            before = source.read_bytes()
            placements = annotate_pdf(source, _package(), output)
            self.assertEqual(source.read_bytes(), before)
            by_id = {item.evidence_id: item for item in placements}

            self.assertEqual(set(by_id), {"E1", "E2", "E3", "E5", "E6", "E10"})
            self.assertNotIn("E4", by_id)
            self.assertEqual(by_id["E1"].role, "weakness")
            self.assertEqual(by_id["E1"].placement, "text")
            self.assertIn("Weakness 1 (major)", by_id["E1"].field)
            self.assertEqual(by_id["E1"].page, 1)
            self.assertEqual(by_id["E1"].note, NOTE_E1)
            self.assertEqual(by_id["E2"].placement, "page-note")
            self.assertFalse(by_id["E2"].located)
            self.assertEqual(by_id["E2"].page, 2)
            self.assertEqual(by_id["E2"].role, "question")
            self.assertEqual(by_id["E3"].placement, "text")
            self.assertEqual(by_id["E3"].page, 2)
            self.assertEqual(by_id["E3"].role, "strength")
            self.assertEqual(by_id["E5"].placement, "region")
            self.assertTrue(by_id["E5"].located)
            self.assertEqual(by_id["E6"].placement, "text")
            self.assertEqual(by_id["E10"].placement, "page-note")
            self.assertEqual(by_id["E10"].page, 1)

            document = pymupdf.open(output)
            try:
                self.assertEqual(document.page_count, 3)
                rows = _annots(document)
                for evidence_id in ("E1", "E2", "E3", "E5", "E6", "E10"):
                    matched = _for_id(rows, evidence_id)
                    kinds = {row["type"] for row in matched}
                    self.assertIn("Text", kinds, evidence_id)
                    self.assertIn("FreeText", kinds, evidence_id)
                    for row in matched:
                        self.assertEqual(row["info"].get("title"), AUTHOR)
                    if by_id[evidence_id].placement == "page-note":
                        self.assertNotIn("Highlight", kinds, evidence_id)
                    else:
                        self.assertIn("Highlight", kinds, evidence_id)
                self.assertEqual(_for_id(rows, "E4"), [])

                e1_highlight = next(row for row in _for_id(rows, "E1") if row["type"] == "Highlight")
                self.assertTrue(_close(e1_highlight["colors"].get("stroke"), HIGHLIGHT_COLORS["weakness"]))
                self.assertIn("Weakness 1 (major)", e1_highlight["info"]["content"])
                self.assertIn(NOTE_E1, e1_highlight["info"]["content"])
                self.assertIn("Dimension: Experimental soundness", e1_highlight["info"]["content"])
                self.assertIn("Highlighted on page 1.", e1_highlight["info"]["content"])

                e2_note = next(row for row in _for_id(rows, "E2") if row["type"] == "Text")
                self.assertIn("Question 1", e2_note["info"]["content"])
                self.assertIn("Quoted span was not found", e2_note["info"]["content"])
                self.assertIn("Page-level note on page 2.", e2_note["info"]["content"])
                self.assertTrue(_close(e2_note["colors"].get("stroke"), MARKER_COLORS["question"]))
                e2_tag = next(row for row in _for_id(rows, "E2") if row["type"] == "FreeText")
                self.assertEqual(e2_tag["info"]["content"], "E2")

                e3_highlight = next(row for row in _for_id(rows, "E3") if row["type"] == "Highlight")
                self.assertTrue(_close(e3_highlight["colors"].get("stroke"), HIGHLIGHT_COLORS["strength"]))
                e6_highlight = next(row for row in _for_id(rows, "E6") if row["type"] == "Highlight")
                self.assertTrue(_close(e6_highlight["colors"].get("stroke"), HIGHLIGHT_COLORS["strength"]))
                self.assertEqual(e6_highlight["page"], 1)

                e5_highlight = next(row for row in _for_id(rows, "E5") if row["type"] == "Highlight")
                self.assertIn("stored region", e5_highlight["info"]["content"])
                self.assertTrue(_close(e5_highlight["colors"].get("stroke"), HIGHLIGHT_COLORS["neutral"]))
                self.assertTrue(e5_highlight["rect"].intersects(pymupdf.Rect(*REGION_BBOX)))

                first_text = document[0].get_text()
                self.assertIn("Legend", first_text)
                self.assertIn("Weakness", first_text)
                self.assertIn("Strength", first_text)
                self.assertIn("Question", first_text)

                index = document[-1]
                index_text = index.get_text()
                self.assertIn("Evidence index", index_text)
                self.assertIn("E1", index_text)
                self.assertIn("E2", index_text)
                self.assertIn("E10", index_text)
                self.assertIn("Weakness 1 (major)", index_text)
                self.assertIn("page note", index_text)
                self.assertNotIn("zebra-quartz", index_text)
                links = index.get_links()
                targets = {link.get("page") for link in links if link.get("kind") == pymupdf.LINK_GOTO}
                self.assertIn(0, targets)
                self.assertIn(1, targets)
            finally:
                document.close()

    def test_refuses_to_overwrite_the_source_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "manuscript.pdf"
            _write_pdf(source)
            with self.assertRaises(ValueError):
                annotate_pdf(source, _package(), source)

    def test_cli_reannotates_a_finished_run_without_a_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "manuscript.pdf"
            _write_pdf(source)
            (workspace / "manuscript.txt").write_text("toy manuscript\n", encoding="utf-8")
            package = _package()
            run_dir = workspace / ".runs" / "toy-annotate"
            report_dir = run_dir / "stages" / "report"
            report_dir.mkdir(parents=True)
            package_path = report_dir / "review_package.json"
            package_path.write_text(
                json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (workspace / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "toy-annotate"
                    title = "Toy annotate"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "report"
                    type = "report"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["annotate", str(workspace / "review.toml")])
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            output = run_dir / "annotated_manuscript.pdf"
            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["output"], str(output))
            self.assertEqual(payload["page_notes"], 2)
            self.assertGreaterEqual(payload["highlighted"], 4)
            self.assertTrue(output.is_file())
            project_copy = workspace / "annotated_manuscript.pdf"
            self.assertEqual(payload["project_copy"], str(project_copy))
            self.assertEqual(project_copy.read_bytes(), output.read_bytes())
            self.assertNotIn(QUOTE_E1, payload["items"][0].get("note", "") + json.dumps(payload["items"]))
            ids = [item["id"] for item in payload["items"]]
            self.assertEqual(ids, ["E1", "E2", "E3", "E5", "E6", "E10"])

    def test_report_stage_writes_annotated_pdf_and_leaves_the_review_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manuscript = workspace / "manuscript.txt"
            manuscript.write_text("toy manuscript\n", encoding="utf-8")
            _write_pdf(workspace / "manuscript.pdf")
            package = _package().model_copy(
                update={
                    "manuscript": make_artifact(
                        sha256=sha256_file(manuscript),
                        size_bytes=manuscript.stat().st_size,
                    )
                }
            )
            (workspace / "package.json").write_text(
                json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            config = workspace / "review.toml"
            config.write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "toy-annotate"
                    title = "Toy annotate"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[stages]]
                    id = "issues"
                    type = "import_issues"
                    [stages.params]
                    path = "package.json"

                    [[stages]]
                    id = "report"
                    type = "report"
                    depends_on = ["issues"]
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            source_hash = sha256_file(workspace / "manuscript.pdf")
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "report"
            markdown = (stage_dir / "review_report.md").read_text(encoding="utf-8")
            summary = json.loads((stage_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("Weakness", markdown)
            self.assertIn("[[E1](#record-e1)]", markdown)
            self.assertNotIn(AUTHOR, markdown)
            self.assertNotIn("Quoted span was not found", markdown)
            self.assertTrue(summary["annotated_pdf"])
            self.assertGreaterEqual(summary["annotated_pdf_highlighted"], 4)
            self.assertEqual(summary["annotated_pdf_page_notes"], 2)
            annotated = stage_dir / "annotated_manuscript.pdf"
            self.assertTrue(annotated.is_file())
            self.assertTrue((workspace / "annotated_manuscript.pdf").is_file())
            released = json.loads((stage_dir / "review_package.json").read_text(encoding="utf-8"))
            self.assertEqual(released["dimension_scores"][0]["score"], 4)
            self.assertEqual(sha256_file(workspace / "manuscript.pdf"), source_hash)

    def test_index_pages_survive_many_evidence_items(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manuscript.pdf"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((72, 140), "A toy page with no quoted evidence.")
            document.save(source)
            document.close()
            source_record = SourceRecord(id="S1", kind="manuscript", title="Toy manuscript", locator="manuscript.txt")
            evidence = [
                EvidenceRecord(
                    id=f"E{number}",
                    source_id="S1",
                    locator="p. 1",
                    claim=f"Toy claim {number}.",
                    excerpt="absent",
                )
                for number in range(1, 46)
            ]
            package = ReviewPackage(
                project_id="toy-index",
                manuscript=make_artifact(),
                sources=[source_record],
                evidence=evidence,
                claims=[
                    ReviewClaim(
                        id="C1",
                        kind=ReviewClaimKind.comment,
                        text=localized("Every toy id is listed once."),
                        evidence_ids=[item.id for item in evidence],
                    )
                ],
            )
            output = root / "annotated.pdf"
            placements = annotate_pdf(source, package, output)
            self.assertEqual(len(placements), 45)
            document = pymupdf.open(output)
            try:
                self.assertGreaterEqual(document.page_count, 4)
                index_text = "\n".join(page.get_text() for page in document)
                self.assertIn("Evidence index", document[1].get_text())
                self.assertNotIn("Evidence\xa0index", document[1].get_text())
                self.assertIn("E1", index_text)
                self.assertIn("E45", index_text)
            finally:
                document.close()

    def test_line_numbers_fuzzy_quotes_and_margin_side(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manuscript.pdf"
            document = pymupdf.open()
            page = document.new_page()
            rows = (
                (140, "101", "The toy tracker reports one accuracy"),
                (160, "102", "of 0.91 on held-out cubes."),
                (180, "103", "The method uses a single synthetic seed for the toy cubes."),
                (200, "104", "The loss uses an arrow a -> b in the toy formula."),
                (220, "105", "The algo-"),
                (236, "106", "rithm stays inside the toy arena."),
            )
            for y, number, text in rows:
                page.insert_text((36, y), number)
                page.insert_text((72, y), text)
            second = document.new_page()
            second.insert_text((320, 180), "The wide left margin holds this toy sentence.")
            document.save(source)
            document.close()

            quotes = {
                "E21": "The toy tracker reports one accuracy of 0.91 on held-out cubes.",
                "E22": "101 The toy tracker reports one accuracy 102 of 0.91 on held-out cubes.",
                "E23": "The method uses one single synthetic seed for the toy cubes.",
                "E24": "The loss uses an arrow (a → b) in the toy formula.",
                "E25": "105 The algo-\nrithm stays 106 inside the toy arena.",
                "E26": "The wide left margin holds this toy sentence.",
            }
            source_record = SourceRecord(id="S1", kind="manuscript", title="Toy manuscript", locator="manuscript.txt")
            evidence = [
                EvidenceRecord(
                    id=evidence_id,
                    source_id="S1",
                    locator="p. 2" if evidence_id == "E26" else "p. 1",
                    claim="A toy sentence is cited.",
                    excerpt=excerpt,
                )
                for evidence_id, excerpt in quotes.items()
            ]
            package = ReviewPackage(
                project_id="toy-lines",
                manuscript=make_artifact(),
                sources=[source_record],
                evidence=evidence,
                claims=[
                    ReviewClaim(
                        id="C1",
                        kind=ReviewClaimKind.comment,
                        text=localized("The toy lines are cited together."),
                        evidence_ids=list(quotes),
                    )
                ],
            )
            output = root / "annotated.pdf"
            placements = annotate_pdf(source, package, output)
            by_id = {item.evidence_id: item for item in placements}
            for evidence_id in quotes:
                self.assertEqual(by_id[evidence_id].placement, "text", evidence_id)
            self.assertEqual(by_id["E26"].page, 2)

            document = pymupdf.open(output)
            try:
                rows = _annots(document)
                first_words = [
                    word
                    for word in document[0].get_text("words")
                    if not str(word[4]).startswith("E")
                ]
                body_right = max(float(word[2]) for word in first_words if float(word[0]) >= 60)
                line_numbers = [word for word in first_words if str(word[4]).isdigit()]
                self.assertGreaterEqual(len(line_numbers), 6)
                for evidence_id in ("E21", "E22", "E23", "E24", "E25"):
                    tag = next(row for row in _for_id(rows, evidence_id) if row["type"] == "FreeText")
                    highlight = next(row for row in _for_id(rows, evidence_id) if row["type"] == "Highlight")
                    self.assertEqual(tag["page"], 0, evidence_id)
                    self.assertGreater(tag["rect"].x0, body_right, evidence_id)
                    for word in first_words:
                        word_rect = pymupdf.Rect(word[0], word[1], word[2], word[3])
                        self.assertFalse(tag["rect"].intersects(word_rect), evidence_id)
                    for word in line_numbers:
                        word_rect = pymupdf.Rect(word[0], word[1], word[2], word[3])
                        self.assertFalse(highlight["rect"].intersects(word_rect), evidence_id)
                second_words = [
                    word
                    for word in document[1].get_text("words")
                    if not str(word[4]).startswith("E")
                ]
                body_left = min(float(word[0]) for word in second_words)
                left_tag = next(row for row in _for_id(rows, "E26") if row["type"] == "FreeText")
                self.assertLess(left_tag["rect"].x1, body_left)
                for word in second_words:
                    word_rect = pymupdf.Rect(word[0], word[1], word[2], word[3])
                    self.assertFalse(left_tag["rect"].intersects(word_rect))
            finally:
                document.close()

    def test_review_markdown_sets_role_colours(self) -> None:
        import pymupdf

        review = textwrap.dedent(
            """
            ## Strengths / 优点
            - The toy setup is clear. [E8]

            ## Weaknesses / 不足
            - The single accuracy is not enough (major). [E7]

            ## 严重问题
            - This heading is not a question section. [E11]

            ## Detailed Comments
            - A side remark stays neutral. [E12]

            ## Questions / 问题
            - Where is the second seed? [E9]
            """
        ).strip() + "\n"
        quotes = {
            "E7": "The single accuracy is not enough for the toy.",
            "E8": "The toy setup is clear and small.",
            "E9": "Where is the second seed in the toy run?",
            "E11": "A serious heading does not recolor this toy line.",
            "E12": "A side comment stays on the toy page.",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "manuscript.pdf"
            document = pymupdf.open()
            page = document.new_page()
            for index, excerpt in enumerate(quotes.values()):
                page.insert_text((72, 140 + index * 22), excerpt)
            document.save(source)
            document.close()
            source_record = SourceRecord(id="S1", kind="manuscript", title="Toy manuscript", locator="manuscript.txt")
            evidence = [
                EvidenceRecord(
                    id=evidence_id,
                    source_id="S1",
                    locator="p. 1",
                    claim="A toy sentence is cited.",
                    excerpt=excerpt,
                )
                for evidence_id, excerpt in quotes.items()
            ]
            package = ReviewPackage(
                project_id="toy-roles",
                manuscript=make_artifact(),
                sources=[source_record],
                evidence=evidence,
                claims=[
                    ReviewClaim(
                        id="C1",
                        kind=ReviewClaimKind.comment,
                        text=localized("The package itself only has a neutral comment."),
                        evidence_ids=list(quotes),
                    )
                ],
            )
            output = root / "annotated.pdf"
            placements = annotate_pdf(source, package, output, review_markdown=review)
            by_id = {item.evidence_id: item for item in placements}
            self.assertEqual(by_id["E7"].role, "weakness")
            self.assertEqual(by_id["E8"].role, "strength")
            self.assertEqual(by_id["E9"].role, "question")
            self.assertEqual(by_id["E11"].role, "neutral")
            self.assertEqual(by_id["E12"].role, "neutral")
            self.assertIn("Weakness 1 (major)", by_id["E7"].field)
            self.assertIn("not enough", by_id["E7"].note)

            document = pymupdf.open(output)
            try:
                rows = _annots(document)
                expected = {
                    "E7": HIGHLIGHT_COLORS["weakness"],
                    "E8": HIGHLIGHT_COLORS["strength"],
                    "E9": HIGHLIGHT_COLORS["question"],
                    "E11": HIGHLIGHT_COLORS["neutral"],
                    "E12": HIGHLIGHT_COLORS["neutral"],
                }
                for evidence_id, color in expected.items():
                    highlight = next(row for row in _for_id(rows, evidence_id) if row["type"] == "Highlight")
                    self.assertTrue(_close(highlight["colors"].get("stroke"), color), evidence_id)
                    self.assertEqual(highlight["info"].get("title"), AUTHOR)
            finally:
                document.close()

            review_path = root / "review.md"
            review_path.write_text(review, encoding="utf-8")
            package_path = root / "package.json"
            package_path.write_text(
                json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            cli_output = root / "cli.pdf"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "annotate",
                        str(package_path),
                        "--pdf",
                        str(source),
                        "--review",
                        str(review_path),
                        "--output",
                        str(cli_output),
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            roles = {item["id"]: item["role"] for item in payload["items"]}
            self.assertEqual(roles["E7"], "weakness")
            self.assertEqual(roles["E8"], "strength")
            self.assertEqual(roles["E9"], "question")
            self.assertEqual(roles["E11"], "neutral")
