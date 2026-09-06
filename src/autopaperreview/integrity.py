"""Read-only workspace inventory and artifact-aware integrity records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifacts import artifact_from_path
from .models import (
    Artifact,
    EvidenceRecord,
    IntegrityKind,
    IntegrityRecord,
    IntegrityVerdict,
    LocalizedText,
    RetrievedSourceSnapshot,
)


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FIXTURE_ARXIV = re.compile(r"^0+\.0+$")
_SENTENCE = re.compile(r"(?<=[.!?。？！])\s+")


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.split(text) if part and part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def reporting_is_present(text: str, term: str) -> bool:
    """True only when `term` is mentioned as reported, not merely listed as absent."""
    lowered = text.lower()
    needle = term.lower()
    found_positive = False
    for sentence in _sentences(lowered):
        if needle not in sentence:
            continue
        negated = (
            sentence.startswith(("no ", "none "))
            or bool(re.search(r"\bnot\s+report", sentence))
            or bool(re.search(rf"\bno {re.escape(needle)}\b", sentence))
        )
        if not negated:
            found_positive = True
    return found_positive


def _localized(text: str) -> LocalizedText:
    return LocalizedText(primary=text, language="en")


def assert_inside_workspace(path: Path, workspace: Path) -> Path:
    resolved = path.resolve()
    workspace_resolved = workspace.resolve()
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError as exc:
        raise PermissionError(f"workspace inspect refuses path outside workspace: {path}") from exc
    if resolved.is_symlink():
        target = resolved.readlink()
        target_resolved = target if target.is_absolute() else (resolved.parent / target).resolve()
        try:
            target_resolved.relative_to(workspace_resolved)
        except ValueError as exc:
            raise PermissionError(f"workspace inspect refuses symlink escape: {path}") from exc
    return resolved


def collect_inventory(
    paths: list[str],
    *,
    workspace: Path,
    generated_by: str,
) -> list[Artifact]:
    artifacts: list[Artifact] = []
    seen: set[Path] = set()
    for raw in paths:
        candidate = Path(raw).expanduser()
        root = candidate if candidate.is_absolute() else workspace / candidate
        resolved = assert_inside_workspace(root, workspace)
        files = [resolved] if resolved.is_file() else sorted(p for p in resolved.rglob("*") if p.is_file())
        for file_path in files:
            file_path = assert_inside_workspace(file_path, workspace)
            if file_path in seen:
                continue
            seen.add(file_path)
            artifacts.append(
                artifact_from_path(
                    file_path,
                    role="workspace.inventory",
                    generated_by=generated_by,
                    base=workspace,
                    base_name="workspace",
                )
            )
    return artifacts


def _load_json_artifact(artifact: Artifact, workspace: Path) -> dict[str, Any] | None:
    path = workspace / artifact.path if artifact.metadata.get("path_base") == "workspace" else Path(artifact.path)
    if not path.is_file() or path.suffix.lower() != ".json":
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def build_integrity_records(
    *,
    manuscript_text: str,
    snapshots: list[RetrievedSourceSnapshot],
    inventory: list[Artifact],
    evidence: list[EvidenceRecord],
    workspace: Path,
) -> list[IntegrityRecord]:
    evidence_ids = {item.id for item in evidence}
    records: list[IntegrityRecord] = []

    for snapshot in snapshots:
        cited = [f"E-{snapshot.id}"] if f"E-{snapshot.id}" in evidence_ids else []
        fixture = bool((snapshot.metadata or {}).get("fixture")) or (
            snapshot.arxiv_id is not None and _FIXTURE_ARXIV.match(snapshot.arxiv_id)
        )
        if fixture:
            verdict = IntegrityVerdict.missing
            notes = "Fixture or placeholder identifier; not treated as a resolved publication."
            note_code = "fixture"
        elif snapshot.doi or snapshot.arxiv_id:
            verdict = IntegrityVerdict.unclear
            notes = "Identifier is present but was not resolved against a live registry."
            note_code = "unresolved"
        else:
            verdict = IntegrityVerdict.missing
            notes = "No DOI or arXiv ID; reference integrity cannot be established."
            note_code = "no-id"
        records.append(
            IntegrityRecord(
                id=f"INT-REF-{snapshot.id}",
                kind=IntegrityKind.reference_integrity,
                verdict=verdict,
                subject=snapshot.title,
                expected=snapshot.doi or snapshot.arxiv_id,
                observed=None,
                evidence_ids=cited,
                notes=_localized(notes),
                metadata={"snapshot_id": snapshot.id, "fixture": fixture, "note_code": note_code},
            )
        )

    percents = [float(match) for match in _PERCENT.findall(manuscript_text)]
    results_artifact = next((item for item in inventory if Path(item.path).name == "results.json"), None)
    if percents and results_artifact:
        payload = _load_json_artifact(results_artifact, workspace) or {}
        observed = payload.get("accuracy")
        expected = percents[0] / 100.0 if percents[0] > 1 else percents[0]
        if isinstance(observed, (int, float)):
            verdict = IntegrityVerdict.exact if abs(float(observed) - expected) < 1e-9 else IntegrityVerdict.mismatch
            records.append(
                IntegrityRecord(
                    id="INT-RES-accuracy",
                    kind=IntegrityKind.results_integrity,
                    verdict=verdict,
                    subject="manuscript accuracy claim versus workspace results.json",
                    expected=str(expected),
                    observed=str(observed),
                    artifact_sha256s=[results_artifact.sha256],
                    notes=_localized(
                        "Workspace accuracy matches the manuscript claim."
                        if verdict is IntegrityVerdict.exact
                        else "Workspace accuracy disagrees with the manuscript claim."
                    ),
                    metadata={
                        "note_code": (
                            "match" if verdict is IntegrityVerdict.exact else "mismatch"
                        )
                    },
                )
            )
    elif percents:
        records.append(
            IntegrityRecord(
                id="INT-RES-accuracy",
                kind=IntegrityKind.results_integrity,
                verdict=IntegrityVerdict.missing,
                subject="manuscript accuracy claim",
                expected=str(percents[0]),
                notes=_localized("No results.json was present in the inspected workspace."),
                metadata={"note_code": "no-results"},
            )
        )

    has_ci = reporting_is_present(manuscript_text, "confidence interval") or reporting_is_present(
        manuscript_text, "confidence intervals"
    )
    has_seed = reporting_is_present(manuscript_text, "seed") or reporting_is_present(
        manuscript_text, "random seed"
    )
    records.append(
        IntegrityRecord(
            id="INT-REPRO-reporting",
            kind=IntegrityKind.reproducibility_attestation,
            verdict=IntegrityVerdict.exact if has_ci and has_seed else IntegrityVerdict.major,
            subject="repeated-run and uncertainty reporting",
            expected="confidence interval and seed",
            observed=f"ci={has_ci}; seed={has_seed}",
            notes=_localized(
                "Manuscript reports uncertainty and seeds."
                if has_ci and has_seed
                else "Manuscript does not report both a confidence interval and a seed."
            ),
            metadata={"note_code": "CI+seed" if has_ci and has_seed else "no-CI/seed"},
        )
    )
    return records
