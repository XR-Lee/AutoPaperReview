"""P1 CVPR tier probes, variant runs, and stratified metrics. Synthetic only."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from autopaperreview.cli import main
from autopaperreview.cvpr_metrics import (
    CPOP_WEIGHTS,
    POPULATION_2024,
    adjust_bh,
    adjust_holm,
    auc_o_rest_weighted_2024,
    auc_oral_versus_rest,
    cpop,
    delong_auc,
    delong_paired,
    extended_metrics,
    icc_matrix,
    macro_auc,
    quadratic_weighted_kappa,
    stratified_bootstrap,
)
from autopaperreview.cvpr_probe import (
    assemble_batch,
    build_arxiv_query,
    fetch_negative_controls,
    make_decop_questions,
    parse_arxiv_atom,
    recognise_paper,
    render_batch_prompt,
    run_probe,
    score_item,
    surname_match,
    synthetic_decoys,
    token_f1,
    venue_year_score,
)
from autopaperreview.cvpr_rounds import append_round, load_rounds
from autopaperreview.cvpr_tier import (
    CvprTierError,
    HeldoutTiers,
    SampleManifest,
    SamplePaper,
    YearSummary,
    run_directory,
    run_model,
    write_report,
)

SENTINEL = "ZZ-LABEL-SENTINEL-9f3a"
VERBATIM = "Synthetic stand-in crosses eight cells."
PARAPHRASES = [
    "A stand-in note tallies cells without leaving the grid.",
    "The note only rehearses a cell count.",
    "The grid exercise stops after the count.",
]
ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://example.invalid/abs/syn-1v1</id>
    <published>2026-08-01T00:00:00Z</published>
    <title>Synthetic control note</title>
    <summary>A synthetic control abstract about lattice cells.</summary>
    <author><name>Nia North</name></author>
    <arxiv:primary_category term="cs.CV"/>
  </entry>
  <entry>
    <id>http://example.invalid/abs/old</id>
    <published>2020-01-01T00:00:00Z</published>
    <title>Old synthetic note</title>
    <summary>This one is before the window.</summary>
    <author><name>Old Name</name></author>
    <arxiv:primary_category term="cs.CV"/>
  </entry>
  <entry>
    <id>http://example.invalid/abs/other</id>
    <published>2026-08-02T00:00:00Z</published>
    <title>Other category note</title>
    <summary>This one is outside cs.CV.</summary>
    <author><name>Other Name</name></author>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
</feed>
"""


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


def _write_cache(root: Path, papers: list[tuple[str, str, str]]) -> None:
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
        papers=[
            SamplePaper(paper_id=paper_id, year=2024, pdf_url="fixture://synthetic")
            for paper_id, _tier, _text in papers
        ],
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample_manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    labels = {paper_id: tier for paper_id, tier, _text in papers}
    labels["not-in-sample"] = SENTINEL
    held = HeldoutTiers(papers=labels)
    (root / "heldout").mkdir(parents=True, exist_ok=True)
    (root / "heldout" / "tiers.json").write_text(held.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "catalog.json").write_text(json.dumps({"note": SENTINEL}) + "\n", encoding="utf-8")
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "manuscripts").mkdir(parents=True, exist_ok=True)
    for paper_id, _tier, text in papers:
        (root / "raw" / f"{paper_id}.txt").write_text(text, encoding="utf-8")
        (root / "manuscripts" / f"{paper_id}.txt").write_text(text, encoding="utf-8")
        for variant in ("A0", "A1"):
            folder = root / "manuscripts" / variant
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{paper_id}.txt").write_text(text, encoding="utf-8")


def _paper(paper_id: str, base: str) -> str:
    return (
        "Synthetic lattice counter\n\n"
        "Ada North\n"
        "Bea South\n\n"
        "Abstract\n\n"
        "This synthetic note counts eight lattice cells and stops there.\n\n"
        "1 Introduction\n"
        f"BASE {base}\n"
        f"PAPERMARK {paper_id}\n"
    )


