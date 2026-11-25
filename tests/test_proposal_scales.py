"""Proposal intake uses venue rating scales and the ICLR anonymizer. Synthetic notes only."""

from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

from autopaperreview.proposal.data import digest, iclr_case
from autopaperreview.proposal.fetch import live_cases
from autopaperreview.proposal.fixed_benchmark import evaluate_fixed
from autopaperreview.proposal.reference_coding import validate_cases
from autopaperreview.public_calibration import ChallengeRequired


def _submission(fid: str, abstract: str, *, year: int = 2024) -> tuple[dict, str]:
    group = f"ICLR.cc/{year}/Conference"
    sub = {
        "id": fid,
        "forum": fid,
        "number": 1,
        "tmdate": 1,
        "invitations": [group + "/-/Submission"],
        "content": {
            "abstract": {"value": abstract},
            "primary_area": {"value": "optimization"},
            "venue": {"value": "HIDDEN DECISION"},
        },
    }
    return sub, group


def _review(fid: str, note_id: str, rating: str | None, *, year: int = 2024) -> dict:
    group = f"ICLR.cc/{year}/Conference"
    content = {"strengths": {"value": "The proposed solver would help a named user group."}}
    if rating is not None:
        content["rating"] = {"value": rating}
    return {
        "id": note_id,
        "forum": fid,
        "replyto": fid,
        "tcdate": 2,
        "tmdate": 3,
        "signatures": [note_id],
        "invitations": [f"{group}/Submission1/-/Official_Review"],
        "content": content,
    }


class ProposalScaleTests(unittest.TestCase):
    def test_clean_abstract_is_unchanged_and_markers_are_removed(self) -> None:
        abstract = (
            "We study university course timetables with a proposed solver.\n"
            "Write to ada.example@nowhere.test about the plan.\n"
            "Code is at https://github.com/ada-example/prism.\n"
            "Published as a conference paper at ICLR 2024\n"
        )
        sub, group = _submission("fixture-scale", abstract)
        case = iclr_case(sub, [_review("fixture-scale", "opinion-a", "6: accept")], group)
        self.assertIsNotNone(case)
        packet = json.dumps(case)
        self.assertIn("university course timetables", packet)
        self.assertNotIn("Submission", json.dumps(case["candidate"]))
        self.assertNotIn("ada.example@nowhere.test", packet)
        self.assertNotIn("github.com/ada-example", packet)
        self.assertNotIn("Published as a conference paper", packet)
        self.assertNotIn("HIDDEN", packet)
        self.assertNotIn("6: accept", packet)
        self.assertEqual(case["provenance"]["final_mean"], 6)
        self.assertEqual(case["provenance"]["rating_scale"], "iclr2024")
        self.assertEqual(case["provenance"]["ratings_unparsed"], 0)

    def test_2024_mean_uses_the_discrete_scale_and_withholds_an_off_scale_rating(self) -> None:
        sub, group = _submission(
            "fixture-mean",
            "We propose a catalogue of archive symbols and the tests that would check it.",
        )
        reviews = [
            _review("fixture-mean", "opinion-a", "8: accept"),
            _review("fixture-mean", "opinion-b", "6: marginally above"),
        ]
        case = iclr_case(sub, reviews, group)
        self.assertEqual(case["provenance"]["final_mean"], 7)
        self.assertEqual(case["provenance"]["ratings_parsed"], 2)
        off = iclr_case(
            sub,
            reviews + [_review("fixture-mean", "opinion-c", "4: not a 2024 bin")],
            group,
        )
        self.assertIsNone(off["provenance"]["final_mean"])
        self.assertEqual(off["provenance"]["ratings_parsed"], 2)
        self.assertEqual(off["provenance"]["ratings_unparsed"], 1)
        self.assertNotIn("not a 2024 bin", json.dumps(off))

    def test_2026_even_scale_keeps_zero_and_refuses_a_2024_bin(self) -> None:
        sub, group = _submission(
            "fixture-2026",
            "We propose a catalogue of archive symbols and the tests that would check it.",
            year=2026,
        )
        case = iclr_case(
            sub,
            [
                _review("fixture-2026", "opinion-a", "0: strong reject", year=2026),
                _review("fixture-2026", "opinion-b", "2: reject", year=2026),
            ],
            group,
        )
        self.assertEqual(case["provenance"]["year"], 2026)
        self.assertEqual(case["provenance"]["rating_scale"], "iclr2026")
        self.assertEqual(case["provenance"]["final_mean"], 1)
        refused = iclr_case(
            sub,
            [
                _review("fixture-2026", "opinion-a", "0: strong reject", year=2026),
                _review("fixture-2026", "opinion-b", "5: not on the 2026 scale", year=2026),
            ],
            group,
        )
        self.assertIsNone(refused["provenance"]["final_mean"])
        self.assertEqual(refused["provenance"]["ratings_unparsed"], 1)
        self.assertNotIn("not on the 2026 scale", json.dumps(refused))

    def test_reference_and_fixed_benchmark_follow_the_same_range(self) -> None:
        row = {
            "forum": "f",
            "reviews": [{"id": "n", "units": {"u": "The proposed test is explicit."}}],
        }
        validate_cases([{**row, "final_rating": 2.0}])
        validate_cases([{**row, "final_rating": 7.5, "year": 2024}])
        validate_cases([{**row, "final_rating": 0, "year": 2026}])
        validate_cases([{**row, "final_rating": 0, "rating_scale": "iclr2026"}])
        for bad in (
            {**row, "final_rating": 0},
            {**row, "final_rating": 0, "year": 2024},
            {**row, "final_rating": 11, "rating_scale": "iclr2026"},
            {**row, "final_rating": True, "year": 2026},
        ):
            with self.assertRaises(ValueError):
                validate_cases([bad])

        ids = [f"trial-{i}" for i in range(100)]
        manifest = {
            "cases": [{"forum": key, "split": "test"} for key in ids],
            "ids_sha256": digest(sorted(ids)),
        }
        predictions = [{"forum": key, "overall": 3} for key in ids]
        refs = [
            {
                "forum": key,
                "prospective": 3,
                "final_rating": 0,
                "year": 2026,
                "accept": index % 2,
            }
            for index, key in enumerate(ids)
        ]
        with patch(
            "autopaperreview.proposal.fixed_benchmark.evaluate",
            return_value={"rho": 0.75, "auc": 0.8},
        ):
            scored = evaluate_fixed(manifest, predictions, refs)
        self.assertTrue(scored["passed"])
        refs[0]["year"] = 2024
        with self.assertRaises(ValueError):
            evaluate_fixed(manifest, predictions, refs)


