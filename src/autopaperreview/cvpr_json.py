"""Schema-aware JSON recovery for CVPR tier model answers."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, TypeVar

Parsed = TypeVar("Parsed")
_FENCE = re.compile(r"^[ \t]*(`{3,})([^`\r\n]*)\r?$", re.MULTILINE)
_JSON_START = re.compile(r'\{\s*(?:["}]|$)|\[\s*(?:[\[\]{"\d-]|true\b|false\b|null\b|$)')
_TRUNCATION_REASONS = {"length", "max_tokens", "max_output_tokens", "max_tokens_truncation"}
_TRUNCATION_MESSAGE = re.compile(r"\b(?:response|output) truncated (?:by|due to) max_(?:output_)?tokens\b", re.IGNORECASE)
_TRUNCATION_LINE = re.compile(
    r"^[ \t]*(?:Error:[ \t]*)?(?:response|output) truncated (?:by|due to) max_(?:output_)?tokens\b",
    re.IGNORECASE | re.MULTILINE,
)


class JsonOutputError(ValueError):
    """The answer is not JSON, does not match its schema, or is truncated."""


def _metadata_truncation(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("finish_reason", "stop_reason", "error_kind"):
        reason = payload.get(key)
        if isinstance(reason, str) and reason.lower() in _TRUNCATION_REASONS:
            return f"{key}={reason.lower()}"
    if payload.get("status") == "incomplete":
        return "status=incomplete"
    message = payload.get("message")
    if isinstance(message, str) and _TRUNCATION_MESSAGE.search(message):
        return "response truncated by max_tokens"
    for key in ("error", "incomplete_details"):
        detail = payload.get(key)
        if isinstance(detail, dict):
            reason = detail.get("reason")
            if isinstance(reason, str) and reason.lower() in _TRUNCATION_REASONS:
                return f"{key}.reason={reason.lower()}"
            found = _metadata_truncation(detail)
            if found:
                return found
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            found = _metadata_truncation(choice)
            if found:
                return found
    return None


def _regions(text: str) -> tuple[list[str], bool]:
    """Find balanced top-level containers without treating quoted braces as syntax."""
    regions: list[str] = []
    stack: list[str] = []
    start = 0
    quoted = False
    escaped = False
    for offset, char in enumerate(text):
        if not stack:
            if char not in "{[":
                continue
            start = offset
            stack.append(char)
            quoted = False
            escaped = False
        elif quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if (stack[-1], char) not in (("{", "}"), ("[", "]")):
                stack.clear()
                continue
            stack.pop()
            if not stack:
                regions.append(text[start : offset + 1])
    return regions, bool(stack) and _JSON_START.match(text, start) is not None


def _json_fences(text: str) -> tuple[list[tuple[str, int]], bool]:
    blocks: list[tuple[str, int]] = []
    opening = None
    for match in _FENCE.finditer(text):
        if opening is None:
            opening = match
        elif not match.group(2).strip() and len(match.group(1)) >= len(opening.group(1)):
            if opening.group(2).strip().lower() == "json":
                blocks.append((text[opening.end() : match.start()], match.end()))
            opening = None
    incomplete = opening is not None and opening.group(2).strip().lower() == "json"
    return blocks, incomplete


def reject_truncated_output(text: str) -> None:
    """Check CLI diagnostics or answer text for explicit completion-limit signals."""
    try:
        payloads = [json.loads(text)]
    except json.JSONDecodeError:
        if _TRUNCATION_LINE.search(text):
            raise JsonOutputError("truncated/max_tokens: response truncated by max_tokens")
        regions, _incomplete = _regions(text)
        payloads = []
        for region in regions:
            try:
                payloads.append(json.loads(region))
            except json.JSONDecodeError:
                continue
    for payload in payloads:
        reason = _metadata_truncation(payload)
        if reason:
            raise JsonOutputError(f"truncated/max_tokens: {reason}")


def parse_json_output(text: str, validate: Callable[[Any], Parsed]) -> Parsed:
    """Try the full answer, reversed JSON fences, then the last top-level object."""
    reject_truncated_output(text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        json_error = exc.msg
    else:
        try:
            return validate(payload)
        except ValueError as exc:
            raise JsonOutputError(f"schema mismatch: {exc}") from exc

    blocks, open_fence = _json_fences(text)
    regions, open_container = _regions(text)
    if open_fence:
        raise JsonOutputError("truncated: unclosed JSON fence (possible max_tokens)")
    unfinished = "truncated: unfinished JSON object, array, or string (possible max_tokens)"
    if blocks:
        suffix = text[blocks[-1][1] :]
        reject_truncated_output(suffix)
        if _regions(suffix)[1]:
            raise JsonOutputError(unfinished)

    schema_error: ValueError | None = None
    objects = [region for region in regions if region.startswith("{")]
    candidates = [(block, False) for block, _end in reversed(blocks)]
    candidates.extend((region, True) for region in objects[-1:])
    for candidate, top_level in candidates:
        incomplete = open_container if top_level else _regions(candidate)[1]
        if incomplete:
            raise JsonOutputError(unfinished)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            json_error = exc.msg
            continue
        reason = _metadata_truncation(payload)
        if reason:
            raise JsonOutputError(f"truncated/max_tokens: {reason}")
        try:
            return validate(payload)
        except ValueError as exc:
            schema_error = exc
    if open_container:
        raise JsonOutputError(unfinished)
    if schema_error is not None:
        raise JsonOutputError(f"schema mismatch: {schema_error}") from schema_error
    raise JsonOutputError(f"not JSON: {json_error}")
