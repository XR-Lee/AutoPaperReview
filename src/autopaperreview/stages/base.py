"""Stage extension contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import LoadedConfig, StageConfig
from ..models import Artifact, RunManifest, StageRecord


@dataclass
class StageContext:
    loaded_config: LoadedConfig
    stage: StageConfig
    run_dir: Path
    stage_dir: Path
    source_path: Path
    manifest: RunManifest
    dependency_records: dict[str, StageRecord]

    @property
    def workspace(self) -> Path:
        return self.loaded_config.workspace

    def dependency_dir(self, stage_id: str) -> Path:
        return self.run_dir / "stages" / stage_id


@dataclass
class StageOutcome:
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class StageHandler(ABC):
    type_name: str
    version = "1"
    params_model: type[BaseModel] | None = None

    def validate_params(self, params: dict[str, Any]) -> None:
        if self.params_model is not None:
            self.params_model.model_validate(params)

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        return {}

    @abstractmethod
    def run(self, context: StageContext) -> StageOutcome:
        raise NotImplementedError