class OpenReviewStopTests(unittest.TestCase):
    def test_listing_403_stops_before_any_search(self) -> None:
        calls = []

        class Client:
            def get_json(self, path, params=None):
                calls.append(path)
                raise ChallengeRequired("browser check")

        with patch(
            "autopaperreview.proposal.fetch.HttpOpenReviewClient", return_value=Client()
        ) as constructed:
            with self.assertRaises(ChallengeRequired) as caught:
                list(live_cases(2024, 3, ["optimization"], False))
        self.assertEqual(calls, ["/notes"])
        self.assertIn("403", str(caught.exception))
        self.assertIn("stopped", str(caught.exception).lower())
        self.assertIs(constructed.call_args.kwargs["sleeper"], time.sleep)

    def test_forum_403_does_not_continue_to_search(self) -> None:
        calls = []
        submission = {
            "id": "forum-1",
            "forum": "forum-1",
            "number": 1,
            "content": {"primary_area": {"value": "optimization"}},
        }

        class Client:
            def get_json(self, path, params=None):
                calls.append((path, dict(params or {})))
                if params and "forum" in params:
                    raise ChallengeRequired("403")
                return {"notes": [submission]}

        with patch(
            "autopaperreview.proposal.fetch.HttpOpenReviewClient", return_value=Client()
        ):
            with self.assertRaises(ChallengeRequired):
                list(live_cases(2024, 1, [], False))
        self.assertEqual([path for path, _params in calls], ["/notes", "/notes"])
        self.assertNotIn("/notes/search", [path for path, _params in calls])
