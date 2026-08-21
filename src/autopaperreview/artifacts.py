"""Artifact creation and portable path resolution."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .models import Artifact


def artifact_from_path(
    path: Path,
    *,
    role: str,
    generated_by: str | None = None,
    base: Path | None = None,
    base_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    resolved = path.resolve()
    display_path = str(resolved)
    artifact_metadata = dict(metadata or {})
    if base is not None:
        try:
            display_path = str(resolved.relative_to(base.resolve()))
            if base_name:
                artifact_metadata["path_base"] = base_name
        except ValueError:
            artifact_metadata["path_base"] = "absolute"
    media_type, _ = mimetypes.guess_type(resolved.name)
    return Artifact(
        path=display_path,
        sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        role=role,
        media_type=media_type or "application/octet-stream",
        generated_by=generated_by,
        metadata=artifact_metadata,
    )


def resolve_artifact_path(artifact: Artifact, *, workspace: Path, run_dir: Path) -> Path:
    path = Path(artifact.path)
    if path.is_absolute():
        return path
    base_name = artifact.metadata.get("path_base")
    if base_name == "workspace":
        return (workspace / path).resolve()
    return (run_dir / path).resolve()
