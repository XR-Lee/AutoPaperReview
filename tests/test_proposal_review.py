"""Synthetic regression cases for prospective-only proposal review."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal import evaluation
from autopaperreview.proposal.data import (
    apd_cases,
    archived_notes,
    iclr_case,
    prepare,
    read,
    write,
)
from autopaperreview.proposal.runner import run_batch, stage_result
from autopaperreview.proposal.stages import (
    AUDIT,
    COMPRESS,
    JUDGE,
    LABEL,
    RUBRIC,
    SECTIONS,
    scored,
    validate,
)


def note_case(index):
    fid = f"fixture-{index}"
    group = "ICLR.cc/2024/Conference"
    sub = {
        "id": fid,
        "forum": fid,
        "number": index,
        "tmdate": 1,
        "invitations": [group + "/-/Submission"],
        "content": {
            "abstract": {
                "value": f"We propose a dictionary of archive symbols for fixture {index}. We outperform all baselines."
            },
            "venue": {"value": "HIDDEN DECISION"},
        },
    }
    review = {
        "id": f"opinion-{index}",
        "forum": fid,
        "replyto": fid,
        "tcdate": 2,
        "tmdate": 3,
        "invitations": [f"{group}/Submission{index}/-/Official_Review"],
        "content": {
            "strengths": {
                "value": "The proposed dictionary would be useful to archivists."
            },
            "rating": {"value": "HIDDEN SCORE"},
        },
    }
    return sub, review, group


def clean_judge(_):
    return {"verdict": "clean"}


class TestProposalReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_separation_and_dates(self):
        sub, review, group = note_case(1)
        cross = {**review, "id": "foreign", "forum": "different"}
        meta = {
            **review,
            "id": "meta",
            "invitations": [group + "/Submission1/-/Meta_Review"],
        }
        old = {
            **review,
            "tmdate": 2,
            "content": {"strengths": {"value": "Outdated opinion."}},
        }
        case = iclr_case(sub, [meta, old, review, cross], group)
        packet = json.dumps(case)
        for text in ("outperform", "HIDDEN", "foreign", "Outdated"):
            self.assertNotIn(text, packet)
        self.assertIn("dictionary of archive symbols", packet)
        self.assertIsNone(case["provenance"]["final_mean"])
        self.assertEqual(case["provenance"]["rating_scale"], "iclr2024")
        self.assertEqual(case["provenance"]["ratings_unparsed"], 1)
        self.assertEqual(len(case["comments"]), 1)
        self.assertNotIn("archivists", str(case["candidate"]))
        sub["tmdate"] = 2
        self.assertIsNone(iclr_case(sub, [review], group))
        self.assertFalse(
            iclr_case(sub, [review], group, allow_late=True)["provenance"]["pre_review"]
        )

    def test_snapshot_stream_boundaries(self):
        path = self.root / "batch.json"
        notes = [{"id": "a", "text": "x" * 130000}, {"id": "b"}]
        write(path, {"notes": notes})
        self.assertEqual(list(archived_notes(path)), notes)
        path.write_text('{"notes": [{"id":')
        with self.assertRaises(ValueError):
            list(archived_notes(path))

    def test_all_apd_candidates(self):
        path = self.root / "c.json"
        rows = [
            {
                "id": x,
                "contribution": "Build a catalogue of units and contexts.",
                "existing_evidence": "PRIVATE",
                "expected_main_table": {"values": "PRIVATE"},
            }
            for x in ["first", "second"]
        ]
        write(
            path,
            {
                "motivation": {
                    "statement": "Relate archive tables.",
                    "hash": "PRIVATE",
                },
                "selected": {"id": "first"},
                "candidates": rows,
            },
        )
        cases = list(apd_cases(path))
        self.assertEqual([c["id"] for c in cases], ["first", "second"])
        self.assertNotIn("PRIVATE", str(cases))
        with self.assertRaises(ValueError):
            prepare([cases[0]] * 2, self.root, seed="0", limit=None, source={})

    def test_audit_requires_exact_quote_and_rejects_outcomes(self):
        source = {
            "r1.novelty": {
                "units": [
                    "The pairing is creative. It outperforms the previous approach."
                ]
            }
        }
        self.assertEqual(
            validate("audit", {"r1.novelty": []}, source), {"r1.novelty": []}
        )
        for quote in ("It is astonishing.", "It outperforms the previous approach."):
            with self.assertRaises(ValueError):
                validate("audit", {"r1.novelty": [quote]}, source)

    def test_authored_chinese_and_reported_outcomes(self):
        from autopaperreview.proposal.data import reported_result, units

        self.assertTrue(units({"contribution": "方法对比。"}))
        self.assertTrue(
            reported_result("Our experiments demonstrate substantial improvements.")
        )
        self.assertFalse(reported_result("We plan to compare the two naming schemes."))

    def test_probabilities_and_abstention(self):
        output = {
            d: {
                "panel": [[0, 0, 0.25, 0.75, 0]] * 3,
                "basis": ["problem"],
                "rationale": "Explicit scope.",
            }
            for d in RUBRIC
        }
        output["novelty"] = {"panel": None, "basis": [], "rationale": "No comparator."}
        validate("forecast", output, {"problem": "An archive task."})
        result = scored({"request": {"stage": "forecast"}, "output": output})
        self.assertEqual(result["importance"]["score"], 3.75)
        self.assertIsNone(result["novelty"]["score"])
        output["importance"]["panel"] = [[0, 0, 0, 0, 0]] * 3
        with self.assertRaises(ValueError):
            validate("forecast", output, {"problem": "An archive task."})

    def test_end_to_end_isolation_resume_and_calibration(self):
        def cases():
            for index in range(7):
                sub, review, group = note_case(index)
                yield iclr_case(sub, [review], group)

        run = self.root / "run"
        m = prepare(cases(), run, seed="demo", limit=4, source={})
        replication = prepare(
            cases(), self.root / "new", seed="demo", limit=2, source={}, previous=m
        )
        self.assertTrue(
            {c["key"] for c in m["cases"]}.isdisjoint(
                c["key"] for c in replication["cases"]
            )
        )
        self.assertEqual({c["split"] for c in replication["cases"]}, {"holdout"})
        calls = []

        def agent(argv, *, system, prompt, model, timeout):
            data = json.loads(prompt)
            calls.append(system)
            if system == COMPRESS:
                self.assertNotIn("archivists", prompt)
                out = {k: {"text": None, "evidence": []} for k in SECTIONS}
                out["approach"] = {"text": data["s1"], "evidence": ["s1"]}
            elif system == JUDGE:
                self.assertEqual(set(data), set(SECTIONS))
                self.assertNotIn("HIDDEN", prompt)
                out = {
                    d: {
                        "score": None,
                        "rationale": "No assessable evidence.",
                        "basis": [],
                    }
                    for d in RUBRIC
                }
                out["potential_value"] = {
                    "score": 3,
                    "rationale": "The dictionary can clarify names.",
                    "basis": ["approach"],
                }
            elif system == LABEL:
                self.assertNotIn("fixture", prompt)
                out = {
                    "r1": [
                        {"score": 4, "dimension": "potential_value", "evidence": ["s1"]}
                    ]
                }
            elif system == AUDIT:
                out = {"r1.potential_value": data["r1.potential_value"]["units"]}
            else:
                self.fail("unexpected agent stage")
            return json.dumps(out), 0.01

        def stage(name, model="mock"):
            run_batch(run, stage=name, model=model, command=["mock"], jobs=1, timeout=5, leak_judge=clean_judge)

        with patch("autopaperreview.proposal.stages.run_command", agent):
            for name in ("compress", "review", "label", "audit"):
                stage(name)
            count = len(calls)
            stage("review")
            self.assertEqual(len(calls), count)
            stage("review", "mock2")
            self.assertGreater(len(calls), count)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["proposal-review", "fit", "--work", str(run)]), 0)
            self.assertEqual(main(["proposal-review", "report", "--work", str(run)]), 0)
        metrics = read(run / "metrics.json")
        self.assertEqual(metrics["dimensions"]["potential_value"]["paired"], 2)
        self.assertIsNone(metrics["dimensions"]["importance"]["raw_mae"])
        holdout = next(c for c in m["cases"] if c["split"] == "holdout")
        (run / "runs" / holdout["key"] / "label.json").unlink()
        self.assertEqual(evaluation.fit(run), read(run / "calibration.json"))
        write(
            run / "inputs" / holdout["bundle"] / "candidate.json",
            {"s1": "A changed source idea."},
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            stage_result(run, holdout, "review")
