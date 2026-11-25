"""Human rating scales for public calibration.

The locked model bins stay 1/3/5/6/8/10. This module only describes the scale
a venue asked its reviewers to use. A year with no discrete scale uses the
explicit integer range in ``rating_scales.json`` and is not folded onto the
2024 bins.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .venues import profile_dir

_YEAR = re.compile(r"(?:ICLR\.cc/|ICLR\s+)(20\d{2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class RatingScale:
    """One allowed set of integer ratings, or an inclusive integer range."""

    key: str
    values: frozenset[int] | None
    minimum: int
    maximum: int
    source: str

    def allows(self, number: int) -> bool:
        if self.values is not None:
            return number in self.values
        return self.minimum <= number <= self.maximum

    def label(self) -> str:
        if self.values is not None:
            ordered = ", ".join(str(item) for item in sorted(self.values))
            return f"{self.key} ({ordered})"
        return f"{self.key} ({self.minimum}–{self.maximum} inclusive)"


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    path = profile_dir() / "rating_scales.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("rating_scales.json must be an object")
    return data


def unparsed_fraction_limit() -> float:
    value = float(_payload().get("unparsed_fraction_limit", 0.0))
    if value < 0 or value > 1:
        raise ValueError("unparsed_fraction_limit must be between 0 and 1")
    return value


def _range_scale(key: str) -> RatingScale:
    block = _payload()["generic_range"]
    return RatingScale(
        key=key,
        values=None,
        minimum=int(block["minimum"]),
        maximum=int(block["maximum"]),
        source=str(block.get("source") or ""),
    )


@lru_cache(maxsize=1)
def registered_scales() -> dict[str, RatingScale]:
    found: dict[str, RatingScale] = {}
    for key, block in _payload()["scales"].items():
        values = frozenset(int(item) for item in block["values"])
        found[str(key)] = RatingScale(
            key=str(key),
            values=values,
            minimum=min(values),
            maximum=max(values),
            source=str(block.get("source") or ""),
        )
    return found


def classic_scale() -> RatingScale:
    """ICLR 2024/2025 bins. The default when a caller does not name a venue."""
    return registered_scales()["iclr2024"]


def scale_for_key(key: str | None) -> RatingScale:
    if not key:
        return classic_scale()
    registered = registered_scales()
    if key in registered:
        return registered[key]
    if key.startswith("range:"):
        return _range_scale(key)
    raise KeyError(f"unknown rating scale {key!r}")


def scale_for_year(year: int | None, *, venue: str = "ICLR") -> RatingScale:
    """Discrete scale for a known ICLR year, otherwise the explicit 0–10 range."""
    if venue.upper() == "ICLR" and year is not None:
        key = f"iclr{int(year)}"
        registered = registered_scales()
        if key in registered:
            return registered[key]
        return _range_scale(f"range:{_payload()['generic_range']['minimum']}-{_payload()['generic_range']['maximum']}")
    return _range_scale(f"range:{_payload()['generic_range']['minimum']}-{_payload()['generic_range']['maximum']}")


def scale_for_venue_text(*parts: str) -> RatingScale:
    """Resolve a scale from an OpenReview venue id or a venue display string."""
    blob = " ".join(part for part in parts if part)
    match = _YEAR.search(blob)
    if match and "iclr" in blob.lower():
        return scale_for_year(int(match.group(1)))
    return scale_for_year(None, venue="")
