"""execute_review through a local CLI. A fake CLI answers with synthetic judgments."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from autopaperreview.command_transport import (
    CommandTransportError,
    command_argv,
    flatten_messages,
    recover_json,
    reply_text,
)
from autopaperreview.model_exec import ModelExecutionError, OpenAICompatibleAdapter
from autopaperreview.pipeline import run_pipeline
from tests.test_model_exec import (
    MANUSCRIPT,
    SECRET,
    _contribution,
    _dimensions,
    _fatal,
    _fatal_verify,
    _ledger,
    _text_request,
)

# Reads the system file and stdin, logs what it saw, and answers by the judgment named on
# the first system-prompt line. ``style`` picks a plain, fenced, or Claude Code JSON reply.
FAKE_CLI = textwrap.dedent(
    """
    import json, os, sys
    args = sys.argv[1:]
    def value(flag):
        return args[args.index(flag) + 1] if flag in args else None
    system = open(value("--system-file"), encoding="utf-8").read() if value("--system-file") else ""
    prompt = sys.stdin.read()
    judgment = (system or prompt).splitlines()[0].split("=", 1)[1]
    log_path = value("--log")
    calls = json.load(open(log_path, encoding="utf-8")) if os.path.exists(log_path) else []
    calls.append({
        "judgment": judgment, "argv": args, "cwd": os.getcwd(), "cwd_entries": sorted(os.listdir(".")),
        "env": sorted(os.environ), "env_values": sorted(os.environ.values()), "system": system, "prompt": prompt,
    })
    json.dump(calls, open(log_path, "w", encoding="utf-8"))
    if value("--exit"):
        sys.stderr.write("synthetic failure")
        sys.exit(int(value("--exit")))
    replies = json.load(open(value("--replies"), encoding="utf-8"))
    attempt = sum(1 for call in calls if call["judgment"] == judgment)
    answer = replies[judgment] if isinstance(replies[judgment], str) else replies[judgment][attempt - 1]
    style = value("--style") or "plain"
    if style == "fenced":
        answer = "Here is the judgment.\\n```json\\n" + answer + "\\n```\\n"
    if style == "claude":
        answer = json.dumps({
            "type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
            "result": "```json\\n" + answer + "\\n```",
            "usage": {"input_tokens": 3, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 2,
                      "output_tokens": 7},
            "total_cost_usd": 0.01,
        })
    sys.stdout.write(answer)
    """
)
REPLIES = {
    "contributions": _contribution(),
    "ledger": _ledger(),
    "dimensions": _dimensions(),
    "fatal": _fatal(),
    "fatal-verify": _fatal_verify(),
}


class CommandTransportCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.cli = self.root / "fake_cli.py"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.log = self.root / "calls.json"
        self.replies = self.root / "replies.json"
        self.replies.write_text(json.dumps(REPLIES), encoding="utf-8")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def command(self, *extra: str) -> list[str]:
        return [
            sys.executable, str(self.cli), "--system-file", "{system_file}", "--model", "{model}",
            "--replies", str(self.replies), "--log", str(self.log), *extra,
        ]

    def calls(self) -> list[dict[str, Any]]:
        return json.loads(self.log.read_text(encoding="utf-8")) if self.log.exists() else []

    def execute(self, *extra: str, **metadata: Any):
        values = {
            "base_url": "",
            "command": self.command(*extra),
            "model": "synthetic-cli",
            "allow_remote": True,
            "network_policy": "allow",
        }
        values.update(metadata)
        scratch = self.root / "scratch"
        scratch.mkdir(exist_ok=True)
        return OpenAICompatibleAdapter().execute(_text_request(**values), scratch)


class CommandAdapterTests(CommandTransportCase):
    def test_each_judgment_runs_the_cli_in_an_empty_directory(self) -> None:
        result = self.execute()
        self.assertEqual(result.metadata["egress"], "command")
        self.assertTrue(result.metadata["manuscript_left_machine"])
        self.assertEqual(result.metadata["base_url"], "")
        self.assertEqual(result.metadata["command"]["argv0"], Path(sys.executable).name)
        self.assertIn("--system-file", result.metadata["command"]["flags"])
        self.assertTrue(result.package.issues)
        (call,) = self.calls()
        self.assertEqual(call["judgment"], "contributions")
        self.assertEqual(call["cwd_entries"], [])
        self.assertNotEqual(Path(call["cwd"]).resolve(), (self.root / "scratch").resolve())
        self.assertIn("synthetic-cli", call["argv"])
        self.assertNotIn("{system_file}", call["argv"])
        self.assertTrue(call["system"].startswith("judgment=contributions\n"))
        self.assertIn(MANUSCRIPT.splitlines()[0], call["prompt"])
        self.assertFalse(Path(call["argv"][call["argv"].index("--system-file") + 1]).exists())

    def test_claude_code_json_and_fenced_replies_are_unwrapped(self) -> None:
        for style in ("fenced", "claude"):
            with self.subTest(style=style):
                self.log.unlink(missing_ok=True)
                result = self.execute("--style", style)
                self.assertTrue(result.package.issues)
        totals = result.metadata["usage_totals"]
        self.assertEqual(totals, {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17})

    def test_credentials_do_not_reach_the_cli(self) -> None:
        with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET, "SYNTHETIC_API_TOKEN": SECRET}):
            self.execute()
        (call,) = self.calls()
        self.assertNotIn("APR_MODEL_API_KEY", call["env"])
        self.assertNotIn("SYNTHETIC_API_TOKEN", call["env"])
        self.assertNotIn(SECRET, call["env_values"])

    def test_schema_retry_quotes_the_earlier_reply(self) -> None:
        self.replies.write_text(json.dumps({"contributions": ["not json", _contribution()]}), encoding="utf-8")
        result = self.execute()
        self.assertTrue(result.package.issues)
        first, second = self.calls()
        self.assertNotIn("=== Your earlier reply ===", first["prompt"])
        self.assertIn("=== Your earlier reply ===\nnot json", second["prompt"])
        self.assertIn("Validation failed", second["prompt"])

    def test_a_failing_cli_fails_the_judgment_without_transport_retries(self) -> None:
        with self.assertRaises(ModelExecutionError) as caught:
            self.execute("--exit", "4")
        self.assertIn("command exited 4", str(caught.exception))
        self.assertIn("synthetic failure", str(caught.exception))
        self.assertNotIn("transport retries", str(caught.exception))
        self.assertEqual(len(self.calls()), 1)

    def test_a_command_needs_allow_remote_and_the_allow_policy(self) -> None:
        for override in ({"allow_remote": False}, {"network_policy": "deny"}):
            with self.subTest(override=override), self.assertRaises(PermissionError):
                self.execute(**override)
        self.assertEqual(self.calls(), [])

    def test_base_url_and_command_cannot_both_be_set(self) -> None:
        with self.assertRaises(ModelExecutionError):
            self.execute(base_url="http://127.0.0.1:9/v1")
        with self.assertRaises(ModelExecutionError):
            self.execute(model="")


class CommandPipelineTests(CommandTransportCase):
    def test_review_stage_records_the_command_and_its_egress(self) -> None:
        workspace = self.root / "project"
        workspace.mkdir()
        (workspace / "manuscript.txt").write_text(MANUSCRIPT, encoding="utf-8")
        (workspace / "prompts").mkdir()
        (workspace / "prompts" / "contribution_audit_v1.md").write_text("Audit each contribution.\n", encoding="utf-8")
        config = workspace / "review.toml"

        def write(command: list[str]) -> None:
            config.write_text(
                textwrap.dedent(
                    f"""
                    schema_version = 1

                    [project]
                    id = "synthetic-command-review"
                    title = "Synthetic command review"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    default_language = "en"
                    network_policy = "allow"

                    [[routes]]
                    id = "M7"
                    name = "Per-contribution evidence and retrieval audit"
                    kind = "prompt"
                    prompt = "prompts/contribution_audit_v1.md"
                    prompt_version = "1.0"

                    [[stages]]
                    id = "ingest"
                    type = "ingest"

                    [[stages]]
                    id = "prompt_packets"
                    type = "prompt_packet"
                    depends_on = ["ingest"]
                    [stages.params]
                    include_source_text = true

                    [[stages]]
                    id = "review"
                    type = "execute_review"
                    depends_on = ["prompt_packets"]
                    [stages.params]
                    adapter = "autopaperreview.model_exec:OpenAICompatibleAdapter"
                    command = {json.dumps(command)}
                    model = "synthetic-cli"
                    allow_remote = true
                    requires_network = true
                    judgments = ["contributions"]
                    timeout_seconds = 30
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

        write(self.command())
        manifest = run_pipeline(config)
        review = manifest.stage("review")
        assert review is not None
        self.assertEqual(review.metadata["egress"], "command")
        self.assertTrue(manifest.metadata["manuscript_left_machine"])
        first_signature = review.signature
        self.assertEqual(len(self.calls()), 1)

        again = run_pipeline(config).stage("review")
        assert again is not None
        self.assertEqual(again.signature, first_signature)
        self.assertEqual(len(self.calls()), 1)

        write(self.command("--style", "fenced"))
        changed = run_pipeline(config).stage("review")
        assert changed is not None
        self.assertNotEqual(changed.signature, first_signature)
        self.assertEqual(len(self.calls()), 2)


