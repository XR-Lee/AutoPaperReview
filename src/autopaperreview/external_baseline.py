"""Score agreement for an external reviewer on a local calibration cache.

The cache is gitignored. This module does not fetch manuscripts, and it does
not store forum ids, titles, or review text. Callers pass numbers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .public_calibration import kendall_tau, nearest_bin, roc_auc, spearman

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_RATING_HEADING = re.compile(
    r"(?:^|\n)\s*#{0,3}\s*rating\s*:?\s*(?:\n|\s)+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_BARE_RATING = re.compile(r"\brating\b[^0-9]{0,40}(\d+(?:\.\d+)?)", re.IGNORECASE)


def manuscript_excerpt(text: str, *, limit: int, start_at_abstract: bool) -> str:
    """Main-text excerpt. Appendix after a references heading is dropped."""
    if limit < 1:
        raise ValueError("limit must be positive")
    body = text.replace("\r\n", "\n")
    if start_at_abstract:
        match = re.search(r"\bABSTRACT\b", body, re.IGNORECASE)
        if match and match.start() < 8000:
            body = body[match.start() :]
    reference = re.search(
        r"\n\s*(?:references|r e f e r e n c e s)\s*\n",
        body,
        re.IGNORECASE,
    )
    if reference and reference.start() > 1500:
        body = body[: reference.start()]
    body = body.strip()
    if len(body) > limit:
        body = body[:limit]
    return body


def parse_json_field(text: str, field: str) -> float | None:
    """Read a numeric field from a JSON object, optionally inside a fence."""
    candidates = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for blob in candidates:
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and field in payload:
            return _as_score(payload[field])
    return None


def parse_rating_heading(text: str) -> float | None:
    """First integer under a Rating heading. Ten stays ten, not one."""
    match = _RATING_HEADING.search(text)
    if match:
        return _as_score(match.group(1))
    match = _BARE_RATING.search(text)
    if match:
        return _as_score(match.group(1))
    return None


def parse_last_rating(text: str) -> float | None:
    """Last rating-like number. Used when a trace states several reviewer scores."""
    found = [item.group(1) for item in _RATING_HEADING.finditer(text)]
    if not found:
        found = [item.group(1) for item in _BARE_RATING.finditer(text)]
    if not found:
        return None
    return _as_score(found[-1])


def _as_score(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            return None
        number = float(match.group(0))
    if not 0 <= number <= 10:
        return None
    return number


def histogram(scores: list[float]) -> dict[str, int]:
    """Counts on the ICLR bins after nearest-bin snapping. Ties stay low."""
    counts = {str(bin_value): 0 for bin_value in (1, 3, 5, 6, 8, 10)}
    for score in scores:
        counts[str(nearest_bin(score))] += 1
    return counts


def summarize_scores(
    scores: list[float],
    means: list[float],
    decisions: list[str],
    ratings: list[list[int]] | None = None,
) -> dict[str, Any]:
    """Agreement of native scores with official means. No fitted weights."""
    if not scores or len(scores) != len(means) or len(scores) != len(decisions):
        raise ValueError("scores, means, and decisions must be the same non-empty length")
    if ratings is not None and len(ratings) != len(scores):
        raise ValueError("ratings must align with scores")
    labels = [1 if decision == "accept" else 0 for decision in decisions]
    bins = [nearest_bin(score) for score in scores]
    human_bins = [nearest_bin(mean) for mean in means]
    absolute = [abs(score - mean) for score, mean in zip(scores, means, strict=True)]
    bin_absolute = [abs(bin_value - mean) for bin_value, mean in zip(bins, means, strict=True)]
    exact = [bin_value == human for bin_value, human in zip(bins, human_bins, strict=True)]
    reviewer_hits: list[float] = []
    if ratings:
        for score, group in zip(scores, ratings, strict=True):
            if not group:
                continue
            snapped = nearest_bin(score)
            reviewer_hits.append(sum(item == snapped for item in group) / len(group))
    score_mean = sum(scores) / len(scores)
    human_mean = sum(means) / len(means)
    accept_scores = [score for score, decision in zip(scores, decisions, strict=True) if decision == "accept"]
    reject_scores = [score for score, decision in zip(scores, decisions, strict=True) if decision == "reject"]

    def _mean(values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    return {
        "n": len(scores),
        "n_accept": len(accept_scores),
        "n_reject": len(reject_scores),
        "spearman": spearman(scores, means),
        "kendall": kendall_tau(scores, means),
        "mae": _mean(absolute),
        "mae_nearest_bin": _mean(bin_absolute),
        "bias": score_mean - human_mean,
        "score_mean": score_mean,
        "human_mean": human_mean,
        "bin_agreement": _mean([1.0 if item else 0.0 for item in exact]),
        "reviewer_bin_agreement": _mean(reviewer_hits),
        "accept_reject_auc": roc_auc(scores, labels),
        "histogram": histogram(scores),
        "mean_accept": _mean(accept_scores),
        "mean_reject": _mean(reject_scores),
        "bin_rule": "nearest bin on 1/3/5/6/8/10; a distance tie stays on the lower bin",
        "mae_rule": "mean absolute error of the native score against the mean official rating",
    }
