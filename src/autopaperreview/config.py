"""TOML configuration loading and DAG validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .hashing import hash_json
from .models import NetworkPolicy, RouteKind


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(ConfigModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    run_root: str = ".autopaperreview/runs"
    default_language: str = "en"
    network_policy: NetworkPolicy = NetworkPolicy.deny
    copy_source: bool = False


class RouteConfig(ConfigModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1)
    kind: RouteKind
    prompt: str | None = None
    prompt_version: str | None = None
    manuscript_access: bool = True
    network_access: bool = False
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StageConfig(ConfigModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    type: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class HarnessConfig(ConfigModel):
    schema_version: Literal[1] = 1
    project: ProjectConfig
    routes: list[RouteConfig] = Field(default_factory=list)
    stages: list[StageConfig]

    @model_validator(mode="after")
    def validate_graph(self) -> "HarnessConfig":
        stage_ids = [stage.id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("stage IDs must be unique")
        route_ids = [route.id for route in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("route IDs must be unique")

        known = set(stage_ids)
        for stage in self.stages:
            missing = sorted(set(stage.depends_on) - known)
            if missing:
                raise ValueError(f"stage {stage.id} depends on unknown stages: {missing}")
            if stage.id in stage.depends_on:
                raise ValueError(f"stage {stage.id} cannot depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {stage.id: stage for stage in self.stages}

        def visit(stage_id: str) -> None:
            if stage_id in visited:
                return
            if stage_id in visiting:
                raise ValueError(f"stage graph contains a cycle at {stage_id}")
            visiting.add(stage_id)
            for dependency in by_id[stage_id].depends_on:
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)

        for stage_id in stage_ids:
            visit(stage_id)
        return self

    def ordered_stages(self) -> list[StageConfig]:
        result: list[StageConfig] = []
        emitted: set[str] = set()
        pending = [stage for stage in self.stages if stage.enabled]
        while pending:
            ready = [stage for stage in pending if set(stage.depends_on) <= emitted]
            if not ready:
                blocked = {stage.id: stage.depends_on for stage in pending}
                raise ValueError(f"enabled stages are blocked by disabled dependencies: {blocked}")
            for stage in ready:
                result.append(stage)
                emitted.add(stage.id)
                pending.remove(stage)
        return result


@dataclass(frozen=True)
class LoadedConfig:
    path: Path
    workspace: Path
    raw: dict[str, Any]
    config: HarnessConfig
    sha256: str

    def resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.workspace / path).resolve()


def load_config(path: str | Path) -> LoadedConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    config = HarnessConfig.model_validate(raw)
    return LoadedConfig(
        path=config_path,
        workspace=config_path.parent,
        raw=raw,
        config=config,
        sha256=hash_json(raw),
    )
