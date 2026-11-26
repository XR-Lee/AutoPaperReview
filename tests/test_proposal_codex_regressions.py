"""Regressions for the proposal-rank leaks, scales, and cohort scoring."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import contrast, priority, reference_coding, source_evidence, stages
from autopaperreview.proposal.cohort import validate_manifest, validate_predictions, validate_references
from autopaperreview.proposal.data import digest, iclr_case, label_leak, official_rating_summary, write
from autopaperreview.proposal.fixed_benchmark import blind_inputs, evaluate_fixed
from autopaperreview.proposal.overall_rank import STRATIFIED_DRAWS, stratified_ci
from autopaperreview.proposal.prior_index import build, retrieve
from autopaperreview.public_calibration import latest_official_reviews
from autopaperreview.rating_scales import scale_for_key, scale_for_year


def _proposal_profile(stage, feature):
    model, command = priority.route(stage)
    return {
        "ranker": {"names": [feature]},
        "prompts": {stage: digest(stages.PROMPTS[stage])},
        "routes": {stage: {"model": model, "command_sha256": digest(command)}},
    }


class ScalePropagationTests(unittest.TestCase):
    def test_code_idea_references_keeps_year_through_evaluate_rank(self) -> None:
        row = {
            "forum": "fixture-2026",
            "year": 2026,
            "rating_scale": "iclr2026",
            "final_rating": 0,
            "accept": 1,
            "reviews": [
                {
                    "id": "note-1",
                    "units": {
                        "u": "The proposed test is explicit. Overall Rating: 8/10. Decision: Accept as Oral."
                    },
                }
            ],
        }
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)

            def invoke(stage, source, folder, model, command, timeout, *, cached_only):
                blob = json.dumps(source)
                self.assertNotIn("Overall Rating", blob)
                self.assertNotIn("Accept as Oral", blob)
                self.assertIn("The proposed test is explicit.", blob)
                if stage == "ref_extract":
                    output = {"r1": [{"id": "u", "quote": "The proposed test is explicit."}]}
                else:
                    output = {"r1": {"score": 4, "used": [0]}}
                path = cache / f"{stage}.json"
                write(path, {"output": output})
                return path

            with patch.object(reference_coding, "invoke", side_effect=invoke):
                coded = reference_coding.code_paper(row, cache, ["judge"])
        self.assertEqual(coded["year"], 2026)
        self.assertEqual(coded["rating_scale"], "iclr2026")
        self.assertEqual(coded["final_rating"], 0)
        manifest = {
            "cases": [{"forum": "fixture-2026", "split": "test"}],
            "ids_sha256": digest(["fixture-2026"]),
        }
        scored = evaluate_fixed(
            manifest,
            [{"forum": "fixture-2026", "overall": 4}],
            [coded],
            bootstrap=0,
            stratified_draws=0,
        )
        self.assertEqual(scored["n"], 1)
        self.assertNotIn("outside iclr2024", json.dumps(scored))

    def test_contradictory_year_and_scale_are_rejected(self) -> None:
        row = {
            "forum": "fixture-mix",
            "year": 2026,
            "rating_scale": "iclr2024",
            "final_rating": 6,
            "reviews": [{"id": "note-1", "units": {"u": "The proposed test is explicit."}}],
        }
        with self.assertRaisesRegex(ValueError, "does not match rating_scale"):
            reference_coding.validate_cases([row])
        manifest = {
            "cases": [{"forum": "fixture-mix", "split": "test"}],
            "ids_sha256": digest(["fixture-mix"]),
        }
        reference = {
            "forum": "fixture-mix",
            "prospective": 3,
            "final_rating": 6,
            "accept": 1,
            "year": 2026,
            "rating_scale": "iclr2024",
        }
        with self.assertRaisesRegex(ValueError, "does not match rating_scale"):
            evaluate_fixed(
                manifest,
                [{"forum": "fixture-mix", "overall": 3}],
                [reference],
                bootstrap=0,
                stratified_draws=0,
            )
        self.assertEqual(scale_for_key("range:1-3").maximum, 3)

    def test_reference_coding_strips_acceptance_before_the_model(self) -> None:
        row = {
            "forum": "fixture-strip",
            "reviews": [
                {"id": "kept", "units": {"u": "The proposed test is explicit."}},
                {
                    "id": "only-label",
                    "units": {"u": "Overall Rating: 8/10. Decision: Accept as Oral."},
                },
            ],
        }
        seen = []
        with TemporaryDirectory() as tmp:

            def invoke(stage, source, folder, model, command, timeout, *, cached_only):
                seen.append(source)
                output = (
                    {"r1": [{"id": "u", "quote": "The proposed test is explicit."}]}
                    if stage == "ref_extract"
                    else {"r1": {"score": 3, "used": [0]}}
                )
                path = Path(tmp) / f"{stage}.json"
                write(path, {"output": output})
                return path

            with patch.object(reference_coding, "invoke", side_effect=invoke):
                reference_coding.code_paper(row, Path(tmp), ["judge"])
        self.assertEqual(list(seen[0]), ["r1"])
        self.assertNotIn("8/10", json.dumps(seen))
        self.assertNotIn("Oral", json.dumps(seen))


class LeakGuardTests(unittest.TestCase):
    def test_rank_blocks_main_fallback_and_legacy_before_a_call(self) -> None:
        calls = []

        def call(*args, **kwargs):
            calls.append(args)
            raise AssertionError("model was called")

        profile = {"ranker": {"names": []}}
        leaked = {"proposal": "Index the archive. Overall Rating: 8/10. Decision: Accept as Oral."}
        with self.assertRaisesRegex(ValueError, "identity leak"):
            priority.rank(leaked, Path("."), profile, Path("."), call=call)
        clean = {"proposal": "Index the archive labels for a planned catalogue."}
        legacy = {"proposal": "Write to ada.example@nowhere.edu. Best paper award."}
        with self.assertRaisesRegex(ValueError, "identity leak"):
            priority.rank(clean, Path("."), profile, Path("."), legacy=legacy, call=call)
        with self.assertRaisesRegex(ValueError, "identity leak"):
            priority.rank(
                leaked,
                Path("."),
                profile,
                Path("."),
                screen_receipt={"cleared_packet": digest(leaked)},
                call=call,
            )
        self.assertEqual(calls, [])

    def test_forecast_source_is_checked_when_legacy_is_selected(self) -> None:
        profile = _proposal_profile("forecast", "old_novelty")
        clean = {"proposal": "Index archive labels for a planned catalogue of names."}
        legacy = {"proposal": "Decision: Accept as Oral."}
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "identity leak"):
                priority.rank(
                    clean,
                    Path(tmp),
                    profile,
                    Path(tmp),
                    legacy=legacy,
                    call=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called")),
                )


class RetrievalExclusionTests(unittest.TestCase):
    def test_contrast_exclude_key_drops_the_target_even_when_text_differs(self) -> None:
        target = {"problem": "Catalogue pollen grains with a bench microscope."}
        own = {
            "key": "target-key",
            "forum": "target-forum",
            "title": "The Paper",
            "arxiv": "2099.00001v2",
            "target": 4.8,
            "proposal": {"problem": "Catalogue pollen slides with a bench microscope and a log."},
            "opinions": ["SECRET-LABEL"],
        }
        bank = [
            own,
            {
                "key": "near-low",
                "target": 2.0,
                "proposal": {"problem": "Catalogue pollen slides for a different bench microscope."},
                "opinions": ["low-note"],
            },
            {
                "key": "near-high",
                "target": 4.2,
                "proposal": {"problem": "Catalogue pollen slides beside another bench microscope."},
                "opinions": ["high-note"],
            },
            {
                "key": "far-a",
                "target": 1.1,
                "proposal": {"problem": "Forge hammer iron stock in a workshop bay."},
                "opinions": ["far-a"],
            },
            {
                "key": "far-b",
                "target": 3.5,
                "proposal": {"problem": "Forge hammer iron stock beside a workshop anvil."},
                "opinions": ["far-b"],
            },
            {
                "key": "far-c",
                "target": 2.4,
                "proposal": {"problem": "Forge hammer iron stock under a workshop lamp."},
                "opinions": ["far-c"],
            },
            {
                "key": "wood-a",
                "target": 1.4,
                "proposal": {"problem": "Map birch wood grain in a quiet workshop."},
                "opinions": ["wood-a"],
            },
            {
                "key": "wood-b",
                "target": 4.0,
                "proposal": {"problem": "Map spruce wood grain in a quiet workshop."},
                "opinions": ["wood-b"],
            },
        ]
        for row in bank:
            row.setdefault("split", "dev")
            row.setdefault("year", 2018)
        leaked, _scores = contrast.packet(target, bank)
        self.assertIn("SECRET-LABEL", json.dumps(leaked))
        for kwargs in (
            {"exclude_key": "target-key"},
            {"exclude_forum": "target-forum"},
            {"exclude_arxiv": "arxiv:2099.00001"},
            {"exclude_title": "the paper"},
        ):
            hidden, _scores = contrast.packet(target, bank, **kwargs)
            self.assertNotIn("SECRET-LABEL", json.dumps(hidden), kwargs)

    def test_rank_passes_exclude_key_and_retrieval_cutoffs(self) -> None:
        captured = {}

        def stop_contrast(*args, **kwargs):
            captured["exclude_key"] = kwargs.get("exclude_key")
            captured["exclude_forum"] = kwargs.get("exclude_forum")
            captured["exclude_title"] = kwargs.get("exclude_title")
            raise RuntimeError("captured contrast")

        def stop_retrieve(*args, **kwargs):
            captured["retrieve"] = kwargs
            raise RuntimeError("captured retrieve")

        packet = {
            "proposal": "Catalogue archival indexing synonyms for transcript lookup dictionaries."
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "contrast-bank.json", [])
            with patch.object(contrast, "packet", side_effect=stop_contrast):
                with self.assertRaisesRegex(RuntimeError, "captured contrast"):
                    priority.rank(
                        packet,
                        root,
                        _proposal_profile("contrast", "pair_priority"),
                        root,
                        exclude_key="target-key",
                        forum="target-forum",
                        title="The Paper",
                        call=lambda *args, **kwargs: None,
                    )
            with patch.object(priority, "retrieve", side_effect=stop_retrieve):
                with self.assertRaisesRegex(RuntimeError, "captured retrieve"):
                    priority.rank(
                        packet,
                        root,
                        _proposal_profile("challenge", "crit_originality"),
                        root,
                        exclude_key="target-key",
                        forum="target-forum",
                        arxiv="2099.00001v3",
                        year=2024,
                        cutoff_ms=50,
                        title="The Paper",
                        call=lambda *args, **kwargs: None,
                    )
        self.assertEqual(captured["exclude_key"], "target-key")
        self.assertEqual(captured["exclude_forum"], "target-forum")
        self.assertEqual(captured["exclude_title"], "The Paper")
        self.assertEqual(captured["retrieve"]["exclude_forum"], "target-forum")
        self.assertEqual(captured["retrieve"]["exclude_arxiv"], "2099.00001v3")
        self.assertEqual(captured["retrieve"]["target_year"], 2024)
        self.assertEqual(captured["retrieve"]["cutoff_ms"], 50)

    def test_prior_index_excludes_forum_arxiv_title_and_later_years(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus.jsonl"
            rows = [
                {
                    "title": "Earlier adaptive indexing",
                    "abstract": "Adaptive indexing of archive transcripts with sparse lookup dictionaries.",
                    "year": 2021,
                    "revision_ms": 10,
                    "forum": "earlier",
                    "arxiv": "2099.00010",
                },
                {
                    "title": "Same forum rewrite",
                    "abstract": "Adaptive indexing of archive transcripts under a new forum title.",
                    "year": 2020,
                    "revision_ms": 11,
                    "forum": "target-forum",
                    "arxiv": "2099.00011",
                },
                {
                    "title": "The Paper",
                    "abstract": "Adaptive indexing of archive transcripts with a normalized title twin.",
                    "year": 2019,
                    "revision_ms": 12,
                    "forum": "title-twin",
                    "arxiv": "2099.00012",
                },
                {
                    "title": "Arxiv twin",
                    "abstract": "Adaptive indexing of archive transcripts for the preprint twin.",
                    "year": 2018,
                    "revision_ms": 13,
                    "forum": "arxiv-twin",
                    "arxiv": "2099.00001v2",
                },
                {
                    "title": "Later adaptive indexing",
                    "abstract": "Adaptive indexing of archive transcripts after the target year.",
                    "year": 2024,
                    "revision_ms": 14,
                    "forum": "later-year",
                    "arxiv": "2099.00014",
                },
                {
                    "title": "Late revision",
                    "abstract": "Adaptive indexing of archive transcripts revised after the cutoff.",
                    "year": 2017,
                    "revision_ms": 90,
                    "forum": "late-revision",
                    "arxiv": "2099.00015",
                },
            ]
            rows.extend(
                {
                    "title": f"Filler {index}",
                    "abstract": "Unrelated quartz specimen notes.",
                    "year": 2016,
                    "revision_ms": 1,
                    "forum": f"filler-{index}",
                    "arxiv": f"2099.200{index:02d}",
                }
                for index in range(24)
            )
            for row in rows:
                row.setdefault("split", "dev")
            corpus.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            database = root / "prior.sqlite"
            build(corpus, database)
            hits = retrieve(
                database,
                {"problem": "Adaptive indexing of archive transcripts"},
                "The Paper",
                "",
                exclude_forum="target-forum",
                exclude_arxiv="https://arxiv.org/abs/2099.00001v9",
                target_year=2024,
                cutoff_ms=80,
            )
            forums = [hit["forum"] for hit in hits]
            self.assertIn("earlier", forums)
            for blocked in ("target-forum", "title-twin", "arxiv-twin", "later-year", "late-revision"):
                self.assertNotIn(blocked, forums)


class FirstReviewTests(unittest.TestCase):
    def test_first_review_time_is_computed_before_signature_dedup(self) -> None:
        group = "ICLR.cc/2024/Conference"
        forum = "fixture-revised"
        late = {
            "id": forum,
            "forum": forum,
            "number": 7,
            "tmdate": 150,
            "invitations": [group + "/-/Submission"],
            "content": {
                "abstract": {"value": "We propose a late catalogue of symbols."},
                "primary_area": {"value": "optimization"},
            },
        }
        early = {
            **late,
            "tmdate": 50,
            "content": {
                "abstract": {"value": "We propose an early catalogue of symbols."},
                "primary_area": {"value": "optimization"},
            },
        }

        def review(note_id, created, modified, text):
            return {
                "id": note_id,
                "forum": forum,
                "replyto": forum,
                "signatures": ["signer"],
                "tcdate": created,
                "tmdate": modified,
                "invitations": [f"{group}/Submission7/-/Official_Review"],
                "content": {
                    "strengths": {"value": text},
                    "rating": {"value": "6: marginally above"},
                },
            }

        original = review("first-note", 100, 100, "The early plan is concrete.")
        revised = review("later-note", 200, 200, "The revised plan is concrete.")
        self.assertIsNone(iclr_case(late, [original, revised], group))
        allowed = iclr_case(late, [original, revised], group, allow_late=True)
        self.assertFalse(allowed["provenance"]["pre_review"])
        self.assertEqual(allowed["provenance"]["first_review_created_ms"], 100)
        case = iclr_case(late, [original, revised, early], group)
        self.assertTrue(case["provenance"]["pre_review"])
        self.assertEqual(case["provenance"]["submission_modified_ms"], 50)
        packet = json.dumps(case)
        self.assertIn("early catalogue", packet)
        self.assertNotIn("late catalogue", packet)
        self.assertIn("revised plan", packet)
        self.assertNotIn("early plan is concrete", packet)


class AuditAndCohortTests(unittest.TestCase):
    def test_unsupported_audit_conclusions_are_sentence_filtered(self) -> None:
        proposal = {
            "mechanism": "Gather the tokens. Overall Rating: 8/10. Decision: Accept as Oral."
        }
        source = {"s1": "Gather the tokens with a counter."}
        request = source_evidence.packet(source, proposal)
        stated = {key: ["stated", "s1"] for key in request["claims"]}
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)

            def invoke(stage, body, folder, model, command, timeout, *, cached_only=False):
                path = cache / "audit.json"
                visible = {key: ["stated", "s1"] for key in body["claims"]}
                write(path, {"output": visible})
                return path

            with patch.object(stages, "invoke", side_effect=invoke):
                audited = source_evidence.audit(source, proposal, cache)
        text = audited["proposal"]["mechanism"]
        self.assertEqual(text, "Gather the tokens.")
        self.assertNotIn("Oral", text)
        quote_source = {
            "r1.novelty": {
                "units": ["The pairing is creative. Decision: Accept as Oral."]
            }
        }
        with self.assertRaisesRegex(ValueError, "identity text"):
            stages.validate(
                "audit",
                {"r1.novelty": ["Decision: Accept as Oral."]},
                quote_source,
            )
        self.assertEqual(
            stages.validate(
                "audit",
                {"r1.novelty": ["The pairing is creative."]},
                quote_source,
            )["r1.novelty"],
            ["The pairing is creative."],
        )

    def test_evaluate_rank_accepts_any_n_and_stratified_bootstrap(self) -> None:
        self.assertEqual(STRATIFIED_DRAWS, 3000)
        ids = [f"synthetic-{index:03d}" for index in range(199)]
        manifest = {
            "version": 1,
            "cases": [
                {"forum": forum, "split": "test", "stratum": "low" if index < 100 else "high"}
                for index, forum in enumerate(ids)
            ],
            "ids_sha256": digest(sorted(ids)),
        }
        predictions = [{"forum": forum, "overall": 1 + (index % 5)} for index, forum in enumerate(ids)]
        references = [
            {
                "forum": forum,
                "prospective": 1 + (index % 5),
                "final_rating": 0 if index % 2 == 0 else 6,
                "year": 2026,
                "rating_scale": "iclr2026",
                "accept": None if index == 0 else index % 2,
                "stratum": "low" if index < 100 else "high",
            }
            for index, forum in enumerate(ids)
        ]
        validate_manifest(manifest)
        validate_predictions(predictions)
        validate_references(references)
        scored = evaluate_fixed(manifest, predictions, references, bootstrap=0, stratified_draws=40)
        self.assertEqual(scored["n"], 199)
        self.assertEqual(scored["decision_n"], 198)
        self.assertEqual(scored["stratified_draws"], 40)
        self.assertEqual(scored["top_fraction"]["k"], 40)
        interval = scored["stratified_ci95"]["rho"]
        self.assertLessEqual(interval[0], scored["rho"])
        self.assertGreaterEqual(interval[1], scored["rho"])
        rows = [
            {
                "key": str(index),
                "target": 1 + index,
                "rating": index,
                "accept": index % 2,
                "features": {"score": 1 + index},
                "stratum": f"only-{index}",
            }
            for index in range(4)
        ]
        point = stratified_ci(rows, {"method": "mean", "names": ["score"]}, draws=20, seed=1)
        self.assertAlmostEqual(point["rho"][0], 1.0)
        self.assertAlmostEqual(point["rho"][1], 1.0)

    def test_reproduce_script_scores_a_supplied_cohort(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "reproduce_proposal_rank.py"
        partial = subprocess.run(
            [sys.executable, str(script), "--manifest", "missing.json"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(partial.returncode, 2)
        self.assertIn("Pass --manifest, --predictions, and --references together.", partial.stderr)
        absent = subprocess.run(
            [sys.executable, str(script), "--root", str(Path(__file__).parent / "no-such-repro")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(absent.returncode, 2)
        self.assertIn("not in the repository", absent.stderr)
        self.assertNotIn("reproduced from committed artifacts", absent.stderr)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = ["synthetic-a", "synthetic-b"]
            manifest = {
                "version": 1,
                "ids_sha256": digest(sorted(ids)),
                "cases": [{"forum": forum, "split": "test", "stratum": "s"} for forum in ids],
            }
            predictions = [{"forum": forum, "overall": 3} for forum in ids]
            references = [
                {
                    "forum": forum,
                    "prospective": 3,
                    "final_rating": 6,
                    "accept": 1,
                    "year": 2024,
                    "stratum": "s",
                }
                for forum in ids
            ]
            write(root / "manifest.json", manifest)
            write(root / "predictions.json", predictions)
            write(root / "references.json", references)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--manifest",
                    str(root / "manifest.json"),
                    "--predictions",
                    str(root / "predictions.json"),
                    "--references",
                    str(root / "references.json"),
                    "--bootstrap",
                    "0",
                    "--stratified-draws",
                    "10",
                    "--output",
                    str(root / "metrics.json"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            saved = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["n"], 2)
            self.assertNotIn("not yet reproducible", completed.stdout)
            self.assertNotIn("published_headlines", saved)

    def test_cli_import_does_not_load_sqlite3(self) -> None:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "from autopaperreview.cli import build_parser\n"
                "build_parser()\n"
                "raise SystemExit(0 if 'sqlite3' not in sys.modules else 1)\n",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)


class AdversarialRegressionTests(unittest.TestCase):
    def test_nonpositive_years_are_rejected_and_unregistered_years_stay_generic(self) -> None:
        for year in (0, -1):
            with self.assertRaisesRegex(ValueError, "positive venue year"):
                scale_for_year(year)
            with self.assertRaisesRegex(ValueError, "positive venue year"):
                reference_coding.validate_cases(
                    [{"forum": "synthetic-year", "year": year, "reviews": []}]
                )
        self.assertEqual(scale_for_year(2027).key, "range:0-10")
        self.assertFalse(label_leak("We accept the assumption that the catalogue is finite."))
        self.assertFalse(label_leak("Use one of the planned catalogues for the index."))

    def test_indirect_obfuscated_and_legacy_reported_text_never_reaches_a_call(self) -> None:
        facts = [
            "Rating：8",
            "Decision：Accept",
            "I assign a score of eight.",
            "The score is eight out of ten.",
            "I recommend acceptance of the idea.",
            "Write to a\u200bda@example.com about the plan.",
            "See forum?id=AbCdEfGh12 in the note.",
            "Write to ada [at] example [dot] edu about the plan.",
            "Write to ada(at)example(dot)edu about the plan.",
            "Write to ada AT example DOT edu about the plan.",
            "See export.arxiv.org/pdf/2099.00001?utm=1 for the draft.",
            "The best paper award goes to this idea.",
        ]
        paraphrases = [
            "This paper should be accepted at the conference.",
            "The work deserves to be published.",
        ]
        calls = []

        def call(*args, **kwargs):
            calls.append(args)
            raise AssertionError("model was called")

        profile = {"ranker": {"names": [], "method": "mean"}}
        for text in facts:
            with self.assertRaisesRegex(ValueError, "identity leak"):
                priority.rank(
                    {"proposal": "Index the archive. " + text},
                    Path("."),
                    profile,
                    Path("."),
                    call=call,
                )
        for text in paraphrases:
            self.assertFalse(label_leak(text))
            packet = {"proposal": "Index the archive. " + text}
            for verdict in ("leak", "unsure", {"verdict": "unsure"}):
                with self.assertRaisesRegex(ValueError, "leak audit blocked"):
                    priority.rank(
                        packet,
                        Path("."),
                        profile,
                        Path("."),
                        call=call,
                        leak_judge=lambda _proposal, verdict=verdict: verdict,
                    )
            priority.rank(
                packet,
                Path("."),
                profile,
                Path("."),
                call=call,
                leak_judge=lambda _proposal: "clean",
            )
        legacy = {"proposal": "Our experiments show a higher accuracy on the planned task."}
        clean = {"proposal": "Index archive labels for a planned catalogue of names."}
        with self.assertRaisesRegex(ValueError, "reported outcomes"):
            priority.rank(clean, Path("."), profile, Path("."), legacy=legacy, call=call)
        with self.assertRaisesRegex(ValueError, "nested proposal field"):
            priority.rank(
                {"problem": {"text": "Index the archive.", "decision": "Accept"}},
                Path("."),
                profile,
                Path("."),
                call=call,
            )
        self.assertEqual(calls, [])

    def test_contrast_drops_test_rows_future_years_query_arxiv_and_reported_excerpts(self) -> None:
        target = {"problem": "Catalogue pollen grains with a bench microscope."}

        def example(key, opinion, **extra):
            row = {
                "key": key,
                "split": "dev",
                "year": 2020,
                "target": extra.pop("target", 4.8),
                "proposal": {
                    "problem": extra.pop(
                        "problem",
                        "Catalogue pollen slides with a bench microscope and a log.",
                    )
                },
                "opinions": [opinion, "Our experiments show a gain on the same bench."],
            }
            row.update(extra)
            return row

        bank = [
            example("query-paper", "QUERY-PAPER", arxiv="https://arxiv.org/abs/2099.00001?utm=1"),
            example("future-dev", "FUTURE-DEV", year=2099, split="dev"),
            example("other-test", "OTHER-TEST", year=2020, split="test"),
            example("spaced-test", "SPACED-TEST", split=" Test "),
            example("held-out", "HELD-OUT", split="HOLD OUT"),
            example("no-year", "NO-YEAR", year=None),
            example("no-split", "NO-SPLIT", split=None),
            example(
                "near-low",
                "low-note",
                target=2.0,
                problem="Catalogue pollen slides for a different bench microscope.",
            ),
            example(
                "near-high",
                "high-note",
                target=4.2,
                problem="Catalogue pollen slides beside another bench microscope.",
            ),
            example("far-a", "far-a", target=1.1, problem="Forge hammer iron stock in a workshop bay."),
            example("far-b", "far-b", target=3.5, problem="Forge hammer iron stock beside a workshop anvil."),
            example("wood-a", "wood-a", target=1.4, problem="Map birch wood grain in a quiet workshop."),
            example("wood-b", "wood-b", target=4.0, problem="Map spruce wood grain in a quiet workshop."),
        ]
        hidden, _scores = contrast.packet(target, bank, exclude_arxiv="2099.00001")
        text = json.dumps(hidden)
        for blocked in (
            "QUERY-PAPER",
            "FUTURE-DEV",
            "OTHER-TEST",
            "SPACED-TEST",
            "HELD-OUT",
            "NO-YEAR",
            "NO-SPLIT",
            "experiments show",
        ):
            self.assertNotIn(blocked, text)
        self.assertIn("low-note", text)

    def test_prior_index_without_cutoff_skips_future_and_bad_timestamps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {
                    "title": "Earlier adaptive indexing",
                    "abstract": "Adaptive indexing of archive transcripts with sparse lookup dictionaries.",
                    "year": 2021,
                    "revision_ms": 10,
                    "forum": "earlier",
                    "arxiv": "2099.00010",
                },
                {
                    "title": "Future sentinel indexing",
                    "abstract": "Adaptive indexing of archive transcripts in a far future year.",
                    "year": 2099,
                    "revision_ms": 11,
                    "forum": "year-2099",
                    "arxiv": "2099.00099",
                },
                {
                    "title": "Missing stamp indexing",
                    "abstract": "Adaptive indexing of archive transcripts with no usable stamp.",
                    "year": None,
                    "revision_ms": "not-a-time",
                    "forum": "bad-stamp",
                    "arxiv": "2099.00098",
                },
                {
                    "title": "Earlier adaptive indexing companion",
                    "abstract": "Adaptive indexing of archive transcripts with a second sparse dictionary.",
                    "year": 2018,
                    "revision_ms": 8,
                    "forum": "earlier-peer",
                    "arxiv": "2099.00009",
                },
            ]
            rows.extend(
                {
                    "title": f"Filler {index}",
                    "abstract": "Unrelated quartz specimen notes.",
                    "year": 2016,
                    "revision_ms": 1,
                    "forum": f"filler-{index}",
                    "arxiv": f"2099.300{index:02d}",
                }
                for index in range(24)
            )
            corpus = root / "corpus.jsonl"
            rows.append(
                {
                    "title": "Infinite stamp indexing",
                    "abstract": "Adaptive indexing of archive transcripts with a non-finite stamp.",
                    "year": 2015,
                    "revision_ms": "inf",
                    "forum": "infinite-stamp",
                    "arxiv": "2099.00097",
                    "split": "dev",
                }
            )
            for row in rows:
                row.setdefault("split", "dev")
            rows.append(
                {
                    "title": "Unlabeled split indexing",
                    "abstract": "Adaptive indexing of archive transcripts with no cohort split.",
                    "year": 2014,
                    "revision_ms": 9,
                    "forum": "missing-split",
                    "arxiv": "2099.00096",
                    "split": None,
                }
            )
            corpus.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            database = root / "prior.sqlite"
            build(corpus, database)
            with self.assertRaises(ValueError):
                retrieve(
                    database,
                    {"problem": "Adaptive indexing of archive transcripts"},
                    cutoff_ms=float("inf"),
                )
            hits = retrieve(database, {"problem": "Adaptive indexing of archive transcripts"})
            forums = [hit["forum"] for hit in hits]
            self.assertIn("earlier", forums)
            self.assertNotIn("year-2099", forums)
            self.assertNotIn("bad-stamp", forums)
            self.assertNotIn("infinite-stamp", forums)
            self.assertNotIn("missing-split", forums)
            self.assertTrue(hits)
            self.assertTrue(all(hit["split"] == "dev" for hit in hits))

    def test_missing_or_zero_review_time_is_not_pre_review_and_ties_are_stable(self) -> None:
        group = "ICLR.cc/2024/Conference"
        forum = "fixture-time"
        late = {
            "id": forum,
            "forum": forum,
            "number": 7,
            "tmdate": 150,
            "invitations": [group + "/-/Submission"],
            "content": {"abstract": {"value": "We propose a late catalogue of symbols."}},
        }
        early = {
            **late,
            "tmdate": 50,
            "content": {"abstract": {"value": "We propose an early catalogue of symbols."}},
        }

        def review(note_id, created, rating):
            return {
                "id": note_id,
                "forum": forum,
                "replyto": forum,
                "signatures": ["signer"],
                "tcdate": created,
                "tmdate": 100,
                "invitations": [f"{group}/Submission7/-/Official_Review"],
                "content": {
                    "strengths": {"value": "The plan is concrete and specific."},
                    "rating": {"value": rating},
                    "decision": {"value": "Accept"},
                },
            }

        undated = review("undated-note", 0, "6: marginally above")
        case = iclr_case(late, [undated, early], group, allow_late=True)
        self.assertFalse(case["provenance"]["pre_review"])
        self.assertIsNone(case["provenance"]["first_review_created_ms"])
        self.assertNotIn("early catalogue", json.dumps(case["candidate"]))
        missing = review("missing-note", 100, "6: marginally above")
        missing.pop("tcdate")
        case = iclr_case(late, [missing, early], group, allow_late=True)
        self.assertFalse(case["provenance"]["pre_review"])
        low = review("a-note", 100, "6: marginally above")
        high = review("m-note", 100, "8: accept")
        self.assertEqual(
            [note["id"] for note in latest_official_reviews([low, high])],
            ["m-note"],
        )
        self.assertEqual(
            [note["id"] for note in latest_official_reviews([high, low])],
            ["m-note"],
        )
        forward = iclr_case(late, [low, high, early], group)
        backward = iclr_case(late, [high, low, early], group)
        self.assertEqual(
            forward["provenance"]["final_mean"],
            backward["provenance"]["final_mean"],
        )
        self.assertEqual(forward["provenance"]["final_mean"], 8)
        noisy = review("z-note", 100, "6: marginally above")
        noisy["tmdate"] = None
        self.assertEqual(latest_official_reviews([noisy, high])[0]["id"], "m-note")
        self.assertEqual(official_rating_summary([high], 2024)["final_mean"], 8)
        plus = review("plus-note", 100, "8: accept")
        plus["tmdate"] = "+120"
        older = review("older-note", 100, "6: marginally above")
        older["tmdate"] = 50
        self.assertEqual(latest_official_reviews([older, plus])[0]["id"], "plus-note")
        same_a = review("same-id", 100, "8: accept")
        same_b = review("same-id", 100, "6: marginally above")
        same_a["tmdate"] = "+120"
        same_b["tmdate"] = 120
        same_a["content"] = {**same_a["content"], "strengths": {"value": "Alpha record is specific."}}
        same_b["content"] = {**same_b["content"], "strengths": {"value": "Beta record is specific."}}
        self.assertEqual(
            [note["content"]["strengths"]["value"] for note in latest_official_reviews([same_a, same_b])],
            [note["content"]["strengths"]["value"] for note in latest_official_reviews([same_b, same_a])],
        )

    def test_reference_offsets_use_the_original_unit_and_prose_strips_decisions(self) -> None:
        original = "Decision: Accept as Oral. The proposed test is explicit."
        quote = "The proposed test is explicit."
        row = {
            "forum": "fixture-offset",
            "reviews": [{"id": "note-1", "units": {"u": original}}],
        }
        with TemporaryDirectory() as tmp:

            def invoke(stage, source, folder, model, command, timeout, *, cached_only):
                blob = json.dumps(source)
                self.assertNotIn("Accept", blob)
                self.assertIn(quote, blob)
                output = (
                    {"r1": [{"id": "u", "quote": quote}]}
                    if stage == "ref_extract"
                    else {"r1": {"score": 4, "used": [0]}}
                )
                path = Path(tmp) / f"{stage}.json"
                write(path, {"output": output})
                return path

            with patch.object(reference_coding, "invoke", side_effect=invoke):
                coded = reference_coding.code_paper(row, Path(tmp), ["judge"])
        evidence = coded["reviewers"][0]["evidence"][0]
        self.assertGreater(evidence["start"], 0)
        self.assertEqual(original[evidence["start"] : evidence["end"]], quote)
        self.assertIn("text_cleaning_sha256", reference_coding.protocol(["judge"]))
        group = "ICLR.cc/2024/Conference"
        forum = "fixture-prose"
        submission = {
            "id": forum,
            "forum": forum,
            "number": 3,
            "tmdate": 40,
            "invitations": [group + "/-/Submission"],
            "content": {"abstract": {"value": "We propose a catalogue of symbols for the index."}},
        }
        note = {
            "id": "prose-note",
            "forum": forum,
            "replyto": forum,
            "signatures": ["signer"],
            "tcdate": 80,
            "tmdate": 80,
            "invitations": [f"{group}/Submission3/-/Official_Review"],
            "content": {
                "strengths": {
                    "value": "The method is clear and the plan is concrete. This paper should be accepted."
                },
                "weaknesses": {"value": "Rating：8. I assign a score of eight."},
                "questions": {"value": "Write to ada [at] example [dot] edu before the study."},
                "decision": {"value": "Accept"},
                "rating": {"value": "8: accept"},
                "confidence": {"value": "4: confident"},
            },
        }
        case = iclr_case(submission, [note], group)
        packet = json.dumps(case["comments"])
        self.assertIn("method is clear", packet)
        self.assertIn("should be accepted", packet)
        for blocked in ("Rating", "score of eight", "ada", "Accept", "confident"):
            self.assertNotIn(blocked, packet, blocked)

    def test_row_year_and_cutoff_cannot_override_the_cohort_lock(self) -> None:
        ids = [f"synthetic-{index:03d}" for index in range(100)]
        manifest = {
            "cases": [
                {"forum": fid, "split": "test", "year": 2024, "cutoff_ms": 10} for fid in ids
            ],
            "ids_sha256": digest(sorted(ids)),
        }
        conflict = [
            {"forum": fid, "proposal": "Map old diary labels.", "year": 2099, "cutoff_ms": 99}
            for fid in ids
        ]
        with self.assertRaisesRegex(ValueError, "disagrees with the cohort lock"):
            blind_inputs(manifest, conflict)
        unlocked = {
            "cases": [{"forum": fid, "split": "test"} for fid in ids],
            "ids_sha256": digest(sorted(ids)),
        }
        introduced = [
            {"forum": fid, "proposal": "Map old diary labels.", "year": 2099} for fid in ids
        ]
        with self.assertRaisesRegex(ValueError, "must be set on the cohort lock"):
            blind_inputs(unlocked, introduced)
        matched = [
            {"forum": fid, "proposal": "Map old diary labels.", "year": 2024, "submission_ms": 10}
            for fid in ids
        ]
        rows = blind_inputs(manifest, matched)
        self.assertEqual(rows[0][2]["year"], 2024)
        self.assertEqual(rows[0][2]["cutoff_ms"], 10)
        locked_arxiv = {
            "cases": [
                {"forum": fid, "split": "test", "arxiv": "2099.00001"} for fid in ids
            ],
            "ids_sha256": digest(sorted(ids)),
        }
        override = [
            {"forum": fid, "proposal": "Map old diary labels.", "arxiv": "2099.00002"}
            for fid in ids
        ]
        with self.assertRaisesRegex(ValueError, "disagrees with the cohort lock"):
            blind_inputs(locked_arxiv, override)
        row_only = [
            {"forum": fid, "proposal": "Map old diary labels.", "arxiv_id": "2099.00002"}
            for fid in ids
        ]
        with self.assertRaisesRegex(ValueError, "must be set on the cohort lock"):
            blind_inputs(unlocked, row_only)
        kept = blind_inputs(
            locked_arxiv,
            [{"forum": fid, "proposal": "Map old diary labels."} for fid in ids],
        )
        self.assertEqual(kept[0][2]["arxiv"], "2099.00001")
        reference = {
            "forum": ids[0],
            "prospective": 3,
            "final_rating": 6,
            "accept": 1,
            "year": 2026,
            "rating_scale": "iclr2026",
        }
        one = {
            "cases": [{"forum": ids[0], "split": "test", "year": 2024}],
            "ids_sha256": digest([ids[0]]),
        }
        with self.assertRaisesRegex(ValueError, "disagrees with the cohort lock"):
            evaluate_fixed(
                one,
                [{"forum": ids[0], "overall": 3}],
                [reference],
                bootstrap=0,
                stratified_draws=0,
            )

    def test_stratified_interval_does_not_depend_on_row_order(self) -> None:
        spec = {"method": "mean", "names": ["score"]}

        def row(key, score):
            return {
                "key": key,
                "target": score,
                "rating": score,
                "accept": 1,
                "features": {"score": score},
                "stratum": "same",
            }

        forward = stratified_ci([row("b", 1), row("a", 5)], spec, draws=25, seed=3)
        backward = stratified_ci([row("a", 5), row("b", 1)], spec, draws=25, seed=3)
        self.assertEqual(forward, backward)

    def test_duplicate_excerpts_use_each_original_offset(self) -> None:
        original = "The plan is concrete. The plan is concrete."
        quote = "The plan is concrete."
        result = reference_coding.reviewer_result(
            {"id": "n", "units": {"u": original}},
            {},
            [{"id": "u", "quote": quote}, {"id": "u", "quote": quote}],
            {"score": 4, "used": [0, 1]},
        )
        spans = result["evidence"]
        self.assertEqual(original[spans[0]["start"] : spans[0]["end"]], quote)
        self.assertGreater(spans[1]["start"], spans[0]["end"] - 1)
        self.assertEqual(original[spans[1]["start"] : spans[1]["end"]], quote)

    def test_pair_material_drops_unsupported_sentences(self) -> None:
        from autopaperreview.proposal.material import material

        proposal = {
            "problem": "Count the tokens. Invent an unsupported widget.",
            "prior_alternatives": None,
            "mechanism": None,
            "assumptions": None,
            "discriminating_test": None,
            "intended_value": None,
        }
        source = {"u": "Count the tokens."}
        item = {
            "id": "a",
            "overall": 3,
            "proposal": proposal,
            "source_audit": {
                "source_units": source,
                "proposal_sha256": digest(proposal),
                "source_sha256": digest(source),
                "claims": {
                    "u1": {"text": "Count the tokens.", "status": "stated", "field": "problem"},
                    "u2": {
                        "text": "Invent an unsupported widget.",
                        "status": "unsupported",
                        "field": "problem",
                    },
                },
            },
            "outcome_screen": {"proposal": {}},
        }
        sentences = material(item)
        self.assertIn("Count the tokens.", sentences)
        self.assertNotIn("Invent an unsupported widget.", sentences)


if __name__ == "__main__":
    unittest.main()