LISTED_MANUSCRIPT = (
    "Synthetic cohort uses eight images from one specimen.\n"
    "Our contributions are as follows.\n"
    "(1) We propose a counting rule for the synthetic cohort and test it on held-out pairs.\n"
    "(2) We report accuracy on the held-out pair with a synthetic protocol for every image.\n"
    "Accuracy was reported as 95 percent on the held-out pair.\n"
)
AUDIT_EXCERPT = "a counting rule for the synthetic cohort"


class LedgerConsensusTests(CommandTransportCase):
    def test_model_audit_replaces_the_pending_placeholder_and_its_evidence(self) -> None:
        """The deterministic ledger and the model audit both write C1 and E-C1; consensus keeps the audit."""
        self.replies.write_text(json.dumps({"contributions": _contribution(AUDIT_EXCERPT)}), encoding="utf-8")
        workspace = self.root / "project"
        (workspace / "prompts").mkdir(parents=True)
        (workspace / "manuscript.txt").write_text(LISTED_MANUSCRIPT, encoding="utf-8")
        (workspace / "prompts" / "closed_book_v1.md").write_text("Review the synthetic manuscript.\n", encoding="utf-8")
        config = workspace / "review.toml"
        config.write_text(
            textwrap.dedent(
                f"""
                schema_version = 1

                [project]
                id = "synthetic-ledger-consensus"
                title = "Synthetic ledger consensus"
                source = "manuscript.txt"
                run_root = ".runs"
                default_language = "en"
                network_policy = "allow"

                [[routes]]
                id = "M0"
                name = "Structured closed-book review"
                kind = "prompt"
                prompt = "prompts/closed_book_v1.md"
                prompt_version = "1.1"

                [[stages]]
                id = "ingest"
                type = "ingest"

                [[stages]]
                id = "ledger"
                type = "ledger"
                depends_on = ["ingest"]

                [[stages]]
                id = "agenda"
                type = "agenda"
                depends_on = ["ledger"]

                [[stages]]
                id = "prompt_packets"
                type = "prompt_packet"
                depends_on = ["ingest"]
                [stages.params]
                include_source_text = true

                [[stages]]
                id = "review"
                type = "execute_review"
                depends_on = ["prompt_packets", "ledger"]
                [stages.params]
                adapter = "autopaperreview.model_exec:OpenAICompatibleAdapter"
                command = {json.dumps(self.command())}
                model = "synthetic-cli"
                allow_remote = true
                requires_network = true
                judgments = ["contributions"]
                timeout_seconds = 30

                [[stages]]
                id = "consensus"
                type = "consensus"
                depends_on = ["review", "agenda"]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        manifest = run_pipeline(config)
        run_dir = workspace / ".runs" / manifest.run_id
        placeholder = json.loads((run_dir / "stages" / "ledger" / "package.json").read_text(encoding="utf-8"))
        placeholder_evidence = {item["id"]: item["excerpt"] for item in placeholder["evidence"]}
        self.assertNotEqual(placeholder_evidence["E-C1"], AUDIT_EXCERPT)
        merged = json.loads((run_dir / "stages" / "consensus" / "consensus.json").read_text(encoding="utf-8"))
        evidence = {item["id"]: item["excerpt"] for item in merged["evidence"]}
        claims = {item["id"]: item for item in merged["ledger_claims"]}
        self.assertEqual(evidence["E-C1"], AUDIT_EXCERPT)
        self.assertEqual(claims["C1"]["claim"]["primary"], "The counting rule is the first listed contribution.")
        self.assertEqual(claims["C1"]["in_paper_evidence_ids"], ["E-C1"])
        self.assertEqual(claims["C2"]["metadata"]["audit"], "pending")
        self.assertEqual(evidence["E-C2"], placeholder_evidence["E-C2"])


class HelperTests(unittest.TestCase):
    def test_command_argv_accepts_a_list_or_a_quoted_string(self) -> None:
        self.assertEqual(command_argv(["claude", "-p"]), ["claude", "-p"])
        self.assertEqual(command_argv("claude -p --tools ''"), ["claude", "-p", "--tools", ""])
        for bad in ("", [], [""], [1], None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                command_argv(bad)

    def test_flatten_keeps_a_single_turn_unchanged(self) -> None:
        system, prompt = flatten_messages(
            [{"role": "system", "content": "judgment=x"}, {"role": "user", "content": "packet"}]
        )
        self.assertEqual((system, prompt), ("judgment=x", "packet"))
        with self.assertRaises(CommandTransportError):
            flatten_messages([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}])

    def test_reply_text_unwraps_only_a_claude_code_envelope(self) -> None:
        plain = json.dumps({"result": "kept as the reply", "items": []})
        self.assertEqual(reply_text(plain), (plain, {}))
        error = json.dumps({"type": "result", "is_error": True, "result": "synthetic error"})
        with self.assertRaises(CommandTransportError):
            reply_text(error)
        truncated = json.dumps({"type": "result", "is_error": False, "stop_reason": "max_tokens", "result": "{"})
        with self.assertRaises(CommandTransportError):
            reply_text(truncated)

    def test_recover_json_leaves_unrecoverable_text_alone(self) -> None:
        self.assertEqual(recover_json('note\n```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(recover_json("no object here"), "no object here")
        self.assertEqual(recover_json('{"a": '), '{"a": ')


if __name__ == "__main__":
    unittest.main()
