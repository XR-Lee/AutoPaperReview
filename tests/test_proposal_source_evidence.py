import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from autopaperreview.proposal import priority, stages
from autopaperreview.proposal import source_evidence as evidence
from autopaperreview.proposal.data import read, write


def clean_judge(_):
    return {"verdict": "clean"}


class TestProposalSourceEvidence(unittest.TestCase):
    def test_whole_fields_and_partial_fields(self):
        proposal = {
            "mechanism": "Gather the tokens. Sort all tokens.",
            "problem": "  Count tokens.  ",
            "intended_value": None,
        }
        req = evidence.packet({"s1": "Gather tokens with a counter."}, proposal)
        votes = {
            "u1": ["stated", "s1"],
            "u2": ["inference", "s1"],
            "u3": ["unsupported"],
        }
        self.assertEqual(stages.validate("source_evidence", votes, req), votes)
        selected = evidence.select(proposal, req["claims"], {"u1", "u3"})
        self.assertEqual(selected, {**proposal, "mechanism": "Gather the tokens."})
        for invalid in (
            ["stated"],
            ["stated", "bad"],
            ["inference", "s1", "s1"],
            ["unsupported", "s1"],
            ["unknown"],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                evidence.validate({**votes, "u1": invalid}, req)

    def test_missing_source_and_offline_cache(self):
        with (
            tempfile.TemporaryDirectory() as t,
            patch.object(stages, "run_command", side_effect=AssertionError),
        ):
            cache = Path(t)
            plan = {"mechanism": "Gather tokens."}
            result = evidence.audit({}, plan, cache, cached_only=True, leak_judge=clean_judge)
            self.assertEqual(result["claims"]["u1"]["status"], "unclear")
            self.assertIsNone(result["proposal"]["mechanism"])
            with self.assertRaisesRegex(ValueError, "missing cached"):
                evidence.audit({"s": "Gather tokens."}, plan, cache, cached_only=True, leak_judge=clean_judge)

    def test_paired_cli_accepts_empty_source_and_preserves_score(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            inp = root / "candidate.json"
            source = {"u": "Count the tokens."}
            proposal = {"problem": "Count the tokens."}
            write(
                inp,
                [
                    {"id": "a", "source": source, "proposal": proposal},
                    {"id": "b", "source": {}, "proposal": {}},
                ],
            )
            args = Namespace(
                proposal=inp,
                work=root,
                output=root / "output.json",
                profile=root / "profile.json",
                screen_outcomes=False,
                include_source_audit=True,
            )
            write(args.profile, {"ranker": {"names": ["novelty"]}})

            def screening(p, _root, **kwargs):
                return p, {"removed": []}

            def judged(
                stage, request, cache, model, command, timeout, *, cached_only=False, leak_judge
            ):
                self.assertEqual(stage, "source_evidence")
                path = cache / "synthetic.json"
                write(path, {"output": {"u1": ["stated", "u"]}})
                return path

            with (
                patch.object(priority, "screen_packet", side_effect=screening),
                patch.object(priority, "rank", return_value={"overall": 3}),
                patch.object(stages, "invoke", side_effect=judged),
            ):
                self.assertEqual(priority.dispatch(args), 0)
            a, b = read(args.output)
            self.assertEqual(
                (a["overall"], a["rank"], a["source_audit"]["proposal"]),
                (3, 1, proposal),
            )
            self.assertEqual(a["source_audit"]["source_units"], source)
            self.assertIsNone(b["overall"])
            self.assertEqual(b["source_audit"]["claims"], {})
            write(inp, [{"proposal": proposal}])
            with self.assertRaisesRegex(ValueError, "explicit source"):
                priority.dispatch(args)
