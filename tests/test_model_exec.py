"""Closed-loop model execution against a loopback chat server. Synthetic text only."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from autopaperreview.adapters import FixtureReviewAdapter, ReviewRequest
from autopaperreview.anchors import AUDIT_CHECK_IDS, LOCKED_HOLISTIC_SAMPLES, checks_score
from autopaperreview.bilingual_coverage import package_bilingual_gaps
from autopaperreview.hashing import sha256_file
from autopaperreview.model_exec import (
    ModelExecutionError,
    ModelSession,
    OpenAICompatibleAdapter,
    OpenAICompatibleVisionInspector,
    endpoint_kind,
    locate_excerpt,
)
from autopaperreview.models import ReviewRoute, RouteKind
from autopaperreview.pipeline import PipelineError, run_pipeline
from autopaperreview.visual import VisualPageRequest
from tests.support import legacy_issue, make_artifact


MANUSCRIPT = (
    "Synthetic cohort uses eight images from one specimen.\n"
    "Accuracy was reported as 95 percent on the held-out pair.\n"
    "Contribution 1. A counting rule for the synthetic cohort.\n"
)
COHORT = "Synthetic cohort uses eight images from one specimen."
ACCURACY = "Accuracy was reported as 95 percent on the held-out pair."
CONTRIBUTION = "Contribution 1. A counting rule for the synthetic cohort."
SECRET = "apr-test-key-9f3c2a"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
DIMENSIONS = (
    ("originality", "or-4"),
    ("importance_of_research_question", "im-4"),
    ("claims_supported", "cs-4"),
    ("experimental_soundness", "es-4"),
    ("writing_clarity", "wc-4"),
    ("community_value", "cv-4"),
    ("prior_work_contextualization", "pw-4"),
)


def _chat(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


def _contribution(excerpt: str = CONTRIBUTION) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "id": "C1",
                    "claim": "The counting rule is the first listed contribution.",
                    "excerpt": excerpt,
                    "tests": "The held-out pair is the only test named for this counting rule.",
                    "comparator": "No matched-setting comparator is named for the counting rule.",
                    "missing": "A specimen-level holdout is missing for the counting rule.",
                    "severity": "major",
                    "confidence": 0.5,
                }
            ]
        }
    )


def _ledger() -> str:
    return json.dumps(
        {
            "ledger": [
                {
                    "id": "L1",
                    "claim": "The manuscript reports 95 percent accuracy.",
                    "excerpt": ACCURACY,
                    "risk": "A single accuracy number has no interval.",
                }
            ],
            "agenda": [
                {
                    "id": "Q1",
                    "question": "Does a specimen-level holdout exist for the counting rule?",
                    "claim_ids": ["C1", "L1"],
                }
            ],
        }
    )


def _dimensions(excerpts: dict[str, str] | None = None) -> str:
    overrides = excerpts or {}
    return json.dumps(
        {
            "dimensions": [
                {
                    "dimension": name,
                    "clause_id": clause,
                    "rationale": f"The synthetic cohort sentence is the evidence for {name}.",
                    "excerpt": overrides.get(name, COHORT),
                }
                for name, clause in DIMENSIONS
            ]
        }
    )


def _zh_hans() -> str:
    return json.dumps(
        {
            "issues": [
                {
                    "id": "I-C1",
                    "title": "计数规则是列出的第一项贡献。",
                    "evidence": "留出配对是该计数规则唯一被点名的测试。",
                    "impact": "计数规则缺少标本级留出。",
                    "required_action": "计数规则没有匹配设定的对照。",
                    "paper_said": "稿件把计数规则列为第一项贡献。",
                    "explanation": "计数规则缺少标本级留出，因此该贡献尚不完整。",
                }
            ],
            "ledger": [
                {
                    "id": "C1",
                    "claim": "计数规则是列出的第一项贡献。",
                    "risk": "计数规则缺少标本级留出。",
                },
                {
                    "id": "L1",
                    "claim": "稿件报告了百分之九十五的准确率。",
                    "risk": "单个准确率数字没有区间。",
                },
            ],
            "dimensions": [
                {
                    "dimension": name,
                    "rationale": f"合成队列这句话是{name}维度的证据。",
                }
                for name, _clause in DIMENSIONS
            ],
        },
        ensure_ascii=False,
    )


def _fatal(excerpt: str = CONTRIBUTION) -> str:
    return json.dumps(
        {
            "classifications": [
                {
                    "issue_id": "I-C1",
                    "fatal_class": "leakage",
                    "decision_relevant": True,
                    "excerpt": excerpt,
                },
                {
                    "issue_id": "I-missing",
                    "fatal_class": "wrong_metric",
                    "decision_relevant": True,
                    "excerpt": excerpt,
                },
            ]
        }
    )


def _fatal_verify(holds: bool = True) -> str:
    return json.dumps({"verdicts": [{"issue_id": "I-C1", "holds": holds}]})


def _judgment_name(payload: dict[str, Any]) -> str:
    system = payload["messages"][0]["content"]
    line = system.splitlines()[0]
    if not line.startswith("judgment="):
        raise AssertionError(f"system prompt did not name a judgment: {line}")
    return line.split("=", 1)[1]


class ChatServer:
    def __init__(self, reply: Callable[[dict[str, Any], int], bytes]) -> None:
        self.reply = reply
        self.bodies: list[bytes] = []
        self.authorizations: list[str | None] = []
        self.counts: dict[str, int] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                owner.bodies.append(body)
                owner.authorizations.append(self.headers.get("Authorization"))
                payload = json.loads(body.decode("utf-8"))
                name = _judgment_name(payload)
                owner.counts[name] = owner.counts.get(name, 0) + 1
                data = owner.reply(payload, owner.counts[name])
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                return

        return Handler

    def __enter__(self) -> "ChatServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"


def _closed_loop_reply(payload: dict[str, Any], attempt: int) -> bytes:
    del attempt
    name = _judgment_name(payload)
    content = {
        "contributions": _contribution(),
        "ledger": _ledger(),
        "dimensions": _dimensions(),
        "fatal": _fatal(),
        "fatal-verify": _fatal_verify(),
    }[name]
    return _chat(content)


def _assert_secret_absent(root: Path, secret: str = SECRET) -> None:
    token = secret.encode("utf-8")
    for path in root.rglob("*"):
        if path.is_file() and token in path.read_bytes():
            raise AssertionError(f"API key was written to {path}")


def _write_project(
    workspace: Path,
    *,
    base_url: str,
    judgments: list[str],
    network_policy: str = "deny",
    allow_remote: bool = False,
    requires_network: bool = False,
    packet_routes: dict[str, str] | None = None,
    bilingual: bool = False,
    with_report: bool = False,
    cheap: bool = False,
    holistic_samples: int | None = None,
    holistic_mode: str | None = None,
    manuscript: str = MANUSCRIPT,
) -> Path:
    (workspace / "manuscript.txt").write_text(manuscript, encoding="utf-8")
    prompts = workspace / "prompts"
    prompts.mkdir()
    (prompts / "contribution_audit_v1.md").write_text(
        "Audit each listed contribution. This packet is synthetic.\n",
        encoding="utf-8",
    )
    routes = packet_routes or {name: "M7" for name in judgments}
    route_table = "\n".join(f'{name} = "{route}"' for name, route in routes.items())
    judgment_list = ", ".join(f'"{item}"' for item in judgments)
    bilingual_line = "bilingual = true\n" if bilingual else ""
    cheap_line = "cheap = true\n" if cheap else ""
    samples_line = f"holistic_samples = {holistic_samples}\n" if holistic_samples is not None else ""
    mode_line = f'holistic_mode = "{holistic_mode}"\n' if holistic_mode is not None else ""
    report = ""
    if with_report:
        report = """
[[stages]]
id = "report"
type = "report"
depends_on = ["review"]
[stages.params]
bilingual = true
"""
    config = workspace / "review.toml"
    config.write_text(
        f"""
schema_version = 1

[project]
id = "synthetic-model-review"
title = "Synthetic model review"
source = "manuscript.txt"
run_root = ".runs"
default_language = "en"
network_policy = "{network_policy}"

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
base_url = "{base_url}"
model = "synthetic-reviewer"
api_key_env = "APR_MODEL_API_KEY"
allow_remote = {"true" if allow_remote else "false"}
requires_network = {"true" if requires_network else "false"}
network_scope = "fulltext"
judgments = [{judgment_list}]
timeout_seconds = 10
{bilingual_line}{cheap_line}{samples_line}{mode_line}
[stages.params.packet_routes]
{route_table}
{report}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config


def _exchange_hashes_match(stage_dir: Path, exchanges: list[dict[str, Any]]) -> None:
    for item in exchanges:
        request_path = stage_dir / item["request_path"]
        response_path = stage_dir / item["response_path"]
        if sha256_file(request_path) != item["request_sha256"]:
            raise AssertionError(item["request_path"])
        if sha256_file(response_path) != item["response_sha256"]:
            raise AssertionError(item["response_path"])


