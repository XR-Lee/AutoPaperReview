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
) -> Path:
    (workspace / "manuscript.txt").write_text(MANUSCRIPT, encoding="utf-8")
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
{bilingual_line}{cheap_line}{samples_line}
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


class HolisticSampleTests(unittest.TestCase):
    def test_samples_average_and_a_bad_excerpt_does_not_drop_the_rating(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            self.assertEqual(_judgment_name(payload), "holistic")
            self.assertEqual(payload["temperature"], 0.7)
            rating = (6.0, 7.0, 8.0)[attempt - 1]
            excerpt = "this sentence was not written in the manuscript" if attempt == 1 else ""
            return _chat(
                json.dumps(
                    {
                        "rating": rating,
                        "rationale": "Synthetic cohort is a clear decision.",
                        "strength_excerpt": excerpt,
                        "weakness_excerpt": "",
                    }
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["holistic"],
                holistic_samples=3,
            )
            manifest = run_pipeline(config)
            stage_dir = workspace / ".runs" / manifest.run_id / "stages" / "review"
            package = json.loads((stage_dir / "package.json").read_text(encoding="utf-8"))
            rating = package["metadata"]["holistic_rating"]
            self.assertEqual(rating["samples"], [6.0, 7.0, 8.0])
            self.assertEqual(rating["rating"], 7.0)
            self.assertTrue(rating["verified"])
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["drop_counts"], {"holistic_excerpt": 1})
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
                holistic_samples=1,
            )
            with self.assertRaises(PipelineError) as caught:
                run_pipeline(config)
            self.assertIn("holistic", str(caught.exception))
            self.assertEqual(server.counts["holistic"], 2)

    def test_cheap_mode_calls_only_the_holistic_judgment(self) -> None:
        def reply(payload: dict[str, Any], attempt: int) -> bytes:
            del attempt
            name = _judgment_name(payload)
            if name != "holistic":
                raise AssertionError(name)
            return _chat(
                json.dumps(
                    {
                        "rating": 6.5,
                        "rationale": "Synthetic cohort is a borderline accept.",
                        "strength_excerpt": "",
                        "weakness_excerpt": "",
                    }
                )
            )

        with ChatServer(reply) as server, tempfile_dir() as workspace:
            config = _write_project(
                workspace,
                base_url=server.base_url,
                judgments=["contributions", "ledger", "dimensions", "fatal", "holistic"],
                cheap=True,
                holistic_samples=1,
            )
            manifest = run_pipeline(config)
            self.assertEqual(server.counts, {"holistic": 1})
            review = manifest.stage("review")
            assert review is not None
            self.assertEqual(review.metadata["judgments"], ["holistic"])


class tempfile_dir:
    def __enter__(self) -> Path:
        import tempfile

        self._raw = tempfile.TemporaryDirectory()
        return Path(self._raw.__enter__())

    def __exit__(self, *exc: object) -> None:
        self._raw.__exit__(*exc)


if __name__ == "__main__":
    unittest.main()
