"""The signal a stage raises while it waits for a drop from a host agent."""

from __future__ import annotations

from typing import Any


class AwaitingInput(FileNotFoundError):
    """A declared agent drop does not exist yet.

    ``request`` is JSON-compatible. It names the drop path as declared in
    ``review.toml``, the run-relative files the agent must read, and, for a
    review judgment, the packet digests the drop must echo. The runner records
    the stage as ``awaiting_input`` and keeps running stages that do not
    depend on it. It subclasses ``FileNotFoundError`` so callers that already
    catch a missing drop keep working.
    """

    def __init__(self, message: str, *, request: dict[str, Any]) -> None:
        super().__init__(message)
        self.request = request
