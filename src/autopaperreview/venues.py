"""Venue deadline profiles for concurrent-work checks.

Format-check profiles (page size, margins, anonymity) live on the rating and
visual branches and are not copied here. This loader reads
``profiles/<key>.deadlines.json``. When a format profile ``profiles/<key>.json``
is added later, deadline fields are filled in without replacing format keys.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict


_PROFILES = Path(__file__).resolve().parent / "profiles"
_DEADLINE_FIELDS = (
    "venue",
    "year",
    "abstract_deadline",
    "submission_deadline",
    "deadline_timezone",
)


class VenueDeadlines(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    venue: str
    year: int
    profile_key: str
    source: str
    abstract_deadline: date
    submission_deadline: date
    deadline_timezone: str = "Anywhere on Earth"
    pinned: str
    notes: str = ""

    @property
    def submission_deadline_iso(self) -> str:
        return self.submission_deadline.isoformat()


def profile_dir() -> Path:
    return _PROFILES


def known_venue_keys() -> list[str]:
    keys = sorted(path.name[: -len(".deadlines.json")] for path in _PROFILES.glob("*.deadlines.json"))
    if not keys:
        raise FileNotFoundError(f"no venue deadline profiles in {_PROFILES}")
    return keys


def load_venue_profile(key: str, *, directory: Path | None = None) -> dict:
    """Return deadline fields, overlaid onto a format profile when one exists."""
    root = directory or _PROFILES
    deadline_path = root / f"{key}.deadlines.json"
    if not deadline_path.is_file():
        known = sorted(path.name[: -len(".deadlines.json")] for path in root.glob("*.deadlines.json"))
        raise KeyError(f"unknown venue profile {key!r}; available: {', '.join(known) or 'none'}")
    deadlines = json.loads(deadline_path.read_text(encoding="utf-8"))
    if not isinstance(deadlines, dict):
        raise ValueError(f"{deadline_path.name} must be a JSON object")
    format_path = root / f"{key}.json"
    if not format_path.is_file():
        return deadlines
    merged = json.loads(format_path.read_text(encoding="utf-8"))
    if not isinstance(merged, dict):
        raise ValueError(f"{format_path.name} must be a JSON object")
    for field in _DEADLINE_FIELDS:
        merged.setdefault(field, deadlines[field])
    merged.setdefault("deadline_profile_id", deadlines.get("id"))
    merged.setdefault("deadline_source", deadlines.get("source"))
    return merged


def load_venue_deadlines(key: str, *, directory: Path | None = None) -> VenueDeadlines:
    payload = load_venue_profile(key, directory=directory)
    # A merged format profile keeps its own id. Deadline identity stays on the sidecar.
    root = directory or _PROFILES
    sidecar = json.loads((root / f"{key}.deadlines.json").read_text(encoding="utf-8"))
    data = dict(sidecar)
    for field in _DEADLINE_FIELDS:
        data[field] = payload.get(field, sidecar[field])
    return VenueDeadlines.model_validate(data)


def submission_deadline(key: str) -> date:
    return load_venue_deadlines(key).submission_deadline