def _script(path: Path) -> None:
    path.write_text(
        "import json, re, sys\n"
        "from pathlib import Path\n"
        "text = sys.stdin.read()\n"
        "state_path = Path(sys.argv[1])\n"
        "if 'handoff_id:' in text:\n"
        "    ids = re.findall(r'handoff_id: ([0-9a-f]{12})', text)\n"
        "    items = []\n"
        "    if 'Which passage' in text:\n"
        "        blocks = re.split(r'handoff_id: ', text)[1:]\n"
        "        for block in blocks:\n"
        "            hid = block[:12]\n"
        "            choices = []\n"
        "            for question in re.split(r'Question \\d+', block)[1:]:\n"
        "                letter = 'A'\n"
        "                for match in re.finditer(r'^([ABCD])\\. (.*)$', question, re.M):\n"
        f"                    if match.group(2).strip() == {VERBATIM!r}:\n"
        "                        letter = match.group(1)\n"
        "                choices.append(letter)\n"
        "            items.append({'handoff_id': hid, 'title': '', 'authors': [], 'venue': '', 'year': None, 'tier': 'unknown', 'choices': choices})\n"
        "    else:\n"
        "        for hid in ids:\n"
        "            items.append({'handoff_id': hid, 'title': 'Synthetic lattice counter', 'authors': ['Ada North', 'Bea South'], 'venue': 'CVPR', 'year': 2024, 'tier': 'unknown', 'choices': []})\n"
        "    json.dump({'items': items}, sys.stdout)\n"
        "    raise SystemExit(0)\n"
        "state = json.loads(state_path.read_text()) if state_path.is_file() else {}\n"
        "mark = re.search(r'PAPERMARK (\\S+)', text).group(1)\n"
        "base = float(re.search(r'BASE ([0-9.]+)', text).group(1))\n"
        "count = int(state.get(mark, 0))\n"
        "state[mark] = count + 1\n"
        "state_path.write_text(json.dumps(state))\n"
        "score = base if count == 0 else base + 1\n"
        "json.dump({'rationale': 'synthetic lattice reading', 'rating': 4, 'predicted_mean': score, 'tier': 'poster'}, sys.stdout)\n",
        encoding="utf-8",
    )


class ScoringTests(unittest.TestCase):
    def test_token_f1_surname_venue_and_recognition(self) -> None:
        self.assertAlmostEqual(token_f1("the synthetic lattice", "synthetic lattice counter"), 0.8)
        self.assertAlmostEqual(token_f1("Synthetic lattice counter", "synthetic lattice counter"), 1.0)
        self.assertFalse(surname_match("South", ["North", "South", "West"])["hit"])
        self.assertTrue(surname_match("North", ["North", "South", "West"])["hit"])
        self.assertTrue(surname_match("South West", ["North", "South", "West"])["hit"])
        venue = venue_year_score("CVPR 2024", 2024, "CVPR", 2024)
        self.assertTrue(venue["venue_hit"])
        self.assertTrue(venue["year_hit"])
        self.assertEqual(venue["score"], 1.0)
        questions = make_decop_questions(VERBATIM, PARAPHRASES, seed=4)
        self.assertEqual(len(questions), 4)
        self.assertEqual({question["answer"] for question in questions} <= set("ABCD"), True)
        pa = score_item(
            "P-A",
            {"title": "Synthetic lattice counter", "authors": ["Ada North"], "venue": "CVPR", "year": 2024, "tier": "unknown"},
            {"title": "Synthetic lattice counter", "surnames": ["North", "South"], "venue": "CVPR", "year": 2024},
        )
        self.assertTrue(pa["title_hit"])
        self.assertEqual(pa["tier_qa"], 2)
        pt = score_item(
            "P-T",
            {"title": "", "authors": ["Bea South"], "venue": "CVPR", "year": 2024, "tier": "poster"},
            {"title": "Synthetic lattice counter", "surnames": ["North", "South"], "venue": "CVPR", "year": 2024},
        )
        self.assertTrue(pt["author_hit"])
        self.assertEqual(pt["tier_qa"], 1)
        decop = score_item(
            "P-D",
            {"choices": [question["answer"] for question in questions]},
            {"decop_answers": [question["answer"] for question in questions], "title": "", "surnames": [], "venue": "", "year": None},
        )
        self.assertEqual(decop["decop_correct"], 4)
        self.assertTrue(recognise_paper([pa])["recognised"])
        self.assertTrue(recognise_paper([pt])["recognised"])
        self.assertTrue(recognise_paper([decop])["recognised"])
        weak = dict(decop)
        weak["decop_correct"] = 2
        weak["decop_accuracy"] = 0.5
        self.assertFalse(recognise_paper([weak])["recognised"])

    def test_batch_hides_kind_and_catalog_id(self) -> None:
        targets = [
            {
                "item_id": "syn-note",
                "paper_id": "syn-note",
                "kind": "target",
                "title": "Synthetic lattice counter",
                "abstract": "This synthetic note counts eight lattice cells and stops there.",
                "year": 2024,
            },
            {
                "item_id": "syn-other",
                "paper_id": "syn-other",
                "kind": "target",
                "title": "Synthetic raster note",
                "abstract": "Another synthetic note counts cells in a later year.",
                "year": 2025,
            },
        ]
        controls = [
            {
                "item_id": "control-syn-1",
                "paper_id": "control-syn-1",
                "title": "Synthetic control note",
                "abstract": "A synthetic control abstract about lattice cells.",
                "year": 2026,
            }
        ]
        decoys = synthetic_decoys(1, seed=3)
        self.assertEqual(synthetic_decoys(1, seed=3), decoys)
        self.assertIn(decoys[0]["year"], range(2018, 2026))
        self.assertEqual(decoys[0]["venue"], "CVPR")
        decoy_blob = json.dumps(decoys).lower()
        self.assertNotIn("2099", decoy_blob)
        self.assertNotIn("synthetic", decoy_blob)
        self.assertNotIn("stand-in", decoy_blob)
        self.assertNotIn("arxiv.org", decoy_blob)
        first, omitted = assemble_batch(targets, controls, decoys, size=4, seed=9)
        second, _ = assemble_batch(targets, controls, decoys, size=4, seed=9)
        self.assertEqual([item["item_id"] for item in first], [item["item_id"] for item in second])
        self.assertEqual({item["kind"] for item in first}, {"target", "control", "decoy", "other_year"})
        self.assertEqual(omitted, [])
        for offset, item in enumerate(first):
            item["handoff_id"] = f"{offset:012x}"
        prompt = render_batch_prompt("P-A", first)
        self.assertNotIn("syn-note", prompt)
        self.assertNotIn("decoy", prompt)
        self.assertNotIn("control-", prompt)
        self.assertNotIn(SENTINEL, prompt)
        self.assertIn("Abstract:", prompt)


