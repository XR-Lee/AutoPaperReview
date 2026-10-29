"""Synthetic tests for the agent drop and the optional hosted vision adapter."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from autopaperreview.models import Severity
from autopaperreview.pipeline import run_pipeline
from autopaperreview.visual import VisualPageRequest
from autopaperreview.visual_agent import AgentFindingsInspector, HostedVisionInspector


def _request(page: int = 1, digest: str | None = None) -> VisualPageRequest:
    return VisualPageRequest(
        page=page,
        page_image_sha256=digest or ("a" * 64),
        image_path="page.png",
        width_px=32,
        height_px=32,
        captions=["Figure 1"],
    )


def _finding(page: int, digest: str, **overrides) -> dict:
    payload = {
        "id": f"V{page}",
        "page": page,
        "bbox": [8, 8, 24, 24],
        "page_image_sha256": digest,
        "kind": "legibility",
        "visible_mark": "tick labels collide with the legend",
        "caption_excerpt": "A schematic.",
        "severity": "major",
        "clause_id": "wc-4",
        "figure_id": "Figure 1",
    }
    payload.update(overrides)
    return payload


class AgentDropTests(unittest.TestCase):
    def test_drop_must_mark_every_page_and_match_the_hash(self) -> None:
        digest = "b" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(
                json.dumps(
                    {
                        "pages_seen": [1],
                        "findings": [_finding(1, digest)],
                    }
                ),
                encoding="utf-8",
            )
            inspector = AgentFindingsInspector()
            inspector.bind(findings_path=path)
            with self.assertRaisesRegex(ValueError, "pages as seen"):
                inspector.assert_pages_seen([1, 2])
            found = inspector.inspect(_request(1, digest), Path(temporary))
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].severity, Severity.major)
            self.assertEqual(found[0].clause_id, "wc-4")
            self.assertEqual(inspector.status, "checked")

    def test_caption_restatement_and_stale_hash_are_rejected(self) -> None:
        digest = "c" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "findings.json"
            path.write_text(
                json.dumps(
                    {
                        "pages_seen": [1],
                        "findings": [_finding(1, digest, visible_mark="A schematic.", caption_excerpt="A schematic.")],
                    }
                ),
                encoding="utf-8",
            )
            inspector = AgentFindingsInspector()
            inspector.bind(findings_path=path)
            with self.assertRaisesRegex(ValueError, "caption"):
                inspector.inspect(_request(1, digest), Path(temporary))
            path.write_text(
                json.dumps(
                    {
                        "pages_seen": [1],
                        "findings": [_finding(1, "d" * 64)],
                    }
                ),
                encoding="utf-8",
            )
            inspector._payload = None
            with self.assertRaisesRegex(ValueError, "hash"):
                inspector.inspect(_request(1, digest), Path(temporary))

    def test_pipeline_accepts_a_synthetic_drop(self) -> None:
        try:
            import reportlab  # noqa: F401
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("visual and pdf extras are required")
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "manuscript.pdf"
            document = canvas.Canvas(str(pdf), pagesize=(612, 792))
            document.drawString(72, 400, "Synthetic page")
            document.save()
            (root / "manuscript.txt").write_text("Synthetic page.\n", encoding="utf-8")
            (root / "prompt.md").write_text("Look at the page.\n", encoding="utf-8")
            # Render once so the drop can cite the real hash, then point the stage at that drop.
            from autopaperreview.visual import render_pdf_pages

            manifest = render_pdf_pages(pdf, root / "preview", dpi=72)
            digest = manifest["pages"][0]["sha256"]
            drop = {
                "pages_seen": [1],
                "findings": [
                    _finding(
                        1,
                        digest,
                        id="V-ink",
                        visible_mark="the word Synthetic is the only ink",
                        caption_excerpt=None,
                        clause_id=None,
                        figure_id=None,
                        severity="minor",
                    )
                ],
            }
            (root / "findings.json").write_text(json.dumps(drop), encoding="utf-8")
            (root / "review.toml").write_text(
                textwrap.dedent(
                    """
                    schema_version = 1

                    [project]
                    id = "agent-visual"
                    title = "Agent visual"
                    source = "manuscript.txt"
                    run_root = ".runs"
                    network_policy = "deny"

                    [[routes]]
                    id = "M9"
                    name = "Layout QA"
                    kind = "visual"
                    prompt = "prompt.md"

                    [[stages]]
                    id = "ingest"
                    type = "ingest"

                    [[stages]]
                    id = "pages"
                    type = "render_pages"
                    depends_on = ["ingest"]
                    [stages.params]
                    dpi = 72

                    [[stages]]
                    id = "inspect"
                    type = "visual_inspect"
                    depends_on = ["pages"]
                    [stages.params]
                    adapter = "autopaperreview.visual_agent:AgentFindingsInspector"
                    findings_path = "findings.json"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            run = run_pipeline(root / "review.toml")
            package = json.loads(
                (root / ".runs" / run.run_id / "stages" / "inspect" / "package.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(package["metadata"]["visual_status"], "checked")
            self.assertEqual(package["visual_findings"][0]["severity"], "minor")
            self.assertEqual(package["visual_findings"][0]["page_image_sha256"], digest)


class HostedVisionTests(unittest.TestCase):
    def test_missing_key_sends_nothing(self) -> None:
        calls: list[object] = []

        def transport(request, timeout):
            calls.append(request)
            raise AssertionError("transport should not be called")

        inspector = HostedVisionInspector(transport=transport)
        inspector.bind(endpoint="https://vision.example/inspect", network_allowed=True)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("APR_VISION_API_KEY", None)
            found = inspector.inspect(_request(), Path("."))
        self.assertEqual(found, [])
        self.assertEqual(inspector.status, "key_absent")
        self.assertEqual(calls, [])

    def test_network_deny_does_not_send_pixels(self) -> None:
        def transport(request, timeout):
            raise AssertionError("transport should not be called")

        inspector = HostedVisionInspector(transport=transport)
        inspector.bind(
            endpoint="https://vision.example/inspect",
            network_allowed=False,
        )
        with mock.patch.dict(os.environ, {"APR_VISION_API_KEY": "test-key"}):
            with self.assertRaisesRegex(PermissionError, "network_policy"):
                inspector.inspect(_request(), Path("."))
        self.assertEqual(inspector.status, "network_denied")

    def test_key_and_allow_policy_post_the_page(self) -> None:
        seen: dict[str, object] = {}

        def transport(request, timeout):
            seen["timeout"] = timeout
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data.decode("utf-8"))
            digest = seen["body"]["page_image_sha256"]
            return json.dumps(
                {
                    "findings": [
                        _finding(
                            1,
                            digest,
                            id="V-api",
                            visible_mark="a red overlap on the axis label",
                            severity="moderate",
                        )
                    ]
                }
            ).encode("utf-8")

        inspector = HostedVisionInspector(transport=transport)
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "page.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
            request = _request()
            request = request.model_copy(update={"image_path": str(image)})
            inspector.bind(endpoint="https://vision.example/inspect", network_allowed=True)
            with mock.patch.dict(os.environ, {"APR_VISION_API_KEY": "test-key"}):
                found = inspector.inspect(request, Path(temporary) / "scratch")
        self.assertEqual(seen["url"], "https://vision.example/inspect")
        self.assertGreaterEqual(seen["timeout"], 7200)
        self.assertIn("image_base64", seen["body"])
        self.assertEqual(found[0].id, "V-api")
        self.assertEqual(found[0].severity, Severity.moderate)
        self.assertEqual(inspector.status, "checked")


if __name__ == "__main__":
    unittest.main()
