"""Test doubles for the visual inspector contract. No manuscript content."""

from __future__ import annotations

from pathlib import Path

from autopaperreview.models import VisualFinding, VisualFindingKind
from autopaperreview.visual import VisualPageRequest


class GoodInspector:
    def inspect(self, request: VisualPageRequest, scratch: Path) -> list[VisualFinding]:
        del scratch
        label = request.page_id or str(request.page)
        return [
            VisualFinding(
                id=f"V{label}",
                page=request.page,
                page_id=request.page_id,
                bbox=[72, 72, 120, 110],
                page_image_sha256=request.page_image_sha256,
                kind=VisualFindingKind.axis_legend,
                visible_mark="tick labeled with a unit",
                figure_id="Figure 1",
                caption_excerpt="A schematic.",
                supports_citation=True,
            )
        ]


class ParaphraseInspector:
    def inspect(self, request: VisualPageRequest, scratch: Path) -> list[VisualFinding]:
        del scratch
        return [
            VisualFinding(
                id="V-bad",
                page=request.page,
                bbox=[72, 72, 120, 110],
                page_image_sha256=request.page_image_sha256,
                kind=VisualFindingKind.legibility,
                visible_mark="same words",
                caption_excerpt="same words",
            )
        ]
