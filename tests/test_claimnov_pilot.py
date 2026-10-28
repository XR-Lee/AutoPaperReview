"""ClaimNov pilot sample, rates, and the refusal to call a model. Synthetic rows only."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from benchmarks.claimnov_pilot.catalog import assert_public
from benchmarks.claimnov_pilot.metrics import (
    choose_threshold,
    claim_agreement,
    headline,
    split_rows,
)
from benchmarks.claimnov_pilot.project import write_project
from benchmarks.claimnov_pilot.published import load_gate_maps, system_report
from benchmarks.claimnov_pilot.run import blocker_message, input_inventory, inventory_blocks
from benchmarks.claimnov_pilot.select import SEED, allocate, draw_sample

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "src" / "autopaperreview" / "prompts"
SAMPLE = ROOT / "benchmarks" / "claimnov_pilot" / "sample_ids.json"


def _population() -> dict[str, list[dict]]:
    disguised = []
    for condition, label in (("B_in", "non_novel"), ("B_out", "src_out")):
        for rung in (1, 2, 3, 4):
            for index in range(100):
                item_id = f"s{index}_r{rung}_{condition}"
                disguised.append(
                    {
                        "key": item_id,
                        "item_id": item_id,
                        "label": label,
                        "condition": condition,
                        "rung": rung,
                    }
                )
    disputed = []
    for decision, count in (("accept", 62), ("reject", 96)):
        for index in range(count):
            item_id = f"n2f_{decision}_{index}"
            disputed.append(
                {"key": item_id, "item_id": item_id, "label": "claim_disputed", "decision": decision}
            )
    later = []
    for bucket, count in (("a", 101), ("b", 261), ("c", 38)):
        for index in range(count):
            item_id = f"{bucket}{index:03d}"
            later.append(
                {"key": item_id, "item_sha256": item_id, "label": "novel", "bucket": bucket}
            )
    hybrids = []
    for index in range(100):
        item_id = f"h{index:03d}"
        hybrids.append({"key": item_id, "item_sha256": item_id, "label": "spliced"})
    return {
        "disguised": disguised,
        "claim_disputed": disputed,
        "later_realized": later,
        "hybrids": hybrids,
    }


class SampleTests(unittest.TestCase):
    def test_accept_reject_quota(self) -> None:
        self.assertEqual(allocate({"accept": 62, "reject": 96}, 50), {"accept": 20, "reject": 30})

    def test_later_bucket_quota(self) -> None:
        self.assertEqual(allocate({"a": 101, "b": 261, "c": 38}, 50), {"a": 13, "b": 32, "c": 5})

    def test_draw_is_balanced_and_repeatable(self) -> None:
        first = draw_sample(_population(), seed=SEED)
        second = draw_sample(_population(), seed=SEED)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 200)
        disguised = [item for item in first if item["dataset"] == "disguised"]
        self.assertEqual(sum(item["condition"] == "B_in" for item in disguised), 25)
        for condition in ("B_in", "B_out"):
            rungs = [item["rung"] for item in disguised if item["condition"] == condition]
            self.assertEqual(rungs.count(1), 7)
            self.assertEqual(rungs.count(2), 6)
        disputed = [item for item in first if item["dataset"] == "claim_disputed"]
        self.assertEqual(sum(item["decision"] == "accept" for item in disputed), 20)
        self.assertEqual(sum(item["decision"] == "reject" for item in disputed), 30)
        later = [item for item in first if item["dataset"] == "later_realized"]
        self.assertEqual(sum(item["bucket"] == "c" for item in later), 5)
        self.assertEqual(sum(item["bucket"] == "a" for item in later), 13)
        self.assertEqual(sum(item["bucket"] == "b" for item in later), 32)

    def test_assert_public_rejects_prose(self) -> None:
        with self.assertRaises(ValueError):
            assert_public(
                {
                    "items": [
                        {
                            "dataset": "disguised",
                            "item_id": "seed0_r1_B_in",
                            "title": "Synthetic title that must not be stored",
                        }
                    ]
                }
            )
        with self.assertRaises(ValueError):
            assert_public({"items": [{"dataset": "disguised", "item_id": "has space"}]})

    def test_committed_sample_is_opaque(self) -> None:
        document = json.loads(SAMPLE.read_text(encoding="utf-8"))
        assert_public(document)
        self.assertEqual(document["seed"], SEED)
        self.assertEqual(len(document["items"]), 200)
        counts: dict[str, int] = {}
        for item in document["items"]:
            counts[item["dataset"]] = counts.get(item["dataset"], 0) + 1
        self.assertEqual(counts, {name: 50 for name in ("disguised", "claim_disputed", "later_realized", "hybrids")})
        disguised = [item for item in document["items"] if item["dataset"] == "disguised"]
        self.assertEqual(sum(item["condition"] == "B_in" for item in disguised), 25)
        disputed = [item for item in document["items"] if item["dataset"] == "claim_disputed"]
        self.assertEqual(sum(item["decision"] == "accept" for item in disputed), 20)
        later = [item for item in document["items"] if item["dataset"] == "later_realized"]
        self.assertEqual(
            {item["bucket"]: sum(other["bucket"] == item["bucket"] for other in later) for item in later},
            {"a": 13, "b": 32, "c": 5},
        )
        for item in document["items"]:
            if item["dataset"] in {"later_realized", "hybrids"}:
                self.assertEqual(len(item["item_sha256"]), 64)
                self.assertNotIn("item_id", item)


class MetricTests(unittest.TestCase):
    def test_threshold_prefers_informedness_then_fewer_false_alarms(self) -> None:
        tied = [
            {"label": "non_novel", "originality": 4},
            {"label": "novel", "originality": 8},
        ]
        self.assertEqual(choose_threshold(tied), 4)
        false_alarm_tie = [
            {"label": "non_novel", "originality": 6},
            {"label": "novel", "originality": 4},
        ]
        self.assertEqual(choose_threshold(false_alarm_tie), 2)

    def test_leakage_is_out_of_the_rate(self) -> None:
        rows = [
            {"dataset": "disguised", "label": "non_novel", "key": "a", "originality": 2, "leakage": True},
            {"dataset": "disguised", "label": "non_novel", "key": "b", "originality": 8},
            {"dataset": "later_realized", "label": "novel", "key": "c", "originality": 8},
        ]
        report = headline(rows, rows, rows)
        self.assertEqual(report["catch"]["n"], 1)
        self.assertEqual(report["catch"]["rate"], 0.0)
        self.assertEqual(report["leakage_excluded"], 1)

    def test_split_uses_the_hash_and_is_half(self) -> None:
        rows = [
            {"dataset": "hybrids", "label": "spliced", "item_sha256": value}
            for value in ("b" * 64, "a" * 64, "d" * 64, "c" * 64)
        ]
        train, test = split_rows(rows, seed=SEED)
        self.assertEqual(len(train), 2)
        self.assertEqual(len(test), 2)
        again_train, again_test = split_rows(rows, seed=SEED)
        self.assertEqual(train, again_train)
        self.assertEqual(test, again_test)

    def test_claim_agreement(self) -> None:
        gold = [{"text": "synthetic widget counter", "established": True}]
        matched = claim_agreement([{"text": "synthetic widget counter", "tag": "overlap"}], gold)
        self.assertFalse(matched["unusable"])
        self.assertEqual(matched["agreement"], 1.0)
        missed = claim_agreement([{"text": "synthetic widget counter", "tag": "supported"}], gold)
        self.assertEqual(missed["agreement"], 0.0)
        empty = claim_agreement([], gold)
        self.assertTrue(empty["unusable"])
        far = claim_agreement([{"text": "unrelated token", "tag": "overlap"}], gold)
        self.assertTrue(far["unusable"])


class PublishedRateTests(unittest.TestCase):
    def test_same_id_in_two_datasets_stays_separate(self) -> None:
        items = [
            {"dataset": "later_realized", "label": "novel", "item_id": "same"},
            {"dataset": "hybrids", "label": "spliced", "item_sha256": "same"},
            {"dataset": "disguised", "label": "non_novel", "item_id": "seed0_r1_B_in", "condition": "B_in", "rung": 1},
            {"dataset": "disguised", "label": "src_out", "item_id": "seed0_r1_B_out", "condition": "B_out", "rung": 1},
            {"dataset": "claim_disputed", "label": "claim_disputed", "item_id": "n2f_0", "decision": "accept"},
        ]
        verdicts = {
            ("later_realized", "same"): True,
            ("hybrids", "same"): False,
            ("disguised", "seed0_r1_B_in"): False,
            ("disguised", "seed0_r1_B_out"): True,
            ("claim_disputed", "n2f_0"): False,
        }
        ranks = {("claim_disputed", "n2f_0"): 1}
        report = system_report(items, verdicts, ranks)
        self.assertEqual(report["false_alarm"]["rate"], 0.0)
        self.assertEqual(report["hybrid_keep"]["rate"], 0.0)
        self.assertEqual(report["catch"]["rate"], 1.0)
        self.assertEqual(report["src_out_rejection"]["rate"], 0.0)
        self.assertEqual(report["claim_disputed_top1"]["rate"], 1.0)
        self.assertEqual(report["claim_disputed_rejection_on_accept"]["rate"], 1.0)
        self.assertLessEqual(report["catch"]["low"], report["catch"]["rate"])
        self.assertGreaterEqual(report["catch"]["high"], report["catch"]["rate"])

    def test_disagreeing_rows_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            first.write_text(
                json.dumps({"item": "seed0_r1_B_in", "gate": "coi", "novel": True}) + "\n",
                encoding="utf-8",
            )
            second.write_text(
                json.dumps({"item": "seed0_r1_B_in", "gate": "coi", "novel": False}) + "\n",
                encoding="utf-8",
            )
            gates, dropped = load_gate_maps([first, second])
        self.assertEqual(dropped, 1)
        self.assertNotIn("seed0_r1_B_in", gates["coi"])


class RunRefusalTests(unittest.TestCase):
    def test_blocker_names_the_secret_and_not_a_value(self) -> None:
        saved = os.environ.pop("APR_MODEL_API_KEY", None)
        try:
            message = blocker_message()
        finally:
            if saved is not None:
                os.environ["APR_MODEL_API_KEY"] = saved
        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("APR_MODEL_API_KEY", message)
        self.assertIn("grok-4.7", message)
        if saved:
            self.assertNotIn(saved, message)

    def test_empty_checkout_blocks_a_scored_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = input_inventory(Path(temporary))
        self.assertFalse(inventory["proposal_text_present"]["disguises"])
        self.assertTrue(inventory_blocks(inventory))

    def test_project_pool_reaches_the_packet(self) -> None:
        from autopaperreview.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_project(
                root,
                project_id="synthetic-proposal",
                proposal_text="Title: Synthetic Widget Counter\n\nAbstract\nCount synthetic widgets.\n",
                pool=[
                    {
                        "id": "synthetic-prior",
                        "title": "Synthetic Prior Note",
                        "abstract": "A prior method for counting synthetic widgets.",
                        "date": "2024-05-01",
                    },
                    {
                        "id": "synthetic-target",
                        "title": "Synthetic Target Note",
                        "abstract": "The target record.",
                        "date": "2024-06-01",
                    },
                ],
                cutoff_date="2026-03-01",
                blocked_ids=["synthetic-target"],
                prompts=PROMPTS,
                model="grok-4.7",
                base_url="https://api.x.ai/v1",
                use_model=False,
            )
            manifest = run_pipeline(root / "review.toml")
            stage = root / ".runs" / manifest.run_id / "stages"
            packet = (stage / "prompt_packets" / "M7.md").read_text(encoding="utf-8")
            judgment = json.loads((stage / "search" / "judgment.json").read_text(encoding="utf-8"))

        self.assertIn("- Input mode: `proposal`", packet)
        self.assertIn("Synthetic Prior Note", packet)
        self.assertNotIn("Synthetic Target Note", packet)
        self.assertEqual(judgment["cutoff_date"], "2026-03-01")
        self.assertTrue(judgment["target_leakage"])
        self.assertEqual(judgment["status"], "complete")


if __name__ == "__main__":
    unittest.main()
