"""Shape of a locked proposal-rank cohort.

The published fixed100, confirm100, replications199, top-20%, and 84-NLP
headline aggregates are committed. Per-paper rows stay outside git and are
reread by ``scripts/reproduce_proposal_rank.py`` when
``APR_PROPOSAL_REPRO_DIR`` is set. This module checks a manifest, a
prediction file, and a reference file so ``evaluate-rank`` can score any
locked cohort. It does not contain those papers.

``validate_manifest`` is the strict schema (version 1). ``cohort_cases``
checks the sorted-id digest and also accepts a lock that has no version
field. A year below 1 is rejected in both.
"""

from __future__ import annotations

import re
from typing import Any

from .data import declared_rating_scale, require_finite_number

MANIFEST_VERSION = 1
CASE_SPLITS = frozenset({"test", "dev", "holdout"})


def _object(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be a JSON object")
    return value


def _string(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return value


def index_by_forum(doc: Any, label: str) -> dict:
    if not isinstance(doc, list) or not doc:
        raise ValueError(f"{label} must be a non-empty array")
    indexed = {}
    for item in doc:
        row = _object(item, f"{label} row")
        forum = _string(row.get("forum"), "forum")
        if forum in indexed:
            raise ValueError("duplicate forum ID")
        indexed[forum] = row
    return indexed


def case_split(row: dict, *, legacy: bool) -> str:
    fields = ("split", "partition", "cohort") if legacy else ("split",)
    values = [row[field] for field in fields if field in row]
    if legacy and not values:
        return "test"
    allowed = CASE_SPLITS | {"val"} if legacy else CASE_SPLITS
    if not values or any(not isinstance(value, str) or value not in allowed for value in values):
        raise ValueError("split must be test, dev, or holdout")
    if any(value != values[0] for value in values[1:]):
        raise ValueError("conflicting cohort split fields")
    return values[0]


def _validate_stratum(row: dict) -> None:
    if "stratum" in row and not isinstance(row["stratum"], str):
        raise ValueError("stratum must be a string")


def validate_manifest(doc: Any, *, allow_legacy: bool = False) -> dict:
    """Validate a strict lock, or a versionless published or legacy lock."""
    manifest = _object(doc, "manifest")
    allowed = {"version", "ids_sha256", "cases", "seed", "source"}
    unknown = set(manifest) - allowed
    legacy = allow_legacy and "version" not in manifest
    if unknown and not legacy:
        raise ValueError(f"unknown manifest fields: {sorted(unknown)}")
    if not legacy and (
        type(manifest.get("version")) is not int or manifest["version"] != MANIFEST_VERSION
    ):
        raise ValueError(f"manifest version must be {MANIFEST_VERSION}")
    digest = manifest.get("ids_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("ids_sha256 must be a SHA-256 hex digest")
    for row in index_by_forum(manifest.get("cases"), "manifest cases").values():
        case_split(row, legacy=legacy)
        _validate_stratum(row)
        case_allowed = {
            "forum",
            "split",
            "key",
            "year",
            "rating_scale",
            "arxiv",
            "arxiv_id",
            "stratum",
            "cutoff_ms",
            "submission_ms",
        }
        extra = set(row) - case_allowed
        if extra and not legacy:
            raise ValueError(f"unknown manifest case fields: {sorted(extra)}")
        if "year" in row and (type(row["year"]) is not int or row["year"] < 1):
            raise ValueError("year must be a positive integer venue year")
        declared_rating_scale(row)
        for stamp in ("cutoff_ms", "submission_ms"):
            if stamp in row and (type(row[stamp]) is not int or not 0 < row[stamp] < 2 ** 63):
                raise ValueError(f"{stamp} must be a positive integer millisecond timestamp")
    if "seed" in manifest and not isinstance(manifest["seed"], str):
        raise ValueError("seed must be a string")
    if "source" in manifest and not isinstance(manifest["source"], dict):
        raise ValueError("source must be an object")
    return manifest


def validate_predictions(doc: Any) -> list:
    for row in index_by_forum(doc, "predictions").values():
        require_finite_number(row.get("overall"), "overall")
    return doc


def validate_decision(value: Any) -> None:
    if value is not None and (type(value) is not int or value not in (0, 1)):
        raise ValueError("accept must be 0, 1, or null")


def validate_references(doc: Any, *, allow_legacy: bool = False) -> list:
    allowed = {
        "forum",
        "prospective",
        "final_rating",
        "accept",
        "year",
        "rating_scale",
        "stratum",
        "reference_protocol",
        "reviewers",
        "requests",
        "input_sha256",
        "independent_reference",
    }
    if allow_legacy:
        allowed.update({"full_ratings", "opinion_evidence"})
    for row in index_by_forum(doc, "references").values():
        extra = set(row) - allowed
        if extra:
            raise ValueError(f"unknown reference fields: {sorted(extra)}")
        for field in ("prospective", "final_rating"):
            require_finite_number(row.get(field), field)
        validate_decision(row.get("accept"))
        _validate_stratum(row)
        declared_rating_scale(row, allow_null=True)
    return doc
