"""CVPR answer recovery and transport regression tests; all content is invented."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopaperreview.cvpr_handoff import (
    HandoffError,
    grok_json_prompt,
    run_isolated_command,
    split_usage,
)
from autopaperreview.cvpr_probe import ProbeError, parse_probe_response, run_probe
from autopaperreview.cvpr_tier import CvprTierError, _command_paper, _prompt_for, build_prompt, parse_verdict, render_command
from autopaperreview.hashing import sha256_bytes

HANDOFF = "012345abcdef"
OTHER_HANDOFF = "fedcba543210"
MANUSCRIPT = (
    "Synthetic Button Sorting\n\nAvery Example\n\nAbstract\n\n"
    "This invented exercise sorts wooden buttons into empty jars and counts the remaining buttons.\n\n"
    "1 Introduction\nThe exercise uses only invented objects.\n"
)


def probe_payload(handoff_id: str = HANDOFF, title: str = "Synthetic button exercise") -> dict:
    return {
        "items": [
            {
                "handoff_id": handoff_id,
                "title": title,
                "authors": [],
                "venue": "",
                "year": None,
                "tier": "unknown",
                "choices": [],
            }
        ]
    }


def verdict_payload(score: float = 4.0) -> dict:
    return {"tier": "poster", "rationale": "An invented exercise.", "rating": 4, "predicted_mean": score}


def fenced(payload: object) -> str:
    return "```json\n" + json.dumps(payload) + "\n```"


class JsonRecoveryTests(unittest.TestCase):
    def test_plain_json_is_unchanged(self) -> None:
        self.assertEqual(parse_probe_response(json.dumps(probe_payload())), probe_payload()["items"])
        self.assertEqual(parse_verdict("mean", json.dumps(verdict_payload()))["score"], 4.0)

    def test_long_preamble_with_braces_and_final_fence(self) -> None:
        preamble = ('Synthetic explanation {not JSON}; sample {"unrelated": true}.\n' * 4000)
        answer = preamble + fenced(probe_payload()) + "\nEnd of synthetic response."
        self.assertEqual(parse_probe_response(answer), probe_payload()["items"])
        self.assertEqual(parse_verdict("mean", preamble + fenced(verdict_payload()))["score"], 4.0)

    def test_last_schema_valid_fence_wins(self) -> None:
        answer = "\n".join(
            [fenced(probe_payload(title="First invented answer")), fenced(probe_payload()), fenced({"items": "wrong"})]
        )
        self.assertEqual(parse_probe_response(answer), probe_payload()["items"])

    def test_unfinished_preamble_example_does_not_hide_a_complete_final_fence(self) -> None:
        prefix = 'An abandoned invented example: {"draft": [\n'
        answer = prefix + fenced(probe_payload())
        self.assertEqual(parse_probe_response(answer), probe_payload()["items"])
        self.assertEqual(parse_verdict("mean", prefix + fenced(verdict_payload()))["score"], 4.0)

    def test_unfinished_preamble_does_not_hide_real_truncation(self) -> None:
        prefix = 'An abandoned invented example: {"draft": [\n'
        tails = ('\n{"items": [', '\n{"stop_reason": "max_tokens"}')
        for tail in tails:
            with self.subTest(tail=tail):
                with self.assertRaisesRegex(ProbeError, "truncated"):
                    parse_probe_response(prefix + fenced(probe_payload()) + tail)
        with self.assertRaisesRegex(ProbeError, "truncated/max_tokens"):
            parse_probe_response(prefix + fenced({**probe_payload(), "finish_reason": "length"}))

    def test_verdict_fences_use_existing_mode_schema(self) -> None:
        answer = "\n".join([fenced(verdict_payload(3.0)), fenced(verdict_payload()), fenced({"tier": "poster"})])
        self.assertEqual(parse_verdict("mean", answer)["score"], 4.0)

    def test_syntactically_invalid_closed_fence_is_skipped(self) -> None:
        answer = fenced(probe_payload()) + '\n```json\n{"items": invalid}\n```'
        self.assertEqual(parse_probe_response(answer), probe_payload()["items"])

    def test_last_balanced_top_level_object(self) -> None:
        title = 'Invented braces } { and escaped quote " plus slash \\ and brackets []'
        payload = probe_payload(title=title)
        answer = 'Preamble {"unrelated": {"nested": true}}\n' + json.dumps(payload) + "\nDone."
        self.assertEqual(parse_probe_response(answer), payload["items"])

    def test_fence_has_priority_over_unfenced_object(self) -> None:
        answer = fenced(probe_payload()) + "\n" + json.dumps(probe_payload(title="Later unfenced answer"))
        self.assertEqual(parse_probe_response(answer), probe_payload()["items"])

    def test_token_limit_words_inside_answer_strings_are_not_diagnostics(self) -> None:
        payload = probe_payload(title="Invented response truncated by max_tokens example")
        self.assertEqual(parse_probe_response(json.dumps(payload)), payload["items"])
        self.assertEqual(parse_probe_response("Synthetic prefix\n" + fenced(payload)), payload["items"])

    def test_nested_object_in_invalid_outer_json_is_not_a_candidate(self) -> None:
        with self.assertRaisesRegex(ProbeError, "not JSON"):
            parse_probe_response("{" + json.dumps(probe_payload()) + "}")

    def test_last_unfenced_object_must_match_schema(self) -> None:
        answer = json.dumps(probe_payload()) + '\n{"unrelated": true}'
        with self.assertRaisesRegex(ProbeError, "schema mismatch"):
            parse_probe_response(answer)

    def test_array_and_single_item_shapes_remain_supported(self) -> None:
        items = probe_payload()["items"]
        for payload in (items, items[0]):
            with self.subTest(payload=type(payload).__name__):
                self.assertEqual(parse_probe_response(json.dumps(payload)), items)
                self.assertEqual(parse_probe_response("Synthetic prefix\n" + fenced(payload)), items)

    def test_case_insensitive_and_longer_fences(self) -> None:
        answer = "Synthetic prefix\n  ````JSON\r\n" + json.dumps(probe_payload()) + "\r\n  ````\n"
        self.assertEqual(parse_probe_response(answer), probe_payload()["items"])

    def test_truncation_does_not_recover_earlier_valid_answer(self) -> None:
        tails = (
            '\n{"items": [',
            '\n{"items": [{"handoff_id": "0123',
            '\n```json\n{"items": [',
            "\n```json\n" + json.dumps(probe_payload()),
            '\n```json\n{"items": [\n```',
        )
        for tail in tails:
            with self.subTest(tail=tail):
                with self.assertRaisesRegex(ProbeError, "truncated.*max_tokens"):
                    parse_probe_response(fenced(probe_payload()) + tail)
                with self.assertRaisesRegex(CvprTierError, "truncated.*max_tokens"):
                    parse_verdict("mean", fenced(verdict_payload()) + tail)

    def test_partial_outer_object_cannot_salvage_nested_item(self) -> None:
        text = json.dumps(probe_payload())[:-1]
        with self.assertRaisesRegex(ProbeError, "truncated"):
            parse_probe_response(text)

    def test_explicit_token_limit_rejects_complete_json(self) -> None:
        markers = (
            {"finish_reason": "length"},
            {"stop_reason": "max_tokens"},
            {"error_kind": "max_tokens_truncation"},
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
            {"error": {"error_kind": "max_tokens_truncation"}},
        )
        for marker in markers:
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(ProbeError, "truncated/max_tokens"):
                    parse_probe_response(json.dumps({**probe_payload(), **marker}))
                with self.assertRaisesRegex(CvprTierError, "truncated/max_tokens"):
                    parse_verdict("mean", json.dumps({**verdict_payload(), **marker}))

    def test_completion_envelope_cannot_salvage_nested_json(self) -> None:
        payload = {"choices": [{"finish_reason": "length", "message": {"content": fenced(probe_payload())}}]}
        with self.assertRaisesRegex(ProbeError, "truncated/max_tokens"):
            parse_probe_response(json.dumps(payload))

    def test_diagnostic_after_valid_answer_is_not_ignored(self) -> None:
        for diagnostic in ('\nresponse truncated by max_tokens', '\n{"stop_reason":"max_tokens"}'):
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaisesRegex(ProbeError, "truncated/max_tokens"):
                    parse_probe_response(fenced(probe_payload()) + diagnostic)

    def test_non_json_has_clear_error(self) -> None:
        with self.assertRaisesRegex(ProbeError, "not JSON"):
            parse_probe_response("Only an invented explanation; there is no answer.")

    def test_wrong_probe_schema_is_rejected(self) -> None:
        invalid = (
            {}, {"items": []}, {"items": "wrong"}, {"items": [42]}, {"items": [{}]},
            {"items": [{"handoff_id": "wrong"}]},
            {"items": [{"handoff_id": HANDOFF, "title": []}]},
            {"items": [{"handoff_id": HANDOFF, "authors": [42]}]},
            {"items": [probe_payload()["items"][0]] * 2},
            {"wrapper": probe_payload()},
        )
        for payload in invalid:
            for text in (json.dumps(payload), "Synthetic prefix\n" + fenced(payload)):
                with self.subTest(payload=payload, fenced=text.startswith("Synthetic")):
                    with self.assertRaisesRegex(ProbeError, "schema mismatch"):
                        parse_probe_response(text)

    def test_batch_schema_rejects_missing_or_unexpected_ids(self) -> None:
        for ids in ({OTHER_HANDOFF}, {HANDOFF, OTHER_HANDOFF}):
            with self.subTest(ids=ids):
                with self.assertRaisesRegex(ProbeError, "requested batch"):
                    parse_probe_response(fenced(probe_payload()), expected_handoff_ids=ids)

    def test_fence_selection_checks_requested_batch(self) -> None:
        text = fenced(probe_payload()) + "\n" + fenced(probe_payload(OTHER_HANDOFF))
        self.assertEqual(parse_probe_response(text, expected_handoff_ids={HANDOFF}), probe_payload()["items"])

    def test_verdict_schema_and_sample_count_are_not_relaxed(self) -> None:
        invalid = (
            {**verdict_payload(), "tier": "not-a-tier"},
            {**verdict_payload(), "rating": 99},
            {**verdict_payload(), "unexpected": True},
            {"wrapper": verdict_payload()},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(CvprTierError, "schema mismatch"):
                    parse_verdict("mean", "Synthetic prefix\n" + fenced(payload))
        with self.assertRaisesRegex(CvprTierError, "expected 2 samples"):
            parse_verdict("mean", fenced(verdict_payload()), samples=2)


class TransportTests(unittest.TestCase):
    def test_command_fences_preserve_metadata_without_relaxing_verdict_schema(self) -> None:
        payload = {**verdict_payload(), "usage": {"output_tokens": 30}, "sentinel": {"safe": True}}
        invalid = {**verdict_payload(9.0), "unexpected": True, "usage": {"output_tokens": 900}}
        text = "Synthetic prefix\n" + "\n".join([fenced(verdict_payload(3.0)), fenced(payload), fenced(invalid)])
        completed = subprocess.CompletedProcess(["synthetic-cli"], 0, text.encode(), b"")
        with tempfile.TemporaryDirectory() as temporary, patch(
            "autopaperreview.cvpr_handoff.cli_version", return_value="synthetic"
        ), patch("autopaperreview.cvpr_handoff.subprocess.run", return_value=completed):
            result = _command_paper(
                template="synthetic-cli", prompt="Synthetic prompt", handoff_id=HANDOFF,
                model_id="any-local", mode="mean", samples=1, timeout=1,
                failure_log=Path(temporary) / "failures.jsonl",
            )
        self.assertTrue(result["parsed"])
        self.assertEqual(result["retries"], 0)
        self.assertEqual(result["verdict"]["score"], 4.0)
        self.assertEqual(result["usage"], payload["usage"])
        self.assertEqual(result["sentinel"], payload["sentinel"])
        self.assertEqual(json.loads(result["text"]), verdict_payload())
        with self.assertRaisesRegex(CvprTierError, "schema mismatch"):
            parse_verdict("mean", fenced(payload))

    def test_split_usage_preserves_unparsed_text_and_truncation(self) -> None:
        payload = {**verdict_payload(), "usage": {"output_tokens": 30}, "sentinel": {"safe": True}}
        body, usage, sentinel = split_usage(json.dumps(payload))
        self.assertEqual(json.loads(body), verdict_payload())
        self.assertEqual(usage, payload["usage"])
        self.assertEqual(sentinel, payload["sentinel"])
        for text in (fenced(payload), json.dumps(payload) + '\n{"unfinished":'):
            with self.subTest(text=text):
                self.assertEqual(split_usage(text), (text, None, None))

    def test_cli_token_limit_overrides_even_a_complete_answer_file(self) -> None:
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                def completed(argv, **kwargs):
                    Path(argv[-1]).write_text(json.dumps(probe_payload()), encoding="utf-8")
                    return subprocess.CompletedProcess(
                        argv, returncode, b"", b'{"error_kind":"max_tokens_truncation"}'
                    )

                with patch("autopaperreview.cvpr_handoff.cli_version", return_value="synthetic"), patch(
                    "autopaperreview.cvpr_handoff.subprocess.run", side_effect=completed
                ):
                    with self.assertRaisesRegex(HandoffError, "truncated/max_tokens"):
                        run_isolated_command(
                            "synthetic-cli {answer_file}", prompt="Synthetic prompt", handoff_id=HANDOFF,
                            model_id="any-local", mode="probe", timeout=1, render=render_command,
                        )

    def test_cli_does_not_strip_a_truncated_suffix(self) -> None:
        text = json.dumps(probe_payload()) + '\n{"items": ['
        result = subprocess.CompletedProcess(["synthetic-cli"], 0, text.encode(), b"")
        with patch("autopaperreview.cvpr_handoff.cli_version", return_value="synthetic"), patch(
            "autopaperreview.cvpr_handoff.subprocess.run", return_value=result
        ):
            answer = run_isolated_command(
                "synthetic-cli", prompt="Synthetic prompt", handoff_id=HANDOFF,
                model_id="any-local", mode="probe", timeout=1, render=render_command,
            )
        self.assertEqual(answer["text"], text)
        with self.assertRaisesRegex(ProbeError, "truncated"):
            parse_probe_response(answer["text"])

    def test_grok_instructions_do_not_change_other_models(self) -> None:
        prompt = build_prompt(MANUSCRIPT, "mean")
        for label in ("opus-local", "astra-local", "other-backend"):
            self.assertEqual(grok_json_prompt(prompt, label), prompt)
        for label in ("grok-cli", "grok-4.7-cursor", "GROK-local"):
            tightened = grok_json_prompt(prompt, label)
            self.assertTrue(tightened.endswith(prompt))
            self.assertIn("Return only one complete JSON object", tightened)
            self.assertIn("Do not call tools", tightened)
            self.assertIn("workspace, filesystem, or web", tightened)

    def test_verdict_prompt_digest_covers_grok_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manuscripts").mkdir()
            (root / "manuscripts" / "synthetic.txt").write_text(MANUSCRIPT, encoding="utf-8")
            for label in ("grok-cli", "opus-local"):
                prompt, digest = _prompt_for(root, "synthetic", "mean", 1, model_id=label)
                self.assertEqual(prompt, grok_json_prompt(build_prompt(MANUSCRIPT, "mean"), label))
                self.assertEqual(digest, sha256_bytes(prompt.encode()))


class ProbeIntegrationTests(unittest.TestCase):
    def run_synthetic_probe(self, root: Path, *, backend: str, model_id: str, truncated: bool = False) -> dict:
        cache = root / "cache"
        (cache / "raw").mkdir(parents=True)
        (cache / "raw" / "fixture-button-00.txt").write_text(MANUSCRIPT, encoding="utf-8")
        (cache / "sample_manifest.json").write_text(
            json.dumps({"papers": [{"paper_id": "fixture-button-00", "year": 2024}]}), encoding="utf-8"
        )

        def invoke(_backend, **kwargs):
            prompt = kwargs["prompt"]
            self.assertEqual("Do not call tools" in prompt, "grok" in model_id)
            self.assertEqual(kwargs["packet_sha256"], sha256_bytes(prompt.encode()))
            handoff_id = re.search(r"handoff_id: ([0-9a-f]{12})", prompt).group(1)
            text = 'Synthetic preamble {"draft": true}.\n' * 2000 + fenced(probe_payload(handoff_id))
            if truncated:
                text += '\n{"items": ['
            return text, {"estimated_tokens": 100}

        with patch("autopaperreview.cvpr_probe._invoke", side_effect=invoke):
            return run_probe(
                cache, model_id=model_id, backend=backend, templates=("P-A", "P-T", "P-D"),
                decoy_count=0, handoff_dir=root / "handoffs",
            )

    def test_all_probe_templates_and_backends_recover(self) -> None:
        for backend in ("command", "replay"):
            for label in ("grok-cli", "opus-local", "astra-local"):
                with self.subTest(backend=backend, label=label), tempfile.TemporaryDirectory() as temporary:
                    result = self.run_synthetic_probe(Path(temporary), backend=backend, model_id=label)
                    self.assertEqual(result["errors"], [])
                    self.assertEqual(result["calls"], 3)
                    self.assertEqual(result["batches"], 3)
                    self.assertEqual(result["items"], 3)

    def test_truncated_batches_are_logged_and_never_completed(self) -> None:
        for backend in ("command", "replay"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = self.run_synthetic_probe(root, backend=backend, model_id="any-local", truncated=True)
                self.assertEqual(result["items"], 0)
                self.assertEqual(result["batches"], 0)
                self.assertEqual(len(result["errors"]), 3)
                destination = root / "cache" / "probes" / "any-local"
                failures = [json.loads(line) for line in (destination / "failures.jsonl").read_text().splitlines()]
                self.assertEqual(len(failures), 3)
                self.assertTrue(all("truncated" in failure["error"] for failure in failures))
                self.assertFalse((destination / "batches").exists())


if __name__ == "__main__":
    unittest.main()
