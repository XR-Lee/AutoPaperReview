import json
import tempfile
import unittest
from pathlib import Path

from autopaperreview.proposal import merit
from autopaperreview.proposal.exemplars import packet
from autopaperreview.proposal.overall_rank import evaluate, fit_ridge, predict
from autopaperreview.proposal.prior_index import build, retrieve
from autopaperreview.proposal.priority import stage_for_feature, text_packet


def clean_judge(_):
    return {"verdict": "clean"}


class TestProposalPriority(unittest.TestCase):
    def test_fixed_auc_ties_and_missing_values(self):
        rows = [
            {
                "key": str(i),
                "target": t,
                "accept": int(t >= 3.5),
                "rating": t,
                "features": {"novelty": x},
            }
            for i, (t, x) in enumerate([(2, 2), (3, 3), (3.5, 3), (4, 5)])
        ]
        spec = {
            "method": "weighted",
            "names": ["novelty", "reach"],
            "weights": [0.8, 0.2],
        }
        result = evaluate(rows, spec, 20)
        self.assertEqual(result["positive"], 2)
        self.assertEqual(result["auc"], 0.875)
        self.assertEqual(predict({"novelty": 2}, spec), 2)
        with self.assertRaises(ValueError):
            evaluate(rows + rows[:1], spec)
        fit = fit_ridge(rows, ["novelty", "reach"], 4)
        self.assertGreater(predict({"novelty": 4}, fit), predict({"novelty": 2}, fit))

    def test_learning_examples_exclude_target_and_holdout(self):
        rows = [
            {
                "split": "dev",
                "key": str(i),
                "year": 2020,
                "revision_ms": 10,
                "opinion_ms": 11,
                "target": 2 + i / 8,
                "proposal": {"problem": f"Offline archive fixture {i}"},
                "excerpts": [f"opinion-{i}"],
            }
            for i in range(12)
        ]
        rows.append({**rows[0], "split": "test", "excerpts": ["SECRET"]})
        actual = packet({"problem": "archive"}, rows, "0", target_year=2024, cutoff_ms=20)
        self.assertNotIn("SECRET", str(actual))
        self.assertNotIn("'opinion-0'", str(actual))
        self.assertEqual(len(actual["examples"]), 4)

    def test_reference_citation_alignment_and_abstention(self):
        notes = {"r1": {"s1": "The linkage would help catalogue readers."}}
        with self.assertRaises(ValueError):
            merit.validate(
                "ref_extract",
                {"r1": [{"id": "s1", "quote": "fabricated positive verdict"}]},
                notes,
            )
        source = merit.reference_packet(notes, {"r1": []})
        self.assertEqual(
            merit.validate("ref_grade", {"r1": {"score": None, "used": []}}, source)[
                "r1"
            ]["score"],
            None,
        )
        with self.assertRaises(ValueError):
            merit.validate("ref_grade", {"r1": {"score": 4, "used": [0]}}, source)

    def test_design_strips_past_result_sentences(self):
        src = {"u1": "Study indexing of local measurement names."}
        out = {k: {"text": None, "evidence": []} for k in merit.DESIGN_FIELDS}
        out["problem"] = {
            "text": "Study archive indexing. Our experiments demonstrate a gain.",
            "evidence": ["u1"],
        }
        self.assertEqual(
            merit.validate("design", out, src)["problem"]["text"],
            "Study archive indexing.",
        )
        with self.assertRaises(ValueError):
            text_packet({"decision": "hidden"})
        self.assertEqual(stage_for_feature("crit_originality"), "challenge")

    def test_prior_index_excludes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            p = r / "corpus.jsonl"
            db = r / "prior.db"
            rows = [
                {
                    "title": "Archival adaptive indexing",
                    "abstract": "Adaptive indexing with archival terms and synonyms.",
                    "year": 2021,
                    "revision_ms": 1,
                    "forum": "one",
                },
                {
                    "title": "Adaptive indexing and field archives",
                    "abstract": "Adaptive indexing of transcripts relies on rotating sparse lookup dictionaries.",
                    "year": 2022,
                    "revision_ms": 2,
                    "forum": "two",
                },
                {
                    "title": "Descriptive mark catalogues",
                    "abstract": "Adaptive indexing supports map keys for grouping painted symbols.",
                    "year": 2022,
                    "revision_ms": 3,
                    "forum": "three",
                },
            ] + [
                {
                    "title": f"Other fixture {i}",
                    "abstract": "Other unrelated materials.",
                    "year": 2021,
                    "revision_ms": 3,
                    "forum": str(i),
                }
                for i in range(9)
            ]
            for record in rows:
                record.setdefault("split", "dev")
            p.write_text("\n".join(json.dumps(record) for record in rows))
            build(p, db)
            hits = retrieve(
                db,
                {"problem": "Adaptive indexing in archives"},
                rows[0]["title"],
                rows[0]["abstract"],
                target_year=2024,
                cutoff_ms=20,
            )
            self.assertNotIn("one", [h["forum"] for h in hits])
            self.assertIn("two", [h["forum"] for h in hits])
            self.assertNotIn("rating", hits[0])


