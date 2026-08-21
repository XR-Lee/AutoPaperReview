"""Stage registry with Python entry-point plugin discovery."""

from __future__ import annotations

from importlib.metadata import entry_points

from .stages.base import StageHandler


class StageRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, StageHandler] = {}

    def register(self, handler: StageHandler) -> None:
        if handler.type_name in self._handlers:
            raise ValueError(f"stage type already registered: {handler.type_name}")
        self._handlers[handler.type_name] = handler

    def get(self, type_name: str) -> StageHandler:
        try:
            return self._handlers[type_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._handlers)) or "none"
            raise KeyError(f"unknown stage type {type_name!r}; available: {available}") from exc

    def names(self) -> list[str]:
        return sorted(self._handlers)

    def load_entry_points(self) -> None:
        for entry_point in entry_points(group="autopaperreview.stages"):
            loaded = entry_point.load()
            handler = loaded() if isinstance(loaded, type) else loaded
            self.register(handler)
