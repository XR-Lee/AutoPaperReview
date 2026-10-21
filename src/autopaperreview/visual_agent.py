"""Visual inspectors that sit behind `visual_inspect`.

`AgentFindingsInspector` is the primary path. A person or an agent opens every
rendered page PNG, then drops a JSON file of findings. The inspector checks the
page-image hash, rejects caption restatements, and requires every rendered page
to be marked as seen.

`HostedVisionInspector` is optional. It POSTs a page PNG to a declared endpoint
only when the project network policy is `allow` and an API key is present in
the environment. Importing it does not require a key or a provider SDK. A
missing key records `key_absent` and returns no findings. Pixels are not sent
under any other network policy.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .models import Severity, VisualFinding
from .visual import VisualPageRequest, finding_matches_caption

Transport = Callable[[urllib.request.Request, float], bytes]


class AgentFindingsInspector:
    """Load findings written after the pages were actually opened."""

    requires_network = False
    status = "unconfigured"

    def __init__(self) -> None:
        self._path: Path | None = None
        self._payload: dict[str, Any] | None = None

    def bind(
        self,
        *,
        findings_path: Path | None,
        endpoint: str | None = None,
        api_key_env: str | None = None,
        network_allowed: bool = False,
    ) -> None:
        del endpoint, api_key_env, network_allowed
        self._path = findings_path
        self._payload = None
        self.status = "ready" if findings_path is not None else "unconfigured"

    def _load(self) -> dict[str, Any]:
        if self._payload is not None:
            return self._payload
        if self._path is None or not self._path.is_file():
            raise FileNotFoundError(
                "agent visual inspector requires a findings drop; "
                "open every rendered page, then set findings_path"
            )
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("agent findings drop must be a JSON object")
        seen = payload.get("pages_seen")
        if not isinstance(seen, list) or not all(isinstance(item, int) for item in seen):
            raise ValueError("agent findings drop requires pages_seen as a list of page numbers")
        findings = payload.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError("agent findings drop requires findings to be a list")
        self._payload = payload
        self.status = "checked"
        return payload

    def assert_pages_seen(self, pages: list[int]) -> None:
        payload = self._load()
        seen = set(payload["pages_seen"])
        missing = [page for page in pages if page not in seen]
        if missing:
            raise ValueError(f"agent findings drop did not mark these pages as seen: {missing}")

    def inspect(self, request: VisualPageRequest, scratch: Path) -> list[VisualFinding]:
        del scratch
        payload = self._load()
        if request.page not in set(payload["pages_seen"]):
            raise ValueError(f"page {request.page} was not marked seen in the agent findings drop")
        matched: list[VisualFinding] = []
        for raw in payload["findings"]:
            if int(raw.get("page", -1)) != request.page:
                continue
            finding = VisualFinding.model_validate(raw)
            if finding.page_image_sha256 != request.page_image_sha256:
                raise ValueError(
                    f"visual finding {finding.id} hash does not match rendered page {request.page}"
                )
            if finding_matches_caption(finding):
                raise ValueError(f"visual finding {finding.id} repeats the caption")
            matched.append(finding)
        return matched


class HostedVisionInspector:
    """Optional HTTP vision endpoint. No key is required to construct it."""

    requires_network = True
    status = "unconfigured"

    def __init__(self, transport: Transport | None = None) -> None:
        self._endpoint: str | None = None
        self._api_key_env = "APR_VISION_API_KEY"
        self._network_allowed = False
        self._transport = transport or _urlopen_transport

    def bind(
        self,
        *,
        findings_path: Path | None = None,
        endpoint: str | None = None,
        api_key_env: str | None = None,
        network_allowed: bool = False,
    ) -> None:
        del findings_path
        self._endpoint = (endpoint or "").strip() or None
        self._api_key_env = api_key_env or "APR_VISION_API_KEY"
        self._network_allowed = network_allowed
        if self._endpoint is None:
            self.status = "unconfigured"
        elif not network_allowed:
            self.status = "network_denied"
        elif not os.environ.get(self._api_key_env):
            self.status = "key_absent"
        else:
            self.status = "ready"

    def inspect(self, request: VisualPageRequest, scratch: Path) -> list[VisualFinding]:
        if not self._endpoint:
            self.status = "unconfigured"
            return []
        if not self._network_allowed:
            self.status = "network_denied"
            raise PermissionError(
                "hosted vision adapter sends page images and requires network_policy = allow"
            )
        key = os.environ.get(self._api_key_env, "").strip()
        if not key:
            self.status = "key_absent"
            return []
        image = Path(request.image_path).read_bytes()
        body = json.dumps(
            {
                "page": request.page,
                "page_image_sha256": request.page_image_sha256,
                "width_px": request.width_px,
                "height_px": request.height_px,
                "captions": request.captions,
                "image_base64": base64.b64encode(image).decode("ascii"),
            }
        ).encode("utf-8")
        # The image bytes travel as a sibling part only when a key and allow policy exist.
        scratch.mkdir(parents=True, exist_ok=True)
        upload = scratch / f"page-{request.page:03d}.png"
        upload.write_bytes(image)
        http_request = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "X-Page-Image-SHA256": request.page_image_sha256,
            },
            method="POST",
        )
        try:
            raw = self._transport(http_request, 30.0)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"hosted vision endpoint failed: {exc}") from exc
        payload = json.loads(raw.decode("utf-8"))
        findings = []
        for item in payload.get("findings", []):
            finding = VisualFinding.model_validate(item)
            if finding.page_image_sha256 != request.page_image_sha256 or finding.page != request.page:
                raise ValueError(f"hosted finding {finding.id} does not match the rendered page")
            if finding_matches_caption(finding):
                raise ValueError(f"hosted finding {finding.id} repeats the caption")
            findings.append(finding)
        self.status = "checked"
        return findings


def _urlopen_transport(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def severity_counts(findings: list[VisualFinding]) -> dict[str, int]:
    counts = {item.value: 0 for item in Severity}
    for finding in findings:
        key = finding.severity.value if finding.severity is not None else "unspecified"
        counts[key] = counts.get(key, 0) + 1
    return counts
