"""Regressions for the CVPR tier R0 anonymizer and probe bugs. Synthetic text only."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from autopaperreview.cvpr_anonymize import anonymize, leak_counts, leak_failed
from autopaperreview.cvpr_probe import (
    ProbeError,
    assemble_batches,
    extract_abstract,
    extraction_rate_exceeded,
    generate_distinctive_passage,
    run_probe,
    synthetic_decoys,
    target_from_text,
)
from autopaperreview.cvpr_tier import (
    SampleManifest,
    SamplePaper,
    YearSummary,
    list_catalog,
    normalize_title,
)
from autopaperreview.hashing import sha256_bytes

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "synthetic" / "cvpr_tier"
GAP = " " * 8
ABSTRACT = "This note counts eight lattice cells in reading order."
RIGHT_COLUMN = "They stay on the right side."
BODY = (
    "The lattice counter recovers paired cells from a quiet grid "
    "and records one comparison across every split."
)
CATALOG_TITLES = (
    "Purple Widget Counting under Moonlight",
    "Orange Basket Folding with Quiet Motors",
)


def _columns(rows: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for left, right in rows:
        if right:
            lines.append(f"{left}{GAP}{right}")
        else:
            lines.append(left)
    return "\n".join(lines) + "\n"


def _two_column_note() -> str:
    return _columns(
        [
            ("Lattice Counter for Column Fixtures", ""),
            ("", ""),
            ("Ada North", ""),
            ("Example Academy", ""),
            ("", ""),
            ("Abstract", "1 Introduction"),
            ("This note counts eight", "Earlier columns are not the abstract."),
            ("lattice cells in reading order.", RIGHT_COLUMN),
            ("1 Introduction", ""),
            ("The lattice counter recovers paired", ""),
            ("cells from a quiet grid and records", ""),
            ("one comparison across every split.", ""),
        ]
    )


def _source() -> YearSummary:
    return YearSummary(
        year=2024,
        virtual_json="fixture://virtual",
        openaccess="fixture://openaccess",
        complete=True,
        label_field="decision",
        tiers={"oral": 1, "highlight": 1, "poster": 1},
        unlabeled=0,
        with_pdf=1,
        notes="synthetic",
    )


def _write_manifest(root: Path, papers: list[str]) -> None:
    manifest = SampleManifest(
        seed=11,
        n_per_tier={"oral": 1, "highlight": 1, "poster": 1},
        years=[2024],
        draws={
            "oral": {"available": 1, "requested": 1, "drawn": 1},
            "highlight": {"available": 1, "requested": 1, "drawn": 1},
            "poster": {"available": 1, "requested": 1, "drawn": 1},
        },
        sources=[_source()],
        papers=[SamplePaper(paper_id=paper_id, year=2024, pdf_url="fixture://synthetic") for paper_id in papers],
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample_manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "catalog.json").write_text(json.dumps({"note": "ZZ-LABEL-SENTINEL-9f3a"}) + "\n", encoding="utf-8")
    (root / "raw").mkdir(parents=True, exist_ok=True)


def _good_paper(index: int, abstract: str | None = None) -> str:
    sentence = abstract or ABSTRACT
    head, tail = sentence.split(" eight ", 1)
    return _columns(
        [
            (f"Lattice Note Number {index}", ""),
            ("", ""),
            ("Ada North", ""),
            ("Example Academy", ""),
            ("", ""),
            ("Abstract", "1 Introduction"),
            (f"{head} eight", "Earlier columns stay apart."),
            (tail, ""),
            ("1 Introduction", ""),
            ("The lattice counter recovers paired cells from a quiet grid.", ""),
        ]
    )


def _answer_script(path: Path, state: Path, *, fail_on: int | None = None) -> None:
    path.write_text(
        "import json, re, sys\n"
        "from pathlib import Path\n"
        "text = sys.stdin.read()\n"
        f"state_path = Path({str(state)!r})\n"
        "count = int(state_path.read_text()) if state_path.is_file() else 0\n"
        "count += 1\n"
        "state_path.write_text(str(count))\n"
        + (f"if count == {fail_on}:\n    raise SystemExit(1)\n" if fail_on is not None else "")
        + "ids = re.findall(r'handoff_id: ([0-9a-f]{12})', text)\n"
        "items = [\n"
        "    {'handoff_id': hid, 'title': 'Lattice Note', 'authors': ['Ada North'],\n"
        "     'venue': 'CVPR', 'year': 2024, 'tier': 'unknown', 'choices': []}\n"
        "    for hid in ids\n"
        "]\n"
        "json.dump({'items': items}, sys.stdout)\n",
        encoding="utf-8",
    )


class AbstractExtractionTests(unittest.TestCase):
    def test_two_column_heading_keeps_the_left_column_only(self) -> None:
        text = _two_column_note()
        abstract = extract_abstract(text)
        self.assertEqual(abstract, ABSTRACT)
        self.assertNotIn(RIGHT_COLUMN, abstract)
        self.assertNotIn("Earlier columns", abstract)
        record = target_from_text("column-note", 2024, text, seed=1)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["abstract"], ABSTRACT)
        self.assertEqual(record["title"], "Lattice Counter for Column Fixtures")

    def test_heading_case_shared_line_and_inline_forms(self) -> None:
        prose = "This note counts eight lattice cells in order."
        self.assertEqual(extract_abstract(f"ABSTRACT\n\n{prose}\n"), prose)
        self.assertEqual(extract_abstract(f"Abstract {prose}\n"), prose)
        self.assertEqual(extract_abstract(f"Abstract. {prose}\n"), prose)
        self.assertEqual(extract_abstract(f"Abstract\u2014{prose}\n"), prose)
        self.assertEqual(extract_abstract("Abstractive counting of eight lattice cells in a note.\n"), "")
        self.assertEqual(extract_abstract("Abstract Models for Vision\n\nThis title is not a heading.\n"), "")

    def test_layout_falls_back_to_raw_text(self) -> None:
        prose = "This raw extract counts eight lattice cells in order."
        layout = "Lattice Note\n\nAda North\n\n1 Introduction\nNo marker in the layout extract.\n"
        raw = f"Lattice Note\n\nAda North\n\nAbstract. {prose}\n"
        self.assertEqual(extract_abstract(layout), "")
        self.assertEqual(extract_abstract(layout, alternate=raw), prose)

    def test_failure_rate_fails_closed_above_five_percent(self) -> None:
        self.assertFalse(extraction_rate_exceeded(1, 20))
        self.assertTrue(extraction_rate_exceeded(1, 19))
        self.assertFalse(extraction_rate_exceeded(5, 100))
        self.assertTrue(extraction_rate_exceeded(6, 100))


class AcknowledgementTests(unittest.TestCase):
    def test_heading_and_inline_acks_stop_at_references(self) -> None:
        raw = _columns(
            [
                ("Lattice Note for Section Checks", ""),
                ("", ""),
                ("Ada North", ""),
                ("Example Academy", ""),
                ("", ""),
                ("Abstract", "1 Introduction"),
                ("This note counts eight", "Funding rates are studied here."),
                ("lattice cells in reading order.", "The funding rate stays visible."),
                ("Acknowledgements of the method are deferred to the supplement.", ""),
                ("The count is eight. Acknowledgement: the grant is SYN-22.", ""),
                ("More thanks follow this line.", ""),
                ("8. Acknowledgements", ""),
                ("The grant number is SYN-1.", ""),
                ("Acknowledgment", ""),
                ("A singular heading still leaks.", ""),
                ("Funding Information", ""),
                ("The agency name is Quiet Fund.", ""),
                ("References", ""),
                ("Ada North. A cited note.", ""),
            ]
        )
        before = leak_counts(raw, {"title": "Lattice Note for Section Checks", "surnames": ["North"]})
        self.assertGreater(before["acknowledgement"], 0)
        self.assertTrue(leak_failed(before))
        text, _meta, counts = anonymize(raw, "A1")
        self.assertEqual(counts["acknowledgement"], 0)
        self.assertFalse(leak_failed(counts))
        self.assertIn("The count is eight.", text)
        self.assertIn("Funding rates are studied here.", text)
        self.assertIn("The funding rate stays visible.", text)
        self.assertIn("Acknowledgements of the method are deferred to the supplement.", text)
        self.assertIn("References", text)
        self.assertIn("cited note", text)
        self.assertNotIn("SYN-22", text)
        self.assertNotIn("SYN-1", text)
        self.assertNotIn("More thanks", text)
        self.assertNotIn("Quiet Fund", text)
        self.assertNotIn("singular heading", text)


class TitleMaskTests(unittest.TestCase):
    def test_line_breaks_ligatures_and_case_count_as_leaks_until_masked(self) -> None:
        raw = _columns(
            [
                ("Ef\ufb01cient Lattice", ""),
                ("Counter Model", ""),
                ("", ""),
                ("Ada North", ""),
                ("Example Academy", ""),
                ("", ""),
                ("Abstract", "1 Introduction"),
                ("This note counts eight lattice cells.", "The right column is not the title."),
                ("The Ef-", ""),
                ("ficient Lattice Counter Model", ""),
                ("stays after the heading.", ""),
                ("THE EFFICIENT LATTICE COUNTER MODEL", ""),
                ("is repeated in uppercase.", ""),
            ]
        )
        meta = {"title": "Ef\ufb01cient Lattice Counter Model", "surnames": ["North"]}
        self.assertGreater(leak_counts(raw, meta)["title_4gram"], 0)
        text, extracted, counts = anonymize(raw, "A1")
        self.assertEqual(extracted["title"], "Ef\ufb01cient Lattice Counter Model")
        self.assertEqual(counts["title_4gram"], 0)
        self.assertNotIn("ficient", text.lower())
        self.assertNotIn("efficient lattice", text.lower())
        self.assertIn("[TITLE]", text)
        self.assertIn("stays after the heading.", text)


class BatchTests(unittest.TestCase):
    def test_batches_cover_every_item_and_keep_the_seed(self) -> None:
        targets = [{"item_id": f"t-{index}", "year": 2024, "kind": "target"} for index in range(10)]
        targets.extend(
            [
                {"item_id": "y-0", "year": 2019, "kind": "target"},
                {"item_id": "y-1", "year": 2019, "kind": "target"},
            ]
        )
        controls = [{"item_id": "c-0"}, {"item_id": "c-1"}]
        decoys = [{"item_id": "d-0"}, {"item_id": "d-1"}]
        first = assemble_batches(targets, controls, decoys, size=10, seed=11, primary_year=2024)
        second = assemble_batches(targets, controls, decoys, size=10, seed=11, primary_year=2024)
        self.assertEqual(first, second)
        self.assertEqual(len(first[0]), 10)
        self.assertLess(len(first[-1]), 10)
        ids = [item["item_id"] for batch in first for item in batch]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {f"t-{index}" for index in range(10)} | {"y-0", "y-1", "c-0", "c-1", "d-0", "d-1"})
        self.assertEqual({item["kind"] for batch in first for item in batch}, {"target", "control", "decoy", "other_year"})
        self.assertGreater(len({item["kind"] for item in first[0]}), 1)


class PassageTests(unittest.TestCase):
    def test_distinctive_passage_is_one_body_sentence(self) -> None:
        text = _two_column_note()
        passage = generate_distinctive_passage(text, seed=4, avoid="column-note")
        self.assertIsNotNone(passage)
        assert passage is not None
        self.assertEqual(passage["verbatim"], BODY)
        self.assertEqual(len(passage["paraphrases"]), 3)
        self.assertNotIn("Column Fixtures", passage["verbatim"])
        self.assertNotIn("Earlier columns", passage["verbatim"])
        self.assertNotIn(RIGHT_COLUMN, passage["verbatim"])
        norms = [" ".join(passage["verbatim"].lower().split())]
        for paraphrase in passage["paraphrases"]:
            key = " ".join(paraphrase.lower().split())
            self.assertNotIn(key, norms)
            norms.append(key)


class DecoyTests(unittest.TestCase):
    def test_decoys_are_plausible_and_miss_the_catalog(self) -> None:
        decoys = synthetic_decoys(1, seed=3)
        self.assertEqual(synthetic_decoys(1, seed=3), decoys)
        self.assertIn(decoys[0]["year"], range(2018, 2026))
        self.assertEqual(decoys[0]["venue"], "CVPR")
        self.assertEqual(decoys[0]["item_id"], "decoy-00")
        blob = json.dumps(decoys).lower()
        for banned in ("synthetic", "stand-in", "2099", "arxiv.org", "not a conference"):
            self.assertNotIn(banned, blob)
        catalog_norms = {normalize_title(title) for title in CATALOG_TITLES}
        catalog_tokens = [set(normalize_title(title).split()) for title in CATALOG_TITLES]
        crossed = synthetic_decoys(2, seed=4, catalog_titles=list(CATALOG_TITLES))
        crosses = 0
        for row in crossed:
            self.assertNotIn(normalize_title(row["title"]), catalog_norms)
            tokens = set(normalize_title(row["title"]).split())
            if sum(1 for source in catalog_tokens if tokens & source) >= 2:
                crosses += 1
        self.assertGreaterEqual(crosses, 1)
        blocked = crossed[0]["title"]
        other = synthetic_decoys(1, seed=4, catalog_titles=list(CATALOG_TITLES), blocked_titles=[blocked])
        self.assertNotEqual(normalize_title(other[0]["title"]), normalize_title(blocked))
        digest = sha256_bytes(normalize_title(blocked).encode("utf-8"))
        digested = synthetic_decoys(
            1,
            seed=4,
            catalog_titles=list(CATALOG_TITLES),
            catalog_digests={digest},
        )
        self.assertNotEqual(normalize_title(digested[0]["title"]), normalize_title(blocked))

    def test_list_writes_title_digests_without_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            list_catalog(cache, years=(2024,), fixture_dir=FIXTURE)
            stored = (cache / "title_digests.json").read_text(encoding="utf-8")
            self.assertNotIn("Synthetic", stored)
            self.assertNotIn("Inventor", stored)
            payload = json.loads((FIXTURE / "cvpr-2024-orals-posters.json").read_text(encoding="utf-8"))
            name = next(str(row["name"]) for row in payload["results"] if row.get("name"))
            digest = sha256_bytes(normalize_title(name).encode("utf-8"))
            digests = set(json.loads(stored)["digests"])
            self.assertIn(digest, digests)
            catalog = (cache / "catalog.json").read_text(encoding="utf-8")
            self.assertNotIn("Synthetic", catalog)
            rows = synthetic_decoys(3, seed=6, catalog_digests=digests)
            for row in rows:
                self.assertNotIn(sha256_bytes(normalize_title(row["title"]).encode("utf-8")), digests)


class ProbeRunTests(unittest.TestCase):
    def test_extraction_failures_are_reported_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            papers = ["note-good", "note-blank", "note-missing"]
            _write_manifest(cache, papers)
            (cache / "raw" / "note-good.txt").write_text(_two_column_note(), encoding="utf-8")
            (cache / "raw" / "note-blank.txt").write_text("Just a title line without a marker.\n", encoding="utf-8")
            flag = root / "called.txt"
            script = root / "answer.py"
            script.write_text(f"from pathlib import Path\nPath({str(flag)!r}).write_text('called')\n", encoding="utf-8")
            with self.assertRaises(ProbeError) as caught:
                run_probe(
                    cache,
                    model_id="opus-local",
                    backend="command",
                    command_template=f"{sys.executable} {script}",
                    templates=("P-A",),
                    batch_size=10,
                    seed=5,
                    decoy_count=0,
                )
            self.assertIn("5%", str(caught.exception))
            self.assertFalse(flag.exists())
            manifest = json.loads((cache / "sample_manifest.json").read_text(encoding="utf-8"))
            extraction = manifest["probe_extraction"]
            self.assertEqual(extraction["attempted"], 3)
            self.assertEqual(extraction["failed"], 2)
            self.assertEqual(extraction["paper_ids"], ["note-blank", "note-missing"])
            self.assertEqual(extraction["reasons"]["note-blank"], "no_abstract")
            self.assertEqual(extraction["reasons"]["note-missing"], "missing_text")
            self.assertNotIn("note-good", extraction["paper_ids"])
            probe_manifest = json.loads((cache / "probes" / "opus-local" / "manifest.json").read_text(encoding="utf-8"))
            predictions = json.loads((cache / "probes" / "opus-local" / "predictions.json").read_text(encoding="utf-8"))
            self.assertEqual(probe_manifest["extraction"]["paper_ids"], extraction["paper_ids"])
            self.assertEqual(predictions["extraction"]["failed"], 2)
            self.assertEqual(predictions["skipped"], ["note-blank", "note-missing"])
            self.assertNotIn("ZZ-LABEL-SENTINEL-9f3a", json.dumps(predictions))

    def test_under_five_percent_is_reported_and_still_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            ids = [f"note-{index:02d}" for index in range(20)] + ["note-gap"]
            _write_manifest(cache, ids)
            raw = cache / "raw"
            (raw / "note-00.layout.txt").write_text(
                "Lattice Note Number 0\n\nAda North\nExample Academy\n\n1 Introduction\nNo marker in the layout extract.\n",
                encoding="utf-8",
            )
            (raw / "note-00.raw.txt").write_text(
                "Lattice Note Number 0\n\nAda North\nExample Academy\n\n"
                "Abstract. This raw extract counts eight lattice cells in order.\n",
                encoding="utf-8",
            )
            for index in range(1, 20):
                (raw / f"note-{index:02d}.txt").write_text(_good_paper(index), encoding="utf-8")
            (raw / "note-gap.txt").write_text("Just a title line without a marker.\n", encoding="utf-8")
            state = root / "calls.txt"
            script = root / "answer.py"
            _answer_script(script, state)
            result = run_probe(
                cache,
                model_id="opus-local",
                backend="command",
                command_template=f"{sys.executable} {script}",
                templates=("P-A",),
                batch_size=10,
                seed=5,
                decoy_count=0,
            )
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["skipped"], ["note-gap"])
            self.assertEqual(result["extraction"]["failed"], 1)
            self.assertEqual(result["extraction"]["attempted"], 21)
            self.assertFalse(extraction_rate_exceeded(1, 21))
            self.assertEqual(result["calls"], 2)
            self.assertEqual(result["items"], 20)
            prompts = "\n".join(path.read_text(encoding="utf-8") for path in (cache / "probes" / "opus-local" / "prompts").glob("*.txt"))
            self.assertIn("This raw extract counts eight lattice cells in order.", prompts)
            self.assertNotIn("note-00", prompts)
            self.assertNotIn("ZZ-LABEL-SENTINEL-9f3a", prompts)
            manifest = json.loads((cache / "sample_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["probe_extraction"]["paper_ids"], ["note-gap"])

    def test_batches_are_stored_and_a_later_run_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            ids = [f"item-{index:02d}" for index in range(12)]
            _write_manifest(cache, ids)
            for index, paper_id in enumerate(ids):
                (cache / "raw" / f"{paper_id}.txt").write_text(_good_paper(index), encoding="utf-8")
            state = root / "calls.txt"
            script = root / "answer.py"
            _answer_script(script, state, fail_on=2)
            first = run_probe(
                cache,
                model_id="opus-local",
                backend="command",
                command_template=f"{sys.executable} {script}",
                templates=("P-A",),
                batch_size=10,
                seed=5,
                decoy_count=0,
            )
            self.assertEqual(first["calls"], 1)
            self.assertEqual(len(first["errors"]), 1)
            self.assertEqual(first["items"], 10)
            predictions = json.loads((cache / "probes" / "opus-local" / "predictions.json").read_text(encoding="utf-8"))
            self.assertEqual(len(predictions["items"]), 10)
            batch_dir = cache / "probes" / "opus-local" / "batches"
            self.assertTrue(json.loads((batch_dir / "P-A-000.json").read_text(encoding="utf-8"))["complete"])
            self.assertFalse((batch_dir / "P-A-001.json").exists())
            second = run_probe(
                cache,
                model_id="opus-local",
                backend="command",
                command_template=f"{sys.executable} {script}",
                templates=("P-A",),
                batch_size=10,
                seed=5,
                decoy_count=0,
            )
            self.assertEqual(second["errors"], [])
            self.assertEqual(second["calls"], 1)
            self.assertEqual(second["items"], 12)
            self.assertEqual(state.read_text(encoding="utf-8"), "3")
            stored = json.loads((cache / "probes" / "opus-local" / "predictions.json").read_text(encoding="utf-8"))
            self.assertEqual([row["resumed"] for row in stored["batches"]], [True, False])
            self.assertEqual(len(stored["items"]), 12)
            self.assertTrue(json.loads((batch_dir / "P-A-001.json").read_text(encoding="utf-8"))["complete"])

    def test_probe_builds_a_passage_without_a_passages_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            _write_manifest(cache, ["column-note"])
            (cache / "raw" / "column-note.txt").write_text(_two_column_note(), encoding="utf-8")
            controls = root / "controls.json"
            controls.write_text(
                json.dumps(
                    {
                        "papers": [
                            {
                                "item_id": "control-brief",
                                "paper_id": "control-brief",
                                "title": "Brief Control Note",
                                "authors": ["Nia North"],
                                "surnames": ["North"],
                                "abstract": "Too short.",
                                "venue": "arXiv",
                                "year": 2026,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state = root / "calls.txt"
            script = root / "answer.py"
            _answer_script(script, state)
            result = run_probe(
                cache,
                model_id="opus-local",
                backend="command",
                command_template=f"{sys.executable} {script}",
                templates=("P-D",),
                batch_size=10,
                seed=4,
                decoy_count=0,
                controls_path=controls,
            )
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["passage_failures"], [{"item_id": "control-brief", "template": "P-D"}])
            prompts = "\n".join(path.read_text(encoding="utf-8") for path in (cache / "probes" / "opus-local" / "prompts").glob("*.txt"))
            self.assertIn(BODY, prompts)
            for number in range(1, 5):
                self.assertIn(f"Question {number}", prompts)
            self.assertNotIn("column-note", prompts)
            self.assertNotIn("Column Fixtures", prompts)
            self.assertNotIn("Earlier columns", prompts)
            self.assertNotIn(RIGHT_COLUMN, prompts)
            self.assertNotIn("ZZ-LABEL-SENTINEL-9f3a", prompts)


if __name__ == "__main__":
    unittest.main()
