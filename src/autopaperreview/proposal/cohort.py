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

from typing import Any

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


def validate_manifest(doc: Any) -> dict:
    """Require the locked-cohort fields. Extra keys are rejected."""
    manifest = _object(doc, "manifest")
    allowed = {"version", "ids_sha256", "cases", "seed", "source"}
    unknown = set(manifest) - allowed
    if unknown:
        raise ValueError(f"unknown manifest fields: {sorted(unknown)}")
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError(f"manifest version must be {MANIFEST_VERSION}")
    digest = manifest.get("ids_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("ids_sha256 must be a SHA-256 hex digest")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("manifest needs a non-empty cases list")
    forums = set()
    for case in cases:
        row = _object(case, "manifest case")
        forum = _string(row.get("forum"), "forum")
        if forum in forums:
            raise ValueError("duplicate forum ID")
        forums.add(forum)
        if row.get("split") not in CASE_SPLITS:
            raise ValueError("split must be test, dev, or holdout")
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
        if extra:
            raise ValueError(f"unknown manifest case fields: {sorted(extra)}")
        if "year" in row and (type(row["year"]) is not int or row["year"] < 1):
            raise ValueError("year must be a positive integer venue year")
        for stamp in ("cutoff_ms", "submission_ms"):
            if stamp in row and type(row[stamp]) is not int:
                raise ValueError(f"{stamp} must be an integer millisecond timestamp")
    if "seed" in manifest and not isinstance(manifest["seed"], str):
        raise ValueError("seed must be a string")
    if "source" in manifest and not isinstance(manifest["source"], dict):
        raise ValueError("source must be an object")
    return manifest


def validate_predictions(doc: Any) -> list:
    if not isinstance(doc, list) or not doc:
        raise ValueError("predictions must be a non-empty array")
    forums = set()
    for item in doc:
        row = _object(item, "prediction")
        forum = _string(row.get("forum"), "forum")
        if forum in forums:
            raise ValueError("duplicate forum ID")
        forums.add(forum)
        overall = row.get("overall")
        if isinstance(overall, bool) or not isinstance(overall, (int, float)):
            raise ValueError("overall must be a finite number")
    return doc


def validate_references(doc: Any) -> list:
    if not isinstance(doc, list) or not doc:
        raise ValueError("references must be a non-empty array")
    forums = set()
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
    for item in doc:
        row = _object(item, "reference")
        extra = set(row) - allowed
        if extra:
            raise ValueError(f"unknown reference fields: {sorted(extra)}")
        forum = _string(row.get("forum"), "forum")
        if forum in forums:
            raise ValueError("duplicate forum ID")
        forums.add(forum)
        for field in ("prospective", "final_rating"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be a number")
        if "accept" in row and row["accept"] is not None and row["accept"] not in (0, 1):
            raise ValueError("accept must be 0, 1, or null")
        if "year" in row and row["year"] is not None and (
            type(row["year"]) is not int or row["year"] < 1
        ):
            raise ValueError("year must be a positive integer venue year")
    return doc
