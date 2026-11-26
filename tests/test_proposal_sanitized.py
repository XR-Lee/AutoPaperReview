from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from autopaperreview.cli import main
from autopaperreview.proposal import data, runner, sanitized, stages


class SanitizedProposalTests(unittest.TestCase):
    def test_constructor_and_every_reachable_container_are_immutable(self):
        source = {"context": [{"proposal": "Test indexing with wooden beads."}]}
        with self.assertRaises(TypeError):
            sanitized.SanitizedProposal(source)
        payload = sanitized.seal(source)
        with self.assertRaises(TypeError):
            payload |= {"rating": 8}
        with self.assertRaises(TypeError):
            dict.__setitem__(payload, "rating", 8)
        with self.assertRaises(TypeError):
            del payload["context"]
        with self.assertRaises(TypeError):
            payload._items = {}
        with self.assertRaises(TypeError):
            payload["context"][0]["proposal"] = "Decision = Reject"
        with self.assertRaises(AttributeError):
            payload["context"].append({})
        snapshot = sanitized.require_model_input(payload)
        self.assertEqual(snapshot, source)
        self.assertEqual(data.digest(payload), data.digest(source))
        original = source["context"][0]["proposal"]
        source["context"][0]["proposal"] = "Decision = Accept"
        snapshot["context"][0]["proposal"] = "Decision = Reject"
        self.assertEqual(payload["context"][0]["proposal"], original)

    def test_sender_checks_even_an_instance_forged_via_object_internals(self):
        # An ordinary caller cannot construct this instance. Even a deliberate
        # Python-level bypass cannot make the sender trust a type alone.
        forged = object.__new__(sanitized.SanitizedProposal)
        object.__setattr__(forged, "_items", {"problem": "Rating = 8"})
        with (
            tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r1-") as tmp,
            patch.object(stages, "run_command") as send,
        ):
            with self.assertRaisesRegex(ValueError, "identity leak"):
                stages.invoke(
                    "review", forged, Path(tmp), "mock", ["mock"], 1,
                    leak_judge=lambda _: {"verdict": "clean"},
                )
            send.assert_not_called()

    def test_normalized_keys_and_assembled_numeric_contracts(self):
        for key in (
            "score", "RAＴING", "rat\u200bing", "rati\u0301ng", "rаting", "rat%69ng",
            "rat&#105;ng", "reviewerMean", "author.name", "rat<!-- split -->ing",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity leak"):
                sanitized.seal({"context": [{key: "Index tiles by texture."}]})
        with self.assertRaisesRegex(ValueError, "duplicate normalized"):
            sanitized.seal({"Context": "Index tiles.", "co\u200bntext": "Inspect tiles."})
        with self.assertRaisesRegex(ValueError, "duplicate normalized"):
            sanitized.sanitize_proposal({"problem": "Index tiles.", "ＰＲＯＢＬＥＭ": "Inspect tiles."})
        for value in (True, 8, 10**2000, 3.5, {"raw": 8}):
            with self.subTest(value=type(value)), self.assertRaisesRegex(ValueError, "unexpected"):
                sanitized.seal({"context": value})
        with self.assertRaisesRegex(ValueError, "non-finite"):
            sanitized.seal({"context": float("nan")})
        for stage, document in (
            ("peer", {"prior_work": [{"title": "Tile search", "abstract": "Compare tile patterns.", "year": 2022}]}),
            ("contrast", {"examples": {"pair1a": {"proposal": {"problem": "Tile search."}, "prospective_grade": 3.5, "reviewer_excerpts": []}}}),
            ("match", {"examples": {"ex1": {"idea": {"problem": "Tile search."}, "reviewer_mean": 3, "example_opinions": []}}}),
            ("audit", {"r1.novelty": {"dimension": "novelty", "score": 4, "units": ["Tile search has a distinct scope."]}}),
        ):
            with self.subTest(stage=stage):
                packet = sanitized.seal(document, stage=stage)
                self.assertEqual(sanitized.require_model_input(packet, stage=stage), document)
                with self.assertRaises(ValueError):
                    sanitized.require_model_input(packet, stage="compress")
        with self.assertRaisesRegex(ValueError, "identity leak"):
            sanitized.seal(
                {"r1.novelty": {"dimension": "novelty", "score": 9, "units": ["Compare wooden tiles."]}},
                stage="audit",
            )

    def test_compress_rejects_reference_and_calibration_objects_before_the_judge(self):
        documents = (
            {"r1.novelty": {"dimension": "novelty", "score": 4, "units": ["Compare tile patterns."]}},
            {"examples": {"ex1": {"idea": {"problem": "Tile search."}, "reviewer_mean": 3, "example_opinions": []}}},
            {"examples": {"pair1a": {"proposal": {"problem": "Tile search."}, "prospective_grade": 3.5, "reviewer_excerpts": []}}},
        )
        with tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r2-") as tmp, patch.object(stages, "run_command") as send:
            root = Path(tmp)
            data.write(root / "manifest.json", {"cases": [{"bundle": "sample", "key": "synthetic-case"}]})
            for document in documents:
                with self.subTest(document=document):
                    data.write(root / "inputs" / "sample" / "candidate.json", {"problem": "Tile search.", **document})
                    with self.assertRaisesRegex(ValueError, "identity leak"):
                        runner.run_batch(
                            root, stage="compress", model="mock", command=["mock"], jobs=1, timeout=1,
                            leak_judge=lambda _: self.fail("invalid candidate reached the judge"),
                        )
            send.assert_not_called()

    def test_cli_hard_blocks_explicit_facts_before_clean_judge_or_business(self):
        facts = {
            "homoglyph": "Rаting: 8",
            "uts39_mixed_scripts": "ꓣατіոɡ: 8",
            "bidi": "Ra\u202eting: 8",
            "combining": "Rati\u0301ng: 8",
            "percent": "Contact synthetic%2540example.invalid.",
            "entity": "Contact synthetic&amp;#64;example.invalid.",
            "markdown": "Ra**ti**ng: **8**",
            "markdown_link": "[Rating](https://example.invalid): 8",
            "latex": r"Ra\textbf{ti}ng: $8$",
            "latex_mbox": r"Ra\mbox{ti}ng: 8",
            "latex_macro": r"Ra\syntheticStyle{ti}ng: 8",
            "latex_color_macro": r"Ra\textcolor{blue}{ti}ng: 8",
            "html_comment": "Ra<!-- split\nword -->ting: 8",
            "dash_roman_score": "Rating — Ⅷ",
            "qualified_roman_score": "Rating — VIII (borderline)",
            "extended_roman_score": "Rating — XII",
            "equals_rating": "Rating = 8",
            "equals_decision": "Decision = Accept",
            "recommendation": "Recommendation: Reject",
            "bare_fraction": "8/10",
            "inline_bare_fraction": "Read the enclosed text again. Good work, 8/10.",
            "bare_integer": "8",
            "notes_api": "https://api2.openreview.net/" + "notes?limit=1",
            "attachment": "https://openreview.net/" + "attachment?name=supplementary",
            "old_arxiv": "arXiv:hep-th/0000000",
            "bare_old_arxiv": "math.AA/0000000v2",
            "bare_modern_arxiv": "0000.12345v9",
            "bare_openreview": "ZzZz" + "765432",
            "bare_alpha_openreview": "XxYy" + "ZzVvWw",
            "byline": "Written by Example Maker.",
            "bare_byline": "By Example Maker",
            "corresponding_author": "Corresponding author: Example Maker",
            "author_dash": "Author — Example Maker",
            "signed_line": "Index tile patterns.\n— Example Maker",
            "author_prose": "Authors include Example Maker.",
            "latex_author": r"\author{Example Maker}",
            "local_author": "作者：示例姓名",
            "local_decision": "决定：录用",
        }
        with (
            tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r1-") as tmp,
            patch.object(stages, "run_command", return_value=('{"verdict":"clean"}', 0)) as send,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            root = Path(tmp)
            proposal = root / "proposal.txt"
            for case, text in facts.items():
                with self.subTest(case=case):
                    proposal.write_text(text, encoding="utf-8")
                    status = main(["proposal-review", "score", str(proposal), "--work", str(root / case)])
                    self.assertEqual(status, 2)
                    send.assert_not_called()

    def test_cli_allows_scientific_acceptance_and_planned_score_metric(self):
        response = {
            axis: {"score": None, "rationale": "Unassessable.", "basis": []}
            for axis in stages.RUBRIC
        }
        calls = []

        def send(_command, *, system, prompt, model, timeout):
            calls.append((system, json.loads(prompt)))
            result = {"verdict": "clean"} if system == stages.LEAK_AUDIT else response
            return json.dumps(result), 0

        plan = (
            "Estimate the acceptance rate of the proposed sampler. "
            "Measure the average score of six planned bead placement tests. "
            "We accept the assumption that the bias parameter is 0.2. "
            "Use accession AB12345678 for the synthetic comparison task. "
            "Plan measurements at 1234.5678 nanometres with a ratio of 1/10. "
            "Define our score as cosine similarity and use a score of 8 units as an example. "
            "Samples will be prepared by least squares fitting and controls created by iterative simulation."
        )
        with (
            tempfile.TemporaryDirectory(prefix="apr-pr70-fix-r1-") as tmp,
            patch.object(stages, "run_command", side_effect=send),
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            root = Path(tmp)
            proposal = root / "compressor.json"
            doc = {key: {"text": None, "evidence": []} for key in stages.SECTIONS}
            doc["validation_plan"] = {"text": plan, "evidence": ["s1"]}
            proposal.write_text(json.dumps(doc), encoding="utf-8")
            status = main(["proposal-review", "score", str(proposal), "--work", str(root / "work")])
        self.assertEqual(status, 0)
        self.assertEqual([system for system, _ in calls], [stages.LEAK_AUDIT, stages.JUDGE])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(calls[1][1]["validation_plan"], plan)

    def test_protocol_digest_changes_with_sentence_split_and_text_normalization(self):
        original = data.digest(data.cleaning_rules())
        for module, attribute in (
            (data, "_LABEL_SPLIT"),
            (sanitized, "_SCORE"),
            (sanitized, "_ZERO_WIDTH"),
            (sanitized, "_OBFUSCATED_EMAIL"),
        ):
            with self.subTest(attribute=attribute), patch.object(module, attribute, re.compile("synthetic")):
                self.assertNotEqual(data.digest(data.cleaning_rules()), original)
