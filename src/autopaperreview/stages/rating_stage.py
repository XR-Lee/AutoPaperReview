"""Deterministic rating and cross-paper calibration stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import artifact_from_path
from ..integrity import assert_inside_workspace
from ..models import ReviewPackage
from ..rating import TieNote, apply_rating, calibrate_packages
from ..reporting import write_package_json
from ..rubrics import COEFFICIENT_SET
from .base import StageContext, StageHandler, StageOutcome


class ParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _artifact(path: Path, context: StageContext, role: str):
    return artifact_from_path(
        path,
        role=role,
        generated_by=context.stage.id,
        base=context.run_dir,
        base_name="run",
    )


def _load_package(context: StageContext) -> ReviewPackage:
    for stage_id in reversed(context.stage.depends_on):
        stage_dir = context.dependency_dir(stage_id)
        for filename in ("consensus.json", "package.json", "review_package.json"):
            candidate = stage_dir / filename
            if candidate.is_file():
                return ReviewPackage.model_validate_json(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError("rate stage requires a dependency package")


class RateParams(ParamsModel):
    confidence: int = Field(default=3, ge=1, le=5)


class RateStage(StageHandler):
    type_name = "rate"
    version = "2"
    params_model = RateParams

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        from ..rubrics import RUBRIC_ID, rubric_fingerprint

        return {
            "coefficient_set": COEFFICIENT_SET,
            "rubric_id": RUBRIC_ID,
            "rubric_sha256": rubric_fingerprint(),
        }

    def run(self, context: StageContext) -> StageOutcome:
        params = RateParams.model_validate(context.stage.params)
        package = apply_rating(_load_package(context), confidence=params.confidence)
        output = context.stage_dir / "package.json"
        write_package_json(package, output)
        score = package.overall_score
        metadata = score.metadata if score is not None else {}
        return StageOutcome(
            artifacts=[_artifact(output, context, "review.package.rated")],
            metadata={
                "coefficient_set": COEFFICIENT_SET,
                "value": None if score is None else score.value,
                "cap_ids": metadata.get("cap_ids", []),
            },
        )


class TieParams(ParamsModel):
    left_project_id: str
    right_project_id: str
    axis: str = Field(min_length=1)
    same_bin_justified: str = Field(min_length=1)


class CalibrateParams(ParamsModel):
    packages: list[str] = Field(min_length=2)
    ties: list[TieParams] = Field(default_factory=list)


class CalibrateBatchStage(StageHandler):
    type_name = "calibrate_batch"
    version = "1"
    params_model = CalibrateParams

    def signature_material(self, context: StageContext) -> dict[str, Any]:
        params = CalibrateParams.model_validate(context.stage.params)
        digests = []
        for relative in params.packages:
            path = assert_inside_workspace(context.workspace / relative, context.workspace)
            digests.append(_sha(path))
        return {"coefficient_set": COEFFICIENT_SET, "package_sha256": digests}

    def run(self, context: StageContext) -> StageOutcome:
        params = CalibrateParams.model_validate(context.stage.params)
        packages = []
        for relative in params.packages:
            path = assert_inside_workspace(context.workspace / relative, context.workspace)
            packages.append(ReviewPackage.model_validate_json(path.read_text(encoding="utf-8")))
        ties = [
            TieNote(item.left_project_id, item.right_project_id, item.axis, item.same_bin_justified)
            for item in params.ties
        ]
        batch = calibrate_packages(packages, ties)
        output = context.stage_dir / "batch_calibration.json"
        output.write_text(
            json.dumps(batch.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return StageOutcome(
            artifacts=[_artifact(output, context, "review.batch_calibration")],
            metadata={"order": batch.order, "pair_count": len(batch.pairs)},
        )


def _sha(path: Path) -> str:
    from ..hashing import sha256_file

    return sha256_file(path)
