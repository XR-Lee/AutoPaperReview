"""Pull numeric forecast fields out of a worker transcript.

The transcript may contain review prose. This module keeps the decision, the
six probabilities, and the chair mean, then drops every other field. It does
not print or return manuscript text.
"""

from __future__ import annotations

import json
import re
from typing import Any

from autopaperreview.anchors import expected_rating, forecast_mean
from autopaperreview.fusion import decision_bin

_KEEP = ("rating", "probabilities", "confidence")


def _loads_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    span = text[start : end + 1]
    span = re.sub(r",(\s*[}\]])", r"\1", span)
    try:
        payload = json.loads(span)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _from_diff(diff: str) -> dict[str, Any] | None:
    kept: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            kept.append(line[1:])
        elif line.startswith("-"):
            continue
        else:
            kept.append(line)
    return _loads_object("\n".join(kept))


def _seat_rating(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and float(int(value)) == float(value):
        return int(value)
    if isinstance(value, str):
        head = value.strip().split(":", 1)[0].strip()
        if head.isdigit():
            return int(head)
    return None


def numeric_forecast(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The rank score and the venue bin, or None when the payload is not a forecast."""
    reviews = payload.get("reviews")
    if not isinstance(reviews, list) or "predicted_mean" not in payload or "rating" not in payload:
        return None
    seats: list[dict[str, Any]] = []
    expectations: list[float] = []
    for review in reviews:
        if not isinstance(review, dict):
            return None
        probabilities = review.get("probabilities")
        if not isinstance(probabilities, list):
            return None
        try:
            expected, _weights = expected_rating([float(item) for item in probabilities])
        except (TypeError, ValueError):
            return None
        rating = _seat_rating(review.get("rating"))
        if rating is None:
            return None
        confidence = review.get("confidence")
        seats.append(
            {
                "rating": rating,
                "probabilities": [float(item) for item in probabilities],
                "confidence": float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
                "expected": expected,
            }
        )
        expectations.append(expected)
    try:
        rank_score = round(min(10.0, max(1.0, forecast_mean(expectations))), 4)
        decision = int(payload["rating"])
        chair = float(payload["predicted_mean"])
    except (TypeError, ValueError):
        return None
    return {
        "rank_score": rank_score,
        "our_bin": decision_bin(decision),
        "decision": decision,
        "chair_mean": chair,
        "seats": seats,
    }


def _objects_in_text(text: str) -> list[dict[str, Any]]:
    """JSON objects in ``text`` whose raw span mentions a forecast."""
    found: list[dict[str, Any]] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False
    for index, character in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                span = text[start : index + 1]
                if "predicted_mean" in span and "probabilities" in span:
                    payload = _loads_object(span)
                    if isinstance(payload, dict):
                        found.append(payload)
                start = None
    return found


def loose_forecast(text: str) -> dict[str, Any] | None:
    """A forecast written as JSON-like Python, including trailing commas.

    Review prose is never copied. Only the rating, six probabilities, the
    chair mean, and the decision are read.
    """
    if '"predicted_mean"' not in text or '"probabilities"' not in text:
        return None
    positions = [match.start() for match in re.finditer(r'"predicted_mean"', text)]
    for pos in reversed(positions):
        window = text[max(0, pos - 8000) : pos]
        arrays = list(re.finditer(r'"probabilities"\s*:\s*\[([^\]]+)\]', window))
        if len(arrays) < 3:
            continue
        seats: list[dict[str, Any]] = []
        for match in arrays[-3:]:
            numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", match.group(1))]
            if len(numbers) != 6:
                seats = []
                break
            before = window[max(0, match.start() - 120) : match.start()]
            ratings = re.findall(r'"rating"\s*:\s*(\d+)', before)
            if not ratings:
                seats = []
                break
            seats.append({"rating": int(ratings[-1]), "probabilities": numbers})
        if len(seats) != 3:
            continue
        after = text[pos : pos + 240]
        mean = re.search(r'"predicted_mean"\s*:\s*(-?\d+(?:\.\d+)?)', after)
        decision = re.search(r'"rating"\s*:\s*(\d+)', after)
        if mean is None or decision is None:
            continue
        parsed = numeric_forecast(
            {
                "reviews": seats,
                "predicted_mean": float(mean.group(1)),
                "rating": int(decision.group(1)),
            }
        )
        if parsed is not None:
            return parsed
    return None


def _walk(node: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        diff = node.get("diffString")
        if isinstance(diff, str):
            payload = _from_diff(diff)
            if payload is not None:
                parsed = numeric_forecast(payload)
                if parsed is not None:
                    found.append(parsed)
        for key, value in node.items():
            if key == "diffString":
                continue
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)
    elif isinstance(node, str) and "predicted_mean" in node and "probabilities" in node:
        for payload in _objects_in_text(node):
            parsed = numeric_forecast(payload)
            if parsed is not None:
                found.append(parsed)
        loose = loose_forecast(node)
        if loose is not None:
            found.append(loose)


def forecasts_in_transcript(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every numeric forecast in one transcript. The opening request is ignored."""
    found: list[dict[str, Any]] = []
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                continue
            _walk(message, found)
    else:
        _walk(payload, found)
    return found