class ControlFeedTests(unittest.TestCase):
    def test_atom_parser_filters_date_and_category_without_network(self) -> None:
        url = build_arxiv_query(since="2026-07-15", category="cs.CV", limit=5)
        self.assertIn("202607150000", url)
        self.assertIn("cat:cs.CV", url)
        calls: list[str] = []

        def getter(address: str) -> bytes:
            calls.append(address)
            return ATOM.encode("utf-8")

        payload = fetch_negative_controls(getter=getter, since="2026-07-15", category="cs.CV", limit=5)
        self.assertEqual(calls, [url])
        self.assertEqual([paper["item_id"] for paper in payload["papers"]], ["control-syn-1"])
        self.assertEqual(parse_arxiv_atom(ATOM)[0]["year"], 2026)


class MetricTests(unittest.TestCase):
    def test_macro_auc_cpop_kappa_delong_and_corrections(self) -> None:
        aucs = {"oral_vs_poster": 1.0, "highlight_vs_poster": 0.5, "oral_vs_highlight": 0.0}
        self.assertAlmostEqual(macro_auc(aucs), 0.5)
        self.assertAlmostEqual(
            cpop(aucs),
            CPOP_WEIGHTS["oral_vs_poster"] * 1.0
            + CPOP_WEIGHTS["highlight_vs_poster"] * 0.5
            + CPOP_WEIGHTS["oral_vs_highlight"] * 0.0,
        )
        self.assertAlmostEqual(
            auc_o_rest_weighted_2024(aucs),
            (POPULATION_2024["highlight"] * 0.0 + POPULATION_2024["poster"] * 1.0) / 2626,
        )
        rows = [
            {"paper_id": "a", "score": 3, "true_tier": "oral", "predicted_tier": "oral", "year": 2024},
            {"paper_id": "b", "score": 2, "true_tier": "highlight", "predicted_tier": "highlight", "year": 2024},
            {"paper_id": "c", "score": 1, "true_tier": "poster", "predicted_tier": "poster", "year": 2024},
        ]
        self.assertEqual(quadratic_weighted_kappa(rows), 1.0)
        shifted = [dict(row, predicted_tier="poster") for row in rows]
        self.assertLess(quadratic_weighted_kappa(shifted), 1.0)
        estimate = delong_auc([1, 2, 0, 1], [1, 1, 0, 0])
        self.assertAlmostEqual(estimate["auc"], 0.875)
        self.assertAlmostEqual(estimate["se"], 0.03125 ** 0.5)
        paired = delong_paired([1, 2, 0, 1], [1, 2, 0, 1], [1, 1, 0, 0])
        self.assertEqual(paired["diff"], 0.0)
        self.assertEqual(paired["p"], 1.0)
        self.assertEqual(adjust_holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
        self.assertEqual(adjust_bh([0.01, 0.04, 0.03]), [0.03, 0.04, 0.04])
        only_oral = [{"paper_id": "y", "score": 1, "true_tier": "oral", "predicted_tier": "oral", "year": 2025}]
        self.assertIsNone(auc_oral_versus_rest(only_oral, year=2025))
        mixed = only_oral + [
            {"paper_id": "z", "score": 0, "true_tier": "non_oral", "predicted_tier": "poster", "year": 2025}
        ]
        self.assertEqual(auc_oral_versus_rest(mixed, year=2025), 1.0)
        bootstrap = stratified_bootstrap(rows, lambda sample: float(len(sample)), resamples=20, seed=1)
        self.assertEqual(bootstrap["point"], 3.0)
        self.assertEqual(bootstrap["method"], "year_tier")
        self.assertEqual(bootstrap["low"], bootstrap["high"])
        icc = icc_matrix([[1, 2], [3, 4], [5, 6]])
        self.assertAlmostEqual(icc["icc_3_1"], 1.0)
        self.assertAlmostEqual(icc["icc_2_1"], 8 / 9)

    def test_extended_metrics_use_population_weights(self) -> None:
        rows = [
            {"paper_id": "a", "score": 3, "true_tier": "oral", "predicted_tier": "oral", "year": 2024, "repeat_scores": [3, 3]},
            {"paper_id": "b", "score": 3, "true_tier": "oral", "predicted_tier": "oral", "year": 2024, "repeat_scores": [3, 3]},
            {"paper_id": "c", "score": 1, "true_tier": "poster", "predicted_tier": "poster", "year": 2024, "repeat_scores": [1, 1]},
            {"paper_id": "d", "score": 1, "true_tier": "poster", "predicted_tier": "poster", "year": 2024, "repeat_scores": [1, 1]},
            {"paper_id": "e", "score": 2, "true_tier": "highlight", "predicted_tier": "highlight", "year": 2024, "repeat_scores": [2, 2]},
            {"paper_id": "f", "score": 2, "true_tier": "highlight", "predicted_tier": "highlight", "year": 2024, "repeat_scores": [2, 2]},
        ]
        metrics = extended_metrics(rows, resamples=20, seed=2)
        self.assertEqual(metrics["primary"], "macro_auc")
        self.assertAlmostEqual(metrics["macro_auc"]["point"], 1.0)
        self.assertAlmostEqual(metrics["labelled_share"]["predictions"], 1.0)
        self.assertEqual(metrics["labelled_share"]["strata"]["2024:oral"]["population"], 90)
        self.assertAlmostEqual(metrics["icc"]["icc_3_1"], 1.0)


class RunLayoutTests(unittest.TestCase):
    def test_variant_round_repeats_probe_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            papers = [
                ("syn-note", "poster", _paper("syn-note", "1")),
                ("syn-other", "oral", _paper("syn-other", "3")),
            ]
            _write_cache(cache, papers)
            material = cache / "runs" / "opus-local"
            self.assertEqual(run_directory(cache, "opus-local", variant=None, round_id=None), material)
            self.assertEqual(
                run_directory(cache, "opus-local", variant="A1", round_id="r1"),
                cache / "runs" / "opus-local" / "A1" / "r1",
            )
            state = root / "repeat-state.json"
            script = root / "answer.py"
            _script(script)
            template = f"{sys.executable} {script} {state} {{handoff_id}} {{mode}}"
            legacy = run_model(
                cache,
                model_id="opus-local",
                backend="command",
                holistic_mode="mean",
                command_template=template,
            )
            self.assertEqual(legacy["errors"], [])
            self.assertTrue((cache / "runs" / "opus-local" / "manifest.json").is_file())
            self.assertFalse((cache / "runs" / "opus-local" / "default").exists())
            state.unlink(missing_ok=True)
            for variant in ("A0", "A1"):
                state.unlink(missing_ok=True)
                result = run_model(
                    cache,
                    model_id="astra-local",
                    backend="command",
                    holistic_mode="mean",
                    command_template=template,
                    variant=variant,
                    round_id="r1",
                    repeats=2,
                )
                self.assertEqual(result["errors"], [])
                parsed = list((cache / "runs" / "astra-local" / variant / "r1" / "parsed").glob("*.json"))
                self.assertEqual(len(parsed), 2)
                row = json.loads(parsed[0].read_text(encoding="utf-8"))
                self.assertEqual(len(row["repeat_scores"]), 2)
                self.assertNotIn("true_tier", row)
            self.assertFalse((cache / "runs" / "astra-local" / "manifest.json").exists())
            rounds = load_rounds(cache)
            self.assertEqual([row["round"] for row in rounds], ["r1", "r1"])
            passages = root / "passages.json"
            passages.write_text(
                json.dumps({"papers": {"syn-note": {"verbatim": VERBATIM, "paraphrases": PARAPHRASES}, "syn-other": {"verbatim": VERBATIM, "paraphrases": PARAPHRASES}}}),
                encoding="utf-8",
            )
            controls = root / "controls.json"
            controls.write_text(
                json.dumps(
                    {
                        "papers": [
                            {
                                "item_id": "control-syn-1",
                                "paper_id": "control-syn-1",
                                "title": "Synthetic control note",
                                "authors": ["Nia North"],
                                "surnames": ["North"],
                                "abstract": "A synthetic control abstract about lattice cells.",
                                "venue": "arXiv",
                                "year": 2026,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            probed = run_probe(
                cache,
                model_id="opus-local",
                backend="command",
                command_template=template,
                templates=("P-A", "P-T", "P-D"),
                batch_size=6,
                seed=5,
                decoy_count=1,
                controls_path=controls,
                passages_path=passages,
            )
            self.assertEqual(probed["errors"], [])
            self.assertGreater(probed["items"], 0)
            predictions = (cache / "probes" / "opus-local" / "predictions.json").read_text(encoding="utf-8")
            prompts = "\n".join(path.read_text(encoding="utf-8") for path in (cache / "probes" / "opus-local" / "prompts").glob("*.txt"))
            self.assertNotIn(SENTINEL, predictions)
            self.assertNotIn(SENTINEL, prompts)
            self.assertNotIn("syn-note", prompts)
            self.assertNotIn("syn-other", prompts)
            self.assertNotIn("true_tier", predictions)
            keys = json.loads((cache / "probe_keys" / "opus-local.json").read_text(encoding="utf-8"))
            self.assertIn("target", {item["kind"] for item in keys["items"]})
            self.assertIn("decoy", {item["kind"] for item in keys["items"]})
            self.assertNotIn(SENTINEL, json.dumps(keys))
            output = root / "report.md"
            payload = write_report(cache, output, resamples=15, seed=3)
            text = output.read_text(encoding="utf-8")
            self.assertIn("macro-AUC", text)
            self.assertIn("## Variant pairs", text)
            self.assertIn("astra-local/A0/r1", text)
            self.assertIn("## Rounds", text)
            self.assertIn("r1", text)
            self.assertTrue(output.with_suffix(".analysis.csv").is_file())
            csv_text = output.with_suffix(".analysis.csv").read_text(encoding="utf-8")
            self.assertIn("true_tier", csv_text.splitlines()[0])
            self.assertNotIn(SENTINEL, csv_text)
            pair = payload["variant_pairs"]
            self.assertTrue(pair)
            self.assertEqual(next(iter(pair.values()))["n"], 2)
            recognition = payload["models"]["opus-local"]["metrics"]["recognition"]
            self.assertGreaterEqual(recognition["recognised"]["n"], 1)
            extended = payload["models"]["astra-local/A0/r1"]["metrics"]["extended"]
            self.assertAlmostEqual(extended["icc"]["icc_3_1"], 1.0)
            self.assertAlmostEqual(extended["icc"]["icc_2_1"], 0.8)

    def test_cli_decoys_round_and_adapter_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            decoy_path = root / "decoys.json"
            code = main(
                [
                    "calibrate-eval",
                    "cvpr-tier",
                    "decoys",
                    "--output",
                    str(decoy_path),
                    "--count",
                    "2",
                    "--seed",
                    "8",
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(decoy_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["papers"]), 2)
            cache = root / "cache"
            cache.mkdir()
            code = main(
                [
                    "calibrate-eval",
                    "cvpr-tier",
                    "round",
                    "--cache",
                    str(cache),
                    "--round",
                    "r9",
                    "--model-id",
                    "opus-local",
                    "--notes",
                    "synthetic candidate",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(load_rounds(cache)[0]["notes"], "synthetic candidate")
            append_round(cache, {"round": "r9", "model_id": "astra-local", "notes": "second"})
            self.assertEqual(len(load_rounds(cache)), 2)
            code = main(
                [
                    "calibrate-eval",
                    "cvpr-tier",
                    "run",
                    "--cache",
                    str(cache),
                    "--model-id",
                    "opus-local",
                    "--backend",
                    "adapter",
                ]
            )
            self.assertEqual(code, 2)
        with self.assertRaises(CvprTierError) as caught:
            run_model(Path("unused-cache"), model_id="opus-local", backend="adapter")
        self.assertIn("local CLI", str(caught.exception))
        self.assertIn("no API key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