class TestFixedProposalBenchmark(unittest.TestCase):
    def test_blind_input_rejects_labels_and_partial_cohort(self):
        from autopaperreview.proposal.data import digest
        from autopaperreview.proposal.fixed_benchmark import blind_inputs

        ids = [f"synthetic-{i:03}" for i in range(100)]
        manifest = {
            "cases": [{"forum": fid, "split": "test"} for fid in ids],
            "ids_sha256": digest(ids),
        }
        records = [
            {"forum": fid, "proposal": "Map old diary labels."} for fid in reversed(ids)
        ]
        self.assertEqual([row[0] for row in blind_inputs(manifest, records)], ids)
        with self.assertRaisesRegex(ValueError, "complete fixed test"):
            blind_inputs(manifest, records[:-1])
        with self.assertRaisesRegex(ValueError, "blind proposal fields"):
            blind_inputs(
                manifest, [{**records[0], "review": "Synthetic opinion."}, *records[1:]]
            )

    def test_cached_only_miss_never_starts_a_provider(self):
        from unittest.mock import patch

        from autopaperreview.proposal.stages import invoke

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("autopaperreview.proposal.stages.run_command") as run,
        ):
            from autopaperreview.proposal.sanitized import sanitize_proposal

            with self.assertRaisesRegex(TypeError, "SanitizedProposal"):
                invoke(
                    "merit",
                    {"problem": "Catalogue sample names."},
                    Path(tmp),
                    "synthetic",
                    [],
                    2,
                    cached_only=True,
                    leak_judge=clean_judge,
                )
            with self.assertRaisesRegex(ValueError, "missing cached merit"):
                invoke(
                    "merit",
                    sanitize_proposal({"problem": "Catalogue sample names."}),
                    Path(tmp),
                    "synthetic",
                    [],
                    2,
                    cached_only=True,
                    leak_judge=clean_judge,
                )
            run.assert_not_called()

    def test_refuses_incomplete_or_changed_test_set(self):
        from autopaperreview.proposal.data import digest
        from autopaperreview.proposal.fixed_benchmark import evaluate_fixed

        ids = [f"case-{i}" for i in range(100)]
        manifest = {
            "cases": [{"forum": f, "split": "test"} for f in ids],
            "ids_sha256": digest(sorted(ids)),
        }
        pred = [{"forum": f, "overall": i / 25} for i, f in enumerate(ids)]
        gold = [
            {
                "forum": f,
                "prospective": 1 + i / 25,
                "final_rating": 1 + i / 12,
                "accept": int(i >= 60),
            }
            for i, f in enumerate(ids)
        ]
        result = evaluate_fixed(manifest, pred, gold, 0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["n"], 100)
        self.assertEqual(result["positive"], 37)
        with self.assertRaises(ValueError):
            evaluate_fixed(manifest, pred[:-1], gold, 0)
        with self.assertRaises(ValueError):
            evaluate_fixed({**manifest, "ids_sha256": "wrong"}, pred, gold, 0)

    def test_anonymous_panels_and_two_sided_calibration(self):
        from autopaperreview.proposal.preference import panel, score_panel

        bank = [
            {
                "key": str(i),
                "split": "dev",
                "year": 2020,
                "revision_ms": 10,
                "target_ms": 11,
                "target": 2 + i / 12,
                "proposal": {"problem": f"Archive notation {i}."},
            }
            for i in range(23)
        ]
        proposal = {"problem": "Compare how archival aliases are chosen."}
        a, g = panel(proposal, bank, 0, "4", target_year=2024, cutoff_ms=20)
        b, h = panel(proposal, bank, 1, "4", target_year=2024, cutoff_ms=20)
        self.assertEqual(len(a), 6)
        self.assertNotEqual(list(a.values()), list(b.values()))
        self.assertEqual(sum(v is None for v in h.values()), 1)
        self.assertNotIn("Archive notation 4.", str(a))
        self.assertNotIn("target", str(a))
        v = {
            k: {
                "priority": 50 if t is None else 10 * t,
                "advance": 50 if t is None else 10 * t,
            }
            for k, t in g.items()
        }
        self.assertEqual(score_panel(v, g)["wins_priority"], 5)


