import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import reference_coding
from autopaperreview.proposal.data import digest, write


class ProposalReferenceCodingTest(unittest.TestCase):
    def test_models_and_exact_evidence_are_isolated_from_outcomes(self):
        quotes = ["A useful question.", "The proposed test is ambiguous."]
        data = {
            "forum": "f",
            "accept": 0,
            "final_rating": 2.0,
            "reviews": [
                {"id": str(i), "units": {"u": "Reviewer writes " + q}}
                for i, q in enumerate(quotes)
            ],
        }
        calls = []
        with TemporaryDirectory() as tmp:

            def fake(stage, src, cache, model, command, timeout, *, cached_only):
                calls.append((stage, model, src))
                obj = (
                    {
                        "r1": [{"id": "u", "quote": quotes[0]}],
                        "r2": [{"id": "u", "quote": quotes[1]}],
                    }
                    if stage == "ref_extract"
                    else {
                        "r1": {"score": 4, "used": [0]},
                        "r2": {"score": 2.5, "used": [0]},
                    }
                )
                path = Path(tmp) / (stage + ".json")
                write(path, {"output": obj})
                return path

            with patch.object(reference_coding, "invoke", side_effect=fake):
                actual = reference_coding.code_paper(data, Path(tmp), ["judge"])
        self.assertEqual(actual["prospective"], 3.25)
        self.assertEqual([c[1] for c in calls], list(reference_coding.MODELS.values()))
        self.assertEqual(list(calls[0][2]), ["r1", "r2"])
        self.assertEqual(
            calls[1][2]["r1"],
            [{"excerpt": quotes[0], "context": "Reviewer writes " + quotes[0]}],
        )
        for n, review in enumerate(actual["reviewers"]):
            e = review["evidence"][0]
            self.assertEqual(
                data["reviews"][n]["units"][e["unit"]][e["start"] : e["end"]], quotes[n]
            )
        self.assertEqual(
            actual["reference_protocol"], digest(reference_coding.protocol(["judge"]))
        )
        self.assertEqual(actual["final_rating"], 2.0)

    def test_independent_grades_reuse_excerpts_without_peer_context(self):
        row = {
            "forum": "f",
            "reviews": [
                {"id": "left", "units": {"s": "The test is clear."}},
                {"id": "empty", "units": {}},
                {"id": "right", "units": {"s": "The goal is ambitious."}},
            ],
        }
        calls = []
        with TemporaryDirectory() as temp:
            def invoke(stage, source, cache, model, cmd, timeout, *, cached_only):
                self.assertTrue(cached_only)
                calls.append((stage, source))
                if stage == "ref_extract":
                    out = {k: [{"id": "s", "quote": u["s"]}] if u else [] for k, u in source.items()}
                elif len(source) > 1:
                    out = {k: {"score": 3 if v else None, "used": [0] if v else []}
                           for k, v in source.items()}
                else:
                    out = {"r1": {"score": 4 if source["r1"][0]["excerpt"].endswith("clear.") else 5,
                                  "used": [0]}}
                path = Path(temp) / (digest([stage, source]) + ".json")
                write(path, {"output": out})
                return path

            with patch.object(reference_coding, "invoke", side_effect=invoke):
                out = reference_coding.code_paper(row, Path(temp), ["judge"], cached_only=True,
                                                  include_independent_grades=True)
        self.assertEqual(len(calls), 4)
        self.assertEqual([v for _, v in calls[2:]], [{"r1": calls[1][1][k]} for k in ["r1", "r3"]])
        audit = out["independent_reference"]
        self.assertEqual(out["prospective"], 3)
        self.assertEqual(audit["prospective"], 4.5)
        self.assertEqual([r["score"] for r in audit["reviewers"]], [4, None, 5])
        self.assertEqual(set(audit["grade_requests_by_note"]), {"left", "right"})
        self.assertNotEqual(audit["reference_protocol"], out["reference_protocol"])
        self.assertEqual(audit["reviewers"][2]["evidence"], out["reviewers"][2]["evidence"])

    def test_empty_and_duplicate_notes(self):
        row = {"forum": "f", "reviews": [{"id": "n", "units": {}}]}
        reference_coding.validate_cases([row])
        with patch.object(reference_coding, "invoke") as call:
            ref = reference_coding.code_paper(row, Path("."), ["judge"])
            call.assert_not_called()
        self.assertIsNone(ref["prospective"])
        self.assertEqual(ref["requests"], {})
        with self.assertRaisesRegex(ValueError, "unique note"):
            reference_coding.validate_cases([row, {**row, "forum": "other"}])

    def test_benchmark_reports_mixed_and_legacy_protocols(self):
        from autopaperreview.proposal.fixed_benchmark import evaluate_fixed

        ids = [f"trial-{i}" for i in range(100)]
        manifest = {
            "cases": [{"forum": k, "split": "test"} for k in ids],
            "ids_sha256": digest(sorted(ids)),
        }
        predictions = [{"forum": k, "overall": 1 + i % 5} for i, k in enumerate(ids)]
        refs = [
            {
                "forum": k,
                "prospective": 1 + i % 5,
                "final_rating": 1 + i % 10,
                "accept": i % 2,
                "reference_protocol": "frozen",
            }
            for i, k in enumerate(ids)
        ]
        with patch(
            "autopaperreview.proposal.fixed_benchmark.evaluate",
            side_effect=lambda *args: {"rho": 0.75, "auc": 0.75},
        ):
            same = evaluate_fixed(manifest, predictions, refs)
            self.assertTrue(same["reference_protocol_consistent"])
            refs[0].pop("reference_protocol")
            mixed = evaluate_fixed(manifest, predictions, refs)
            self.assertFalse(mixed["reference_protocol_consistent"])
            for r in refs:
                r.pop("reference_protocol", None)
            self.assertNotIn(
                "reference_protocols", evaluate_fixed(manifest, predictions, refs)
            )


