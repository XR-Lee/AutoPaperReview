import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autopaperreview.proposal import outcome_screen, priority
from autopaperreview.proposal.data import read, write
from autopaperreview.proposal.stages import validate


def clean_judge(_):
    return {"verdict": "clean"}


class OutcomeScreenTests(unittest.TestCase):
    def test_explicit_provider_enables_screening_and_separates_cached_judgments(self):
        import argparse
        from autopaperreview.proposal import fixed_benchmark

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(required=True)
        priority.register(sub)
        fixed_benchmark.register(sub)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp, dst, profile = (root / f for f in ("idea.json", "ranked.json", "profile.json"))
            write(inp, {"proposal": "We observed a benefit in the archive."})
            write(profile, {"assets": {}, "ranker": {"method": "mean", "names": ["novelty"]}})
            args = parser.parse_args(["prioritize", str(inp), "--work", str(root), "--profile",
                                      str(profile), "--output", str(dst), "--outcome-screen-provider", "claude"])
            raw = '{"exclude":[{"unit":"u1","quote":"observed a benefit","kind":"empirical"}]}'
            with (patch("autopaperreview.proposal.stages.run_command", return_value=(raw, 0.1)) as calls,
                  patch.object(priority, "model_leak_judge", return_value=clean_judge)):
                self.assertEqual(args.func(args), 0)
                result = read(dst)
                self.assertIsNone(result["overall"])
                receipt = result["outcome_screen"]["proposal"]
                self.assertEqual(receipt["screen_model"], "claude-sonnet-5-5")
                actual = read(root / "cache" / "outcome_screen" / (receipt["cache_key"] + ".json"))
                self.assertEqual(actual["request"]["model"], receipt["screen_model"])
                self.assertEqual(calls.call_count, 1)
                packet = {"proposal": "We observed a benefit in the archive."}
                clean, cached = priority.screen_packet(packet, root, cached_only=True, provider="claude", leak_judge=clean_judge)
                self.assertEqual(cached, receipt)
                with self.assertRaisesRegex(ValueError, "missing cached outcome_screen"):
                    priority.screen_packet(packet, root, cached_only=True, provider="grok", leak_judge=clean_judge)
                self.assertEqual(calls.call_count, 1)

    def test_preserves_future_and_removes_only_complete_unit_in_english_and_chinese(
        self,
    ):
        packet = {
            "problem": "Can archive staff recover missing shelf codes?\nWe plan to ask two cataloguers to check independently.",
            "mechanism": "We compared three shelf layouts.\nWe plan to compare the new layout with a paper ledger.",
            "intended_value": "计划节约清点时间。刚才已经验证了收益。",
            "assumptions": None,
        }
        units = outcome_screen.units(packet)
        flags = []
        for key, row in units.items():
            if row["text"] in (
                "We compared three shelf layouts.",
                "刚才已经验证了收益。",
            ):
                flags.append({"unit": key, "quote": row["text"], "kind": "empirical"})
        clean, audit = outcome_screen.filter_units(packet, units, {"exclude": flags})
        self.assertEqual(clean["problem"], packet["problem"])
        self.assertEqual(
            clean["mechanism"], "We plan to compare the new layout with a paper ledger."
        )
        self.assertEqual(clean["intended_value"], "计划节约清点时间。")
        self.assertIsNone(clean["assumptions"])
        self.assertEqual(len(audit["removed"]), 2)

    def test_validates_exact_unit_scope_and_preserves_full_unchanged_field(self):
        raw = {
            "proposal": "Could we sort boxes by weight?  Would that make a different protocol possible?"
        }
        units = outcome_screen.units(raw)
        self.assertEqual(
            outcome_screen.filter_units(raw, units, {"exclude": []})[0], raw
        )
        for value in (
            {"unit": "u1", "quote": "different protocol", "kind": "empirical"},
            {"unit": "missing", "quote": "weight", "kind": "proof"},
        ):
            with self.assertRaises(ValueError):
                validate("outcome_screen", {"exclude": [value]}, units)
        valid = {"unit": "u1", "quote": "weight", "kind": "proof"}
        with self.assertRaises(ValueError):
            validate("outcome_screen", {"exclude": [valid, valid]}, units)

    def test_batch_cli_screens_before_scoring_retains_abstentions_and_ties(self):
        import argparse

        with TemporaryDirectory() as tmp:
            p = Path(tmp)
            inp = p / "sample.json"
            dst = p / "ranked.json"
            profile = p / "profile.json"
            write(
                profile,
                {"assets": {}, "ranker": {"method": "mean", "names": ["novelty"]}},
            )
            write(
                inp,
                [
                    {"id": "empty", "proposal": "The shelves are already assembled."},
                    {
                        "id": "first",
                        "proposal": "Can colored bands identify shelf ownership?",
                    },
                    {"id": "second", "proposal": "Could a paper map guide delivery?"},
                ],
            )

            def invoke(stage, source, cache, model, argv, timeout, *, cached_only, leak_judge):
                self.assertEqual(stage, "outcome_screen")
                source_text = next(iter(source.values()))["text"]
                flags = (
                    [{"unit": "u1", "quote": "already assembled", "kind": "empirical"}]
                    if "already assembled" in source_text
                    else []
                )
                fn = cache / (source_text[:8].strip() + ".json")
                write(fn, {"output": {"exclude": flags}})
                return fn

            def rank(packet, *args, **kwargs):
                self.assertNotIn("already assembled", str(packet))
                return {"overall": 3.0}

            args = argparse.Namespace(
                proposal=inp,
                profile=profile,
                work=p / "cache-root",
                output=dst,
                screen_outcomes=True,
            )
            with (
                patch.object(
                    priority, "assert_local_only", side_effect=lambda path: path
                ),
                patch.object(priority, "invoke", side_effect=invoke),
                patch.object(priority, "rank", side_effect=rank) as rated,
            ):
                priority.dispatch(args)
            rows = read(dst)
            self.assertEqual(rated.call_count, 2)
            self.assertEqual([r["id"] for r in rows], ["first", "second", "empty"])
            self.assertEqual([r["rank"] for r in rows], [1, 1, None])
            self.assertEqual(rows[-1]["status"], "insufficient_prospective_text")

    def test_reviewing_apd_source_preserves_hypotheses_and_omits_result_tables(self):
        from autopaperreview.proposal.data import apd_cases

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "apd.json"
            write(
                path,
                {
                    "motivation": {"statement": "Should a desk use rings?"},
                    "candidates": [
                        {
                            "id": "plan",
                            "contribution": "Could broad rings outperform thin rings?",
                            "existing_evidence": [{"value": "protected evidence"}],
                            "expected_main_table": {"value": "protected table"},
                        },
                        {"id": "empty", "contribution": ""},
                    ],
                },
            )
            with self.assertRaisesRegex(ValueError, "no prospective contribution"):
                list(apd_cases(path))
            cases = list(apd_cases(path, review_filtered_fields=True))
            self.assertEqual([r["id"] for r in cases], ["plan", "empty"])
            self.assertNotIn("fields_to_screen", cases[0])
            self.assertIn("Could broad rings outperform thin rings?", cases[0]["candidate"].values())
            self.assertNotIn("protected", str(cases))
            self.assertNotIn("contribution", cases[1]["fields_to_screen"])

    def test_rechecks_attribution_in_context_before_ranking(self):
        packet = {
            "proposal": "We propose violet tags. Violet looked superior to grey while sorting. Could amber be faster than grey? 旧流程优于纸条。"
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def judge(stage, data, cache, model, argv, timeout, *, cached_only, leak_judge):
                self.assertTrue(cached_only)
                flags = []
                if stage == "outcome_attribution":
                    self.assertEqual(list(data["candidates"]), ["u2", "u3"])
                    self.assertIn("u1", data["context"])
                    flags = [
                        {
                            "unit": "u2",
                            "quote": "superior to grey",
                            "kind": "empirical",
                        }
                    ]
                    with self.assertRaises(ValueError):
                        validate(
                            stage,
                            {
                                "exclude": [
                                    {
                                        "unit": "u1",
                                        "quote": "violet",
                                        "kind": "empirical",
                                    }
                                ]
                            },
                            data,
                        )
                if stage == "outcome_context":
                    self.assertEqual(list(data["candidates"]), ["u4"])
                dest = cache / (stage + ".json")
                write(dest, {"output": {"exclude": flags}})
                return dest

            with patch.object(priority, "invoke", side_effect=judge) as called:
                clean, audit = priority.screen_packet(packet, root, cached_only=True, leak_judge=clean_judge)
            self.assertEqual(called.call_count, 3)
            self.assertEqual(
                clean["proposal"],
                "We propose violet tags. Could amber be faster than grey? 旧流程优于纸条。",
            )
            self.assertEqual(
                audit["removed"],
                [{"unit": "u2", "field": "proposal", "kind": "empirical"}],
            )
            self.assertEqual(audit["attribution_key"], "outcome_attribution")
            self.assertEqual(audit["cleared_packet"], priority.digest(clean))
            profile = {"ranker": {"names": []}}
            # Outcome-bearing packets require the exact screen receipt bound to their content.
            with self.assertRaisesRegex(ValueError, "remove reported outcomes"):
                priority.rank(clean, root, profile, root, leak_judge=clean_judge)
            with self.assertRaisesRegex(ValueError, "remove reported outcomes"):
                priority.rank(
                    {"proposal": "不同结果优于之前。"},
                    root,
                    profile,
                    root,
                    screen_receipt=audit,
                    leak_judge=clean_judge,
                )


if __name__ == "__main__":
    unittest.main()