class TestPriorityBundle(unittest.TestCase):
    def test_conditional_priority_accounts_for_a_weak_mechanism(self):
        spec = {
            "method": "conditional",
            "advance": ["novelty"],
            "value": ["conditional_value"],
            "credibility": ["mechanism"],
        }
        inputs = {"novelty": 4.0, "conditional_value": 4.0}
        self.assertEqual(predict(inputs, spec), 4.0)
        self.assertLess(predict({**inputs, "mechanism": 1.0}, spec), 4.0)
        self.assertEqual(
            text_packet({"proposal": "A synthetic archive plan"}),
            {"proposal": "A synthetic archive plan"},
        )

    def test_historical_and_simulated_review_packets_are_distinct(self):
        proposal = {"problem": "Could catalogue names be made easier to compare?"}
        earlier = [
            {
                "forum": "archive",
                "title": "Name dictionaries",
                "abstract": "A hypothetical archive.",
                "year": 2020,
            }
        ]
        database = {
            "archive": [
                {
                    "opinion": "A useful way to compare names.",
                    "note": "training-example",
                    "opinion_ms": 10,
                }
            ]
        }
        out = merit.field_packet(proposal, earlier, database, cutoff_ms=20)
        self.assertNotIn("training-example", str(out))
        self.assertNotIn("earlier_peer_opinions", out["proposal"])
        self.assertEqual(
            out["earlier_research"][0]["earlier_peer_opinions"],
            ["A useful way to compare names."],
        )
        op = merit.opinion_packet({"r1": ["Mapping aliases would help."]})
        self.assertEqual(op["r1"][0]["context"], "Mapping aliases would help.")


class TestContrastiveProposalReview(unittest.TestCase):
    def test_comparison_uses_disjoint_examples_and_excludes_own_opinion(self):
        from autopaperreview.proposal import contrast

        target = {"problem": "Make archive catalogue aliases consistent."}
        words = [
            "birch",
            "spruce",
            "hickory",
            "maple",
            "pine",
            "oak",
            "willow",
            "ash",
            "linden",
            "cedar",
            "fir",
            "teak",
        ]
        bank = [
            {
                "key": str(i),
                "target": 1.5 + i % 4,
                "proposal": {
                    "problem": f"Record {w} wood grain orientation in a workshop."
                },
                "opinions": [f"Workshop assessment {i}."],
            }
            for i, w in enumerate(words)
        ]
        bank.append(
            {"key": "self", "target": 5, "split": "dev", "year": 2018, "proposal": target, "opinions": ["HELD-OUT"]}
        )
        for row in bank:
            row.setdefault("split", "dev")
            row.setdefault("year", 2018)
            row.setdefault("revision_ms", 10)
            row.setdefault("opinion_ms", 11)
        packet, scores = contrast.packet(target, bank, target_year=2024, cutoff_ms=20)
        self.assertNotIn("HELD-OUT", str(packet))
        self.assertEqual(len(scores), 6)
        self.assertEqual(
            len({v["reviewer_excerpts"][0] for v in packet["examples"].values()}), 6
        )
        opinion = {
            "strength": "The comparison is concrete.",
            "objection": "Coverage is uncertain.",
            "applicable_contrast": "Scope",
            "priority": 3.1,
            "advance": 3.3,
            "consequence": 2.9,
            "win_probability": dict.fromkeys(scores, 0.5),
        }
        self.assertEqual(
            contrast.features(contrast.validate(opinion, packet), scores)["pair_value"],
            2.9,
        )
        with self.assertRaises(ValueError):
            contrast.validate({**opinion, "win_probability": {}}, packet)

    def test_translated_query_matches_examples_without_rewriting_proposal(self):
        from autopaperreview.proposal import contrast

        target = {"problem": "怎样用显微镜给花粉样本分类？"}
        bank = [
            {
                "key": str(i),
                "target": 1.5 + i % 4,
                "proposal": {"problem": f"{topic} specimen variation label{i}."},
                "opinions": ["Illustrative evidence."],
            }
            for i, topic in enumerate(
                ["pollen microscope grain"] * 12 + ["forge hammer iron"] * 12
            )
        ]
        for row in bank:
            row.setdefault("split", "dev")
            row.setdefault("year", 2018)
            row.setdefault("revision_ms", 10)
            row.setdefault("opinion_ms", 11)
        matched, _ = contrast.packet(target, bank, {"query": "pollen microscope grain"}, target_year=2024, cutoff_ms=20)
        self.assertEqual(matched["target"], target)
        self.assertTrue(
            all(
                "pollen" in e["proposal"]["problem"]
                for e in matched["examples"].values()
            )
        )