class SealedOutcomeScreenTests(unittest.TestCase):
    def test_sealed_prediction_preserves_all_forums_and_source_receipts(self):
        from types import SimpleNamespace

        from autopaperreview.proposal import fixed_benchmark as bench
        from autopaperreview.proposal import priority

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = [f"synthetic-{i}" for i in range(100)]
            manifest = {
                "cases": [{"forum": k, "split": "test"} for k in ids],
                "ids_sha256": digest(sorted(ids)),
            }
            records = [
                {
                    "forum": k,
                    "proposal": "planned main",
                    "short_proposal": "planned short",
                }
                for k in ids
            ]
            records[0]["short_proposal"] = "completed only"
            write(root / "manifest.json", manifest)
            write(root / "proposals.json", records)
            args = SimpleNamespace(
                manifest=root / "manifest.json",
                proposals=root / "proposals.json",
                profile=root / "profile",
                work=root / "work",
                output=root / "predictions.json",
                jobs=1,
                cached_only=True,
                screen_outcomes=True,
            )

            def screen(packet, _root, *, cached_only):
                self.assertTrue(cached_only)
                text = packet["proposal"]
                kept = {
                    "proposal": None if text == "completed only" else text + " screened"
                }
                return kept, {"source": text}

            def judge(packet, *_args, **kw):
                self.assertEqual(packet, {"proposal": "planned main screened"})
                self.assertEqual(kw["legacy"], {"proposal": "planned short screened"})
                self.assertEqual(kw["screen_receipt"], {"source": "planned main"})
                return {"overall": 2.5}

            with (
                patch.object(
                    bench,
                    "load_profile",
                    return_value=({"ranker": {"names": ["forecast"]}}, root),
                ),
                patch.object(priority, "stage_for_feature", return_value="forecast"),
                patch.object(priority, "screen_packet", side_effect=screen),
                patch.object(bench, "rank", side_effect=judge) as called,
            ):
                bench.predict_dispatch(args)
            out = {r["forum"]: r for r in bench.read(args.output)}
            self.assertEqual(set(out), set(ids))
            self.assertEqual(called.call_count, 99)
            self.assertIsNone(out[ids[0]]["overall"])
            self.assertEqual(out[ids[0]]["status"], "insufficient_prospective_text")
            self.assertIn("short_proposal", out[ids[1]]["outcome_screen"])