class ClosedLoopTests(unittest.TestCase):
    def test_judgments_store_hashes_and_reuse_the_stage_signature(self) -> None:
        with ChatServer(_closed_loop_reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal"],
            )
            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                first = run_pipeline(config)
            review = first.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["egress"], "loopback")
            self.assertFalse(review.metadata["manuscript_left_machine"])
            self.assertFalse(first.metadata["manuscript_left_machine"])
            self.assertEqual(review.metadata["result_cache"], "stage_signature")
            self.assertEqual(len(review.metadata["model_exchanges"]), 5)
            self.assertEqual(server.authorizations, [f"Bearer {SECRET}"] * 5)
            run_dir = workspace / ".runs" / first.run_id
            stage_dir = run_dir / "stages" / "review"
            _exchange_hashes_match(stage_dir, review.metadata["model_exchanges"])
            _assert_secret_absent(run_dir)
            for item in review.metadata["model_exchanges"]:
                body = json.loads((stage_dir / item["request_path"]).read_text(encoding="utf-8"))
                self.assertEqual(body["response_format"], {"type": "json_object"})
                self.assertNotIn("Authorization", body)
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(package["issues"][0]["id"], "I-C1")
            self.assertEqual(package["issues"][0]["fatal_class"], "leakage")
            self.assertEqual(package["issues"][0]["anchor"]["excerpt"], CONTRIBUTION)
            self.assertEqual([item["id"] for item in package["ledger_claims"]], ["C1", "L1"])
            self.assertEqual(package["agenda"][0]["claim_ids"], ["C1", "L1"])
            self.assertEqual(len(package["dimension_scores"]), 7)
            self.assertEqual(package["dimension_scores"][3]["score"], 4.0)
            rejected = json.loads((stage_dir / "model" / "rejected.json").read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in rejected], ["I-missing"])
            calls = len(server.bodies)
            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                second = run_pipeline(config)
            self.assertTrue(second.stage("review").metadata["cache_hit"])
            self.assertEqual(second.stage("review").signature, review.signature)
            self.assertEqual(len(server.bodies), calls)

    def test_schema_failure_retries_once_then_fails_the_stage(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat('{"wrong": true}')

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["contributions"])
            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                with self.assertRaises(PipelineError) as caught:
                    run_pipeline(config)
            self.assertIn("after one retry", str(caught.exception))
            self.assertNotIn("Deterministic", str(caught.exception))
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertEqual(len(server.bodies), 2)
            run_dir = next((workspace / ".runs").iterdir())
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            review = next(stage for stage in manifest["stages"] if stage["id"] == "review")
            self.assertEqual(review["status"], "failed")
            self.assertEqual(len(review["metadata"]["model_exchanges"]), 2)
            attempt = run_dir / review["metadata"]["attempt_dir"]
            _exchange_hashes_match(attempt, review["metadata"]["model_exchanges"])
            self.assertFalse((run_dir / "stages" / "review" / "package.json").exists())
            _assert_secret_absent(run_dir)

    def test_retry_accepts_the_corrected_json(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload
            if attempt == 1:
                return _chat("not-json")
            return _chat(_contribution())

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["contributions"])
            manifest = run_pipeline(config)
            self.assertEqual(len(server.bodies), 2)
            second = json.loads(server.bodies[1].decode("utf-8"))
            retry = second["messages"][-1]["content"]
            self.assertIn("Validation failed", retry)
            self.assertIn("not a JSON object", retry)
            package = json.loads(
                (workspace / ".runs" / manifest.run_id / "stages" / "review" / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(package["issues"][0]["id"], "I-C1")
            self.assertEqual([item["attempt"] for item in manifest.stage("review").metadata["model_exchanges"]], [1, 2])

    def test_unresolved_excerpt_rejects_the_item(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat(_contribution("this sentence was not written in the manuscript"))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["contributions"])
            manifest = run_pipeline(config)
            self.assertEqual(len(server.bodies), 1)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(package["issues"], [])
            rejected = json.loads((stage_dir / "model" / "rejected.json").read_text(encoding="utf-8"))
            self.assertEqual(rejected[0]["id"], "C1")
            self.assertIn("does not occur", rejected[0]["reason"])
            self.assertEqual(manifest.stage("review").metadata["rejected_count"], 1)

    def test_fixture_json_remains_an_equal_option(self) -> None:
        with tempfile_dir() as workspace:
            (workspace / "manuscript.txt").write_text(MANUSCRIPT, encoding="utf-8")
            (workspace / "review_input.json").write_text(
                json.dumps([legacy_issue(detected_by=[], sources=[])]),
                encoding="utf-8",
            )
            config = workspace / "review.toml"
            config.write_text(
                """
schema_version = 1

[project]
id = "synthetic-agent-review"
title = "Synthetic agent review"
source = "manuscript.txt"
run_root = ".runs"
network_policy = "deny"

[[stages]]
id = "ingest"
type = "ingest"

[[stages]]
id = "prompt_packets"
type = "prompt_packet"
depends_on = ["ingest"]

[[stages]]
id = "review"
type = "execute_review"
depends_on = ["prompt_packets"]
[stages.params]
adapter = "autopaperreview.adapters:FixtureReviewAdapter"
fixture_path = "review_input.json"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            manifest = run_pipeline(config)
            package = json.loads(
                (workspace / ".runs" / manifest.run_id / "stages" / "review" / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(package["issues"][0]["id"], "I001")
            self.assertNotIn("manuscript_left_machine", manifest.stage("review").metadata)
            scratch = workspace / "scratch"
            scratch.mkdir()
            direct = FixtureReviewAdapter().execute(
                ReviewRequest(
                    project_id="synthetic-agent-review",
                    manuscript=make_artifact(),
                    fixture_path=str(workspace / "review_input.json"),
                ),
                scratch,
            )
            self.assertEqual(direct.package.issues[0].id, "I001")


class EgressTests(unittest.TestCase):
    def _request(self, **metadata: Any) -> ReviewRequest:
        values: dict[str, Any] = {
            "base_url": "https://models.example.invalid/v1",
            "model": "synthetic-reviewer",
            "judgments": ["contributions"],
            "packet_routes": {"contributions": "M7", "ledger": "M7", "dimensions": "M7", "fatal": "M7"},
            "network_policy": "deny",
            "allow_remote": False,
            "api_key_env": "APR_MODEL_API_KEY",
        }
        values.update(metadata)
        return ReviewRequest(
            project_id="synthetic-egress",
            manuscript=make_artifact(),
            source_text=MANUSCRIPT,
            packets={"M7": "contribution audit\n" + MANUSCRIPT},
            routes=[
                ReviewRoute(
                    id="M7",
                    name="Per-contribution evidence and retrieval audit",
                    kind=RouteKind.prompt,
                )
            ],
            metadata=values,
        )

    def test_remote_endpoint_is_denied_without_flag_key_or_allow_policy(self) -> None:
        calls: list[Any] = []

        def transport(request: Any, timeout: float) -> bytes:
            del timeout
            calls.append(request.full_url)
            return _chat(_contribution())

        adapter = OpenAICompatibleAdapter(transport)
        with tempfile_dir() as scratch_root:
            scratch = scratch_root / "scratch"
            scratch.mkdir()
            with self.assertRaises(PermissionError) as denied:
                adapter.execute(self._request(), scratch)
            self.assertIn("allow_remote", str(denied.exception))
            self.assertEqual(calls, [])

            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                with self.assertRaises(PermissionError) as missing_policy:
                    adapter.execute(self._request(allow_remote=True), scratch)
            self.assertIn("network_policy", str(missing_policy.exception))
            self.assertNotIn(SECRET, str(missing_policy.exception))
            self.assertEqual(calls, [])

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("APR_MODEL_API_KEY", None)
                with self.assertRaises(PermissionError) as missing_key:
                    adapter.execute(
                        self._request(allow_remote=True, network_policy="allow"),
                        scratch,
                    )
            self.assertIn("APR_MODEL_API_KEY", str(missing_key.exception))
            self.assertEqual(calls, [])

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("APR_MODEL_API_KEY", None)
                result = adapter.execute(
                    self._request(base_url="http://127.0.0.1:9/v1", network_policy="deny"),
                    scratch,
                )
            self.assertFalse(result.metadata["manuscript_left_machine"])
            self.assertEqual(result.metadata["egress"], "loopback")
            self.assertEqual(calls, ["http://127.0.0.1:9/v1/chat/completions"])
            _assert_secret_absent(scratch_root)

    def test_remote_run_notes_that_manuscript_content_left_the_machine(self) -> None:
        calls: list[str] = []

        def transport(request: Any, timeout: float) -> bytes:
            del timeout
            calls.append(request.full_url)
            if request.get_header("Authorization") != f"Bearer {SECRET}":
                raise AssertionError("missing bearer token")
            return _chat(_contribution())

        OpenAICompatibleAdapter.transport = transport
        try:
            with tempfile_dir() as workspace:
                config = _write_project(
                    workspace,
                    base_url="https://models.example.invalid/v1",
                    judgments=["contributions"],
                    network_policy="allow",
                    allow_remote=True,
                    requires_network=True,
                )
                with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                    manifest = run_pipeline(config)
                self.assertEqual(calls, ["https://models.example.invalid/v1/chat/completions"])
                self.assertTrue(manifest.metadata["manuscript_left_machine"])
                self.assertEqual(manifest.metadata["manuscript_egress"], "remote")
                review = manifest.stage("review")
                assert review is not None
                self.assertTrue(review.metadata["manuscript_left_machine"])
                self.assertEqual(review.metadata["egress"], "remote")
                run_dir = workspace / ".runs" / manifest.run_id
                _assert_secret_absent(run_dir)
                request_path = (
                    run_dir / "stages" / "review" / review.metadata["model_exchanges"][0]["request_path"]
                )
                self.assertNotIn(SECRET, request_path.read_text(encoding="utf-8"))
        finally:
            OpenAICompatibleAdapter.transport = None


class VisualModelTests(unittest.TestCase):
    def test_page_image_is_sent_and_a_bad_hash_is_rejected(self) -> None:
        digest = hashlib.sha256(TINY_PNG).hexdigest()

        seen: list[str] = []

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            user = payload["messages"][1]["content"]
            seen.append(user[1]["type"] + ":" + user[1]["image_url"]["url"][:22])
            if attempt == 1:
                return _chat('{"findings":[{"id":"broken"}]}')
            return _chat(
                json.dumps(
                    {
                        "findings": [
                            {
                                "id": "V1",
                                "page": 1,
                                "bbox": [0, 0, 1, 1],
                                "page_image_sha256": digest,
                                "kind": "legibility",
                                "visible_mark": "tick labels collide",
                                "caption_excerpt": "A schematic.",
                            },
                            {
                                "id": "V2",
                                "page": 1,
                                "bbox": [0, 0, 1, 1],
                                "page_image_sha256": "b" * 64,
                                "kind": "axis_legend",
                                "visible_mark": "axis titles overlap",
                            },
                        ]
                    }
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as scratch:
            image = scratch / "page.png"
            image.write_bytes(TINY_PNG)
            inspector = OpenAICompatibleVisionInspector()
            inspector.bind(endpoint=server.base_url, network_allowed=False)
            inspector.configure(
                model="synthetic-vision",
                allow_remote=False,
                base_url=server.base_url,
                packets={"M6": "Inspect the figure on this page image.\n"},
                packet_route="M6",
                network_policy="deny",
                stage_signature="abc",
                timeout_seconds=10,
                temperature=0,
                json_object=True,
            )
            request = VisualPageRequest(
                page=1,
                page_image_sha256=digest,
                image_path=str(image),
                width_px=1,
                height_px=1,
            )
            findings = inspector.inspect(request, scratch)
            self.assertEqual(seen, ["image_url:data:image/png;base64,"] * 2)
            self.assertEqual([item.id for item in findings], ["V1"])
            self.assertEqual(len(server.bodies), 2)
            rejected = json.loads((scratch / "model" / "rejected.json").read_text(encoding="utf-8"))
            self.assertEqual(rejected[0]["id"], "V2")
            self.assertIn("hash", rejected[0]["reason"])
            index = json.loads((scratch / "model" / "exchanges.json").read_text(encoding="utf-8"))
            _exchange_hashes_match(scratch, index["model_exchanges"])
            _assert_secret_absent(scratch)

    def test_visual_stage_records_the_page_image_exchange(self) -> None:
        digest = hashlib.sha256(TINY_PNG).hexdigest()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat(
                json.dumps(
                    {
                        "findings": [
                            {
                                "id": "V1",
                                "page": 1,
                                "bbox": [0, 0, 1, 1],
                                "page_image_sha256": digest,
                                "kind": "legibility",
                                "visible_mark": "tick labels collide",
                            }
                        ]
                    }
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            (workspace / "manuscript.txt").write_text(MANUSCRIPT, encoding="utf-8")
            prompts = workspace / "prompts"
            prompts.mkdir()
            (prompts / "figure_grounded_v1.md").write_text(
                "Inspect the figure on the page image.\n",
                encoding="utf-8",
            )
            script = workspace / "write_pages.py"
            script.write_text(
                "import hashlib, json, sys\n"
                "from pathlib import Path\n"
                f"png = {TINY_PNG!r}\n"
                "stage = Path(sys.argv[1])\n"
                "page = stage / 'pages'\n"
                "page.mkdir(parents=True)\n"
                "(page / 'page-001.png').write_bytes(png)\n"
                "manifest = {'pages': [{'page': 1, 'filename': 'page-001.png', "
                "'sha256': hashlib.sha256(png).hexdigest(), 'width_px': 1, 'height_px': 1}]}\n"
                "(stage / 'manifest.json').write_text(json.dumps(manifest))\n",
                encoding="utf-8",
            )
            config = workspace / "review.toml"
            config.write_text(
                f"""
schema_version = 1

[project]
id = "synthetic-visual-review"
title = "Synthetic visual review"
source = "manuscript.txt"
run_root = ".runs"
network_policy = "deny"

[[routes]]
id = "M6"
name = "Figure-grounded visual review"
kind = "visual"
prompt = "prompts/figure_grounded_v1.md"
prompt_version = "1.0"

[[stages]]
id = "ingest"
type = "ingest"

[[stages]]
id = "prompt_packets"
type = "prompt_packet"
depends_on = ["ingest"]

[[stages]]
id = "pages"
type = "command"
depends_on = ["ingest"]
[stages.params]
command = ["python3", "write_pages.py", "{{stage_dir}}"]
outputs = ["manifest.json", "pages/page-001.png"]

[[stages]]
id = "visual"
type = "visual_inspect"
depends_on = ["pages", "prompt_packets"]
[stages.params]
adapter = "autopaperreview.model_exec:OpenAICompatibleVisionInspector"
base_url = "{server.base_url}"
model = "synthetic-vision"
packet_route = "M6"
require_pages_seen = false
timeout_seconds = 10
""".strip()
                + "\n",
                encoding="utf-8",
            )
            first = run_pipeline(config)
            visual = first.stage("visual")
            assert visual is not None
            self.assertEqual(visual.metadata["finding_count"], 1)
            self.assertEqual(visual.metadata["egress"], "loopback")
            self.assertFalse(visual.metadata["manuscript_left_machine"])
            stage_dir = workspace / ".runs" / first.run_id / "stages" / "visual"
            _exchange_hashes_match(stage_dir, visual.metadata["model_exchanges"])
            body = (stage_dir / visual.metadata["model_exchanges"][0]["request_path"]).read_text(encoding="utf-8")
            self.assertIn("image_url", body)
            calls = len(server.bodies)
            second = run_pipeline(config)
            self.assertTrue(second.stage("visual").metadata["cache_hit"])
            self.assertEqual(len(server.bodies), calls)


class DimensionAnchorTests(unittest.TestCase):
    def test_missing_dimension_retries_once_then_fails(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat(_dimensions({"originality": "this sentence was not written in the manuscript"}))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["dimensions"])
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            message = str(caught.exception)
            self.assertIn("after one retry", message)
            self.assertIn("originality", message)
            self.assertIn("does not occur", message)
            self.assertEqual(len(server.bodies), 2)
            retry = json.loads(server.bodies[1].decode("utf-8"))["messages"][-1]["content"]
            self.assertIn("originality", retry)
            self.assertIn("does not occur", retry)
            run_dir = next((workspace / ".runs").iterdir())
            manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            review = next(stage for stage in manifest["stages"] if stage["id"] == "review")
            self.assertEqual(review["status"], "failed")
            self.assertEqual(len(review["metadata"]["model_exchanges"]), 2)
            self.assertFalse((run_dir / "stages" / "review" / "package.json").exists())

    def test_dimension_retry_accepts_resolved_excerpts(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload
            if attempt == 1:
                return _chat(_dimensions({"originality": "this sentence was not written in the manuscript"}))
            return _chat(_dimensions())

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["dimensions"])
            manifest = run_pipeline(config)
            self.assertEqual(len(server.bodies), 2)
            package = json.loads(
                (workspace / ".runs" / manifest.run_id / "stages" / "review" / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(package["dimension_scores"]), 7)
            self.assertEqual(manifest.stage("review").metadata["rejected_count"], 0)

    def test_whitespace_difference_still_resolves_the_excerpt(self) -> None:
        broken = "Synthetic cohort uses eight\nimages from one specimen."

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat(_dimensions({"experimental_soundness": broken}))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["dimensions"])
            manifest = run_pipeline(config)
            self.assertEqual(len(server.bodies), 1)
            package = json.loads(
                (workspace / ".runs" / manifest.run_id / "stages" / "review" / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            evidence = next(item for item in package["evidence"] if item["id"] == "E-experimental_soundness")
            self.assertEqual(evidence["excerpt"], COHORT)
            request = json.loads(server.bodies[0].decode("utf-8"))
            user = request["messages"][1]["content"]
            self.assertIn("sar_bands_v1", user)
            self.assertIn("Matched controls, uncertainty", user)
            self.assertIn("not a default", user)

    def test_unverified_fatal_class_is_not_stored(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name == "fatal-verify":
                return _chat(_fatal_verify(False))
            return _closed_loop_reply(payload, 1)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "fatal"],
            )
            manifest = run_pipeline(config)
            package = json.loads(
                (workspace / ".runs" / manifest.run_id / "stages" / "review" / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(package["issues"][0]["fatal_class"])
            rejected = json.loads(
                (workspace / ".runs" / manifest.run_id / "stages" / "review" / "model" / "rejected.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("not verified", rejected[-1]["reason"])

    def test_anchor_retry_does_not_start_another_schema_retry(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload
            if attempt == 1:
                return _chat(_dimensions({"writing_clarity": "this sentence was not written in the manuscript"}))
            return _chat('{"wrong": true}')

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["dimensions"])
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertEqual(len(server.bodies), 2)
            self.assertIn("after one retry", str(caught.exception))
            self.assertIn("does not occur", str(caught.exception))
            self.assertIn("writing_clarity", str(caught.exception))


class EmptyListTests(unittest.TestCase):
    def test_empty_contribution_list_is_a_schema_failure(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat('{"items":[]}')

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["contributions"])
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("after one retry", str(caught.exception))
            self.assertEqual(len(server.bodies), 2)
            self.assertIn("at least 1", server.bodies[1].decode("utf-8"))
            self.assertIn("Validation failed", server.bodies[1].decode("utf-8"))

    def test_empty_ledger_list_is_a_schema_failure(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat('{"ledger":[],"agenda":[]}')

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["ledger"])
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("after one retry", str(caught.exception))
            self.assertEqual(len(server.bodies), 2)
            self.assertIn("at least 1", server.bodies[1].decode("utf-8"))


def _http_error(status: int, secret: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:9/v1/chat/completions",
        status,
        f"status {status} {secret}".strip(),
        hdrs=None,
        fp=io.BytesIO(b'{"error":"status"}'),
    )


def _text_request(**metadata: Any) -> ReviewRequest:
    values: dict[str, Any] = {
        "base_url": "http://127.0.0.1:9/v1",
        "model": "synthetic-reviewer",
        "judgments": ["contributions"],
        "packet_routes": {"contributions": "M7"},
        "network_policy": "deny",
        "allow_remote": False,
        "api_key_env": "APR_MODEL_API_KEY",
    }
    values.update(metadata)
    return ReviewRequest(
        project_id="synthetic-transport",
        manuscript=make_artifact(),
        source_text=MANUSCRIPT,
        packets={"M7": "contribution audit\n" + MANUSCRIPT},
        routes=[
            ReviewRoute(
                id="M7",
                name="Per-contribution evidence and retrieval audit",
                kind=RouteKind.prompt,
            )
        ],
        metadata=values,
    )


class TransportRetryTests(unittest.TestCase):
    def tearDown(self) -> None:
        import time

        ModelSession.sleeper = time.sleep

    def test_retryable_transport_errors_back_off_then_succeed(self) -> None:
        calls: list[int] = []
        delays: list[float] = []

        def transport(request: Any, timeout: float) -> bytes:
            del request, timeout
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("timed out")
            if len(calls) == 2:
                raise _http_error(429)
            return _chat(_contribution())

        ModelSession.sleeper = delays.append
        adapter = OpenAICompatibleAdapter(transport)
        with tempfile_dir() as scratch:
            result = adapter.execute(_text_request(), scratch)
            self.assertEqual(result.package.issues[0].id, "I-C1")
            self.assertEqual(len(calls), 3)
            self.assertEqual(delays, [0.5, 1.0])
            self.assertEqual([item["transport_try"] for item in result.metadata["model_exchanges"]], [1, 2, 3])
            self.assertEqual({item["attempt"] for item in result.metadata["model_exchanges"]}, {1})
            _exchange_hashes_match(scratch, result.metadata["model_exchanges"])

    def test_other_4xx_fails_immediately(self) -> None:
        calls: list[int] = []
        delays: list[float] = []

        def transport(request: Any, timeout: float) -> bytes:
            del request, timeout
            calls.append(1)
            raise _http_error(400, SECRET)

        ModelSession.sleeper = delays.append
        adapter = OpenAICompatibleAdapter(transport)
        with tempfile_dir() as scratch:
            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                with self.assertRaises(ModelExecutionError) as caught:
                    adapter.execute(_text_request(), scratch)
            self.assertEqual(calls, [1])
            self.assertEqual(delays, [])
            self.assertIn("HTTP 400", str(caught.exception))
            self.assertNotIn("transport retries", str(caught.exception))
            self.assertNotIn(SECRET, str(caught.exception))
            _assert_secret_absent(scratch)

    def test_transport_retries_do_not_consume_the_schema_retry(self) -> None:
        calls: list[int] = []
        delays: list[float] = []

        def transport(request: Any, timeout: float) -> bytes:
            del timeout
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.URLError("connection refused")
            content = json.loads(request.data.decode("utf-8"))["messages"][-1]["content"]
            if "Validation failed" in content:
                return _chat(_contribution())
            return _chat("not-json")

        ModelSession.sleeper = delays.append
        adapter = OpenAICompatibleAdapter(transport)
        with tempfile_dir() as scratch:
            result = adapter.execute(_text_request(), scratch)
            self.assertEqual(result.package.issues[0].id, "I-C1")
            self.assertEqual(len(calls), 3)
            self.assertEqual(delays, [0.5])
            self.assertEqual([item["attempt"] for item in result.metadata["model_exchanges"]], [1, 1, 2])

    def test_transport_failure_stops_after_two_retries(self) -> None:
        calls: list[int] = []
        delays: list[float] = []

        def transport(request: Any, timeout: float) -> bytes:
            del request, timeout
            calls.append(1)
            raise _http_error(503)

        ModelSession.sleeper = delays.append
        adapter = OpenAICompatibleAdapter(transport)
        with tempfile_dir() as scratch:
            with self.assertRaises(ModelExecutionError) as caught:
                adapter.execute(_text_request(), scratch)
            self.assertEqual(len(calls), 3)
            self.assertEqual(delays, [0.5, 1.0])
            self.assertIn("after 2 transport retries", str(caught.exception))
            self.assertIn("HTTP 503", str(caught.exception))
            self.assertEqual(len(json.loads((scratch / "model" / "exchanges.json").read_text())["model_exchanges"]), 3)


class BilingualJudgmentTests(unittest.TestCase):
    def test_zh_hans_pass_lets_bilingual_export_succeed(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            if _judgment_name(payload) == "zh-Hans":
                return _chat(_zh_hans())
            return _closed_loop_reply(payload, 1)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal"],
                bilingual=True,
                with_report=True,
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts["zh-Hans"], 1)
            review = manifest.stage("review")
            assert review is not None
            names = [item["judgment"] for item in review.metadata["model_exchanges"]]
            self.assertEqual(
                names,
                ["contributions", "ledger", "dimensions", "fatal", "fatal-verify", "zh-Hans"],
            )
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            _exchange_hashes_match(stage_dir, review.metadata["model_exchanges"])
            package_path = stage_dir / "package.json"
            from autopaperreview.models import ReviewPackage

            package = ReviewPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
            self.assertEqual(package_bilingual_gaps(package, ["en", "zh-Hans"]), [])
            self.assertIn("计数规则", package.issues[0].title.get("zh-Hans") or "")
            report = (workspace / ".runs" / manifest.run_id / "stages" / "report" / "review_report.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("zh-Hans", report)
            self.assertIn("计数规则", report)
            calls = len(server.bodies)
            second = run_pipeline(config)
            self.assertTrue(second.stage("review").metadata["cache_hit"])
            self.assertEqual(second.stage("review").signature, review.signature)
            self.assertEqual(len(server.bodies), calls)

    def test_zh_hans_schema_failure_retries_once(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            if _judgment_name(payload) == "zh-Hans":
                return _chat('{"issues":[],"ledger":[],"dimensions":[]}')
            return _chat(_contribution())

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions"],
                bilingual=True,
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("after one retry", str(caught.exception))
            self.assertEqual(server.counts["contributions"], 1)
            self.assertEqual(server.counts["zh-Hans"], 2)
            self.assertIn("Validation failed", server.bodies[-1].decode("utf-8"))
            self.assertIn("I-C1", server.bodies[-1].decode("utf-8"))

    def test_replacing_the_api_key_does_not_rerun_the_stage(self) -> None:
        with ChatServer(_closed_loop_reply) as server, tempfile_dir() as workspace:
            config = _write_project(workspace, base_url=server.base_url, judgments=["contributions"])
            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": SECRET}):
                first = run_pipeline(config)
            calls = len(server.bodies)
            with mock.patch.dict(os.environ, {"APR_MODEL_API_KEY": "apr-other-key-111"}):
                second = run_pipeline(config)
            self.assertEqual(second.stage("review").signature, first.stage("review").signature)
            self.assertTrue(second.stage("review").metadata["cache_hit"])
            self.assertEqual(len(server.bodies), calls)


class DnsClassificationTests(unittest.TestCase):
    def test_hostname_that_resolves_only_to_loopback_is_loopback(self) -> None:
        def fake(host: str, port: object, *args: object, **kwargs: object) -> list[Any]:
            del port, args, kwargs
            table = {
                "loopback.test": [("127.0.0.1", 0)],
                "mixed.test": [("127.0.0.1", 0), ("203.0.113.5", 0)],
            }
            if host not in table:
                raise socket.gaierror("failed")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", item) for item in table[host]]

        with mock.patch("autopaperreview.model_exec.socket.getaddrinfo", side_effect=fake) as looked:
            self.assertEqual(endpoint_kind("http://loopback.test/v1"), "loopback")
            self.assertEqual(endpoint_kind("http://mixed.test/v1"), "remote")
            self.assertEqual(endpoint_kind("http://missing.test/v1"), "remote")
            looked.reset_mock()
            self.assertEqual(endpoint_kind("http://127.0.0.1:9/v1"), "loopback")
            self.assertEqual(endpoint_kind("http://localhost:9/v1"), "loopback")
            looked.assert_not_called()

    def test_dns_loopback_does_not_require_allow_remote(self) -> None:
        calls: list[str] = []

        def fake(host: str, port: object, *args: object, **kwargs: object) -> list[Any]:
            del host, port, args, kwargs
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

        def transport(request: Any, timeout: float) -> bytes:
            del timeout
            calls.append(request.full_url)
            return _chat(_contribution())

        adapter = OpenAICompatibleAdapter(transport)
        with mock.patch("autopaperreview.model_exec.socket.getaddrinfo", side_effect=fake):
            with tempfile_dir() as scratch:
                result = adapter.execute(
                    _text_request(base_url="http://loopback.test/v1"),
                    scratch,
                )
        self.assertEqual(result.metadata["egress"], "loopback")
        self.assertFalse(result.metadata["manuscript_left_machine"])
        self.assertEqual(calls, ["http://loopback.test/v1/chat/completions"])

    def test_mixed_or_failed_resolution_stays_remote(self) -> None:
        calls: list[str] = []

        def fake(host: str, port: object, *args: object, **kwargs: object) -> list[Any]:
            del port, args, kwargs
            if host == "missing.test":
                raise socket.gaierror("failed")
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.5", 0)),
            ]

        def transport(request: Any, timeout: float) -> bytes:
            del request, timeout
            calls.append("sent")
            return _chat(_contribution())

        adapter = OpenAICompatibleAdapter(transport)
        with mock.patch("autopaperreview.model_exec.socket.getaddrinfo", side_effect=fake):
            with tempfile_dir() as scratch:
                with self.assertRaises(PermissionError) as mixed:
                    adapter.execute(_text_request(base_url="http://mixed.test/v1"), scratch)
                with self.assertRaises(PermissionError) as missing:
                    adapter.execute(_text_request(base_url="http://missing.test/v1"), scratch)
        self.assertIn("allow_remote", str(mixed.exception))
        self.assertIn("allow_remote", str(missing.exception))
        self.assertEqual(calls, [])


class ExcerptMatchTests(unittest.TestCase):
    def test_line_break_hyphen_ligature_and_fuzzy_window_resolve(self) -> None:
        digest = "a" * 64
        hyphenated = "The experi-\nment uses eight images from one specimen."
        ligature = "The ﬁnal count is eight images from one specimen."
        fuzzy_source = "Synthetic cohort uses eight images from one specimen in total."
        hyphen = locate_excerpt(
            "The experiment uses eight images from one specimen.",
            manuscript=hyphenated,
            manuscript_sha=digest,
            snapshots=[],
        )
        lig = locate_excerpt(
            "The final count is eight images from one specimen.",
            manuscript=ligature,
            manuscript_sha=digest,
            snapshots=[],
        )
        fuzzy = locate_excerpt(
            "Synthetic cohort uses eigth images from one specimen in total.",
            manuscript=fuzzy_source,
            manuscript_sha=digest,
            snapshots=[],
        )
        missed = locate_excerpt(
            "This sentence was never written in the source text today.",
            manuscript=fuzzy_source,
            manuscript_sha=digest,
            snapshots=[],
        )
        joined = locate_excerpt(
            "wellknown result",
            manuscript="A well-known result stays hyphenated.",
            manuscript_sha=digest,
            snapshots=[],
        )
        assert hyphen is not None and lig is not None and fuzzy is not None
        self.assertIn("experi", hyphen.excerpt or "")
        self.assertIn("ment", hyphen.excerpt or "")
        self.assertIn("count is eight", lig.excerpt or "")
        self.assertIn("eight images", fuzzy.excerpt or "")
        self.assertIsNone(missed)
        self.assertIsNone(joined)


def _mean_content(*, predicted_mean: float, rating: float) -> str:
    return json.dumps(
        {
            "rationale": "The synthetic comparison shows a gain a reviewer would rely on, so the mean is on the accept side.",
            "rating": rating,
            "predicted_mean": predicted_mean,
        }
    )


def _audit_content(
    *,
    bad_excerpt_id: str | None = None,
    fail_id: str | None = None,
    unclear_id: str | None = None,
    flaws: list[dict[str, Any]] | None = None,
) -> str:
    checks = []
    for check_id in AUDIT_CHECK_IDS:
        row: dict[str, Any] = {
            "id": check_id,
            "verdict": "pass",
            "excerpt": COHORT,
            "note": "The cohort line shows it.",
        }
        if check_id == bad_excerpt_id:
            row["excerpt"] = "This passage was never written in the synthetic manuscript."
        if check_id == fail_id:
            row.update({"verdict": "fail", "excerpt": ACCURACY, "note": "one number with no spread"})
        if check_id == unclear_id:
            row.update({"verdict": "unclear", "excerpt": "", "note": ""})
        checks.append(row)
    return json.dumps({"checks": checks, "flaws": flaws if flaws is not None else []})


class HolisticSampleTests(unittest.TestCase):
    """``holistic_mode = "mean"``: the previous candidate, kept as a declared switch."""

    def test_three_means_average_and_a_shorter_run_stays_short(self) -> None:
        pending = [(4.2, 3.0), (6.0, 4.0), (7.2, 5.0)]
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            self.assertEqual(name, "holistic")
            self.assertEqual(payload["temperature"], 0.7)
            user = payload["messages"][1]["content"]
            self.assertIn("predicted_mean", user)
            self.assertIn("borderline reject", user)
            self.assertNotIn("weak-reject", user)
            self.assertNotIn("strong-accept", user)
            self.assertNotIn("## Reference", user)
            self.assertNotIn("## Verified audit", user)
            with lock:
                predicted_mean, rating = pending.pop()
            return _chat(_mean_content(predicted_mean=predicted_mean, rating=rating))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=3,
                holistic_mode="mean",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(sorted(rating["mean_samples"]), [4.2, 6.0, 7.2])
            self.assertEqual(sorted(rating["samples"]), [3.0, 4.0, 5.0])
            self.assertAlmostEqual(rating["predicted_mean"], (4.2 + 6.0 + 7.2) / 3)
            self.assertAlmostEqual(rating["rating"], 4.0)
            self.assertEqual(rating["n"], 3)
            self.assertEqual(rating["mode"], "mean")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertNotIn("audited_samples", rating)
            self.assertNotIn("expert_samples", rating)
            self.assertNotIn("panel_samples", rating)
            self.assertNotIn("isolate_samples", rating)
            self.assertNotIn("poles", rating)
            self.assertTrue(rating["verified"])
            self.assertEqual(server.counts, {"holistic": 3})

    def test_venue_mean_calls_overlap(self) -> None:
        release = threading.Event()
        seen: list[str] = []
        gate = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            with gate:
                seen.append(name)
                if len(seen) >= 3:
                    release.set()
            if not release.wait(3):
                raise AssertionError("venue-mean calls did not overlap")
            return _chat(_mean_content(predicted_mean=6.2, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=3,
                holistic_mode="mean",
            )
            run_pipeline(config)
            self.assertEqual(server.counts, {"holistic": 3})

    def test_schema_failure_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat("not-json")

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=3,
                holistic_mode="mean",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("holistic", str(caught.exception))
            self.assertEqual(server.counts["holistic"], 6)

    def test_cheap_mode_calls_only_the_venue_means(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name != "holistic":
                raise AssertionError(name)
            user = payload["messages"][1]["content"]
            if "predicted_mean" not in user:
                raise AssertionError("holistic prompt is missing the venue mean")
            return _chat(_mean_content(predicted_mean=6.4, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=3,
                holistic_mode="mean",
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"holistic": 3})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])

    def test_omitted_sample_count_in_mean_mode_follows_the_locked_k(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "holistic")
            return _chat(_mean_content(predicted_mean=4.8, rating=3))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_mode="mean",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"holistic": LOCKED_HOLISTIC_SAMPLES})
            self.assertEqual(rating["mean_samples"], [4.8] * LOCKED_HOLISTIC_SAMPLES)
            self.assertEqual(rating["mode"], "mean")

    def test_two_samples_keep_their_own_mean(self) -> None:
        pending = [5.2, 6.8]
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "holistic")
            with lock:
                predicted_mean = pending.pop()
            return _chat(_mean_content(predicted_mean=predicted_mean, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="mean",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts["holistic"], 2)
            self.assertEqual(sorted(rating["mean_samples"]), [5.2, 6.8])
            self.assertAlmostEqual(rating["predicted_mean"], 6.0)


class AuditedPairTests(unittest.TestCase):
    """The locked ``audited`` mode: one verified audit, then one scoring call."""

    def test_audit_then_score_passes_only_verified_items(self) -> None:
        seen: list[str] = []
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            user = payload["messages"][1]["content"]
            with lock:
                seen.append(name)
            self.assertEqual(payload["temperature"], 0.7)
            if name == "audit":
                self.assertIn("## Audit", user)
                self.assertIn("You are not scoring it", user)
                for check_id in AUDIT_CHECK_IDS:
                    self.assertIn(f"- {check_id}: ", user)
                self.assertNotIn("predicted_mean", user)
                self.assertNotIn("## Venue mean", user)
                self.assertIn(COHORT, user)
                return _chat(
                    _audit_content(
                        bad_excerpt_id="ablation_isolates_claim",
                        fail_id="variance_reported",
                        unclear_id="breadth_of_evidence",
                        flaws=[
                            {
                                "id": "F1",
                                "title": "Single accuracy number",
                                "excerpt": ACCURACY,
                                "why": "No spread is reported for the headline number.",
                                "severity": "major",
                            },
                            {
                                "id": "F2",
                                "title": "Invented problem",
                                "excerpt": "A sentence that is not in the synthetic manuscript at all.",
                                "why": "This flaw has no passage and must be dropped.",
                                "severity": "blocking",
                            },
                        ],
                    )
                )
            self.assertEqual(name, "holistic")
            self.assertIn("## Verified audit", user)
            self.assertIn("- claim_quantified: pass", user)
            self.assertIn("- variance_reported: fail", user)
            self.assertIn("- ablation_isolates_claim: unclear", user)
            self.assertIn("- breadth_of_evidence: unclear", user)
            self.assertIn("F1 [major] Single accuracy number", user)
            self.assertIn(ACCURACY, user)
            self.assertNotIn("F2", user)
            self.assertNotIn("Invented problem", user)
            self.assertNotIn("never written", user)
            self.assertIn("not a score sheet", user)
            self.assertIn("predicted_mean", user)
            self.assertLess(user.index("## Verified audit"), user.index("## Venue mean"))
            self.assertLess(user.index("## Source Text"), user.index("## Verified audit"))
            return _chat(_mean_content(predicted_mean=4.6, rating=3))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="audited",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"audit": 1, "holistic": 1})
            self.assertEqual(seen, ["audit", "holistic"])
            self.assertEqual(rating["audited_samples"], [4.6])
            self.assertAlmostEqual(rating["audited_mean"], 4.6)
            self.assertEqual(rating["samples"], [3.0])
            self.assertEqual(rating["rating"], 3.0)
            self.assertEqual(rating["n"], 1)
            self.assertEqual(rating["mode"], "audited")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertTrue(rating["verified"])
            self.assertNotIn("mean_samples", rating)
            self.assertNotIn("expert_samples", rating)
            self.assertNotIn("panel_samples", rating)
            self.assertNotIn("predicted_mean", rating)
            audit = rating["audits"][0]
            self.assertEqual(audit["sample"], 0)
            self.assertEqual(
                audit["counts"],
                {
                    "pass": 9,
                    "fail": 1,
                    "unclear": 2,
                    "unanchored": 1,
                    "flaws": 1,
                    "flaws_dropped": 1,
                    "blocking": 0,
                },
            )
            self.assertEqual(rating["checks_samples"], [checks_score(9)])
            self.assertEqual(audit["checks_score"], checks_score(9))
            self.assertEqual([item["id"] for item in audit["checks"]], list(AUDIT_CHECK_IDS))
            by_id = {item["id"]: item for item in audit["checks"]}
            self.assertEqual(by_id["ablation_isolates_claim"]["verdict"], "unclear")
            self.assertEqual(by_id["ablation_isolates_claim"]["claimed"], "pass")
            self.assertFalse(by_id["ablation_isolates_claim"]["verified"])
            self.assertEqual(by_id["ablation_isolates_claim"]["excerpt"], "")
            self.assertEqual(by_id["variance_reported"]["verdict"], "fail")
            self.assertTrue(by_id["variance_reported"]["verified"])
            self.assertEqual(by_id["variance_reported"]["excerpt"], ACCURACY)
            self.assertEqual(by_id["breadth_of_evidence"]["claimed"], "unclear")
            self.assertEqual([item["id"] for item in audit["flaws"]], ["F1"])
            self.assertEqual(audit["flaws"][0]["excerpt"], ACCURACY)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["drop_counts"], {"audit_excerpt": 1, "flaw_excerpt": 1})
            self.assertEqual(review.metadata["judgments"], ["holistic"])

    def test_an_incomplete_checklist_retries_once_then_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            if _judgment_name(payload) != "audit":
                raise AssertionError("the scoring call must not run without an audit")
            checks = json.loads(_audit_content())["checks"][:-1]
            return _chat(json.dumps({"checks": checks, "flaws": []}))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="audited",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("audit", str(caught.exception))
            self.assertIn("missing", str(caught.exception))
            self.assertEqual(server.counts, {"audit": 2})

    def test_two_audited_pairs_run_two_chains(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            if _judgment_name(payload) == "audit":
                return _chat(_audit_content())
            return _chat(_mean_content(predicted_mean=6.4, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="audited",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"audit": 2, "holistic": 2})
            self.assertEqual(rating["audited_samples"], [6.4, 6.4])
            self.assertEqual(rating["checks_samples"], [10.0, 10.0])
            self.assertEqual(rating["n"], 2)
            self.assertEqual([item["sample"] for item in rating["audits"]], [0, 1])

    def test_cheap_audited_mode_runs_only_the_pair(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name == "audit":
                return _chat(_audit_content())
            if name != "holistic":
                raise AssertionError(name)
            return _chat(_mean_content(predicted_mean=5.8, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=1,
                holistic_mode="audited",
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"audit": 1, "holistic": 1})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})


def _expert_content(
    *,
    predicted_mean: float,
    rating: float,
    contribution_type: str = "method",
    triad: tuple[int, int, int] = (3, 3, 2),
    prior_work: list[dict[str, Any]] | None = None,
    field_context: Any = None,
) -> str:
    soundness, presentation, contribution = triad
    return json.dumps(
        {
            "area": "synthetic counting rules",
            "contribution_type": contribution_type,
            "prior_work": prior_work
            if prior_work is not None
            else [
                {"name": "an earlier counting rule", "relation": "same rule on one specimen", "cited": True, "sure": True},
                {"name": "a recalled tally method", "relation": "differs in the pair", "cited": False, "sure": True},
            ],
            "field_context": field_context
            if field_context is not None
            else ["the area compares against the tally method on the standard pair"],
            "soundness": soundness,
            "presentation": presentation,
            "contribution": contribution,
            "strengths": ["the rule is stated as an algorithm"],
            "weaknesses": ["one specimen carries the claim"],
            "rationale": "A known rule on a new pair; one specimen carries it; the panel lands on the reject side.",
            "predicted_mean": predicted_mean,
            "rating": rating,
        }
    )


class ExpertReadTests(unittest.TestCase):
    """``holistic_mode = "expert"``: the round-7 candidate, kept as a declared switch."""

    def test_two_expert_reads_run_together_and_average(self) -> None:
        pending = [
            _expert_content(predicted_mean=5.2, rating=3),
            _expert_content(
                predicted_mean=6.0,
                rating=4,
                contribution_type="Benchmark / dataset",
                triad=(3, 4, 3),
                prior_work=[],
                field_context="one line given as a string",
            ),
        ]
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            self.assertEqual(name, "expert")
            self.assertEqual(payload["temperature"], 0.7)
            system = payload["messages"][0]["content"]
            self.assertIn("contribution_type is one of", system)
            self.assertIn("No other keys", system)
            user = payload["messages"][1]["content"]
            self.assertIn("## Expert read", user)
            self.assertIn("prior_work", user)
            self.assertIn("field_context", user)
            self.assertIn("predicted_mean", user)
            self.assertIn("borderline reject", user)
            self.assertIn(COHORT, user)
            self.assertLess(user.index("## Source Text"), user.index("## Expert read"))
            self.assertNotIn("## Venue mean", user)
            self.assertNotIn("## Review panel", user)
            self.assertNotIn("## Audit", user)
            self.assertNotIn("## Verified audit", user)
            self.assertNotIn("weak-reject", user)
            with lock:
                content = pending.pop()
            return _chat(content)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="expert",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"expert": 2})
            self.assertEqual(sorted(rating["expert_samples"]), [5.2, 6.0])
            self.assertAlmostEqual(rating["expert_mean"], 5.6)
            self.assertEqual(sorted(rating["samples"]), [3.0, 4.0])
            self.assertAlmostEqual(rating["rating"], 3.5)
            self.assertEqual(sorted(rating["form_samples"]), [6.0, 8.0])
            self.assertEqual(rating["n"], 2)
            self.assertEqual(rating["mode"], "expert")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertTrue(rating["verified"])
            for absent in ("mean_samples", "audited_samples", "audits", "predicted_mean", "poles", "panel_samples", "panels"):
                self.assertNotIn(absent, rating)
            forms = sorted(rating["forms"], key=lambda item: item["form_score"])
            self.assertEqual([item["sample"] for item in rating["forms"]], [0, 1])
            self.assertEqual(
                forms[0],
                {
                    "sample": forms[0]["sample"],
                    "contribution_type": "method",
                    "soundness": 3,
                    "presentation": 3,
                    "contribution": 2,
                    "form_score": 6.0,
                    "prior_work": 2,
                    "prior_work_sure": 2,
                    "prior_work_uncited_sure": 1,
                    "field_context_lines": 1,
                },
            )
            self.assertEqual(forms[1]["contribution_type"], "benchmark")
            self.assertEqual(forms[1]["prior_work"], 0)
            self.assertEqual(forms[1]["field_context_lines"], 1)
            blob = json.dumps(rating)
            for text in ("synthetic counting rules", "tally method", "stated as an algorithm", "panel lands"):
                self.assertNotIn(text, blob)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})
            names = sorted(row["judgment"] for row in review.metadata["model_exchanges"])
            self.assertEqual(names, ["expert-1", "expert-2"])

    def test_expert_reads_overlap(self) -> None:
        release = threading.Event()
        seen: list[str] = []
        gate = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            with gate:
                seen.append(name)
                if len(seen) >= 2:
                    release.set()
            if not release.wait(3):
                raise AssertionError("expert reads did not overlap")
            return _chat(_expert_content(predicted_mean=6.2, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="expert",
            )
            run_pipeline(config)
            self.assertEqual(server.counts, {"expert": 2})

    def test_an_unknown_type_retries_once_then_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat(_expert_content(predicted_mean=5.0, rating=3, contribution_type="poem"))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="expert",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("expert", str(caught.exception))
            self.assertIn("contribution_type", str(caught.exception))
            self.assertEqual(server.counts, {"expert": 2})

    def test_cheap_expert_mode_runs_only_the_reads(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name != "expert":
                raise AssertionError(name)
            return _chat(_expert_content(predicted_mean=5.8, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=2,
                holistic_mode="expert",
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"expert": 2})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})

    def test_one_expert_read_when_asked(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "expert")
            return _chat(_expert_content(predicted_mean=4.4, rating=2))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="expert",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"expert": 1})
            self.assertEqual(rating["expert_samples"], [4.4])
            self.assertEqual(rating["samples"], [2.0])
            self.assertEqual(rating["n"], 1)


def _panel_content(
    *,
    ratings: tuple[int, int, int, int],
    predicted_mean: float,
    rating: float,
    reviewers: tuple[str, str, str, str] = ("specialist", "generalist", "methodologist", "senior"),
    confidences: tuple[int, int, int, int] = (4, 3, 4, 3),
    strengths: Any = None,
) -> str:
    reviews = []
    for index in range(4):
        reviews.append(
            {
                "reviewer": reviewers[index],
                "summary": "The paper claims a counting rule for the synthetic cohort.",
                "strengths": strengths if strengths is not None else ["the rule is stated as an algorithm"],
                "weaknesses": ["one specimen carries the claim"],
                "rating": ratings[index],
                "confidence": confidences[index],
            }
        )
    return json.dumps(
        {
            "reviews": reviews,
            "discussion": "The panel splits on novelty; the single specimen decides it.",
            "predicted_mean": predicted_mean,
            "rating": rating,
        }
    )


class ReviewPanelTests(unittest.TestCase):
    """The locked ``panel`` mode: two concurrent simulated panels, no audit, no verification."""

    def test_two_panels_run_together_and_average_their_means(self) -> None:
        pending = [
            _panel_content(ratings=(5, 6, 3, 8), predicted_mean=5.4, rating=3),
            _panel_content(
                ratings=(6, 6, 5, 7),
                predicted_mean=6.1,
                rating=4,
                reviewers=("Reviewer 1 (specialist)", "generalist", "area chair", "Senior researcher"),
                strengths="a single clause as a string",
            ),
        ]
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            self.assertEqual(name, "panel")
            self.assertEqual(payload["temperature"], 0.7)
            system = payload["messages"][0]["content"]
            self.assertIn("exactly 4 objects", system)
            self.assertIn("specialist, generalist, methodologist, senior", system)
            self.assertIn("No other keys", system)
            user = payload["messages"][1]["content"]
            self.assertIn("## Review panel", user)
            self.assertIn("- specialist:", user)
            self.assertIn("- senior:", user)
            self.assertIn("Real panels disagree", user)
            self.assertIn("predicted_mean", user)
            self.assertIn("borderline reject", user)
            self.assertIn(COHORT, user)
            self.assertLess(user.index("## Source Text"), user.index("## Review panel"))
            self.assertNotIn("## Venue mean", user)
            self.assertNotIn("## Expert read", user)
            self.assertNotIn("## Audit", user)
            self.assertNotIn("## Verified audit", user)
            self.assertNotIn("weak-reject", user)
            with lock:
                content = pending.pop()
            return _chat(content)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="panel",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"panel": 2})
            self.assertEqual(sorted(rating["panel_samples"]), [5.5, 6.0])
            self.assertAlmostEqual(rating["panel_mean"], 5.75)
            self.assertEqual(sorted(rating["chair_samples"]), [5.4, 6.1])
            self.assertAlmostEqual(rating["chair_mean"], 5.75)
            self.assertEqual(sorted(rating["high_samples"]), [7.0, 8.0])
            self.assertEqual(sorted(rating["low_samples"]), [3.0, 5.0])
            self.assertEqual(sorted(rating["samples"]), [3.0, 4.0])
            self.assertAlmostEqual(rating["rating"], 3.5)
            self.assertEqual(rating["n"], 2)
            self.assertEqual(rating["mode"], "panel")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertTrue(rating["verified"])
            for absent in ("mean_samples", "expert_samples", "form_samples", "forms", "audited_samples", "audits", "predicted_mean", "poles"):
                self.assertNotIn(absent, rating)
            panels = sorted(rating["panels"], key=lambda item: item["panel_mean"])
            self.assertEqual([item["sample"] for item in rating["panels"]], [0, 1])
            self.assertEqual(
                panels[0],
                {
                    "sample": panels[0]["sample"],
                    "reviews": [
                        {"role": "specialist", "rating": 5, "confidence": 4},
                        {"role": "generalist", "rating": 6, "confidence": 3},
                        {"role": "methodologist", "rating": 3, "confidence": 4},
                        {"role": "senior", "rating": 8, "confidence": 3},
                    ],
                    "panel_mean": 5.5,
                    "high": 8,
                    "low": 3,
                    "off_scale": 0,
                    "chair_mean": 5.4,
                    "decision": 3.0,
                },
            )
            self.assertEqual(
                [review["role"] for review in panels[1]["reviews"]],
                ["specialist", "generalist", "other", "senior"],
            )
            self.assertEqual(panels[1]["off_scale"], 1)
            self.assertEqual((panels[1]["high"], panels[1]["low"]), (7, 5))
            blob = json.dumps(rating)
            for text in ("counting rule", "stated as an algorithm", "single specimen", "panel splits", "a single clause"):
                self.assertNotIn(text, blob)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})
            names = sorted(row["judgment"] for row in review.metadata["model_exchanges"])
            self.assertEqual(names, ["panel-1", "panel-2"])

    def test_panel_calls_overlap(self) -> None:
        release = threading.Event()
        seen: list[str] = []
        gate = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            with gate:
                seen.append(name)
                if len(seen) >= 2:
                    release.set()
            if not release.wait(3):
                raise AssertionError("panel calls did not overlap")
            return _chat(_panel_content(ratings=(6, 6, 5, 8), predicted_mean=6.2, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="panel",
            )
            run_pipeline(config)
            self.assertEqual(server.counts, {"panel": 2})

    def test_a_three_review_panel_retries_once_then_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            content = json.loads(_panel_content(ratings=(5, 6, 3, 8), predicted_mean=5.4, rating=3))
            content["reviews"] = content["reviews"][:3]
            return _chat(json.dumps(content))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="panel",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("panel", str(caught.exception))
            self.assertIn("reviews", str(caught.exception))
            self.assertEqual(server.counts, {"panel": 2})

    def test_cheap_panel_mode_runs_only_the_panels(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name != "panel":
                raise AssertionError(name)
            return _chat(_panel_content(ratings=(5, 6, 6, 8), predicted_mean=6.2, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=2,
                holistic_mode="panel",
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"panel": 2})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})

    def test_one_panel_when_asked(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "panel")
            return _chat(_panel_content(ratings=(3, 5, 3, 6), predicted_mean=4.4, rating=2))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="panel",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"panel": 1})
            self.assertEqual(rating["panel_samples"], [4.25])
            self.assertEqual(rating["chair_samples"], [4.4])
            self.assertEqual(rating["high_samples"], [6.0])
            self.assertEqual(rating["low_samples"], [3.0])
            self.assertEqual(rating["samples"], [2.0])
            self.assertEqual(rating["n"], 1)
            self.assertEqual(rating["panels"][0]["off_scale"], 0)


APPENDIX_LINE = "Synthetic appendix detail that only the appendix holds."
REFERENCE_LINE = "Synthetic reference entry from the reference list."
# A manuscript with a main text, a reference list, and an appendix. The main
# text is padded so the heading sits past the floor share of the text.
LONG_MANUSCRIPT = (
    MANUSCRIPT
    + "".join(f"Synthetic main-text sentence {index} about the counting rule.\n" for index in range(12))
    + "REFERENCES\n"
    + REFERENCE_LINE
    + "\n"
    + "A APPENDIX\n"
    + APPENDIX_LINE
    + "\n"
)


class MatchedPanelTests(unittest.TestCase):
    """The locked ``matched`` mode: two concurrent topic-matched panels on the main text."""

    def test_two_matched_panels_run_together_on_the_main_text(self) -> None:
        from autopaperreview.model_exec import MAIN_TEXT_CAP_MARKER, MAIN_TEXT_END_MARKER

        pending = [
            _panel_content(
                ratings=(5, 6, 3, 8),
                predicted_mean=5.4,
                rating=3,
                reviewers=("insider", "junior", "neighbour", "senior"),
            ),
            _panel_content(
                ratings=(8, 6, 5, 7),
                predicted_mean=6.6,
                rating=5,
                reviewers=("Reviewer 1 (insider)", "PhD student", "area chair", "Senior researcher"),
                strengths="a single clause as a string",
            ),
        ]
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            self.assertEqual(name, "matched")
            self.assertEqual(payload["temperature"], 0.7)
            system = payload["messages"][0]["content"]
            self.assertIn("exactly 4 objects", system)
            self.assertIn("insider, junior, neighbour, senior", system)
            self.assertNotIn("methodologist", system)
            user = payload["messages"][1]["content"]
            self.assertIn("## Assigned panel", user)
            self.assertIn("- insider:", user)
            self.assertIn("- junior:", user)
            self.assertIn("- neighbour:", user)
            self.assertIn("- senior:", user)
            self.assertIn("How reviewers at this venue use the scale", user)
            self.assertIn("predicted_mean", user)
            self.assertIn("borderline reject", user)
            self.assertIn(COHORT, user)
            self.assertIn("Synthetic main-text sentence 11", user)
            self.assertIn(MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(MAIN_TEXT_CAP_MARKER, user)
            self.assertNotIn(REFERENCE_LINE, user)
            self.assertNotIn(APPENDIX_LINE, user)
            self.assertNotIn("\nREFERENCES\n", user)
            self.assertIn("## Listed contributions (audit each one)", user)
            self.assertLess(user.index("## Source Text"), user.index(MAIN_TEXT_END_MARKER))
            self.assertLess(user.index(MAIN_TEXT_END_MARKER), user.index("## Listed contributions"))
            self.assertLess(user.index("## Listed contributions"), user.index("## Assigned panel"))
            for other in ("## Venue mean", "## Expert read", "## Review panel", "## Audit", "## Verified audit", "weak-reject"):
                self.assertNotIn(other, user)
            with lock:
                content = pending.pop()
            return _chat(content)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="matched",
                manuscript=LONG_MANUSCRIPT,
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"matched": 2})
            self.assertEqual(sorted(rating["matched_samples"]), [5.5, 6.5])
            self.assertAlmostEqual(rating["matched_mean"], 6.0)
            self.assertEqual(sorted(rating["chair_samples"]), [5.4, 6.6])
            self.assertAlmostEqual(rating["chair_mean"], 6.0)
            self.assertEqual(sorted(rating["high_samples"]), [8.0, 8.0])
            self.assertEqual(sorted(rating["low_samples"]), [3.0, 5.0])
            self.assertEqual(sorted(rating["samples"]), [3.0, 5.0])
            self.assertAlmostEqual(rating["rating"], 4.0)
            self.assertEqual(rating["n"], 2)
            self.assertEqual(rating["mode"], "matched")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertTrue(rating["verified"])
            main_text = rating["main_text"]
            self.assertEqual(main_text["cut"], "references")
            self.assertGreater(main_text["source_chars"], main_text["shown_chars"])
            self.assertEqual(sorted(main_text), ["cut", "shown_chars", "source_chars"])
            for absent in ("panel_samples", "panel_mean", "panels", "mean_samples", "expert_samples", "forms", "audits", "predicted_mean", "poles"):
                self.assertNotIn(absent, rating)
            panels = sorted(rating["matched_panels"], key=lambda item: item["panel_mean"])
            self.assertEqual([item["sample"] for item in rating["matched_panels"]], [0, 1])
            self.assertEqual(
                panels[0],
                {
                    "sample": panels[0]["sample"],
                    "reviews": [
                        {"role": "insider", "rating": 5, "confidence": 4},
                        {"role": "junior", "rating": 6, "confidence": 3},
                        {"role": "neighbour", "rating": 3, "confidence": 4},
                        {"role": "senior", "rating": 8, "confidence": 3},
                    ],
                    "panel_mean": 5.5,
                    "high": 8,
                    "low": 3,
                    "off_scale": 0,
                    "chair_mean": 5.4,
                    "decision": 3.0,
                },
            )
            self.assertEqual(
                [review["role"] for review in panels[1]["reviews"]],
                ["insider", "junior", "other", "senior"],
            )
            self.assertEqual(panels[1]["off_scale"], 1)
            self.assertEqual((panels[1]["high"], panels[1]["low"]), (8, 5))
            blob = json.dumps(rating)
            for text in ("counting rule", "stated as an algorithm", "single specimen", "panel splits", "a single clause", "Synthetic"):
                self.assertNotIn(text, blob)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})
            names = sorted(row["judgment"] for row in review.metadata["model_exchanges"])
            self.assertEqual(names, ["matched-1", "matched-2"])

    def test_a_short_manuscript_without_a_reference_heading_is_shown_whole(self) -> None:
        from autopaperreview.model_exec import MAIN_TEXT_CAP_MARKER, MAIN_TEXT_END_MARKER

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "matched")
            user = payload["messages"][1]["content"]
            self.assertIn(COHORT, user)
            self.assertIn(ACCURACY, user)
            self.assertNotIn(MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(MAIN_TEXT_CAP_MARKER, user)
            return _chat(
                _panel_content(
                    ratings=(3, 5, 3, 6),
                    predicted_mean=4.4,
                    rating=2,
                    reviewers=("insider", "junior", "neighbour", "senior"),
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="matched",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"matched": 1})
            self.assertEqual(rating["matched_samples"], [4.25])
            self.assertEqual(rating["chair_samples"], [4.4])
            self.assertEqual(rating["high_samples"], [6.0])
            self.assertEqual(rating["low_samples"], [3.0])
            self.assertEqual(rating["samples"], [2.0])
            self.assertEqual(rating["n"], 1)
            self.assertEqual(rating["main_text"]["cut"], "none")
            self.assertEqual(rating["main_text"]["shown_chars"], rating["main_text"]["source_chars"])
            self.assertEqual(rating["matched_panels"][0]["off_scale"], 0)

    def test_matched_calls_overlap(self) -> None:
        release = threading.Event()
        seen: list[str] = []
        gate = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            with gate:
                seen.append(name)
                if len(seen) >= 2:
                    release.set()
            if not release.wait(3):
                raise AssertionError("matched calls did not overlap")
            return _chat(
                _panel_content(
                    ratings=(6, 6, 5, 8),
                    predicted_mean=6.2,
                    rating=4,
                    reviewers=("insider", "junior", "neighbour", "senior"),
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="matched",
            )
            run_pipeline(config)
            self.assertEqual(server.counts, {"matched": 2})

    def test_a_three_review_matched_panel_retries_once_then_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            content = json.loads(
                _panel_content(
                    ratings=(5, 6, 3, 8),
                    predicted_mean=5.4,
                    rating=3,
                    reviewers=("insider", "junior", "neighbour", "senior"),
                )
            )
            content["reviews"] = content["reviews"][:3]
            return _chat(json.dumps(content))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="matched",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("matched", str(caught.exception))
            self.assertIn("reviews", str(caught.exception))
            self.assertEqual(server.counts, {"matched": 2})

    def test_cheap_matched_mode_runs_only_the_matched_panels(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name != "matched":
                raise AssertionError(name)
            return _chat(
                _panel_content(
                    ratings=(5, 6, 6, 8),
                    predicted_mean=6.2,
                    rating=4,
                    reviewers=("insider", "junior", "neighbour", "senior"),
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=2,
                holistic_mode="matched",
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"matched": 2})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})


def _rebuttal_content(
    *,
    ratings: tuple[int, int, int],
    finals: tuple[int, int, int],
    predicted_mean: float,
    rating: float,
    reviewers: tuple[str, str, str] = ("specialist", "generalist", "senior"),
    update_reviewers: tuple[str, str, str] | None = None,
    confidences: tuple[int, int, int] = (4, 3, 3),
    strengths: Any = None,
    response: Any = None,
) -> str:
    reviews = []
    for index in range(3):
        reviews.append(
            {
                "reviewer": reviewers[index],
                "summary": "The paper claims a counting rule for the synthetic cohort.",
                "strengths": strengths if strengths is not None else ["the rule is stated as an algorithm"],
                "weaknesses": ["one specimen carries the claim"],
                "rating": ratings[index],
                "confidence": confidences[index],
            }
        )
    labels = update_reviewers or reviewers
    updates = [
        {
            "reviewer": labels[index],
            "change": "the added comparison settled the concern" if finals[index] != ratings[index] else "the objection stands",
            "final_rating": finals[index],
        }
        for index in range(3)
    ]
    return json.dumps(
        {
            "reviews": reviews,
            "response": response
            if response is not None
            else "The authors point to the appendix for the spread and add the missing comparison.",
            "updates": updates,
            "discussion": "The panel lands on the borderline; the single specimen decides it.",
            "predicted_mean": predicted_mean,
            "rating": rating,
        }
    )


class RebuttalDiscussionTests(unittest.TestCase):
    """The locked ``rebuttal`` mode: two concurrent reviews carried through the discussion on the whole manuscript."""

    def test_two_discussions_run_together_on_the_whole_manuscript(self) -> None:
        from autopaperreview.model_exec import MAIN_TEXT_CAP_MARKER, MAIN_TEXT_END_MARKER

        pending = [
            _rebuttal_content(ratings=(5, 6, 3), finals=(6, 6, 3), predicted_mean=5.4, rating=3),
            _rebuttal_content(
                ratings=(6, 5, 8),
                finals=(8, 8, 8),
                predicted_mean=7.0,
                rating=5,
                reviewers=("Reviewer 1 (specialist)", "area chair", "Senior researcher"),
                update_reviewers=("specialist", "generalist", "generalist"),
                strengths="a single clause as a string",
                response=["a clause", "another clause"],
            ),
        ]
        lock = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            self.assertEqual(name, "rebuttal")
            self.assertEqual(payload["temperature"], 0.7)
            system = payload["messages"][0]["content"]
            self.assertIn("exactly 3 objects", system)
            self.assertIn("specialist, generalist, senior", system)
            self.assertIn("final_rating", system)
            self.assertNotIn("methodologist", system)
            self.assertIn("No other keys", system)
            user = payload["messages"][1]["content"]
            self.assertIn("## Review and discussion", user)
            self.assertIn("- specialist:", user)
            self.assertIn("- generalist:", user)
            self.assertIn("- senior:", user)
            self.assertNotIn("- methodologist:", user)
            self.assertIn("Stage two", user)
            self.assertIn("final_rating", user)
            self.assertIn("Most ratings do not change", user)
            self.assertIn("predicted_mean", user)
            self.assertIn("borderline reject", user)
            self.assertIn(COHORT, user)
            self.assertIn("Synthetic main-text sentence 11", user)
            self.assertIn(REFERENCE_LINE, user)
            self.assertIn(APPENDIX_LINE, user)
            self.assertIn("\nREFERENCES\n", user)
            self.assertNotIn(MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(MAIN_TEXT_CAP_MARKER, user)
            self.assertIn("## Listed contributions (audit each one)", user)
            self.assertLess(user.index("## Source Text"), user.index(REFERENCE_LINE))
            self.assertLess(user.index(REFERENCE_LINE), user.index(APPENDIX_LINE))
            self.assertLess(user.index(APPENDIX_LINE), user.index("## Listed contributions"))
            self.assertLess(user.index("## Listed contributions"), user.index("## Review and discussion"))
            for other in ("## Venue mean", "## Expert read", "## Review panel", "## Assigned panel", "## Audit", "## Verified audit", "weak-reject"):
                self.assertNotIn(other, user)
            with lock:
                content = pending.pop()
            return _chat(content)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="rebuttal",
                manuscript=LONG_MANUSCRIPT,
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"rebuttal": 2})
            self.assertEqual(sorted(rating["rebuttal_samples"]), [5.0, 8.0])
            self.assertAlmostEqual(rating["rebuttal_mean"], 6.5)
            self.assertEqual(sorted(rating["initial_samples"]), [4.6667, 6.3333])
            self.assertAlmostEqual(rating["initial_mean"], 5.5)
            self.assertEqual(sorted(rating["chair_samples"]), [5.4, 7.0])
            self.assertAlmostEqual(rating["chair_mean"], 6.2)
            self.assertEqual(sorted(rating["high_samples"]), [6.0, 8.0])
            self.assertEqual(sorted(rating["low_samples"]), [3.0, 8.0])
            self.assertEqual(sorted(rating["samples"]), [3.0, 5.0])
            self.assertAlmostEqual(rating["rating"], 4.0)
            self.assertEqual(rating["n"], 2)
            self.assertEqual(rating["mode"], "rebuttal")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertTrue(rating["verified"])
            main_text = rating["main_text"]
            self.assertEqual(main_text["cut"], "none")
            self.assertGreater(main_text["source_chars"], 0)
            self.assertEqual(main_text["source_chars"], main_text["shown_chars"])
            self.assertEqual(sorted(main_text), ["cut", "shown_chars", "source_chars"])
            for absent in ("panel_samples", "matched_samples", "panel_mean", "matched_mean", "panels", "matched_panels", "mean_samples", "expert_samples", "forms", "audits", "predicted_mean", "poles"):
                self.assertNotIn(absent, rating)
            panels = sorted(rating["rebuttal_panels"], key=lambda item: item["final_mean"])
            self.assertEqual([item["sample"] for item in rating["rebuttal_panels"]], [0, 1])
            self.assertEqual(
                panels[0],
                {
                    "sample": panels[0]["sample"],
                    "reviews": [
                        {"role": "specialist", "rating": 5, "confidence": 4, "final_rating": 6},
                        {"role": "generalist", "rating": 6, "confidence": 3, "final_rating": 6},
                        {"role": "senior", "rating": 3, "confidence": 3, "final_rating": 3},
                    ],
                    "initial_mean": 4.6667,
                    "final_mean": 5.0,
                    "lift": 0.3333,
                    "raised": 1,
                    "lowered": 0,
                    "high": 6,
                    "low": 3,
                    "off_scale": 0,
                    "label_mismatch": 0,
                    "chair_mean": 5.4,
                    "decision": 3.0,
                },
            )
            self.assertEqual(
                [review["role"] for review in panels[1]["reviews"]],
                ["specialist", "other", "senior"],
            )
            self.assertEqual([review["final_rating"] for review in panels[1]["reviews"]], [8, 8, 8])
            self.assertEqual((panels[1]["raised"], panels[1]["lowered"]), (2, 0))
            self.assertEqual(panels[1]["label_mismatch"], 1)
            self.assertEqual(panels[1]["off_scale"], 0)
            self.assertAlmostEqual(panels[1]["lift"], 1.6667)
            self.assertEqual((panels[1]["high"], panels[1]["low"]), (8, 8))
            blob = json.dumps(rating)
            for text in ("counting rule", "stated as an algorithm", "single specimen", "settled the concern", "objection stands", "point to the appendix", "a single clause", "another clause", "Synthetic"):
                self.assertNotIn(text, blob)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})
            names = sorted(row["judgment"] for row in review.metadata["model_exchanges"])
            self.assertEqual(names, ["rebuttal-1", "rebuttal-2"])

    def test_one_discussion_keeps_a_short_manuscript_whole_and_counts_off_scale_ratings(self) -> None:
        from autopaperreview.model_exec import MAIN_TEXT_CAP_MARKER, MAIN_TEXT_END_MARKER

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "rebuttal")
            user = payload["messages"][1]["content"]
            self.assertIn(COHORT, user)
            self.assertIn(ACCURACY, user)
            self.assertNotIn(MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(MAIN_TEXT_CAP_MARKER, user)
            return _chat(_rebuttal_content(ratings=(3, 5, 6), finals=(3, 5, 7), predicted_mean=4.4, rating=2))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="rebuttal",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"rebuttal": 1})
            self.assertEqual(rating["rebuttal_samples"], [5.0])
            self.assertEqual(rating["initial_samples"], [4.6667])
            self.assertEqual(rating["chair_samples"], [4.4])
            self.assertEqual(rating["high_samples"], [7.0])
            self.assertEqual(rating["low_samples"], [3.0])
            self.assertEqual(rating["samples"], [2.0])
            self.assertEqual(rating["n"], 1)
            self.assertEqual(rating["main_text"]["cut"], "none")
            self.assertEqual(rating["main_text"]["shown_chars"], rating["main_text"]["source_chars"])
            panel = rating["rebuttal_panels"][0]
            self.assertEqual(panel["off_scale"], 1)
            self.assertEqual((panel["raised"], panel["lowered"]), (1, 0))
            self.assertAlmostEqual(panel["lift"], 0.3333)

    def test_the_longest_manuscripts_are_capped_and_the_cut_is_recorded(self) -> None:
        from autopaperreview import model_exec as module

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "rebuttal")
            user = payload["messages"][1]["content"]
            self.assertIn(COHORT, user)
            self.assertIn(module.MAIN_TEXT_CAP_MARKER, user)
            self.assertNotIn(module.MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(APPENDIX_LINE, user)
            self.assertNotIn(REFERENCE_LINE, user)
            self.assertIn("## Listed contributions (audit each one)", user)
            self.assertLess(user.index(COHORT), user.index(module.MAIN_TEXT_CAP_MARKER))
            self.assertLess(user.index(module.MAIN_TEXT_CAP_MARKER), user.index("## Listed contributions"))
            return _chat(_rebuttal_content(ratings=(5, 5, 6), finals=(6, 5, 6), predicted_mean=5.6, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace, mock.patch.object(module, "FULL_TEXT_MAX_CHARS", 200):
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="rebuttal",
                manuscript=LONG_MANUSCRIPT,
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"rebuttal": 1})
            self.assertEqual(rating["main_text"]["cut"], "cap")
            self.assertEqual(rating["main_text"]["shown_chars"], 200)
            self.assertGreater(rating["main_text"]["source_chars"], 200)
            self.assertEqual(rating["rebuttal_samples"], [5.6667])

    def test_discussion_calls_overlap(self) -> None:
        release = threading.Event()
        seen: list[str] = []
        gate = threading.Lock()

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            with gate:
                seen.append(name)
                if len(seen) >= 2:
                    release.set()
            if not release.wait(3):
                raise AssertionError("discussion calls did not overlap")
            return _chat(_rebuttal_content(ratings=(6, 6, 5), finals=(6, 8, 5), predicted_mean=6.2, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="rebuttal",
            )
            run_pipeline(config)
            self.assertEqual(server.counts, {"rebuttal": 2})

    def test_a_two_update_discussion_retries_once_then_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            content = json.loads(_rebuttal_content(ratings=(5, 6, 3), finals=(6, 6, 3), predicted_mean=5.4, rating=3))
            content["updates"] = content["updates"][:2]
            return _chat(json.dumps(content))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="rebuttal",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("rebuttal", str(caught.exception))
            self.assertIn("updates", str(caught.exception))
            self.assertEqual(server.counts, {"rebuttal": 2})

    def test_cheap_rebuttal_mode_runs_only_the_discussions(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name != "rebuttal":
                raise AssertionError(name)
            return _chat(_rebuttal_content(ratings=(5, 6, 6), finals=(6, 6, 8), predicted_mean=6.4, rating=4))

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=2,
                holistic_mode="rebuttal",
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"rebuttal": 2})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})


def _forecast_content(
    *,
    ratings: tuple[int, int, int],
    probabilities: tuple[Any, Any, Any],
    predicted_mean: float,
    rating: float,
    reviewers: tuple[str, str, str] = ("specialist", "generalist", "senior"),
    confidences: tuple[int, int, int] = (4, 3, 3),
    strengths: Any = None,
    discussion: Any = None,
) -> str:
    reviews = []
    for index in range(3):
        reviews.append(
            {
                "reviewer": reviewers[index],
                "summary": "The paper claims a counting rule for the synthetic cohort.",
                "strengths": strengths if strengths is not None else ["the rule is stated as an algorithm"],
                "weaknesses": ["one specimen carries the claim"],
                "rating": ratings[index],
                "probabilities": probabilities[index],
                "confidence": confidences[index],
            }
        )
    body: dict[str, Any] = {"reviews": reviews, "predicted_mean": predicted_mean, "rating": rating}
    if discussion is not None:
        body["discussion"] = discussion
    return json.dumps(body)


class ForecastPanelTests(unittest.TestCase):
    """The locked ``forecast`` mode: one three-reviewer panel with a probability per reviewer on the whole manuscript."""

    def test_one_forecast_panel_is_the_cheap_default_and_ranks_by_expected_ratings(self) -> None:
        from autopaperreview.model_exec import MAIN_TEXT_CAP_MARKER, MAIN_TEXT_END_MARKER

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            self.assertEqual(name, "forecast")
            self.assertEqual(payload["temperature"], 0.7)
            system = payload["messages"][0]["content"]
            self.assertIn("exactly 3 objects", system)
            self.assertIn("specialist, generalist, senior", system)
            self.assertIn("probabilities", system)
            self.assertIn("sum to 1", system)
            self.assertNotIn("methodologist", system)
            self.assertNotIn("final_rating", system)
            self.assertIn("No other keys", system)
            user = payload["messages"][1]["content"]
            self.assertIn("## Review forecast", user)
            self.assertIn("- specialist:", user)
            self.assertIn("- generalist:", user)
            self.assertIn("- senior:", user)
            self.assertNotIn("- methodologist:", user)
            self.assertIn("probabilities: six numbers that sum to one", user)
            self.assertIn("not a hedge", user)
            self.assertIn("predicted_mean", user)
            self.assertIn("borderline reject", user)
            self.assertNotIn("Stage two", user)
            self.assertNotIn("final_rating", user)
            self.assertIn(COHORT, user)
            self.assertIn("Synthetic main-text sentence 11", user)
            self.assertIn(REFERENCE_LINE, user)
            self.assertIn(APPENDIX_LINE, user)
            self.assertNotIn(MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(MAIN_TEXT_CAP_MARKER, user)
            self.assertIn("## Listed contributions (audit each one)", user)
            self.assertLess(user.index("## Source Text"), user.index(REFERENCE_LINE))
            self.assertLess(user.index(APPENDIX_LINE), user.index("## Listed contributions"))
            self.assertLess(user.index("## Listed contributions"), user.index("## Review forecast"))
            for other in ("## Venue mean", "## Expert read", "## Review panel", "## Assigned panel", "## Review and discussion", "## Audit", "## Verified audit", "weak-reject"):
                self.assertNotIn(other, user)
            return _chat(
                _forecast_content(
                    ratings=(5, 6, 3),
                    probabilities=(
                        [0, 0.1, 0.5, 0.3, 0.1, 0],
                        {"1": 0, "3": 0, "5": 0.4, "6": 1.0, "8": 0.6, "10": 0},
                        [0.1, 0.6, 0.3, 0, 0, 0],
                    ),
                    predicted_mean=5.2,
                    rating=3,
                    reviewers=("Reviewer 1 (specialist)", "area chair", "Senior researcher"),
                    strengths="a single clause as a string",
                    discussion="The panel lands on the borderline; the single specimen decides it.",
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                manuscript=LONG_MANUSCRIPT,
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"forecast": 1})
            self.assertEqual(rating["forecast_samples"], [5.0667])
            self.assertAlmostEqual(rating["forecast_mean"], 5.0667)
            self.assertEqual(rating["vote_samples"], [4.6667])
            self.assertAlmostEqual(rating["vote_mean"], 4.6667)
            self.assertEqual(rating["chair_samples"], [5.2])
            self.assertEqual(rating["high_samples"], [6.4])
            self.assertEqual(rating["low_samples"], [3.4])
            self.assertEqual(rating["samples"], [3.0])
            self.assertEqual(rating["rating"], 3.0)
            self.assertEqual(rating["n"], 1)
            self.assertEqual(rating["mode"], "forecast")
            self.assertEqual(rating["locked_candidate"], "forecast-k1")
            self.assertTrue(rating["verified"])
            main_text = rating["main_text"]
            self.assertEqual(main_text["cut"], "none")
            self.assertGreater(main_text["source_chars"], 0)
            self.assertEqual(main_text["source_chars"], main_text["shown_chars"])
            for absent in ("rebuttal_samples", "initial_samples", "panel_samples", "matched_samples", "rebuttal_panels", "panels", "matched_panels", "mean_samples", "expert_samples", "forms", "audits", "predicted_mean", "poles"):
                self.assertNotIn(absent, rating)
            self.assertEqual(
                rating["forecast_panels"],
                [
                    {
                        "sample": 0,
                        "reviews": [
                            {"role": "specialist", "rating": 5, "confidence": 4, "expected": 5.4, "peak": 0.5, "probabilities": [0.0, 0.1, 0.5, 0.3, 0.1, 0.0]},
                            {"role": "other", "rating": 6, "confidence": 3, "expected": 6.4, "peak": 0.5, "probabilities": [0.0, 0.0, 0.2, 0.5, 0.3, 0.0]},
                            {"role": "senior", "rating": 3, "confidence": 3, "expected": 3.4, "peak": 0.6, "probabilities": [0.1, 0.6, 0.3, 0.0, 0.0, 0.0]},
                        ],
                        "forecast_mean": 5.0667,
                        "vote_mean": 4.6667,
                        "high": 6.4,
                        "low": 3.4,
                        "off_scale": 0,
                        "renormalized": 1,
                        "mode_mismatch": 0,
                        "chair_mean": 5.2,
                        "decision": 3.0,
                    }
                ],
            )
            blob = json.dumps(rating)
            for text in ("counting rule", "stated as an algorithm", "single specimen", "a single clause", "borderline", "Synthetic"):
                self.assertNotIn(text, blob)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])
            self.assertEqual(review.metadata["drop_counts"], {})
            names = sorted(row["judgment"] for row in review.metadata["model_exchanges"])
            self.assertEqual(names, ["forecast-1"])

    def test_two_forecast_panels_run_together_and_keep_their_order(self) -> None:
        release = threading.Event()
        seen: list[str] = []
        gate = threading.Lock()
        pending = [
            _forecast_content(ratings=(6, 6, 8), probabilities=([0, 0, 0.2, 0.6, 0.2, 0], [0, 0, 0.1, 0.7, 0.2, 0], [0, 0, 0, 0.3, 0.6, 0.1]), predicted_mean=6.6, rating=4),
            _forecast_content(ratings=(5, 5, 6), probabilities=([0, 0.2, 0.6, 0.2, 0, 0], [0, 0.1, 0.7, 0.2, 0, 0], [0, 0, 0.4, 0.5, 0.1, 0]), predicted_mean=5.3, rating=3),
        ]

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            with gate:
                seen.append(name)
                if len(seen) >= 2:
                    release.set()
                content = pending.pop()
            if not release.wait(3):
                raise AssertionError("forecast calls did not overlap")
            return _chat(content)

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=2,
                holistic_mode="forecast",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"forecast": 2})
            self.assertEqual(rating["n"], 2)
            self.assertEqual(sorted(rating["forecast_samples"]), [5.2, 6.7])
            self.assertEqual(sorted(rating["vote_samples"]), [5.3333, 6.6667])
            self.assertEqual([item["sample"] for item in rating["forecast_panels"]], [0, 1])
            for item in rating["forecast_panels"]:
                self.assertEqual(item["renormalized"], 0)
                self.assertEqual(item["mode_mismatch"], 0)
                self.assertEqual([review["role"] for review in item["reviews"]], ["specialist", "generalist", "senior"])
            review = manifest.stage("review")
            assert review is not None
            names = sorted(row["judgment"] for row in review.metadata["model_exchanges"])
            self.assertEqual(names, ["forecast-1", "forecast-2"])

    def test_the_longest_manuscripts_are_capped_and_off_scale_and_mismatched_votes_are_counted(self) -> None:
        from autopaperreview import model_exec as module

        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            self.assertEqual(_judgment_name(payload), "forecast")
            user = payload["messages"][1]["content"]
            self.assertIn(COHORT, user)
            self.assertIn(module.MAIN_TEXT_CAP_MARKER, user)
            self.assertNotIn(module.MAIN_TEXT_END_MARKER, user)
            self.assertNotIn(APPENDIX_LINE, user)
            self.assertNotIn(REFERENCE_LINE, user)
            self.assertLess(user.index(module.MAIN_TEXT_CAP_MARKER), user.index("## Listed contributions"))
            return _chat(
                _forecast_content(
                    ratings=(7, 6, 5),
                    probabilities=([0, 0, 0, 0.5, 0.5, 0], [0, 0, 0.6, 0.4, 0, 0], [0, 0, 1, 0, 0, 0]),
                    predicted_mean=5.9,
                    rating=4,
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace, mock.patch.object(module, "FULL_TEXT_MAX_CHARS", 200):
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="forecast",
                manuscript=LONG_MANUSCRIPT,
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"forecast": 1})
            self.assertEqual(rating["main_text"]["cut"], "cap")
            self.assertEqual(rating["main_text"]["shown_chars"], 200)
            self.assertGreater(rating["main_text"]["source_chars"], 200)
            panel = rating["forecast_panels"][0]
            self.assertEqual(panel["off_scale"], 1)
            # 7 is not the rating with the most mass (a 6/8 tie), and 6 is not the mode of a 5-heavy forecast.
            self.assertEqual(panel["mode_mismatch"], 2)
            self.assertEqual([review["expected"] for review in panel["reviews"]], [7.0, 5.4, 5.0])
            self.assertEqual(rating["forecast_samples"], [5.8])
            self.assertEqual(rating["vote_samples"], [6.0])

    def test_a_five_probability_forecast_retries_once_then_fails_the_paper(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload, attempt
            return _chat(
                _forecast_content(
                    ratings=(5, 6, 3),
                    probabilities=([0, 0.1, 0.5, 0.3, 0.1], [0, 0, 0.2, 0.5, 0.3, 0], [0.1, 0.6, 0.3, 0, 0, 0]),
                    predicted_mean=5.2,
                    rating=3,
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="forecast",
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("forecast", str(caught.exception))
            self.assertIn("probabilities", str(caught.exception))
            self.assertEqual(server.counts, {"forecast": 2})

    def test_an_all_zero_forecast_is_corrected_on_the_retry(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del payload
            third = [0, 0, 0, 0, 0, 0] if attempt == 1 else [0, 0.5, 0.5, 0, 0, 0]
            return _chat(
                _forecast_content(
                    ratings=(5, 6, 3),
                    probabilities=([0, 0.1, 0.5, 0.3, 0.1, 0], [0, 0, 0.2, 0.5, 0.3, 0], third),
                    predicted_mean=5.2,
                    rating=3,
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=1,
                holistic_mode="forecast",
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(server.counts, {"forecast": 2})
            self.assertEqual(rating["forecast_samples"], [5.2667])
            self.assertEqual(rating["forecast_panels"][0]["reviews"][2]["expected"], 4.0)
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(max(row["attempt"] for row in review.metadata["model_exchanges"]), 2)


class tempfile_dir:
    def __enter__(self) -> Path:
        import tempfile

        self._raw = tempfile.TemporaryDirectory()
        return Path(self._raw.__enter__())

    def __exit__(self, *exc: object) -> None:
        self._raw.__exit__(*exc)


if __name__ == "__main__":
    unittest.main()
